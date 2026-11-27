import pytest

from linkread_desktop.errors import ExtractionError, UnsupportedSourceError
from linkread_desktop.metadata import canonical_video_url, thumbnail_url, video_metadata


class Response:
    def __init__(self, payload=None, status=200, location=None):
        self.payload, self.status_code = payload, status
        self.headers = {"Location": location} if location else {}
    def json(self): return self.payload
    def raise_for_status(self): pass


class Client:
    def __init__(self, responses): self.responses, self.calls = iter(responses), []
    def get(self, url, **kw):
        self.calls.append((url, kw))
        return next(self.responses)


def test_youtube_preview_is_lightweight_and_does_not_claim_duration_or_captions():
    client = Client([Response({"title": "Title", "author_name": "Author"})])
    data = video_metadata("https://youtu.be/1-Fo8McCD9w?si=tracking", session=client).public()
    assert data["title"] == "Title" and data["author"] == "Author"
    assert data["durationMs"] is None
    assert data["thumbnailUrl"] == "https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"
    assert len(client.calls) == 1 and client.calls[0][0].endswith("/oembed")


def test_bilibili_part_metadata_cover_author_and_seconds():
    client = Client([Response({"code": 0, "data": {"title": "Main", "owner": {"name": "UP"},
                      "pic": "https://i0.hdslb.com/bfs/archive/test.jpg",
                      "pages": [{"duration": 20}, {"duration": 30, "part": "Second"}]}})])
    data = video_metadata("https://www.bilibili.com/video/BV1rxqmBhE91?p=2", session=client)
    assert "P2 Second" in data.source.title
    assert data.source.duration_ms == 30000
    assert data.source.thumbnail_url == data.thumbnail_url
    assert data.public()["author"] == "UP"


def test_short_link_never_follows_untrusted_redirect():
    client = Client([Response(status=302, location="https://127.0.0.1/internal")])
    with pytest.raises(UnsupportedSourceError): canonical_video_url("https://b23.tv/xyz", client)
    assert len(client.calls) == 1 and client.calls[0][1]["allow_redirects"] is False


def test_short_link_refuses_cross_platform_redirect_and_missing_location():
    yt = Client([Response(status=302, location="https://www.youtube.com/watch?v=1-Fo8McCD9w")])
    with pytest.raises(UnsupportedSourceError, match="非 B 站域名"):
        canonical_video_url("https://b23.tv/xyz", yt)
    assert len(yt.calls) == 1

    empty = Client([Response(status=302, location="")])
    with pytest.raises(ExtractionError, match="跳转"):
        canonical_video_url("https://b23.tv/xyz", empty)
    assert len(empty.calls) == 1


@pytest.mark.parametrize("url", ["https://evil.example/a.jpg", "http://evil.example/a.jpg", "https://i.ytimg.com.evil.example/a.jpg"])
def test_thumbnail_is_allowlisted(url):
    assert thumbnail_url(url) is None


def test_invalid_metadata_is_readable_error():
    with pytest.raises(ExtractionError, match="仍可直接"):
        video_metadata("https://youtu.be/1-Fo8McCD9w", session=Client([Response({})]))

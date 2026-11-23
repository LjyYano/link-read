"""Public Apple metadata / RSS fixtures; no network, login or paid ASR."""
import json
from types import SimpleNamespace

import pytest
import requests

from linkread_desktop.errors import ExtractionError, SubtitleUnavailableError
from linkread_desktop.extractors import apple_podcasts as apple
from linkread_desktop.extractors import podcast_http as http

URL = "https://podcasts.apple.com/cn/podcast/id1552904790?i=1000801308527"
AUDIO = "https://media.example.com/episode.m4a"
FEED = "https://feeds.example.com/show.xml"
CAPTION = "https://feeds.example.com/captions.vtt"


def lookup(*, episode_id=1000801308527, guid="episode-guid", audio=AUDIO):
    return {"resultCount": 2, "results": [
        {"kind": "podcast", "collectionId": 1552904790, "artistName": "节目作者", "feedUrl": FEED},
        {"kind": "podcast-episode", "wrapperType": "podcastEpisode", "trackId": episode_id,
         "collectionId": 1552904790, "trackName": "用户指定的单集", "episodeGuid": guid,
         "episodeContentType": "audio", "episodeUrl": audio, "trackTimeMillis": 2467000,
         "artworkUrl600": "https://is1-ssl.mzstatic.com/image/600x600bb.jpg"},
    ]}


def feed(*, transcript='', guid="episode-guid", enclosure=AUDIO):
    return (f'<rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">'
            f'<channel><title>节目</title><language>zh-CN</language>'
            f'<item><title>另一期</title><guid>other-guid</guid><enclosure url="https://media.example.com/other.mp3"/></item>'
            f'<item><title>同名不用于匹配</title><guid>{guid}</guid><enclosure url="{enclosure}"/>{transcript}</item>'
            '</channel></rss>').encode()


class Response:
    def __init__(self, body=b"", status=200, headers=None):
        self.body = body.encode() if isinstance(body, str) else body
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start:start + chunk_size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        assert kwargs["allow_redirects"] is False
        assert 0 < kwargs["timeout"][0] <= 10 and 0 < kwargs["timeout"][1] <= 30
        assert kwargs["stream"] is True
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))])


def lookup_response(**kwargs):
    return Response(json.dumps(lookup(**kwargs), ensure_ascii=False))


def metadata_response(**kwargs):
    return Response(page_payload(**kwargs))


def test_generation_resolves_exact_episode_from_single_page_without_catalog():
    session = Session(metadata_response())
    episode = apple.resolve_episode(URL, session=session)
    assert episode.source.title == "用户指定的单集"
    assert episode.source.duration_ms == 2467000
    assert episode.source.platform == "apple_podcasts"
    assert episode.source.video_id == "1000801308527"
    assert episode.source.thumbnail_url.endswith("600x600bb.jpg")
    assert episode.guid == "episode-guid" and episode.audio_url == AUDIO
    assert len(session.calls) == 1
    assert session.calls[0][0] == URL


def page_payload(*, actual_id="1000801308527", entitlement="free", title="用户指定的单集"):

    offer = {"contentId": actual_id, "guid": "episode-guid", "showOffer": {
        "adamId": "1552904790", "title": "节目", "feedUrl": FEED}}
    data = {"data": [{"intent": {"$kind": "EpisodePageIntent", "adamId": actual_id, "showAdamId": "1552904790"},
        "data": {"headerButtonItems": [{"model": {"id": actual_id, "showAdamId": "1552904790",
            "title": title, "showTitle": "节目", "entitlementState": entitlement, "duration": 2467,
            "episodeArtwork": {"template": "https://is1-ssl.mzstatic.com/image/600x600bb.jpg"},
            "playAction": {"episodeOffer": offer}, "mediaEnclosures": [{"streamUrl": AUDIO, "mediaKinds": ["audio"]}]}}]}}]}
    return '<script type="application/json" id="serialized-server-data">' + json.dumps(data) + '</script>'


def test_old_episode_is_not_limited_to_latest_200_catalog_entries():
    session = Session(Response(page_payload(title="旧单集")))
    episode = apple.resolve_episode(URL, session=session)
    assert episode.source.title == "旧单集"
    assert [url for url, _ in session.calls] == [URL]


def test_changed_page_shape_falls_back_to_catalog_for_exact_episode():
    session = Session(Response("<html>different public markup</html>"), lookup_response())
    episode = apple.resolve_episode(URL, session=session)
    assert episode.source.video_id == "1000801308527" and episode.guid == "episode-guid"
    assert len(session.calls) == 2
    assert "entity=podcastEpisode&limit=200" in session.calls[1][0]


def test_catalog_fallback_never_uses_latest_or_wrong_episode():
    session = Session(Response(page_payload(actual_id="222222")), lookup_response(episode_id=222222))
    with pytest.raises(ExtractionError):
        apple.resolve_episode(URL, session=session)


@pytest.mark.parametrize("response", [Response(page_payload(entitlement="subscription")), Response("Forbidden",403)])
def test_paid_or_permission_page_never_tries_catalog_fallback(response):
    session = Session(response)
    with pytest.raises(ExtractionError):
        apple.resolve_episode(URL, session=session)
    assert len(session.calls) == 1


def test_exact_rss_guid_subtitle_preserves_true_timestamps():
    subtitle = '<podcast:transcript url="' + CAPTION + '" type="text/vtt" language="zh-CN"/>'
    session = Session(metadata_response(), Response(feed(transcript=subtitle)),
                      Response("WEBVTT\n\n00:00:01.234 --> 00:00:02.345\n你好。\n\n00:40:00.000 --> 00:40:05.000\n最后一句。\n"))
    doc = apple.extract_apple_podcasts(URL, session=session)
    assert [(s.start_ms, s.end_ms, s.text) for s in doc.segments] == [
        (1234, 2345, "你好。"), (2400000, 2405000, "最后一句。")]
    assert doc.language == "zh-CN" and doc.transcript_source == "subtitle"
    assert [url for url, _ in session.calls][1:] == [FEED, CAPTION]
    assert AUDIO not in [url for url, _ in session.calls]


def test_absent_timed_subtitles_only_after_matching_good_feed():
    with pytest.raises(SubtitleUnavailableError):
        apple.extract_apple_podcasts(URL, session=Session(metadata_response(), Response(feed())))


def test_uses_declared_episode_language_for_original_podcast_track():
    english = "https://feeds.example.com/captions-en.vtt"
    tags = (f'<language>en-US</language><podcast:transcript url="{CAPTION}" type="text/vtt" language="zh-CN"/>'
            f'<podcast:transcript url="{english}" type="text/vtt" language="en"/>')
    session = Session(metadata_response(), Response(feed(transcript=tags)),
                      Response("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nOriginal subtitle\n"))
    doc = apple.extract_apple_podcasts(URL, session=session)
    assert session.calls[-1][0] == english
    assert doc.language == "en" and doc.segments[0].text == "Original subtitle"


@pytest.mark.parametrize("response", [Response(b""), Response("not RSS"), Response("Forbidden", 403),
                                      Response(feed(guid="other-guid")), requests.Timeout()])
def test_feed_failure_cannot_trigger_paid_asr(response):
    with pytest.raises(ExtractionError):
        apple.extract_apple_podcasts(URL, session=Session(metadata_response(), response))


def test_dtd_is_rejected_without_expanding_entities():
    content = b'<!DOCTYPE rss [<!ENTITY x "expanded">]><rss><channel><item><guid>episode-guid</guid></item></channel></rss>'
    with pytest.raises(ExtractionError, match="XML"):
        apple.extract_apple_podcasts(URL, session=Session(metadata_response(), Response(content)))


@pytest.mark.parametrize("response", [Response(""), Response("WEBVTT\n\n"), Response("login", 403), Response(b"\xff\xfe")])
def test_broken_declared_caption_is_not_missing_caption(response):
    tag = f'<podcast:transcript url="{CAPTION}" type="text/vtt"/>'
    with pytest.raises(ExtractionError):
        apple.extract_apple_podcasts(URL, session=Session(metadata_response(), Response(feed(transcript=tag)), response))


def test_json_podcast_namespace_transcript():
    tag = f'<podcast:transcript url="https://feeds.example.com/captions.json" type="application/json"/>'
    content = json.dumps({"version": "1.0.0", "segments": [
        {"startTime": 1.125, "endTime": 2.5, "body": "真话"},
        {"startTime": 500.5, "endTime": 502, "body": "完整保留结尾"}]})
    doc = apple.extract_apple_podcasts(URL, session=Session(metadata_response(), Response(feed(transcript=tag)), Response(content)))
    assert doc.segments[0].start_ms == 1125
    assert doc.segments[-1].end_ms == 502000


def test_json_optional_end_time_is_not_invented():
    segments = apple._json_transcript(json.dumps({"segments": [{"startTime": 10.25, "body": "原文"}]}).encode())
    assert segments[0].start_ms == 10250 and segments[0].end_ms is None


def test_malformed_subtitle_block_cannot_silently_drop_text():
    content = ("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n第一句话\n\n"
               "损坏的时间戳\n这句话也必须完整处理\n\n00:00:03.000 --> 00:00:04.000\n最后一句").encode()
    with pytest.raises(ExtractionError, match="缺失时间戳"):
        apple._timed_transcript(content)


def test_valid_cue_identifiers_with_note_prefix_are_preserved():
    content = b"WEBVTT\n\nNOTE metadata\ncomment\n\nNOTE-ID\n00:00:01.000 --> 00:00:02.000\ncontent\n"
    assert apple._timed_transcript(content)[0].text == "content"


def test_overlapping_repeated_podcast_speech_is_preserved():
    content = ("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n对，对。\n\n"
               "00:00:02.000 --> 00:00:04.000\n对，对。\n").encode()
    segments = apple._timed_transcript(content)
    assert [(row.start_ms, row.end_ms, row.text) for row in segments] == [
        (1000, 3000, "对，对。"), (2000, 4000, "对，对。")]


def test_broken_arrow_cue_is_never_silently_skipped():
    content = ("1\n00:00:01,000 --> 00:00:02,000\n完整\n\n"
               "2\n00:00:03,000 -> 00:00:04,000\n也要保留").encode()
    with pytest.raises(ExtractionError):
        apple._timed_transcript(content)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, "1"])
def test_json_rejects_invalid_timestamps(bad):
    content = json.dumps({"segments": [{"startTime": bad, "endTime": 3, "body": "内容"}]}).encode()
    with pytest.raises(ExtractionError):
        apple._json_transcript(content)


@pytest.mark.parametrize("url", ["http://127.0.0.1/audio", "https://[::1]/", "http://169.254.169.254/", "http://10.0.0.1/",
                                  "file:///etc/passwd", "https://user:secret@media.example.com/file", "https://media.example.com:8443/file",
                                  "https://localhost/", "https://host.local/", "https://media.example.com\\@127.0.0.1/"])
def test_public_resource_urls_reject_private_or_credentialed_targets(url):
    with pytest.raises(ExtractionError):
        http.public_url(url)


def test_private_dns_and_redirect_are_rejected_before_request(monkeypatch):
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("192.168.1.5", 80))])
    session = Session(Response("content"))
    with pytest.raises(ExtractionError):
        http.get_bytes(session, FEED, max_bytes=1024, accept="application/xml")
    assert not session.calls
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))])
    session = Session(Response(status=302, headers={"Location": "http://127.0.0.1/private"}))
    with pytest.raises(ExtractionError):
        http.get_bytes(session, FEED, max_bytes=1024, accept="application/xml")
    assert len(session.calls) == 1


def test_public_requests_do_not_read_netrc_or_forward_session_credentials(tmp_path, monkeypatch):
    credentials = tmp_path / "dummy-netrc"
    credentials.write_text("machine feeds.example.com login dummy-user password dummy-password\n")
    credentials.chmod(0o600)
    monkeypatch.setenv("NETRC", str(credentials))
    monkeypatch.setenv("HTTPS_PROXY", "http://dummy-proxy-user:dummy-proxy-password@proxy.example.com:8080")
    monkeypatch.setenv("https_proxy", "http://dummy-proxy-user:dummy-proxy-password@proxy.example.com:8080")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    session = requests.Session()
    # Establish that this fixture exercises requests' automatic netrc behavior.
    assert "Authorization" in session.prepare_request(requests.Request("GET", FEED)).headers
    session.headers["Authorization"] = "Bearer dummy-session-token"
    session.headers["Proxy-Authorization"] = "Basic dummy-session-proxy-token"
    prepared = []

    def fake_send(request, **kwargs):
        prepared.append(request)
        assert kwargs["proxies"]["https"].startswith("http://dummy-proxy-user:")
        # requests' proxy adapter still supplies configured proxy authentication.
        proxy_headers = session.get_adapter(request.url).proxy_headers(kwargs["proxies"]["https"])
        assert "Proxy-Authorization" in proxy_headers
        return Response(b"public response")

    monkeypatch.setattr(session, "send", fake_send)
    assert http.get_bytes(session, FEED, max_bytes=1024, accept="application/xml") == b"public response"
    assert session.trust_env is True
    assert "Authorization" not in prepared[0].headers
    assert "Proxy-Authorization" not in prepared[0].headers


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "100"}])
def test_body_size_limit_applies_with_and_without_content_length(headers):
    session = Session(Response(b"a" * 100, headers=headers))
    with pytest.raises(ExtractionError, match="大小"):
        http.get_bytes(session, FEED, max_bytes=20, accept="application/xml")


@pytest.mark.parametrize("response", [Response(b"part", status=206),
                                     Response(b"part", headers={"Content-Range": "bytes 0-3/99"}),
                                     Response(b"short", headers={"Content-Length": "100"})])
def test_partial_resource_is_rejected(response):
    with pytest.raises(ExtractionError, match="部分|不完整"):
        http.get_bytes(Session(response), FEED, max_bytes=200, accept="application/xml")


def test_download_audio_uses_exact_enclosure_and_atomic_private_file(tmp_path):
    session = Session(metadata_response(), Response(b"audio-bytes", headers={"Content-Type": "audio/mp4"}))
    config = SimpleNamespace(max_minutes=120, chunk_minutes=0)
    path, source = apple.download_episode_audio(URL, tmp_path, config, session=session)
    assert path.read_bytes() == b"audio-bytes"
    assert path.name == "audio.m4a" and path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.part"))
    assert session.calls[-1][0] == AUDIO
    assert source.video_id == "1000801308527"


def test_download_rejects_excess_duration_before_audio_request(tmp_path):
    session = Session(metadata_response())
    with pytest.raises(Exception, match="时长|上限"):
        apple.download_episode_audio(URL, tmp_path, SimpleNamespace(max_minutes=10, chunk_minutes=5), session=session)
    assert len(session.calls) == 1 and not list(tmp_path.iterdir())


def test_ffmpeg_checked_before_long_audio_download(tmp_path, monkeypatch):
    from linkread_desktop.asr import segments
    def missing():
        raise ExtractionError("FFmpeg missing")
    monkeypatch.setattr(segments, "ffmpeg_binary", missing)
    session = Session(metadata_response())
    with pytest.raises(ExtractionError, match="FFmpeg"):
        apple.download_episode_audio(URL, tmp_path, SimpleNamespace(max_minutes=120, chunk_minutes=5), session=session)
    assert len(session.calls) == 1 and not list(tmp_path.iterdir())


def test_failed_download_removes_partial_file(tmp_path):
    class Broken(Response):
        def iter_content(self, chunk_size):
            yield b"first-chunk"
            raise requests.Timeout()
    session = Session(metadata_response(), Broken(headers={"Content-Type": "audio/mpeg"}))
    with pytest.raises(ExtractionError):
        apple.download_episode_audio(URL, tmp_path, SimpleNamespace(max_minutes=120, chunk_minutes=0), session=session)
    assert not list(tmp_path.iterdir())

import io
import sys
import types

import pytest

from linkread_desktop.errors import UnsupportedSourceError
from linkread_desktop.pipeline import extract


def test_youtube_watch_normalizes_single_video(monkeypatch):
    captured = []
    monkeypatch.setattr("linkread_desktop.pipeline.extract_youtube", lambda url, **kwargs: captured.append(url) or "ok")
    assert extract("https://www.youtube.com/watch?v=1-Fo8McCD9w") == "ok"
    assert captured == ["https://www.youtube.com/watch?v=1-Fo8McCD9w"]


def test_youtube_short_removes_tracking_query(monkeypatch):
    captured = []
    monkeypatch.setattr("linkread_desktop.pipeline.extract_youtube", lambda url, **kwargs: captured.append(url) or "ok")
    assert extract("https://youtu.be/1-Fo8McCD9w?si=JUrjwlVOvHJiTnqU") == "ok"
    assert captured == ["https://www.youtube.com/watch?v=1-Fo8McCD9w"]


def test_youtube_watch_removes_playlist_to_avoid_playlist_detection(monkeypatch):
    captured = []
    monkeypatch.setattr("linkread_desktop.pipeline.extract_youtube", lambda url, **kwargs: captured.append(url) or "ok")
    assert extract("https://www.youtube.com/watch?v=1-Fo8McCD9w&list=PLxyz&index=2") == "ok"
    assert captured == ["https://www.youtube.com/watch?v=1-Fo8McCD9w"]


@pytest.mark.parametrize("source", [
    r"https\://www\.youtube.com/watch\?v\=1-Fo8McCD9w",
    r"https\://www\.youtube.com/watch\\?v\\=1-Fo8McCD9w",
    r"https\://youtu\.be/1-Fo8McCD9w\?si\=abc",
])
def test_youtube_escaped_markup_works(monkeypatch, source):
    captured = []
    monkeypatch.setattr("linkread_desktop.pipeline.extract_youtube", lambda url, **kwargs: captured.append(url) or "ok")
    assert extract(source) == "ok"
    assert captured == ["https://www.youtube.com/watch?v=1-Fo8McCD9w"]


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/playlist?list=PLbad",
    "https://www.youtube.com/watch?list=PLbad",
    "https://www.youtube.com/watch?v=not-valid-id",
    "https://www.youtube.com.evil.org/watch?v=1-Fo8McCD9w",
])
def test_rejects_non_single_video_urls(url):
    with pytest.raises(UnsupportedSourceError):
        extract(url)


class FakeYoutubeDL:
    def __init__(self, opts):
        assert opts["noplaylist"]
        assert opts["skip_download"]
        assert opts["socket_timeout"] == 20

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def extract_info(self, url, download=False):
        assert url == "https://www.youtube.com/watch?v=1-Fo8McCD9w"
        return {
            "id": "1-Fo8McCD9w", "title": "Test video", "channel": "Author", "duration": 61,
            "subtitles": {"zh-Hans": [{"ext": "vtt", "url": "https://caption.example/caption.vtt"}]},
        }

    def urlopen(self, url):
        assert url == "https://caption.example/caption.vtt"
        return io.BytesIO(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nTest subtitle\n")


def test_youtube_progress_stage_order(monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    events = []
    document = extract("https://www.youtube.com/watch?v=1-Fo8McCD9w", progress=events.append)
    assert document.segments[0].text == "Test subtitle"
    assert [e.stage for e in events] == [
        "source", "metadata", "metadata_done", "captions", "captions_done", "download", "parse", "parsed",
    ]

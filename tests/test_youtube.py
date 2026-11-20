import io
import sys
import types

import pytest

from linkread_desktop.errors import SubtitleUnavailableError
from linkread_desktop.extractors.youtube import extract_youtube


class FakeYoutubeDL:
    info = {}

    def __init__(self, opts):
        assert opts["skip_download"]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def extract_info(self, url, download=False):
        assert not download
        return self.info

    def urlopen(self, url):
        assert url == "https://captions.example/zh"
        return io.BytesIO(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello!\n")


def test_youtube_extracts_with_priority_and_no_audio(monkeypatch):
    FakeYoutubeDL.info = {
        "id": "abc", "title": "Video", "channel": "Creator", "upload_date": "20260101", "duration": 31,
        "webpage_url": "https://www.youtube.com/watch?v=abc",
        "subtitles": {"en": [{"ext": "vtt", "url": "https://captions.example/en"}]},
        "automatic_captions": {"zh-Hans": [{"ext": "vtt", "url": "https://captions.example/zh"}]},
    }
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    doc = extract_youtube("https://www.youtube.com/watch?v=abc")
    assert doc.source.video_id == "abc"
    assert doc.source.published_at == "2026-01-01"
    assert doc.language == "zh-Hans"
    assert doc.segments[0].start_ms == 1000


def test_youtube_no_subtitle(monkeypatch):
    FakeYoutubeDL.info = {"id": "abc", "title": "Video", "subtitles": {}, "automatic_captions": {}}
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    with pytest.raises(SubtitleUnavailableError, match="ASR"):
        extract_youtube("https://www.youtube.com/watch?v=abc")

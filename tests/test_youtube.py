import io
import sys
import types

import pytest

from linkread_desktop.asr.config import AsrConfig
from linkread_desktop.errors import ExtractionError, SubtitleUnavailableError
from linkread_desktop.extractors.youtube import extract_youtube
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.pipeline import extract

VIDEO_ID = "1-Fo8McCD9w"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"

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
        "id": VIDEO_ID, "title": "Video", "channel": "Creator", "upload_date": "20260101", "duration": 31,
        "webpage_url": URL,
        "subtitles": {"en": [{"ext": "vtt", "url": "https://captions.example/en"}]},
        "automatic_captions": {"zh-Hans": [{"ext": "vtt", "url": "https://captions.example/zh"}]},
    }
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    doc = extract_youtube(URL)
    assert doc.source.video_id == VIDEO_ID
    assert doc.source.published_at == "2026-01-01"
    assert doc.language == "zh-Hans"
    assert doc.segments[0].start_ms == 1000


def test_youtube_no_subtitle(monkeypatch):
    FakeYoutubeDL.info = {"id": VIDEO_ID, "title": "Video", "subtitles": {}, "automatic_captions": {}}
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    with pytest.raises(SubtitleUnavailableError, match="ASR"):
        extract_youtube(URL)


def test_youtube_confirmed_no_subtitles_uses_enabled_asr(monkeypatch):
    FakeYoutubeDL.info = {"id": VIDEO_ID, "title": "Video",
                          "subtitles": {"live_chat": [{"ext": "json", "url": "https://www.youtube.com/live_chat"}]},
                          "automatic_captions": {}}
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    def transcribe(url, config, **kwargs):
        assert url == URL
        return TranscriptDocument(source=SourceInfo(platform="youtube", title="Video", url=url, video_id=VIDEO_ID),
                                  transcript_source="asr", segments=[TranscriptSegment(start_ms=0, text="原文")])

    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video", transcribe)
    assert extract(URL, asr=AsrConfig()).transcript_source == "asr"


@pytest.mark.parametrize("problem", [
    {"subtitles": None},
    {"availability": "needs_auth"},
    {"subtitles": {"en": [{"ext": "json3", "url": "https://www.youtube.com/api/timedtext"}]}},
    {"id": "hCth7d9_j84"},
])
def test_youtube_inconclusive_catalog_does_not_trigger_asr(monkeypatch, problem):
    FakeYoutubeDL.info = {"id": VIDEO_ID, "subtitles": {}, "automatic_captions": {}, **problem}
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video",
                        lambda *args, **kwargs: pytest.fail("Unconfirmed or restricted access must not trigger ASR"))
    with pytest.raises(ExtractionError):
        extract(URL, asr=AsrConfig())


def test_youtube_omitted_po_token_tracks_do_not_trigger_asr(monkeypatch):
    class LimitedYoutubeDL(FakeYoutubeDL):
        def __init__(self, options):
            super().__init__(options)
            assert options["verbose"]
            self.logger = options["logger"]

        def extract_info(self, url, download=False):
            self.logger.debug("There are missing subtitles languages because a PO token was not provided.")
            return {"id": VIDEO_ID, "subtitles": {}, "automatic_captions": {}}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=LimitedYoutubeDL))
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video",
                        lambda *args, **kwargs: pytest.fail("Skipped subtitle tracks must not trigger ASR"))
    with pytest.raises(ExtractionError) as failure:
        extract(URL, asr=AsrConfig())
    assert failure.value.code == "youtube_subtitles_unconfirmed"


@pytest.mark.parametrize("body", [b"", b"WEBVTT\n\n", b"WEBVTT\n\nbad --> 00:00:03.000\nText"])
def test_youtube_listed_but_empty_or_broken_subtitle_does_not_trigger_asr(monkeypatch, body):
    FakeYoutubeDL.info = {"id": VIDEO_ID, "title": "Video",
                          "subtitles": {"zh-Hans": [{"ext": "vtt", "url": "https://captions.example/zh"}]},
                          "automatic_captions": {}}
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    monkeypatch.setattr(FakeYoutubeDL, "urlopen", lambda *args: io.BytesIO(body))
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video",
                        lambda *args, **kwargs: pytest.fail("Known subtitle tracks must not trigger ASR on parse failure"))
    with pytest.raises(ExtractionError) as failure:
        extract(URL, asr=AsrConfig())
    assert failure.value.code == "youtube_subtitle_invalid"

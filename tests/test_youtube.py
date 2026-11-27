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


def test_youtube_manual_captions_preserve_overlapping_identical_suffix_and_prefix_cues(monkeypatch):
    vtt = (
        b"WEBVTT\n\n"
        b"00:00:01.000 --> 00:00:03.000\nGood morning\n\n"
        b"00:00:01.500 --> 00:00:03.500\nGood morning\n\n"
        b"00:00:02.000 --> 00:00:04.000\nmorning\n\n"
        b"00:00:02.500 --> 00:00:05.000\nmorning everyone\n"
    )
    FakeYoutubeDL.info = {
        "id": VIDEO_ID, "title": "Dialogue",
        "subtitles": {"en": [{"ext": "vtt", "url": "https://captions.example/manual"}]},
        "automatic_captions": {},
    }
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    monkeypatch.setattr(FakeYoutubeDL, "urlopen", lambda _self, _url: io.BytesIO(vtt))
    doc = extract_youtube(URL)
    assert [(s.start_ms, s.end_ms, s.text) for s in doc.segments] == [
        (1000, 3000, "Good morning"),
        (1500, 3500, "Good morning"),
        (2000, 4000, "morning"),
        (2500, 5000, "morning everyone"),
    ]


def test_youtube_automatic_captions_keep_rolling_deduplication(monkeypatch):
    vtt = (
        b"WEBVTT\n\n"
        b"00:00:00.000 --> 00:00:02.000\nGood morning\n\n"
        b"00:00:00.500 --> 00:00:02.500\nGood morning\n\n"
        b"00:00:01.000 --> 00:00:03.000\nmorning\n\n"
        b"00:00:02.000 --> 00:00:04.000\nGood morning everyone\n"
    )
    FakeYoutubeDL.info = {
        "id": VIDEO_ID, "title": "Auto",
        "subtitles": {},
        "automatic_captions": {"en": [{"ext": "vtt", "url": "https://captions.example/auto"}]},
    }
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    monkeypatch.setattr(FakeYoutubeDL, "urlopen", lambda _self, _url: io.BytesIO(vtt))
    doc = extract_youtube(URL)
    assert [(s.start_ms, s.end_ms, s.text) for s in doc.segments] == [
        (0, 2000, "Good morning"),
        (2000, 4000, "everyone"),
    ]


class MedialessYoutubeDL(FakeYoutubeDL):
    """A signed-in session can receive no media URL at all (SABR-only streaming), so only
    storyboard images remain. Mirror yt-dlp: its default media format selection then aborts
    unless ignore_no_formats_error is set."""
    info = {
        "id": VIDEO_ID, "title": "Video", "webpage_url": URL, "duration": 31,
        "subtitles": {},
        "automatic_captions": {"en": [{"ext": "vtt", "url": "https://captions.example/en", "name": "English"}]},
        "formats": [{"format_id": "sb0", "ext": "mhtml", "protocol": "mhtml", "vcodec": "none", "acodec": "none",
                     "url": "https://i.ytimg.com/sb/storyboard"}],
    }

    def __init__(self, options):
        super().__init__(options)
        self.options = options

    def extract_info(self, url, download=False):
        assert not download
        if not self.options.get("ignore_no_formats_error"):
            raise RuntimeError("ERROR: [youtube] Requested format is not available. "
                               "Use --list-formats for a list of available formats")
        return self.info

    def urlopen(self, url):
        assert url == "https://captions.example/en"
        return io.BytesIO(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello!\n")


@pytest.mark.parametrize("cookies_from_browser", [None, "edge"])
def test_youtube_subtitles_do_not_require_a_downloadable_media_format(monkeypatch, cookies_from_browser):
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=MedialessYoutubeDL))
    doc = extract_youtube(URL, cookies_from_browser=cookies_from_browser)
    assert doc.source.video_id == VIDEO_ID
    assert doc.language == "en"
    assert [(s.start_ms, s.end_ms, s.text) for s in doc.segments] == [(1000, 2000, "Hello!")]


def test_youtube_without_captions_or_media_still_reports_missing_subtitles(monkeypatch):
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=MedialessYoutubeDL))
    monkeypatch.setattr(MedialessYoutubeDL, "info", {
        **MedialessYoutubeDL.info, "subtitles": {}, "automatic_captions": {},
    })
    with pytest.raises(SubtitleUnavailableError):
        extract_youtube(URL)

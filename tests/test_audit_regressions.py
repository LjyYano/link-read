"""Offline regression tests covering Stage B backend, ASR, extractor, and renderer fixes."""

from ipaddress import ip_address
import json
from pathlib import Path
from threading import Event
import pytest

from linkread_desktop import covers, reader_cache, source_notes
from linkread_desktop.ai.openai_compatible import OpenAICompatibleProvider, ProviderError, is_loopback_base_url
from linkread_desktop.asr import bailian, segments as asr_segments
from linkread_desktop.asr.config import AsrConfig
from linkread_desktop.extractors import apple_podcasts, podcast_http
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.html import render_html
from linkread_desktop.renderers.markdown import render_markdown
from linkread_desktop.renderers.shownotes import description_html
from linkread_desktop.renderers.subtitles import render_subtitles


def _sample_doc(*, end_ms: int | None = 1500, text: str = "Hello world") -> TranscriptDocument:
    return TranscriptDocument(
        source=SourceInfo(platform="youtube", title="Sample Video", url="https://youtu.be/abc123def45"),
        transcript_source="subtitle",
        language="en",
        segments=[TranscriptSegment(start_ms=0, end_ms=end_ms, text=text)],
    )


def test_covers_rejects_oversized_content_length_and_respects_create_dir_and_cancel(tmp_path):
    iter_called = []

    class FakeResponse:
        status_code = 200
        headers = {"Content-Length": str(covers.MAX_COVER_BYTES + 1)}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_content(self, chunk_size=0):
            iter_called.append(chunk_size)
            yield b"\x89PNG\r\n\x1a\n"

    class FakeSession:
        def __init__(self):
            self.calls = 0

        def get(self, url, **kwargs):
            self.calls += 1
            return FakeResponse()

    oversized_session = FakeSession()
    assert covers.fetch_cover_bytes("https://i.ytimg.com/vi/abc/hqdefault.jpg", session=oversized_session) is None
    assert iter_called == []

    missing_dir = tmp_path / "deleted-article"
    assert covers.save_cover_bytes(missing_dir, ".png", b"\x89PNG\r\n\x1a\n", create_dir=False) is None
    assert not missing_dir.exists()

    cancelled_session = FakeSession()
    cancel = Event()
    cancel.set()
    article_dir = tmp_path / "article"
    article_dir.mkdir()
    assert covers.download_cover(
        "https://i.ytimg.com/vi/abc/hqdefault.jpg",
        article_dir,
        session=cancelled_session,
        cancel=cancel,
    ) is None
    assert cancelled_session.calls == 0


def test_openai_compatible_loopback_allows_empty_token_and_omits_auth_header(monkeypatch):
    assert is_loopback_base_url("http://localhost:11434/v1") is True
    assert is_loopback_base_url("http://127.0.0.1:8000/v1") is True
    assert is_loopback_base_url("https://api.example.com/v1") is False

    with pytest.raises(ProviderError) as remote_err:
        OpenAICompatibleProvider(base_url="https://api.example.com/v1", model="gpt-4o-mini", token="")
    assert remote_err.value.code == "authentication_missing"

    provider = OpenAICompatibleProvider(base_url="http://localhost:11434/v1", model="llama3", token="")
    captured_headers = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": json.dumps({"ok": True})}}]}

        @staticmethod
        def close():
            pass

    def fake_post(url, **kwargs):
        captured_headers.append(kwargs.get("headers", {}))
        return FakeResponse()

    monkeypatch.setattr("linkread_desktop.ai.openai_compatible.requests.post", fake_post)
    resp = provider._post({"model": "llama3", "messages": []}, timeout=(5, 10))
    assert resp.status_code == 200
    assert captured_headers and "Authorization" not in captured_headers[0]


def test_bailian_multichannel_sort_intl_host_and_segment_clamping(tmp_path, monkeypatch):
    merged = bailian.segments_from_result({
        "transcripts": [
            {"channel_id": 0, "sentences": [{"begin_time": 1000, "end_time": 2000, "text": "Second"}]},
            {"channel_id": 1, "sentences": [{"begin_time": 0, "end_time": 900, "text": "First"}]},
        ],
    })
    assert [(seg.start_ms, seg.end_ms, seg.text) for seg in merged] == [
        (0, 900, "First"),
        (1000, 2000, "Second"),
    ]

    assert bailian.api_root("https://dashscope-intl.aliyuncs.com/api/v1/") == "https://dashscope-intl.aliyuncs.com/api/v1"
    with pytest.raises(bailian.AsrError):
        bailian.api_root("https://evil-dashscope-intl.aliyuncs.com/api/v1")

    part_path = tmp_path / "part.wav"
    part_path.write_bytes(b"RIFF")

    class ClampingClient:
        def transcribe(self, audio, state_file, *, progress=None, allow_empty=False, cancel=None):
            return [TranscriptSegment(start_ms=500, end_ms=10500, text="Clamped")]

    monkeypatch.setattr(asr_segments, "BailianAsr", lambda config: ClampingClient())
    part = asr_segments.AudioPart(path=part_path, offset_ms=60000, duration_ms=10000, checksum="abc123")
    cues = asr_segments.transcribe_parts([part], AsrConfig(token="test-token"), workers=1)
    assert [(c.start_ms, c.end_ms, c.text) for c in cues] == [(60500, 70000, "Clamped")]

    overshoot_dir = tmp_path / "overshoot"
    overshoot_dir.mkdir()
    bad_part = overshoot_dir / "part2.wav"
    bad_part.write_bytes(b"RIFF")

    class OvershootClient:
        def transcribe(self, audio, state_file, *, progress=None, allow_empty=False, cancel=None):
            return [TranscriptSegment(start_ms=500, end_ms=15000, text="Too long")]

    monkeypatch.setattr(asr_segments, "BailianAsr", lambda config: OvershootClient())
    with pytest.raises(bailian.AsrError):
        asr_segments.transcribe_parts(
            [asr_segments.AudioPart(path=bad_part, offset_ms=60000, duration_ms=10000, checksum="abc123")],
            AsrConfig(token="test-token"),
            workers=1,
        )


def test_podcast_http_ssrf_and_apple_podcasts_dtd_cdata_and_empty_cues():
    for blocked in (
        "224.0.0.1",
        "ff02::1",
        "::ffff:127.0.0.1",
        "2002:7f00:0001::1",
        "2001::1",
        "64:ff9b::127.0.0.1",
    ):
        assert podcast_http._is_public_ip(ip_address(blocked)) is False

    for allowed in ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"):
        assert podcast_http._is_public_ip(ip_address(allowed)) is True

    assert podcast_http.public_url("https://Example.COM./feed.xml", resolve=False) == "https://example.com/feed.xml"

    episode = apple_podcasts.AppleEpisode(
        source=SourceInfo(platform="apple_podcasts", title="Ep 1", url="https://podcasts.apple.com/us/podcast/show/id1?i=2"),
        show_id="1",
        episode_id="2",
        guid="ep-2",
        feed_url="https://example.com/feed.xml",
        audio_url=None,
    )
    rss_with_cdata = (
        b"<rss><channel><item><guid>ep-2</guid>"
        b"<description><![CDATA[<!DOCTYPE html><p>Notes</p>]]></description>"
        b"</item></channel></rss>"
    )
    _root, item = apple_podcasts._feed_item(rss_with_cdata, episode)
    assert item.findtext("guid") == "ep-2"

    rss_with_dtd = b"<!DOCTYPE rss SYSTEM 'http://127.0.0.1/evil'><rss><channel/></rss>"
    with pytest.raises(apple_podcasts.ExtractionError):
        apple_podcasts._feed_item(rss_with_dtd, episode)

    json_payload = json.dumps({
        "segments": [
            {"startTime": 0.0, "endTime": 1.0, "body": "   "},
            {"startTime": 1.0, "endTime": 2.5, "body": "Actual line"},
        ],
    }).encode("utf-8")
    assert [c.text for c in apple_podcasts._json_transcript(json_payload)] == ["Actual line"]

    vtt_payload = b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n   \n\n00:00:01.000 --> 00:00:02.000\nSpoken cue\n"
    assert [c.text for c in apple_podcasts._timed_transcript(vtt_payload)] == ["Spoken cue"]


def test_renderers_and_reader_cache_regressions():
    assert reader_cache.CACHE_VERSION == 18

    zh_html = render_html(_sample_doc(), language="zh-CN")
    assert 'data-theme-choice="sepia"' in zh_html
    assert 'data-reading-font="serif"' in zh_html
    assert ">暖纸</button>" in zh_html
    assert ">宋体</button>" in zh_html
    assert "linkread-sidebar-v1" in zh_html

    md = render_markdown(_sample_doc())
    assert "Sample Video" in md
    assert "Hello world" in md

    html_desc = description_html(
        SourceInfo(
            platform="youtube",
            title="Desc",
            url="https://youtu.be/abc123def45",
            description="See <https://example.com/a_b#c> for details",
            description_format="text",
        ),
    )
    assert "https://example.com/a_b#c" in html_desc
    assert r"\_" not in html_desc
    assert r"\#" not in html_desc

    raw_none_end = _sample_doc(end_ms=None, text="a & <b> --> c")
    vtt = render_subtitles(raw_none_end, "vtt")
    assert "00:00:00.000 --> 00:00:00.000" in vtt
    assert "a &amp; &lt;b&gt; --&gt; c" in vtt
    assert raw_none_end.segments[0].end_ms is None


def test_source_notes_rejects_symlink_and_boolean_version(tmp_path):
    doc = _sample_doc()

    bool_dir = tmp_path / "bool-ver"
    bool_dir.mkdir()
    (bool_dir / ".source-notes.json").write_text(json.dumps({"version": True, "notes": []}), encoding="utf-8")
    assert source_notes.with_source_notes(doc, bool_dir) is doc

    target = tmp_path / "outside.json"
    target.write_text(json.dumps({"version": 1, "notes": []}), encoding="utf-8")
    symlink_dir = tmp_path / "symlink-notes"
    symlink_dir.mkdir()
    (symlink_dir / ".source-notes.json").symlink_to(target)
    assert source_notes.with_source_notes(doc, symlink_dir) is doc

"""Apple episodes share the real pipeline, archive identity and opt-in ASR cache."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from linkread_desktop import desktop, pipeline
from linkread_desktop.asr import pipeline as asr_pipeline
from linkread_desktop.asr.config import AsrConfig
from linkread_desktop.errors import ExtractionError, SubtitleUnavailableError, UnsupportedSourceError
from linkread_desktop.extractors import apple_podcasts as apple
from linkread_desktop.metadata import thumbnail_url, video_metadata
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.formatting import source_link
from linkread_desktop.renderers.html import render_html
from linkread_desktop.renderers.markdown import render_markdown
from linkread_desktop.workspace import workspace_dir
from test_ai_notes import FakeProvider
from test_desktop import invoke

URL = "https://podcasts.apple.com/cn/podcast/id1552904790?i=1000801308527"
COVER = "https://is1-ssl.mzstatic.com/image/600x600bb.jpg"


def document(*, asr=False):
    return TranscriptDocument(
        schema_version="1.1" if asr else "1.0",
        source=SourceInfo(platform="apple_podcasts", title="商业就是这样 · 单集测试", url=URL,
                          video_id="1000801308527", author="商业就是这样", duration_ms=660000,
                          thumbnail_url=COVER),
        transcript_source="asr" if asr else "subtitle", language="zh-CN",
        segments=[TranscriptSegment(start_ms=i * 30000, end_ms=i * 30000 + 25000,
                                    text=f"这是第 {i + 1} 段原始内容，所有内容应当完整保留。") for i in range(20)],
    )


def test_podcast_preview_only_resolves_metadata(monkeypatch):
    calls = []
    raw = document()
    def resolve(url, **kwargs):
        calls.append(url)
        return raw.source
    monkeypatch.setattr(apple, "preview_episode", resolve)
    monkeypatch.setattr(apple, "extract_apple_podcasts", lambda *a, **k: pytest.fail("Preview fetched subtitles"))
    monkeypatch.setattr(apple, "download_episode_audio", lambda *a, **k: pytest.fail("Preview downloaded audio"))
    metadata = video_metadata(URL).public()
    assert metadata == desktop._source_metadata(raw)
    assert metadata["platform"] == "apple_podcasts" and metadata["durationMs"] == 660000
    assert metadata["thumbnailUrl"] == COVER and calls == [URL]


def test_available_podcast_subtitles_never_start_asr(monkeypatch):
    raw = document()
    monkeypatch.setattr(apple, "extract_apple_podcasts", lambda *a, **k: raw)
    monkeypatch.setattr(asr_pipeline, "transcribe_video", lambda *a, **k: pytest.fail("Unnecessary paid ASR"))
    assert pipeline.extract(URL, asr=AsrConfig(token="test-token")) == raw


def test_absent_podcast_subtitles_require_explicit_asr_and_keep_cache_options(tmp_path, monkeypatch):
    def unavailable(*a, **k):
        raise SubtitleUnavailableError("No public timed transcript")
    calls = []
    def transcribe(url, config, **options):
        calls.append((url, config, options))
        return document(asr=True)
    monkeypatch.setattr(apple, "extract_apple_podcasts", unavailable)
    monkeypatch.setattr(asr_pipeline, "transcribe_video", transcribe)
    with pytest.raises(SubtitleUnavailableError, match="开启语音识别"):
        pipeline.extract(URL)
    assert calls == []
    config = AsrConfig(token="test-token")
    raw = pipeline.extract(URL, asr=config, asr_cache_dir=tmp_path)
    assert raw.transcript_source == "asr"
    assert calls[0][0] == URL and calls[0][1] == config and calls[0][2]["cache_dir"] == tmp_path


@pytest.mark.parametrize("message", ["HTTP 403", "HTTP 429", "Network timeout", "Malformed RSS", "Wrong episode"])
def test_podcast_fetch_errors_never_trigger_asr(monkeypatch, message):
    def failed(*a, **k):
        raise ExtractionError(message)
    monkeypatch.setattr(apple, "extract_apple_podcasts", failed)
    monkeypatch.setattr(asr_pipeline, "transcribe_video", lambda *a, **k: pytest.fail("Fetch errors must not incur ASR"))
    with pytest.raises(ExtractionError, match=message):
        pipeline.extract(URL, asr=AsrConfig(token="test-token"))


def test_podcast_rejects_cookie_options_before_extraction(monkeypatch):
    monkeypatch.setattr(apple, "extract_apple_podcasts", lambda *a, **k: pytest.fail("Unexpected request"))
    with pytest.raises(UnsupportedSourceError, match="仅适用于 YouTube"):
        pipeline.extract(URL, cookies_from_browser="chrome")


def test_podcast_asr_reuses_five_minute_chunks_and_result_without_double_charge(tmp_path, monkeypatch):
    raw = document(asr=True)
    calls = {"download": 0, "split": 0, "transcribe": 0}
    def download(url, folder, config, **kwargs):
        assert url == URL
        calls["download"] += 1
        audio = folder / "audio.m4a"
        audio.write_bytes(b"offline audio fixture")
        return audio, raw.source
    def split(audio, folder, *, minutes, duration_ms, **kwargs):
        assert minutes == 5 and duration_ms == raw.source.duration_ms and audio.is_file()
        calls["split"] += 1
        part_folder = folder / "parts-v1-300"
        part_folder.mkdir()
        part = part_folder / "000.wav"
        part.write_bytes(b"offline split fixture")
        return [SimpleNamespace(path=part)]
    def transcribe(parts, config, *, workers, **kwargs):
        assert workers == 2 and parts[0].path.is_file()
        calls["transcribe"] += 1
        return raw.segments
    monkeypatch.setattr(apple, "download_episode_audio", download)
    monkeypatch.setattr(asr_pipeline, "split_audio", split)
    monkeypatch.setattr(asr_pipeline, "transcribe_parts", transcribe)
    config = AsrConfig(token="offline-test-token")
    first = asr_pipeline.transcribe_video(URL, config, cache_dir=tmp_path)
    # Region / share parameters change neither episode identity nor a paid result.
    second = asr_pipeline.transcribe_video(URL.replace("/cn/", "/us/") + "&utm_source=share", config, cache_dir=tmp_path)
    assert first == second and first.segments == raw.segments
    assert calls == {"download": 1, "split": 1, "transcribe": 1}
    assert len(list(tmp_path.glob("*/transcript.json"))) == 1
    assert not list(tmp_path.rglob("audio.m4a")) and not list(tmp_path.rglob("*.wav"))


def test_podcast_archive_key_and_safe_original_links(tmp_path):
    raw = document()
    assert pipeline.video_source_key(URL) == "apple-podcasts-1552904790-1000801308527"
    assert workspace_dir(tmp_path, raw).name.endswith("[apple-podcasts-1552904790-1000801308527]")
    assert source_link(raw, 10000) == URL  # No undocumented seek parameter.
    assert "Apple Podcasts" in render_html(raw)
    assert f"**原节目**：{URL}" in render_markdown(raw)
    raw.source.url = URL.replace("podcasts.apple.com", "podcasts.apple.com.evil.example")
    assert source_link(raw) is None
    raw.source.url = URL
    raw.source.video_id = "2"
    assert source_link(raw) is None


@pytest.mark.parametrize("url", ["https://is1-ssl.mzstatic.com.evil.example/a.jpg", "https://evil.example/mzstatic.com/a.jpg"])
def test_podcast_artwork_does_not_expand_arbitrary_image_hosts(url):
    assert thumbnail_url(url) is None
    assert thumbnail_url(COVER) == COVER


def test_desktop_podcast_runs_ai_archives_exports_and_rejects_duplicate(tmp_path, monkeypatch, capsys):
    raw = document()
    provider = FakeProvider()
    monkeypatch.setattr(apple, "extract_apple_podcasts", lambda *a, **k: raw)
    monkeypatch.setattr(desktop, "make_provider", lambda *a, **k: provider)
    status, events = invoke(monkeypatch, capsys, tmp_path, "generate", URL)
    assert status == 0, events[-1]
    article = events[-1]["article"]
    assert article["platform"] == "apple_podcasts" and article["hasAnalysis"] is True
    assert article["sourceUrl"] == URL and article["thumbnailUrl"] == COVER
    folder = tmp_path / article["id"]
    page = (folder / "article.html").read_text()
    for panel in ("summary", "mindmap", "transcript", "keywords", "highlights", "shownotes"):
        assert f'id="tab-{panel}"' in page
    assert 'id="tab-notes"' not in page
    assert TranscriptDocument.model_validate_json((folder / "transcript.json").read_text()) == raw
    status, listing = invoke(monkeypatch, capsys, tmp_path, "library")
    assert status == 0 and listing[-1]["articles"] == [article]
    status, exported = invoke(monkeypatch, capsys, tmp_path, "markdown", article["id"])
    assert status == 0 and "**原节目**" in exported[-1]["text"]
    previous_calls = len(provider.calls)
    status, duplicate = invoke(monkeypatch, capsys, tmp_path, "generate", URL.replace("/cn/", "/us/"))
    assert status == 1 and "已存在" in duplicate[-1]["message"]
    assert len(provider.calls) == previous_calls

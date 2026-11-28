"""Local files must follow the same AI stages and safe replacement as videos."""
import json
from pathlib import Path

import pytest

from linkread_desktop import desktop
from linkread_desktop.ai.provider import ProviderError
from linkread_desktop.extractors.local import extract_local
from linkread_desktop.renderers.html import render_html
from linkread_desktop.renderers.site import load_document
from linkread_desktop.workspace import workspace_dir
from test_ai_notes import FakeProvider
from test_desktop import invoke


def legacy_archive(tmp_path):
    source = tmp_path / "查理·芒格：为什么 ETF 可能是散户最大的陷阱？.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:04,000\n这是原始字幕不能丢失\n", encoding="utf-8")
    raw = extract_local(source)
    library = tmp_path / "library"
    folder = workspace_dir(library, raw)
    folder.mkdir(parents=True)
    (folder / "transcript.json").write_text(raw.model_dump_json(), encoding="utf-8")
    (folder / "article.html").write_text(render_html(raw), encoding="utf-8")
    return library, folder, source


def test_upgrade_existing_local_archive_uses_saved_subtitle_and_preserves_id(tmp_path, monkeypatch, capsys):
    library, folder, source = legacy_archive(tmp_path)
    assert desktop._article_record(folder / "article.html")["hasAnalysis"] is False
    source.unlink()  # The original file is no longer required.
    provider = FakeProvider()
    monkeypatch.setattr(desktop, "make_provider", lambda *a, **k: provider)
    before = load_document(folder / "transcript.json")
    status, events = invoke(monkeypatch, capsys, library, "generate", "--local-file", "--replace",
                            str(folder / "transcript.json"))
    assert status == 0, events[-1]
    assert events[-1]["article"]["id"] == folder.name
    assert events[-1]["article"]["hasAnalysis"] is True
    assert load_document(folder / "note.json").transcript == before
    assert (folder / "manifest.json").is_file() and not (folder / "article.html").exists()
    desktop._prepared_reader(folder / "manifest.json")
    assert 'id="tab-summary"' in (folder / ".reader.html").read_text()
    assert len(list(library.glob("*/manifest.json"))) == 1


def test_failed_local_upgrade_keeps_old_article_and_transcript(tmp_path, monkeypatch, capsys):
    library, folder, _ = legacy_archive(tmp_path)
    before = {name: (folder / name).read_bytes() for name in ("article.html", "transcript.json")}
    class FailedProvider:
        name = "api"
        def generate(self, *args, **kwargs):
            raise ProviderError("服务暂不可用")
    monkeypatch.setattr(desktop, "make_provider", lambda *a, **k: FailedProvider())
    status, events = invoke(monkeypatch, capsys, library, "generate", "--provider", "api",
        "--local-file", "--replace", str(folder / "transcript.json"))
    assert status == 1 and events[-1]["message"] == "服务暂不可用"
    assert {name: (folder / name).read_bytes() for name in before} == before
    assert desktop._article_record(folder / "article.html")["hasAnalysis"] is False


def test_local_failure_preserves_transcript_and_retry_completes(tmp_path, monkeypatch, capsys):
    source = tmp_path / "input.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:03,000\n可重试的字幕\n", encoding="utf-8")
    library = tmp_path / "library"
    class FailedProvider:
        name = "codex"
        def generate(self, *args, **kwargs): raise ProviderError("模型不可用")
    monkeypatch.setattr(desktop, "make_provider", lambda *a, **k: FailedProvider())
    status, events = invoke(monkeypatch, capsys, library, "generate", "--local-file", str(source))
    assert status == 1
    saved = list(library.glob("*/transcript.json"))
    assert len(saved) == 1 and not list(library.glob("*/article.html"))
    original = saved[0].read_bytes()
    monkeypatch.setattr(desktop, "make_provider", lambda *a, **k: FakeProvider())
    status, events = invoke(monkeypatch, capsys, library, "generate", "--local-file", str(source))
    assert status == 0, events[-1]
    assert saved[0].read_bytes() == original
    assert events[-1]["article"]["hasAnalysis"] is True


def test_local_generation_never_enables_asr(tmp_path, monkeypatch, capsys):
    source = tmp_path / "captions.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:01,000\n已有字幕\n", encoding="utf-8")
    seen = {}
    real = desktop.generate_article
    def generate(input_source, provider, **options):
        seen.update(options)
        return real(input_source, provider, **options)
    monkeypatch.setattr(desktop, "generate_article", generate)
    monkeypatch.setattr(desktop, "make_provider", lambda *a, **k: FakeProvider())
    status, events = invoke(monkeypatch, capsys, tmp_path / "library", "generate", "--local-file", "--asr", str(source))
    assert status == 0, events[-1]
    assert "asr" not in seen and "asr_cache_dir" not in seen

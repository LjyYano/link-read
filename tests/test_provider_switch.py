"""Regression coverage for API provider metadata and provider switching."""
from types import SimpleNamespace

import pytest

from linkread_desktop import desktop
from linkread_desktop.ai.models import NoteAnalysis, NoteDocument
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.site import load_document
from linkread_desktop.workspace import workspace_dir


def example_transcript():
    return TranscriptDocument(
        source=SourceInfo(
            platform="youtube", title="Example",
            url="https://www.youtube.com/watch?v=1-Fo8McCD9w",
            video_id="1-Fo8McCD9w",
        ),
        transcript_source="subtitle",
        segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="测试字幕")],
    )


def archive_with_api_note(tmp_path, *, complete=False):
    source = example_transcript()
    folder = workspace_dir(tmp_path, source)
    folder.mkdir(parents=True)
    (folder / "transcript.json").write_text(source.model_dump_json(), encoding="utf-8")
    note = NoteDocument(transcript=source, provider="api", analysis=NoteAnalysis(summary="来自 API 的摘要"))
    (folder / "note.json").write_text(note.model_dump_json(), encoding="utf-8")
    if complete:
        (folder / "article.html").write_text("<html>old</html>", encoding="utf-8")
    return folder


def test_existing_api_note_loads_and_stays_valid_when_codex_selected(tmp_path):
    folder = archive_with_api_note(tmp_path, complete=True)
    loaded = load_document(folder / "note.json")
    assert isinstance(loaded, NoteDocument)
    assert loaded.provider == "api"
    item = desktop._article_record(folder / "article.html")
    assert item["platform"] == "youtube"
    assert item["title"] == "Example"


def test_incomplete_api_note_switches_to_codex_without_reusing_partial(tmp_path, monkeypatch):
    folder = archive_with_api_note(tmp_path)
    invoked = []

    def generate(source, provider, *, library, progress):
        invoked.append((source, provider.name, library))
        assert library != tmp_path
        assert library.parent == tmp_path / ".generation-staging"
        assert len(library.name) == 64
        raw = example_transcript()
        result = workspace_dir(library, raw)
        result.mkdir(parents=True)
        (result / "transcript.json").write_text(raw.model_dump_json(), encoding="utf-8")
        fresh = NoteDocument(transcript=raw, provider="codex", analysis=NoteAnalysis(summary="来自 Codex 的摘要"))
        (result / "note.json").write_text(fresh.model_dump_json(), encoding="utf-8")
        (result / "article.html").write_text("<html>fresh</html>", encoding="utf-8")
        return result / "article.html"

    monkeypatch.setattr(desktop, "generate_article", generate)
    events = []
    output = desktop._generate_with_replacement(
        "https://youtu.be/1-Fo8McCD9w", tmp_path, events.append,
        local_file=False, replace=False, provider=SimpleNamespace(name="codex"),
    )
    assert output == folder / "article.html"
    assert len(invoked) == 1
    assert output.read_text() == "<html>fresh</html>"
    assert load_document(folder / "note.json").provider == "codex"
    assert any(x.stage == "provider_switch" for x in events)


def test_old_partial_note_survives_failed_provider_switch(tmp_path, monkeypatch):
    folder = archive_with_api_note(tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(desktop, "generate_article", fail)
    with pytest.raises(RuntimeError, match="model unavailable"):
        desktop._generate_with_replacement(
            "https://www.youtube.com/watch?v=1-Fo8McCD9w",
            tmp_path, lambda e: None, local_file=False, replace=False,
            provider=SimpleNamespace(name="codex"),
        )
    assert load_document(folder / "note.json").provider == "api"

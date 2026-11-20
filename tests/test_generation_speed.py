"""Generation checkpointing and overlap of independent model stages."""

from threading import Barrier

import pytest

from linkread_desktop.ai.models import NoteAnalysis, NoteDocument
from linkread_desktop.generation import generate_article
from linkread_desktop.errors import LinkReadError
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment


def source_document():
    return TranscriptDocument(
        source=SourceInfo(
            platform="youtube", title="速度测试视频",
            url="https://www.youtube.com/watch?v=1-Fo8McCD9w",
            video_id="1-Fo8McCD9w",
        ),
        transcript_source="subtitle",
        segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="完整字幕")],
    )


def test_generation_runs_polish_and_publication_concurrently(tmp_path, monkeypatch):
    raw = source_document()
    note = NoteDocument(transcript=raw, provider="codex", analysis=NoteAnalysis(summary="已完成 AI 提炼"))
    rendezvous = Barrier(2)
    events = []
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *args, **kwargs: raw)
    monkeypatch.setattr("linkread_desktop.generation.enrich", lambda *args, **kwargs: note)

    def fake_polish(document, *_args, **_kwargs):
        assert list(tmp_path.glob("*/note.json")), "analysis must be persisted before polishing"
        rendezvous.wait(timeout=5)
        return document

    def fake_publish(document, extras, *_args, **_kwargs):
        rendezvous.wait(timeout=5)
        return extras

    monkeypatch.setattr("linkread_desktop.generation.polish_reading", fake_polish)
    monkeypatch.setattr("linkread_desktop.generation.complete_extras", fake_publish)
    article = generate_article(
        raw.source.url, object(), library=tmp_path, jobs=2, progress=events.append,
    )
    assert article.is_file()
    stages = [event.stage for event in events]
    assert "ai_saved" in stages
    assert "parallel_start" in stages
    assert "article_ai_done" in stages
    assert "saved_article" in stages


def test_checkpoint_survives_failed_polish_and_is_reused(tmp_path, monkeypatch):
    raw = source_document()
    note = NoteDocument(transcript=raw, provider="codex", analysis=NoteAnalysis(summary="已完成 AI 提炼"))
    calls = []
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *args, **kwargs: raw)

    def fake_enrich(*args, **kwargs):
        calls.append("enrich")
        return note

    def fail_polish(*args, **kwargs):
        raise LinkReadError("校对模型调用失败")

    monkeypatch.setattr("linkread_desktop.generation.enrich", fake_enrich)
    monkeypatch.setattr("linkread_desktop.generation.polish_reading", fail_polish)
    monkeypatch.setattr("linkread_desktop.generation.complete_extras", lambda doc, extras, *a, **kw: extras)

    with pytest.raises(LinkReadError, match="校对模型调用失败"):
        generate_article(raw.source.url, object(), library=tmp_path, jobs=1)
    saved = list(tmp_path.glob("*/note.json"))
    assert len(saved) == 1
    assert NoteDocument.model_validate_json(saved[0].read_text(encoding="utf-8")).analysis.summary == "已完成 AI 提炼"

    with pytest.raises(LinkReadError, match="校对模型调用失败"):
        generate_article(raw.source.url, object(), library=tmp_path, jobs=1)
    assert calls == ["enrich"]

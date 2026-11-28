import json
from pathlib import Path

import pytest

from linkread_desktop import desktop
from linkread_desktop.ai.polish import POLISH_SCHEMA
from linkread_desktop.ai.provider import ProviderError
from linkread_desktop.errors import LinkReadError
from linkread_desktop.extractors.local import extract_local
from linkread_desktop.renderers.reading_state import reading_state_key
from linkread_desktop.transcript_previews import load_preview, prepare_preview, resume_preview, save_preview
from test_ai_notes import FakeProvider, make_transcript
from test_desktop import invoke


def test_transcript_snapshot_is_saved_without_eager_preview_html_and_survives_failure(tmp_path, monkeypatch, capsys):
    raw = make_transcript(text="全部原文必须保留。")
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *args, **kwargs: raw)

    class Failure:
        name = "api"
        def generate(self, *args, **kwargs):
            folders = list((tmp_path / ".transcript-previews").iterdir())
            assert len(folders) == 1
            assert (folders[0] / "transcript.json").is_file()
            assert (folders[0] / "request.json").is_file()
            assert not (folders[0] / "preview.html").exists()
            # Preparing a reader on demand (e.g. standalone downloaded subtitles)
            # renders preview.html without waiting for an archive-wide generation lock.
            assert prepare_preview(tmp_path, folders[0].name)[0] == raw
            assert (folders[0] / "preview.html").is_file()
            assert not list(tmp_path.glob("*/article.html"))
            raise ProviderError("failed", code="model_timeout")

    monkeypatch.setattr(desktop, "make_provider", lambda *_a, **_k: Failure())
    status, events = invoke(monkeypatch, capsys, tmp_path, "generate", raw.source.url)
    assert status == 1
    saved = next(event for event in events if event.get("stage") == "saved_transcript")
    preview_id = saved["previewId"]
    assert len(preview_id) == 64
    status, prepared = invoke(monkeypatch, capsys, tmp_path, "preview-reader", preview_id)
    assert status == 0
    result = prepared[0]
    assert result["previewId"] == result["article"]["previewId"] == preview_id
    assert result["article"]["id"] == "preview-" + preview_id
    assert result["article"]["hasAnalysis"] is False
    assert result["readingKey"] == reading_state_key(raw)
    assert load_preview(tmp_path, preview_id)[1] == raw
    status, shelf = invoke(monkeypatch, capsys, tmp_path, "library")
    assert status == 0 and shelf[0]["articles"] == []


def test_missing_local_input_can_resume_from_bound_snapshot(tmp_path, monkeypatch, capsys):
    original = tmp_path / "original.srt"
    original.write_text("1\n00:00:01,000 --> 00:00:02,000\n恢复后仍是完整原文\n", encoding="utf-8")
    raw = extract_local(original)
    library = tmp_path / "library"
    preview_id = save_preview(library, raw, source=str(original), local_file=True)
    original.unlink()
    status, duplicate = invoke(monkeypatch, capsys, library, "duplicate", "--local-file",
                               "--resume-preview", preview_id, str(original))
    assert status == 0 and duplicate[0]["article"] is None
    provider = FakeProvider()
    monkeypatch.setattr(desktop, "make_provider", lambda *_a, **_k: provider)
    status, events = invoke(monkeypatch, capsys, library, "generate", "--local-file",
                            "--resume-preview", preview_id, str(original))
    assert status == 0, events[-1]
    assert events[-1]["article"]["title"] == "original"
    saved = library / events[-1]["article"]["id"] / "transcript.json"
    assert json.loads(saved.read_text())["segments"] == [item.model_dump() for item in raw.segments]
    assert not original.exists()


def test_resume_rejects_wrong_source_and_changed_original(tmp_path):
    raw = make_transcript()
    preview_id = save_preview(tmp_path, raw, source=raw.source.url, local_file=False)
    with pytest.raises(LinkReadError, match="不匹配"):
        resume_preview(tmp_path, preview_id, source="https://youtu.be/N4jBc7_ThnE", local_file=False)
    original = tmp_path / "subtitles.json"
    original.write_text(raw.model_dump_json())
    save_preview(tmp_path, raw, source=str(original), local_file=True)
    changed = raw.model_copy(deep=True)
    changed.segments[0].text += " changed"
    original.write_text(changed.model_dump_json())
    with pytest.raises(LinkReadError, match="已变化"):
        resume_preview(tmp_path, preview_id, source=str(original), local_file=True)
    with pytest.raises(LinkReadError, match="不匹配"):
        resume_preview(tmp_path, preview_id, source=str(tmp_path / "other.json"), local_file=True)


@pytest.mark.parametrize("invalid", ["../escape", "A" * 64, "a" * 63, "a" * 64 + "/transcript.json"])
def test_preview_ids_are_not_paths(tmp_path, invalid):
    with pytest.raises(LinkReadError):
        load_preview(tmp_path, invalid)


@pytest.mark.parametrize("target", ["transcript.json", "preview.html", "request.json"])
def test_preview_does_not_follow_symlink_files(tmp_path, target):
    raw = make_transcript()
    preview_id = save_preview(tmp_path, raw, source=raw.source.url, local_file=False)
    folder = tmp_path / ".transcript-previews" / preview_id
    outside = tmp_path / "outside"
    outside.write_text("do not change")
    (folder / target).unlink()
    (folder / target).symlink_to(outside)
    with pytest.raises(LinkReadError):
        prepare_preview(tmp_path, preview_id)
    assert outside.read_text() == "do not change"


def test_snapshot_integrity_and_prepared_html_are_revalidated(tmp_path):
    raw = make_transcript()
    preview_id = save_preview(tmp_path, raw, source=raw.source.url, local_file=False)
    folder, _ = load_preview(tmp_path, preview_id)
    (folder / "preview.html").write_text("<script>forged()</script>")
    prepare_preview(tmp_path, preview_id)
    assert "forged()" not in (folder / "preview.html").read_text()
    changed = raw.model_copy(deep=True)
    changed.segments[0].text += "tampered"
    (folder / "transcript.json").write_text(changed.model_dump_json())
    with pytest.raises(LinkReadError, match="已被改动"):
        prepare_preview(tmp_path, preview_id)


def test_preview_uses_saved_shownotes_without_changing_snapshot_identity(tmp_path):
    from linkread_desktop.workspace import workspace_dir, _source_key
    raw = make_transcript()
    archive = workspace_dir(tmp_path, raw)
    archive.mkdir()
    (archive / ".source-metadata.json").write_text(json.dumps({
        "version": 1, "sourceKey": _source_key(raw), "description": "Saved publisher Shownotes",
        "descriptionFormat": "text"}))
    preview_id = save_preview(tmp_path, raw, source=raw.source.url, local_file=False)
    document, _ = prepare_preview(tmp_path, preview_id)
    folder, stored = load_preview(tmp_path, preview_id)
    assert document == stored == raw
    assert "Saved publisher Shownotes" in (folder / "preview.html").read_text()


@pytest.mark.parametrize("target", ["library", "root", "snapshot"])
def test_preview_directory_symlinks_do_not_write_outside(tmp_path, target):
    raw = make_transcript()
    outside = tmp_path / "outside"
    outside.mkdir()
    library = tmp_path / "library"
    if target == "library":
        library.symlink_to(outside, target_is_directory=True)
    else:
        library.mkdir()
        root = library / ".transcript-previews"
        if target == "root":
            root.symlink_to(outside, target_is_directory=True)
        else:
            root.mkdir()
            from linkread_desktop.transcript_previews import snapshot_id
            (root / snapshot_id(raw)).symlink_to(outside, target_is_directory=True)
    with pytest.raises(LinkReadError):
        save_preview(library, raw, source=raw.source.url, local_file=False)
    assert not list(outside.iterdir())


def test_staging_identity_tampering_is_rejected_before_generation(tmp_path, monkeypatch):
    from linkread_desktop.ai.checkpoints import provider_identity
    provider = FakeProvider()
    raw = make_transcript()
    stage = desktop._generation_stage(tmp_path, "youtube-1-Fo8McCD9w", provider_identity(provider), None)
    (stage / "identity.json").write_text('{"version":1,"sourceKey":"other-source"}')
    monkeypatch.setattr(desktop, "generate_article", lambda *_a, **_k: pytest.fail("unverified stage must not call AI"))
    with pytest.raises(LinkReadError, match="不匹配"):
        desktop._generate_with_replacement(raw.source.url, tmp_path, lambda _event: None,
                                            local_file=False, replace=True, provider=provider)


def test_failed_regeneration_keeps_old_article_and_only_retries_missing_stage(tmp_path, monkeypatch, capsys):
    raw = make_transcript(num=10)
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *args, **kwargs: raw)
    provider = FakeProvider()
    monkeypatch.setattr(desktop, "make_provider", lambda *_a, **_k: provider)
    status, events = invoke(monkeypatch, capsys, tmp_path, "generate", raw.source.url)
    assert status == 0
    article = tmp_path / events[-1]["article"]["id"] / "manifest.json"
    original = article.read_bytes()

    class FailedProof(FakeProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            if schema == POLISH_SCHEMA:
                raise ProviderError("proof unavailable", code="model_timeout")
            return super().generate(prompt, schema, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(desktop, "make_provider", lambda *_a, **_k: FailedProof())
    status, failure = invoke(monkeypatch, capsys, tmp_path, "generate", "--replace", raw.source.url)
    assert status == 1 and failure[-1]["stage"] == "polish"
    assert article.read_bytes() == original
    preview_id = next(event["previewId"] for event in failure if event.get("stage") == "saved_transcript")
    assert load_preview(tmp_path, preview_id)[1] == raw
    assert list((tmp_path / ".generation-staging").glob("*/*/note.json"))
    good = FakeProvider()
    monkeypatch.setattr(desktop, "make_provider", lambda *_a, **_k: good)
    status, success = invoke(monkeypatch, capsys, tmp_path, "generate", "--replace",
                             "--resume-preview", preview_id, raw.source.url)
    assert status == 0, success[-1]
    assert good.calls and all(fields == ["blocks"] for fields in good.calls)
    assert load_preview(tmp_path, preview_id)[1] == raw
    assert not list((tmp_path / ".generation-staging").glob("*/*/note.json"))


def test_staging_cache_is_bound_to_model_identity(tmp_path, monkeypatch, capsys):
    raw = make_transcript(num=10)
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *args, **kwargs: raw)
    class FailedProof(FakeProvider):
        model = "first"
        def generate(self, prompt, schema, *, timeout_seconds):
            if schema == POLISH_SCHEMA:
                raise ProviderError("proof unavailable")
            return super().generate(prompt, schema, timeout_seconds=timeout_seconds)
    monkeypatch.setattr(desktop, "make_provider", lambda *_a, **_k: FailedProof())
    status, _ = invoke(monkeypatch, capsys, tmp_path, "generate", "--replace", raw.source.url)
    assert status == 1
    good = FakeProvider()
    good.model = "second"
    monkeypatch.setattr(desktop, "make_provider", lambda *_a, **_k: good)
    status, events = invoke(monkeypatch, capsys, tmp_path, "generate", "--replace", raw.source.url)
    assert status == 0, events[-1]
    assert any(fields != ["blocks"] for fields in good.calls)
    assert list((tmp_path / ".generation-staging").glob("*/*/note.json"))


def test_incomplete_nonreplacement_run_cannot_reuse_another_model(tmp_path, monkeypatch, capsys):
    raw = make_transcript(num=10)
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *args, **kwargs: raw)
    class FirstModel(FakeProvider):
        name = "api"
        model = "first-model"
        base_url = "https://example.com/v1"
        def generate(self, prompt, schema, *, timeout_seconds):
            if schema == POLISH_SCHEMA:
                raise ProviderError("proof unavailable")
            return super().generate(prompt, schema, timeout_seconds=timeout_seconds)
    monkeypatch.setattr(desktop, "make_provider", lambda *_a, **_k: FirstModel())
    status, _ = invoke(monkeypatch, capsys, tmp_path, "generate", raw.source.url)
    assert status == 1 and list(tmp_path.glob("*/note.json"))
    good = FakeProvider()
    good.name, good.model, good.base_url = "api", "second-model", "https://example.com/v1"
    monkeypatch.setattr(desktop, "make_provider", lambda *_a, **_k: good)
    status, events = invoke(monkeypatch, capsys, tmp_path, "generate", raw.source.url)
    assert status == 0, events[-1]
    assert any(event.get("stage") == "provider_switch" for event in events)
    assert any(fields != ["blocks"] for fields in good.calls)
    folder = tmp_path / events[-1]["article"]["id"]
    assert json.loads((folder / ".generation-identity.json").read_text())["model"] == "second-model"


def test_resume_from_asr_snapshot_never_submits_paid_transcription(tmp_path, monkeypatch, capsys):
    raw = make_transcript()
    raw.transcript_source, raw.schema_version = "asr", "1.1"
    preview_id = save_preview(tmp_path, raw, source=raw.source.url, local_file=False)
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video",
                        lambda *_a, **_k: pytest.fail("saved ASR source must remain offline"))
    monkeypatch.setattr(desktop, "make_provider", lambda *_a, **_k: FakeProvider())
    status, events = invoke(monkeypatch, capsys, tmp_path, "generate", "--asr",
                            "--resume-preview", preview_id, raw.source.url)
    assert status == 0, events[-1]
    assert not any(event.get("stage", "").startswith("asr_") for event in events)

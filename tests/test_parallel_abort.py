"""Offline regression test pinning current parallel failure semantics."""

import json

import pytest

from linkread_desktop.ai.pipeline import CHUNK_SCHEMA
from linkread_desktop.ai.polish import POLISH_SCHEMA
from linkread_desktop.ai.provider import ProviderError
from linkread_desktop.generation import generate_article
from test_ai_notes import FakeProvider, make_transcript


def test_analyze_failure_raises_original_error_skips_publish_and_reuses_completed_polish_cache(tmp_path):
    original_error = ProviderError("analysis unavailable", code="model_timeout", stage="analysis")
    seen_publish = []

    class BrokenAnalysis(FakeProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            if schema == CHUNK_SCHEMA:
                raise original_error
            if schema != POLISH_SCHEMA:
                seen_publish.append(schema)
            return super().generate(prompt, schema, timeout_seconds=timeout_seconds)

    raw = make_transcript(num=10)
    path = tmp_path / "input.json"
    path.write_text(raw.model_dump_json(), encoding="utf-8")
    library = tmp_path / "library"

    with pytest.raises(ProviderError) as caught:
        generate_article(str(path), BrokenAnalysis(), library=library, jobs=4)

    assert caught.value is original_error
    assert caught.value.stage == "analysis"
    assert caught.value.code == "model_timeout"
    assert seen_publish == []
    assert not list(library.glob("*/note.json"))
    assert len(list(library.glob("*/.ai-checkpoints/*.json"))) >= 1

    good = FakeProvider()
    article = generate_article(str(path), good, library=library, jobs=4)
    assert article.is_file()
    # Proofreading batches verified during the failed run are reused from cache.
    assert not any(fields == ["blocks"] for fields in good.calls)
    metrics = json.loads(article.with_name(".performance.json").read_text(encoding="utf-8"))
    assert metrics["cache_hits"] >= 1
    assert metrics["model_calls"] == 2

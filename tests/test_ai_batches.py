import json
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait
from threading import Barrier, Event, Lock

import pytest

from linkread_desktop.ai import batches
from linkread_desktop.ai.batches import ordered_batches
from linkread_desktop.ai.models import NoteAnalysis, NoteDocument, lexical_content
from linkread_desktop.ai.pipeline import CHUNK_SCHEMA, enrich
from linkread_desktop.ai.polish import POLISH_SCHEMA, group_source_blocks, polish_reading
from linkread_desktop.ai.provider import ProviderError
from linkread_desktop.errors import LinkReadError
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment


@pytest.mark.parametrize("workers", [2, 4])
def test_parallel_batches_bound_in_flight_calls(workers):
    lock = Lock()
    filled = Event()
    release = Event()
    active = peak = 0
    started = []

    def process(index, value):
        nonlocal active, peak
        with lock:
            started.append(index)
            active += 1
            peak = max(peak, active)
            if active == workers:
                filled.set()
        try:
            assert release.wait(5), "test did not release the active calls"
            return value * 10
        finally:
            with lock:
                active -= 1

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(ordered_batches, list(range(9)), process, workers=workers)
        try:
            assert filled.wait(5), "independent calls did not run concurrently"
            with lock:
                assert active == workers
                assert sorted(started) == list(range(1, workers + 1))
        finally:
            release.set()
        assert result.result(timeout=5) == [value * 10 for value in range(9)]

    assert peak == workers
    assert sorted(started) == list(range(1, 10))


def test_out_of_order_completion_keeps_source_order_and_one_based_indexes():
    second_completed = Event()
    completed = []

    def process(index, value):
        if index == 1:
            assert second_completed.wait(5), "second call was not scheduled"
        completed.append(index)
        if index == 2:
            second_completed.set()
        return index, value

    assert ordered_batches(["first", "second"], process, workers=2) == [
        (1, "first"), (2, "second"),
    ]
    assert completed == [2, 1]


def test_failure_checks_whole_completed_batch_before_scheduling_more(monkeypatch):
    both_started = Barrier(2)
    started = []
    error = LinkReadError("model returned an invalid source reference")

    def process(index, value):
        started.append(index)
        both_started.wait(timeout=5)
        if index == 2:
            raise error
        return value

    def completed_together(futures, *, return_when):
        # Make success and failure observable in the same scheduler iteration.
        # Neither result is allowed to cause a third model call.
        return wait(futures, return_when=ALL_COMPLETED)

    monkeypatch.setattr(batches, "wait", completed_together)
    with pytest.raises(LinkReadError) as caught:
        ordered_batches(list(range(8)), process, workers=2)
    assert caught.value is error
    assert sorted(started) == [1, 2]


@pytest.mark.parametrize("workers", [0, -1, 5])
def test_invalid_parallelism_fails_before_calling_provider(workers):
    calls = []
    with pytest.raises(LinkReadError, match="--jobs"):
        ordered_batches(["source"], lambda index, item: calls.append(item), workers=workers)
    assert calls == []


def test_sequential_failure_stops_before_next_call():
    calls = []

    def process(index, item):
        calls.append(index)
        if index == 2:
            raise LinkReadError("invalid model output")
        return item

    with pytest.raises(LinkReadError, match="invalid model output"):
        ordered_batches(["first", "second", "third"], process, workers=1)
    assert calls == [1, 2]


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_empty_batches_do_not_call_provider(workers):
    calls = []
    assert ordered_batches([], lambda index, item: calls.append(item), workers=workers) == []
    assert calls == []


def _transcript(count=6, *, long_cues=True):
    return TranscriptDocument(
        source=SourceInfo(platform="local", title="并发校对的完整字幕"),
        transcript_source="local",
        segments=[TranscriptSegment(
            start_ms=index * 1234, end_ms=index * 1234 + 1000,
            text=f"第{index}句保留原词" + ("完整字幕" * 130 if long_cues else ""),
        ) for index in range(count)],
    )


def _insights(rows):
    return {
        "summary": rows[0][1][:5], "takeaways": [], "keywords": [],
        "chapters": [{"start_index": 0, "title": rows[0][1][:5], "summary": "本段观点"}],
        "highlights": [{"start_index": 0, "end_index": len(rows) - 1}],
    }


def _edits(rows):
    return {"blocks": [
        {"index": row["index"], "text": row["text"] + "。", "subheading": None,
         "bold": ["保留原词"], "red": ["字幕里不存在的重点"]}
        for row in rows
    ]}


def test_parallel_enrich_maps_local_references_after_out_of_order_completion():
    second_completed = Event()
    completed = []

    class Provider:
        name = "codex"

        def generate(self, prompt, schema, *, timeout_seconds):
            assert schema == CHUNK_SCHEMA
            rows = json.loads(prompt.split("字幕 JSON（每项为 [index, text]，index 仅在当前分块内有效）：\n")[1])
            first = rows[0][1][:2]
            if first == "第0":
                assert second_completed.wait(5)
            completed.append(first)
            if first == "第1":
                second_completed.set()
            return _insights(rows)

    source = _transcript()
    original = source.model_dump_json()
    note = enrich(source, Provider(), max_chars=1000, workers=2)
    assert completed[0] == "第1"
    assert [chapter.start_segment_index for chapter in note.analysis.chapters] == list(range(6))
    assert [chapter.start_ms for chapter in note.analysis.chapters] == [cue.start_ms for cue in source.segments]
    assert [highlight.text for highlight in note.analysis.highlights] == [cue.text for cue in source.segments]
    assert note.analysis.summary.split("\n\n") == [cue.text[:5] for cue in source.segments]
    assert note.transcript == source
    assert source.model_dump_json() == original


def test_parallel_polish_retries_only_bad_block_and_preserves_source_order():
    second_completed = Event()
    calls = []
    lock = Lock()

    class Provider:
        name = "codex"

        def generate(self, prompt, schema, *, timeout_seconds):
            assert schema == POLISH_SCHEMA
            rows = json.loads(prompt.split("字幕块 JSON（index 只在本次输入中有效）：\n")[1])
            with lock:
                calls.append(rows)
            result = _edits(rows)
            if rows[0]["text"].startswith("第0句"):
                assert second_completed.wait(5)
                result["blocks"][1]["text"] = "模型错误替换了原文"
            else:
                second_completed.set()
            return result

    source = _transcript(count=120, long_cues=False)
    note = NoteDocument(transcript=source, provider="codex", analysis=NoteAnalysis(summary="已有摘要"))
    original = note.model_dump_json()
    raw_blocks = group_source_blocks(note)
    result = polish_reading(note, Provider(), max_chars=1000, workers=2)
    assert len(calls) == 3  # Two original batches and one targeted repair.
    retries = [rows for rows in calls if len(rows) == 1]
    assert len(retries) == 1
    assert retries[0][0]["index"] == 0
    assert retries[0][0]["text"] == raw_blocks[1].text
    paragraphs = result.analysis.reading_paragraphs
    assert [(p.start_segment_index, p.end_segment_index) for p in paragraphs] == [
        (block.start, block.end) for block in raw_blocks
    ]
    assert all(paragraph.edited for paragraph in paragraphs)
    assert all(paragraph.bold == ["保留原词"] and paragraph.red == [] for paragraph in paragraphs)
    assert lexical_content("".join(p.text for p in paragraphs)) == lexical_content(
        "".join(cue.text for cue in source.segments)
    )
    assert result.transcript == source
    assert note.model_dump_json() == original


@pytest.mark.parametrize("command", ["enrich", "polish"])
def test_parallel_failure_preserves_input_and_returns_no_partial_result(monkeypatch, command):
    both_started = Barrier(2)
    calls = []

    class Provider:
        name = "codex"

        def generate(self, prompt, schema, *, timeout_seconds):
            if schema == CHUNK_SCHEMA:
                rows = json.loads(prompt.split("字幕 JSON（每项为 [index, text]，index 仅在当前分块内有效）：\n")[1])
                first = rows[0][1]
                result = _insights(rows)
                if first.startswith("第1句"):
                    result["chapters"][0]["start_index"] = 999
            else:
                assert schema == POLISH_SCHEMA
                rows = json.loads(prompt.split("字幕块 JSON（index 只在本次输入中有效）：\n")[1])
                first = rows[0]["text"]
                result = _edits(rows)
                if first.startswith("第1句"):
                    result["blocks"][0]["index"] = 999
            calls.append(first)
            both_started.wait(timeout=5)
            return result

    monkeypatch.setattr(batches, "wait", lambda futures, **kwargs: wait(futures, return_when=ALL_COMPLETED))
    source = _transcript()
    document = source if command == "enrich" else NoteDocument(
        transcript=source, provider="codex", analysis=NoteAnalysis(summary="已有摘要"),
    )
    original = document.model_dump_json()
    operation = enrich if command == "enrich" else polish_reading
    with pytest.raises(ProviderError, match="结构"):
        operation(document, Provider(), max_chars=1000, workers=2)
    assert len(calls) == 2
    assert document.model_dump_json() == original

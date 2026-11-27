"""Short proof requests, source coverage, checkpoints and shared concurrency."""

from concurrent.futures import ThreadPoolExecutor
import json
from threading import Condition, Event, Lock

import pytest

from linkread_desktop.ai.checkpoints import RunProvider
from linkread_desktop.ai.early_reading import attach_reading, reading_seed
from linkread_desktop.ai.models import Chapter, NoteAnalysis, NoteDocument, lexical_content
from linkread_desktop.ai.pipeline import CHUNK_SCHEMA, enrich
from linkread_desktop.ai.polish import POLISH_SCHEMA, polish_reading
from linkread_desktop.ai.provider import ProviderError
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment


POLISH_DATA = "字幕块 JSON（index 只在本次输入中有效）：\n"
ANALYSIS_DATA = "字幕 JSON（每项为 [index, text]，index 仅在当前分块内有效）：\n"


def _note(texts):
    return NoteDocument(
        provider="api", analysis=NoteAnalysis(summary="待校对的完整原文"),
        transcript=TranscriptDocument(
            source=SourceInfo(platform="apple_podcasts", title="完整的播客录音"),
            transcript_source="asr", segments=[TranscriptSegment(
                start_ms=i * 300000, end_ms=(i + 1) * 300000, text=text,
            ) for i, text in enumerate(texts)],
        ),
    )


class UnchangedProvider:
    name = "api"

    def __init__(self):
        self.prompts = []
        self.lock = Lock()

    def generate(self, prompt, schema, *, timeout_seconds):
        assert schema == POLISH_SCHEMA
        with self.lock:
            self.prompts.append(prompt)
        rows = json.loads(prompt.split(POLISH_DATA)[1])
        return {"blocks": [{"index": row["index"], "text": None, "subheading": None} for row in rows]}


@pytest.mark.parametrize("max_chars", [1000, 5000])
def test_long_asr_cue_is_bounded_and_reassembled_after_out_of_order_results(max_chars):
    # Escapes, symbols and Latin text must survive both JSON sizing and slicing.
    long_text = ('第 42 项，引用"文本"与路径 C:\\a\\b；温度 +3℃、价格 ¥25。\n'
                 'Next sentence has ABC123 and → symbols! ' * 420)
    original = _note(["开头。", long_text, "结尾。"])
    before = original.model_dump_json()
    second_returned = Event()

    class Punctuated(UnchangedProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            payload = super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            position = int(prompt.split("校对批次：")[1].split("/", 1)[0])
            if position == 1:
                assert second_returned.wait(5)
            rows = json.loads(prompt.split(POLISH_DATA)[1])
            for item, row in zip(payload["blocks"], rows):
                # Exercise punctuation at both edges of every internal fragment.
                item["text"] = "（" + row["text"] + "）\n"
            if position == 2:
                second_returned.set()
            return payload

    provider = Punctuated()
    events = []
    result = polish_reading(original, provider, max_chars=max_chars, workers=4, progress=events.append)
    assert len(provider.prompts) > 3
    for prompt in provider.prompts:
        assert len(prompt.split(POLISH_DATA)[1]) <= min(4200, max_chars)
    assert [(p.start_segment_index, p.end_segment_index) for p in result.analysis.reading_paragraphs] == [
        (0, 0), (1, 1), (2, 2),
    ]
    for paragraph, cue in zip(result.analysis.reading_paragraphs, original.transcript.segments):
        assert lexical_content(paragraph.text) == lexical_content(cue.text)
    assert result.transcript == original.transcript
    assert original.model_dump_json() == before
    assert NoteDocument.model_validate_json(result.model_dump_json()) == result
    completed = [event.completed for event in events if event.stage == "polish_done"]
    assert completed == list(range(1, len(provider.prompts) + 1))


def test_null_is_an_explicit_checked_result_and_verified_checkpoints_are_reused(tmp_path):
    original = _note(["这是一句标点完整的字幕。" * 300])
    provider = RunProvider(UnchangedProvider(), 4)
    first = polish_reading(original, provider, workers=4, cache_dir=tmp_path)
    calls = provider.calls
    assert calls > 1
    assert all(p.edited for p in first.analysis.reading_paragraphs)
    assert first.analysis.reading_paragraphs[0].text == original.transcript.segments[0].text
    assert polish_reading(original, provider, workers=4, cache_dir=tmp_path) == first
    assert provider.calls == calls and provider.cache_hits == calls
    for cache in tmp_path.glob("*.json"):
        assert all(block["text"] is None for block in json.loads(cache.read_text())["blocks"])


def test_null_text_keeps_sparse_h3_navigation_after_semantic_chapters_arrive():
    original = _note([f"第{i}段。" + "这段原文已有自然标点。" * 25 for i in range(40)])
    original.analysis.chapters = [Chapter(
        start_segment_index=i, start_ms=original.transcript.segments[i].start_ms,
        title=f"第{i}段开始的主题", summary="实际语义章节",
    ) for i in (0, 20)]

    class Headings(UnchangedProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            assert "subheading" in schema["properties"]["blocks"]["items"]["properties"]
            payload = super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            for item in payload["blocks"]:
                item["subheading"] = "话题中的局部转折"
            return payload

    provider = Headings()
    early = polish_reading(reading_seed(original.transcript, provider), provider)
    result = attach_reading(original, early)
    headings = [i for i, paragraph in enumerate(result.analysis.reading_paragraphs) if paragraph.subheading]
    assert headings and all(b - a >= 4 for a, b in zip(headings, headings[1:]))
    assert all(paragraph.subheading is None for paragraph in result.analysis.reading_paragraphs
               if paragraph.start_segment_index in (0, 20))
    assert all("话题中的局部转折" not in paragraph.text for paragraph in result.analysis.reading_paragraphs)
    assert result.analysis.chapters == original.analysis.chapters
    assert result.transcript == original.transcript
    assert NoteDocument.model_validate_json(result.model_dump_json()) == result


def test_unrepairable_fragment_falls_back_to_the_complete_original_cue():
    source_text = "BROKEN 开头原文。" + "后续完整原词与数字 123。" * 240
    original = _note([source_text])

    class Changed(UnchangedProvider):
        bad_calls = 0

        def generate(self, prompt, schema, *, timeout_seconds):
            payload = super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            rows = json.loads(prompt.split(POLISH_DATA)[1])
            for item, row in zip(payload["blocks"], rows):
                if "BROKEN" in row["text"]:
                    item["text"] = row["text"].replace("BROKEN", "改写")
                    self.bad_calls += 1
            return payload

    provider = Changed()
    events = []
    result = polish_reading(original, provider, progress=events.append)
    assert provider.bad_calls == 2  # One targeted repair, never a retry of all text.
    assert len(result.analysis.reading_paragraphs) == 1
    paragraph = result.analysis.reading_paragraphs[0]
    assert paragraph.edited is False and paragraph.text == source_text
    assert result.transcript == original.transcript
    assert any(event.stage == "polish_warning" for event in events)


def test_lexically_corrupt_checkpoint_is_revalidated_and_refetched(tmp_path):
    original = _note(["可完整复用的字幕。"])
    provider = UnchangedProvider()
    first = polish_reading(original, provider, cache_dir=tmp_path)
    cache = next(tmp_path.glob("*.json"))
    cache.write_text(json.dumps({"blocks": [{"index": 0, "text": "缓存被改动"}]}))
    second = polish_reading(original, provider, cache_dir=tmp_path)
    assert second == first
    assert len(provider.prompts) == 2


def test_null_does_not_allow_omitted_blocks():
    class Missing(UnchangedProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            return {"blocks": []}

    provider = Missing()
    with pytest.raises(ProviderError, match="结构不合法"):
        polish_reading(_note(["不能因 null 协议丢弃原文。"]), provider)
    assert len(provider.prompts) == 1


def test_timeout_is_not_retried_or_reissued_with_smaller_batches(tmp_path):
    class TimedOut(UnchangedProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            raise ProviderError("模型调用超时")

    provider = TimedOut()
    with pytest.raises(ProviderError, match="超时"):
        polish_reading(_note(["待校对的原文。" * 500]), provider, workers=1, cache_dir=tmp_path)
    assert len(provider.prompts) == 1
    assert not list(tmp_path.glob("*.json"))


def test_completed_long_cue_batches_survive_a_later_timeout(tmp_path):
    original = _note(["每一段已完成校对都应复用。" * 500])

    class Partial(UnchangedProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            payload = super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            if len(self.prompts) == 2:
                raise ProviderError("后续批次超时")
            return payload

    events = []
    with pytest.raises(ProviderError, match="超时"):
        polish_reading(original, Partial(), progress=events.append, cache_dir=tmp_path)
    total = next(event.total for event in events if event.stage == "polish_start")
    assert total > 2 and len(list(tmp_path.glob("*.json"))) == 1
    resumed = RunProvider(UnchangedProvider(), 4)
    result = polish_reading(original, resumed, workers=4, cache_dir=tmp_path)
    assert resumed.cache_hits == 1 and resumed.calls == total - 1
    assert result.analysis.reading_paragraphs[0].text == original.transcript.segments[0].text
    assert result.transcript == original.transcript


def test_six_stage_workers_share_four_fifo_slots_without_starving_analysis():
    entered = Condition()
    starts = []
    names = [f"proof-{i}" for i in range(4)] + ["analysis-0", "analysis-1", "proof-next"]
    release = {name: Event() for name in names}

    class Provider:
        name = "api"

        def generate(self, prompt, schema, *, timeout_seconds):
            with entered:
                starts.append(prompt)
                entered.notify_all()
            assert release[prompt].wait(5)
            return {"checked": True}

    bounded = RunProvider(Provider(), 4)

    def run_proof(name):
        bounded.generate(name, {}, timeout_seconds=10)
        if name == "proof-0":
            bounded.generate("proof-next", {}, timeout_seconds=10)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(run_proof, f"proof-{i}") for i in range(4)]
        try:
            with entered:
                assert entered.wait_for(lambda: len(starts) == 4, timeout=5)
            for index in range(2):
                futures.append(executor.submit(bounded.generate, f"analysis-{index}", {}, timeout_seconds=10))
                with bounded.condition:
                    assert bounded.condition.wait_for(lambda: len(bounded._waiting) == index + 1, timeout=5)
            release["proof-0"].set()
            with entered:
                assert entered.wait_for(lambda: "analysis-0" in starts, timeout=5)
            release["proof-1"].set()
            with entered:
                assert entered.wait_for(lambda: "analysis-1" in starts, timeout=5)
                assert starts[4:6] == ["analysis-0", "analysis-1"]
                assert "proof-next" not in starts
        finally:
            for gate in release.values():
                gate.set()
        for future in futures:
            future.result(timeout=5)
    assert bounded.peak_concurrency == 4 and bounded.calls == 7
    assert bounded.queue_seconds > 0
    assert 0 < bounded.longest_model_seconds <= bounded.model_seconds
    assert bounded.prompt_chars == sum(map(len, names))
    assert bounded.max_prompt_chars == max(map(len, names))
    response_chars = len(json.dumps({"checked": True}, separators=(",", ":")))
    assert bounded.output_chars == 7 * response_chars
    assert bounded.max_output_chars == response_chars


def test_failed_request_releases_its_slot_and_does_not_claim_a_response():
    class FailsOnce:
        name = "api"
        calls = 0

        def generate(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ProviderError("服务不可用")
            return {}

    bounded = RunProvider(FailsOnce(), 1)
    with pytest.raises(ProviderError):
        bounded.generate("first", {}, timeout_seconds=10)
    assert bounded.generate("second", {}, timeout_seconds=10) == {}
    assert bounded.calls == 2 and bounded.peak_concurrency == 1
    assert bounded.output_chars == 2


def test_analysis_preserves_oversized_cue_instead_of_inventing_timestamps():
    original = _note(["没有更细时间戳的完整字幕。" * 700]).transcript
    seen = []

    class Provider:
        name = "api"

        def generate(self, prompt, schema, *, timeout_seconds):
            assert schema == CHUNK_SCHEMA
            rows = json.loads(prompt.split(ANALYSIS_DATA)[1])
            seen.extend(rows)
            return {"summary": "完整分段总结", "takeaways": [], "keywords": [],
                    "chapters": [{"start_index": 0, "title": "完整片段", "summary": "主题概述"}],
                    "highlights": [{"start_index": 0, "end_index": 0}]}

    result = enrich(original, Provider())
    assert seen == [[0, original.segments[0].text]]
    assert len(seen[0][1]) > 4500
    assert result.transcript == original
    assert result.analysis.chapters[0].start_ms == original.segments[0].start_ms
    assert result.analysis.highlights[0].text == original.segments[0].text


def test_codex_uses_low_reasoning_effort_only_for_mechanical_stages(monkeypatch):
    from linkread_desktop.ai.provider import CodexProvider
    import subprocess
    from pathlib import Path

    seen_cmds = []
    monkeypatch.setattr("linkread_desktop.ai.provider.shutil.which", lambda name: "/mock/" + name)

    def fake_run(cmd, **kwargs):
        seen_cmds.append(cmd)
        Path(cmd[cmd.index("--output-last-message") + 1]).write_text('{"ok": true}', encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("linkread_desktop.ai.provider.subprocess.run", fake_run)
    bounded = RunProvider(CodexProvider(), 2)
    bounded.execute("p1", {}, lambda v: v, timeout_seconds=30, stage="analysis")
    bounded.execute("p2", {}, lambda v: v, timeout_seconds=30, stage="polish")
    bounded.execute("p3", {}, lambda v: v, timeout_seconds=30, stage="translation")
    assert 'model_reasoning_effort="low"' not in seen_cmds[0]
    assert ["-c", 'model_reasoning_effort="low"'] == seen_cmds[1][-3:-1]
    assert ["-c", 'model_reasoning_effort="low"'] == seen_cmds[2][-3:-1]

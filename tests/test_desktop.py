import json
import sys

import pytest

from linkread_desktop import desktop
from linkread_desktop.ai.provider import ProviderError
from linkread_desktop.renderers.site import load_transcript
from test_ai_notes import FakeProvider, make_transcript


def invoke(monkeypatch, capsys, library, *args):
    monkeypatch.setattr(sys, "argv", ["linkread_desktop.desktop", "--library", str(library), *args])
    status = desktop.main()
    output = capsys.readouterr()
    assert output.err == ""
    return status, [json.loads(line) for line in output.out.splitlines()]


def test_desktop_generation_steps_reuse_and_exports(tmp_path, monkeypatch, capsys):
    provider = FakeProvider()
    raw = make_transcript(num=40, text="一段可以离线阅读的文字" * 35)
    raw.source.author = "字幕作者"
    raw.source.published_at = "2026-11-11"
    source = raw.source.url
    extract_calls = []

    def extract(url, *, progress):
        extract_calls.append(url)
        assert progress is not None
        return raw

    monkeypatch.setattr("linkread_desktop.generation.extract_document", extract)
    monkeypatch.setattr(desktop, "make_provider", lambda *args, **kwargs: provider)
    library = tmp_path / "资料库"
    status, events = invoke(monkeypatch, capsys, library, "generate", source)
    assert status == 0
    assert events[0]["stage"] == "extract"
    saved_index = next(index for index, event in enumerate(events) if event.get("stage") == "saved_transcript")
    metadata = events[saved_index]["metadata"]
    assert metadata == {
        "title": raw.source.title, "platform": "youtube", "sourceUrl": source,
        "author": raw.source.author, "publishedAt": raw.source.published_at, "durationMs": raw.source.duration_ms,
        "thumbnailUrl": "https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg",
    }
    assert all("work_dir" not in event for event in events)
    assert saved_index < next(index for index, event in enumerate(events) if event.get("stage") == "ai_start")
    for prefix in ("ai", "polish"):
        start = next(event for event in events if event.get("stage") == f"{prefix}_start")
        done = [event for event in events if event.get("stage") == f"{prefix}_done"]
        assert start["completed"] == 0 and start["total"] > 1
        assert [event["completed"] for event in done] == list(range(1, start["total"] + 1))
        assert all(event["total"] == start["total"] for event in done)
    result = events[-1]
    assert result["type"] == "completed" and result["output"] == "article.html"
    article = result["article"]
    path = library / article["id"] / result["output"]
    assert 'id="tab-summary"' in path.read_text()
    assert article["sourceUrl"] == source and article["title"] == raw.source.title
    assert article["author"] == "字幕作者" and article["publishedAt"] == "2026-11-11"
    assert article["durationMs"] == raw.source.duration_ms
    assert article["thumbnailUrl"] == metadata["thumbnailUrl"]
    assert not (tmp_path / "site").exists()
    assert invoke(monkeypatch, capsys, library, "library") == (
        0, [{"type": "library", "articles": [article], "unavailable": 0}],
    )
    status, output = invoke(monkeypatch, capsys, library, "markdown", article["id"])
    assert status == 0 and output[0]["type"] == "markdown" and raw.source.title in output[0]["text"]
    ai_calls = list(provider.calls)
    status, repeated = invoke(monkeypatch, capsys, library, "generate", source)
    assert status == 1 and "已存在" in repeated[-1]["message"]
    assert extract_calls == [source] and provider.calls == ai_calls
    original_note = path.with_name("note.json").read_bytes()
    status, imported = invoke(monkeypatch, capsys, library, "generate", "--local-file", str(path.with_name("transcript.json")))
    assert status == 1 and "已存在" in imported[-1]["message"]
    assert path.with_name("note.json").read_bytes() == original_note
    assert 'id="tab-summary"' in path.read_text()
    assert extract_calls == [source] and provider.calls == ai_calls
    status, replaced = invoke(monkeypatch, capsys, library, "generate", "--replace", source)
    assert status == 0 and replaced[-1]["article"]["id"] == article["id"]
    assert len(provider.calls) > len(ai_calls)


def test_desktop_failure_emits_error_and_preserves_transcript(tmp_path, monkeypatch, capsys):
    raw = make_transcript(text="失败时也要保存的字幕")

    class FailedProvider:
        name = "codex"

        def generate(self, *args, **kwargs):
            raise ProviderError("模型暂不可用")

    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *args, **kwargs: raw)
    monkeypatch.setattr(desktop, "make_provider", lambda *args, **kwargs: FailedProvider())
    status, events = invoke(monkeypatch, capsys, tmp_path, "generate", raw.source.url)
    assert status == 1 and events[-1] == {"type": "error", "message": "模型暂不可用",
        "code": "model_failed", "stage": "analysis", "retryable": True, "action": "retry"}
    assert next(event for event in events if event.get("stage") == "saved_transcript")["metadata"]["title"] == raw.source.title
    assert len(list(tmp_path.glob("*/transcript.json"))) == 1
    assert not list(tmp_path.glob("*/note.json"))
    assert not list(tmp_path.glob("*/article.html"))


def test_desktop_rejects_non_video_and_outside_library(tmp_path, monkeypatch, capsys):
    for args in (("generate", "file:///captions.srt"), ("markdown", "../foo")):
        status, events = invoke(monkeypatch, capsys, tmp_path, *args)
        assert status == 1 and [event["type"] for event in events] == ["error"]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(("suffix", "contents"), [
    (".SRT", "1\n00:00:01,000 --> 00:00:03,000\n可离线阅读的字幕。\n"),
    (".vtt", "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n可离线阅读的字幕。\n"),
    (".json", '{"body":[{"from":1,"to":3,"content":"可离线阅读的字幕。"}]}'),
])
@pytest.mark.parametrize("provider_name", ["codex", "claude", "api"])
def test_desktop_imports_subtitles_via_ai(tmp_path, monkeypatch, capsys, suffix, contents, provider_name):
    provider = FakeProvider()
    provider.name = provider_name
    chosen = []
    def make(name, **kwargs):
        chosen.append((name, kwargs))
        return provider
    monkeypatch.setattr(desktop, "make_provider", make)
    source = tmp_path / f"字幕样本{suffix}"
    source.write_text(contents, encoding="utf-8")
    library = tmp_path / "library"
    status, events = invoke(monkeypatch, capsys, library, "generate", "--local-file",
        "--provider", provider_name, "--model", "chosen-model", "--base-url", "https://example.com/v1", str(source))
    assert status == 0, events[-1]
    assert chosen == [(provider_name, {"executable": None, "model": "chosen-model", "base_url": "https://example.com/v1"})]
    assert len(provider.calls) >= 3  # enrichment, polish and publication
    article = events[-1]["article"]
    assert article["title"] == "字幕样本" and article["platform"] == "local"
    assert article["hasAnalysis"] is True
    metadata = next(event for event in events if event.get("stage") == "saved_transcript")["metadata"]
    assert metadata["title"] == article["title"] and metadata["thumbnailUrl"] is None
    folder = library / article["id"]
    page = (folder / "article.html").read_text()
    for panel in ("summary", "transcript", "mindmap", "keywords", "highlights"):
        assert f'id="tab-{panel}"' in page
    from linkread_desktop.renderers.site import load_document
    note = load_document(folder / "note.json")
    assert note.provider == provider_name
    assert note.analysis.reading_paragraphs
    assert load_transcript(folder / "transcript.json").segments[0].start_ms == 1000
    assert source.read_text() == contents
    status, events = invoke(monkeypatch, capsys, library, "generate", str(source))
    assert status == 1 and events[0]["type"] == "error"


def test_desktop_local_argument_rejects_non_files_and_urls(tmp_path, monkeypatch, capsys):
    for source in ("captions.srt", "https://youtu.be/1-Fo8McCD9w", str(tmp_path)):
        status, events = invoke(monkeypatch, capsys, tmp_path, "generate", "--local-file", source)
        assert status == 1 and events == [{"type": "error", "message": "找不到字幕文件，请重新选择",
            "code": "source_missing", "stage": "extract", "retryable": False, "action": "source"}]
    assert not list(tmp_path.iterdir())

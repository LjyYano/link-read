import json

import pytest

from linkread_desktop.ai.provider import ProviderError
from linkread_desktop.desktop import _article_markdown, _prepared_reader
from linkread_desktop.extractors.youtube import _choose_caption
from linkread_desktop.generation import generate_article
from linkread_desktop.messages import localize_event
from linkread_desktop.ai.models import lexical_content
from linkread_desktop.translation import EDITION_FILE, load_edition, prepare_edition, reading_translation

from test_ai_notes import FakeProvider, make_transcript


class TranslationProvider(FakeProvider):
    def __init__(self, *, fail_at=None):
        super().__init__()
        self.sent = []
        self.fail_at = fail_at

    def generate(self, prompt, schema, *, timeout_seconds):
        if schema["required"] == ["translations"]:
            fragments = json.loads(prompt.splitlines()[-1])
            self.sent.extend(text for _, text in fragments)
            if self.fail_at is not None and len(self.sent) >= self.fail_at:
                return {"translations": []}
            return {"translations": [{"index": i, "text": f"中文译文 {i}。"} for i, _ in fragments]}
        return super().generate(prompt, schema, timeout_seconds=timeout_seconds)


def test_unchanged_translation_cues_use_proofread_dialogue_and_preserve_translated_terms():
    source = ["这件事很重要", "是的", "再说 childcare 这个例子", "你说得对"]
    proofread = "这件事很重要。\n\n是的，再说 childcare 这个例子。\n\n你说得对。"
    translated = [*source]
    assert reading_translation(source, translated, "zh-CN", proofread) == proofread
    translated[2] = "再说照看孩子这个例子"
    body = reading_translation(source, translated, "zh-CN", proofread)
    assert body == "这件事很重要。\n\n是的，再说照看孩子这个例子。\n\n你说得对。"
    assert lexical_content(body) == lexical_content("".join(translated))


def test_generation_renders_only_the_final_body_in_chinese_offline(tmp_path, monkeypatch):
    raw = make_transcript(10, "Original subtitle").model_copy(update={"language": "en-US"})
    before = raw.model_dump_json()
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *args, **kwargs: raw)
    provider = TranslationProvider()
    library = tmp_path / "library"
    site = tmp_path / "site"
    finished = generate_article(raw.source.url, provider, library=library, site=site, page_language="zh-CN")
    assert provider.sent == [cue.text for cue in raw.segments]
    assert raw.model_dump_json() == before
    assert "简体中文" in provider.prompts[0]
    assert load_edition(finished.parent, raw).language == "zh-CN"
    _prepared_reader(finished)
    for page in (finished, next(site.glob("pages/*/index.html")), finished.parent / ".reader.html"):
        html = page.read_text(encoding="utf-8")
        assert '<html lang="zh-CN">' in html
        assert "data-select-transcript" not in html
        body = html.split('<article class="article-body"', 1)[1].split("</article>", 1)[0]
        assert "中文译文" in body
        assert "Original subtitle" not in body
        assert 'data-cue-start="0"' in body
        assert "Original subtitle" in html.split('<section class="panel" id="panel-highlights"', 1)[1]
    markdown = _article_markdown(library, finished.parent.name)
    assert "## 译文" in markdown and "## 原始字幕" in markdown
    assert "**[00:00]** Original subtitle" in markdown


def test_translation_recovers_verified_batches_and_prefers_saved_original(tmp_path):
    raw = make_transcript(3, "The original remains." * 75).model_copy(update={"language": "en"})
    original = raw.model_dump_json()
    options = dict(max_chars=1000, timeout_seconds=10, workers=1, progress=None, cache_dir=tmp_path / "cache")
    all_batches = TranslationProvider()
    prepare_edition(tmp_path / "normal", raw, "zh-CN", all_batches, **{**options, "cache_dir": tmp_path / "independent"})
    failed = TranslationProvider(fail_at=9)
    with pytest.raises(ProviderError, match="索引无效"):
        prepare_edition(tmp_path / "resume", raw, "zh-CN", failed, **options)
    assert not (tmp_path / "resume" / EDITION_FILE).exists()
    retried = TranslationProvider()
    result = prepare_edition(tmp_path / "resume", raw, "zh-CN", retried, **options)
    assert 0 < len(retried.sent) < len(all_batches.sent)
    assert len(result.cues) == len(raw.segments)
    assert "".join(all_batches.sent) == "".join(cue.text for cue in raw.segments)
    assert raw.model_dump_json() == original
    same_language = TranslationProvider()
    assert prepare_edition(tmp_path / "same", raw, "en", same_language, **options).cues is None
    assert same_language.sent == []


def test_youtube_prefers_original_track_over_automatic_translation():
    selected = _choose_caption({"language": "en-US", "automatic_captions": {
        "zh-Hans": [{"url": "https://caption.example/?lang=en&tlang=zh-Hans", "ext": "vtt"}],
        "en-orig": [{"url": "https://caption.example/?lang=en", "ext": "vtt"}],
    }})
    assert selected[0] == "en"
    assert "tlang" not in selected[1]["url"]


def test_english_diagnostics_keep_counts_metadata_and_recovery_fields(monkeypatch):
    monkeypatch.setenv("LINKREAD_UI_LANGUAGE", "en")
    progress = {"stage": "translation_done", "message": "已翻译 2/3 批字幕",
                "completed": 2, "total": 3, "metadata": {"title": "原始标题"}}
    assert localize_event("progress", progress) == {**progress, "message": "Translation batch complete (2/3)"}
    failure = {"code": "model_timeout", "message": "请求超时", "retryable": True, "action": "retry"}
    assert localize_event("error", failure) == {**failure,
        "message": "The model request timed out. Retry to resume from verified batches."}
    validation_fail = {
        "stage": "polish_request",
        "message": "模型结果未通过原文与结构校验",
        "request": {"phase": "failed", "stage": "polish"},
    }
    assert localize_event("progress", validation_fail)["message"] == "Response did not match original wording or schema"
    network_fail = {
        "stage": "polish_request",
        "message": "本次模型请求未完成",
        "request": {"phase": "failed", "stage": "polish"},
    }
    assert localize_event("progress", network_fail)["message"] == "Model request failed"


def test_bilibili_simplified_ai_zh_skips_redundant_zh_cn_translation(tmp_path):
    from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
    from linkread_desktop.translation import needs_translation

    simplified = TranscriptDocument(
        source=SourceInfo(platform="bilibili", title="中文技术视频"),
        transcript_source="subtitle", language="ai-zh",
        segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="今天我们来讨论开发效率与系统设计问题。")],
    )
    assert needs_translation(simplified, "zh-CN") is False
    assert needs_translation(simplified, "en") is True

    traditional = TranscriptDocument(
        source=SourceInfo(platform="youtube", title="繁體影片"),
        transcript_source="subtitle", language="zh",
        segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="這是一個關於開發與系統設計的討論問題，我們還會繼續學習。")],
    )
    assert needs_translation(traditional, "zh-CN") is True


def test_translated_generation_skips_original_language_proofreading(tmp_path, monkeypatch):
    raw = make_transcript(10, "Original English subtitle sentence").model_copy(update={"language": "en-US"})
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *args, **kwargs: raw)
    provider = TranslationProvider()
    finished = generate_article(raw.source.url, provider, library=tmp_path / "library", page_language="zh-CN")
    assert finished.is_file()
    assert ["blocks"] not in provider.calls
    assert provider.sent == [cue.text for cue in raw.segments]


def test_english_original_uses_english_prompts_and_arabic_section_numbers(tmp_path, monkeypatch):
    from linkread_desktop.ai.pipeline import CHUNK_SCHEMA
    from linkread_desktop.ai.polish import POLISH_SCHEMA
    from linkread_desktop.renderers.overview import section_title

    raw = make_transcript(8, "Shipping reliable code with automated verification loops").model_copy(
        update={"language": "en"},
    )
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *args, **kwargs: raw)

    class EnglishPromptCheckProvider:
        name = "api"

        def __init__(self):
            self.seen = []

        def generate(self, prompt, schema, *, timeout_seconds):
            self.seen.append((schema["required"], prompt))
            if schema == POLISH_SCHEMA:
                assert "Subtitle blocks JSON" in prompt
                assert "Never write Chinese" in prompt
                rows = json.loads(prompt.split("Subtitle blocks JSON (index is local to this batch):\n", 1)[1])
                return {"blocks": [{"index": r["index"], "text": None, "subheading": None} for r in rows]}
            if schema == CHUNK_SCHEMA:
                assert "Subtitle JSON" in prompt
                assert "strictly in English" in prompt
                return {
                    "summary": "A concise English chunk summary.",
                    "takeaways": ["Build deterministic verification loops."],
                    "chapters": [{"start_index": 0, "title": "Building High Trust", "summary": "Introduces the shift."}],
                    "highlights": [{"start_index": 0, "end_index": 1}],
                    "keywords": ["AI Agents", "Verification"],
                }
            assert "Source JSON:\n" in prompt
            assert "Do NOT reply in Chinese" in prompt
            assert "技术与 AI" not in prompt
            return {
                "reading": {
                    "lead": "How to build high-trust agent environments for high-velocity engineering.",
                    "sections": [{"index": 0, "title": "Building High Trust", "label": "High Trust", "summary": "Introduces the core thesis."}],
                },
                "mindmap": {"root": "High-Trust Agents", "nodes": [{"parent": 0, "text": "Deterministic Checks", "index": 0}]},
                "quotes": [],
                "catalog": {"category": "Technology & AI", "tags": ["AI Agents", "Code Quality"]},
            }

    provider = EnglishPromptCheckProvider()
    article = generate_article(raw.source.url, provider, library=tmp_path / "library", page_language="original")
    html = article.read_text(encoding="utf-8")
    assert "<h2>1. Building High Trust</h2>" in html
    assert "一、" not in html
    assert section_title(1, "Chapter 1: Building High Trust", language="en") == "1. Building High Trust"
    assert section_title(1, "玩法与创新", language="zh-CN") == "一、玩法与创新"

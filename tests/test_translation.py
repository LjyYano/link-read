import json

import pytest

from linkread_desktop.ai.provider import ProviderError
from linkread_desktop.desktop import _article_markdown, _prepared_reader
from linkread_desktop.extractors.youtube import _choose_caption
from linkread_desktop.generation import generate_article
from linkread_desktop.messages import localize_event
from linkread_desktop.translation import EDITION_FILE, load_edition, prepare_edition

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


def test_generation_localizes_chinese_page_and_loads_both_editions_offline(tmp_path, monkeypatch):
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
    for page in (finished, next(site.glob("pages/*/index.html"))):
        html = page.read_text(encoding="utf-8")
        assert '<html lang="zh-CN">' in html
        assert 'data-select-transcript="original"' in html
        assert 'data-transcript-version="original" lang="en-us" hidden' in html
        assert '中文译文' in html and 'Original subtitle' in html
        assert 'data-cue-start="0"' in html
    _prepared_reader(finished)
    assert 'data-transcript-version="original"' in (finished.parent / ".reader.html").read_text()
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

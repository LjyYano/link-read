"""M3.1: complete, punctuation-only reading output and reference-style UI."""

import json
from html.parser import HTMLParser

import pytest
from pydantic import ValidationError

from linkread_desktop.ai.models import NoteAnalysis, NoteDocument
from linkread_desktop.ai.polish import POLISH_SCHEMA, _SourceBlock, _batches, _prompt, group_source_blocks, polish_reading
from linkread_desktop.ai.provider import ProviderError
from linkread_desktop.generation import _save_json
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.html import render_html
from linkread_desktop.renderers.markdown import render_markdown
from linkread_desktop.renderers.site import build_site, load_document


class AccurateProvider:
    name = 'codex'

    def __init__(self):
        self.calls = []

    def generate(self, prompt, schema, *, timeout_seconds):
        assert schema == POLISH_SCHEMA
        self.calls.append(prompt)
        blocks = json.loads(prompt.split('字幕块 JSON（index 只在本次输入中有效）：\n')[1])
        return {"blocks": [{"index": i, "text": block["text"] + "。", "subheading": None, "bold": [], "red": []}
                           for i, block in enumerate(blocks)]}


def fixture_note(n=22):
    transcript = TranscriptDocument(
        source=SourceInfo(platform="youtube", title="塞尔达传说为什么是神作", video_id="1-Fo8McCD9w"),
        transcript_source="subtitle", language="zh-Hans",
        segments=[TranscriptSegment(start_ms=i*2000, end_ms=i*2000+1800,
                                    text=f"塞尔达传说时之笛这个游戏设计了第{i}个谜题") for i in range(n)],
    )
    from linkread_desktop.ai.models import Chapter
    return NoteDocument(
        transcript=transcript, provider="codex",
        analysis=NoteAnalysis(
            summary="这是对游戏设计的分析。",
            chapters=[Chapter(start_segment_index=0, start_ms=0, title="玩法与创新", summary="玩法设计"),
                      Chapter(start_segment_index=11, start_ms=22000, title="影响与传承", summary="行业影响")],
        ),
    )


def test_polished_reading_adds_punctuation_and_preserves_every_source_character():
    note = fixture_note()
    provider = AccurateProvider()
    result = polish_reading(note, provider)
    assert result.prompt_version == "2"
    assert result.analysis.reading_paragraphs
    assert result.analysis.reading_paragraphs[0].text.endswith("。")
    assert all(p.edited for p in result.analysis.reading_paragraphs)
    assert len(provider.calls) >= 1
    assert NoteDocument.model_validate_json(result.model_dump_json()) == result
    assert not note.analysis.reading_paragraphs  # no mutation of old document
    assert result.transcript == note.transcript
    assert result.analysis.reading_paragraphs[0].start_segment_index == 0
    assert result.analysis.reading_paragraphs[-1].end_segment_index == 21


def test_groups_split_on_chapter_and_cover_all_cues():
    note = fixture_note(n=22)
    blocks = group_source_blocks(note, max_chars=150)
    assert blocks[0].start == 0
    assert 11 in [block.start for block in blocks]
    assert [(block.start,block.end) for block in blocks]
    assert [i for b in blocks for i in range(b.start, b.end+1)] == list(range(22))
    assert all(block.end < 11 or block.start >= 11 for block in blocks)


def test_short_reading_paragraphs_fill_the_character_budget():
    blocks = [_SourceBlock(i, i, f"第{i}段短字幕。", "同一主题") for i in range(40)]
    batches = _batches(blocks, max_chars=6000)
    assert batches == [blocks]


def test_polish_batches_budget_escaped_text_chapters_and_local_indexes():
    blocks = [_SourceBlock(i, i, f'第{i}段"引用"\\路径\n内容', '章节"标题"\\' * 4)
              for i in range(45)]
    batches = _batches(blocks, max_chars=1800)
    assert len(batches) > 1
    assert [block for batch in batches for block in batch] == blocks
    for position, batch in enumerate(batches, 1):
        payload = _prompt(batch, title="测试", position=position, count=len(batches)).split(
            '字幕块 JSON（index 只在本次输入中有效）：\n', 1)[1]
        assert len(payload) <= 1800
        rows = json.loads(payload)
        assert [row['index'] for row in rows] == list(range(len(batch)))
        assert [row['text'] for row in rows] == [block.text for block in batch]
        assert [row['chapter'] for row in rows] == [block.chapter for block in batch]


def test_single_oversized_reading_block_is_preserved_for_validation():
    blocks = [_SourceBlock(0, 0, '开头', ''), _SourceBlock(1, 1, '长字幕' * 3000, '章节'),
              _SourceBlock(2, 2, '结尾', '')]
    batches = _batches(blocks, max_chars=6000)
    assert batches == [[blocks[0]], [blocks[1]], [blocks[2]]]
    payload = _prompt(batches[1], title="测试", position=2, count=3).split(
        '字幕块 JSON（index 只在本次输入中有效）：\n', 1)[1]
    assert json.loads(payload)[0]['text'] == blocks[1].text


def test_ai_rewriting_or_omission_falls_back_without_losing_lexical_content():
    class Inaccurate(AccurateProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            obj = super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            obj['blocks'][0]['text'] = '这是 AI 编造出来的内容。'
            return obj
    events = []
    note = polish_reading(fixture_note(), Inaccurate(), progress=events.append)
    assert note.analysis.reading_paragraphs[0].edited is False
    assert '第0个谜题' in note.analysis.reading_paragraphs[0].text
    assert any(event.stage == 'polish_warning' for event in events)
    assert any(event.stage == 'polish_retry' for event in events)
    assert NoteDocument.model_validate_json(note.model_dump_json()) == note


def test_invalid_model_block_indexes_produce_a_clear_error():
    class WrongIndex(AccurateProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            obj = super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            obj['blocks'][0]['index'] = 999
            return obj
    with pytest.raises(ProviderError, match='结构不合法'):
        polish_reading(fixture_note(), WrongIndex())


def test_reload_rejects_lexically_corrupt_or_incomplete_reading():
    doc = polish_reading(fixture_note(), AccurateProvider())
    invalid = doc.model_dump()
    invalid['analysis']['reading_paragraphs'][0]['text'] += '私自补充了一段'
    with pytest.raises(ValidationError, match='differs from source'):
        NoteDocument.model_validate(invalid)
    invalid = doc.model_dump()
    invalid['analysis']['reading_paragraphs'].pop()
    with pytest.raises(ValidationError, match='cover'):
        NoteDocument.model_validate(invalid)


def test_reading_has_h1_h2_and_no_timestamps_in_transcript_panel():
    note = polish_reading(fixture_note(), AccurateProvider())
    markup = render_html(note)
    assert 'id="tab-transcript"' in markup and 'id="tab-raw"' not in markup
    assert 'id="chapter-0"' in markup and 'id="chapter-11"' in markup
    assert '<h2>一、玩法与创新</h2>' in markup and '<h2>二、影响与传承</h2>' in markup
    assert '<p class="reading-paragraph"' in markup
    body = markup.split('<article class="article-body">', 1)[1].split('</article>', 1)[0]
    assert 'line-time' not in body
    assert '第0个谜题' in body and '。' in body
    assert 'class="mobile-toc"' in markup and 'data-go-section="chapter-11"' in markup
    assert 'id="panel-raw"' not in markup
    assert '<!--LR:' not in markup
    assert '<script src=' not in markup
    assert render_markdown(note).count('## 玩法与创新') == 1
    assert '## 影响与传承' in render_markdown(note)


def test_polish_keeps_only_emphasis_grounded_in_the_reading_paragraph():
    class EmphasisProvider(AccurateProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            result = super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            result['blocks'][0].update(bold=['塞尔达传说', '额外观点'], red=['第0个谜题'])
            return result

    note = polish_reading(fixture_note(), EmphasisProvider())
    paragraph = note.analysis.reading_paragraphs[0]
    assert paragraph.bold == ['塞尔达传说'] and paragraph.red == ['第0个谜题']
    html = render_html(note)
    assert '<strong>塞尔达传说</strong>' in html and '<strong class="red">第0个谜题</strong>' in html
    serialized = note.model_dump()
    serialized['analysis']['reading_paragraphs'][0]['red'] = ['从未出现的引文']
    with pytest.raises(ValidationError, match='continuous phrase'):
        NoteDocument.model_validate(serialized)


def test_safe_escaping_on_reading_and_subheading():
    note = polish_reading(fixture_note(), AccurateProvider())
    updated = note.model_dump()
    updated['analysis']['chapters'][0]['title'] = '<img src=x onerror=alert(1)>'
    updated['analysis']['reading_paragraphs'][0]['subheading'] = '</script><script>alert(1)</script>'
    note = NoteDocument.model_validate(updated)
    markup = render_html(note)
    assert '&lt;img src=x onerror=alert(1)&gt;' in markup
    assert '&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;' in markup
    assert '<img src=x onerror=alert(1)>' not in markup
    assert '<script>alert(1)</script>' not in markup


def test_polish_upgrades_existing_note_without_recomputing_analysis(tmp_path):
    source = tmp_path/'note.json'
    original = fixture_note()
    source.write_text(original.model_dump_json(), encoding='utf-8')
    updated = polish_reading(load_document(source), AccurateProvider())
    _save_json(updated, tmp_path/'note-polished.json')
    upgraded = load_document(tmp_path/'note-polished.json')
    assert isinstance(upgraded, NoteDocument)
    assert len(upgraded.analysis.reading_paragraphs) > 0
    assert not load_document(source).analysis.reading_paragraphs
    assert build_site(tmp_path, tmp_path/'site') == 1
    html = next((tmp_path/'site'/'pages').glob('*/index.html')).read_text(encoding='utf-8')
    assert 'class="callout"' not in html
    assert upgraded.analysis.reading_paragraphs[0].text in html


def test_subheading_is_rendered_as_h3_when_meaningful():
    class Headed(AccurateProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            res = super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            if len(res['blocks']) >= 7:
                res['blocks'][6]['subheading'] = '玩法带来的设计启发'
            return res
    note = fixture_note(n=88)
    # Force short groups and several paragraphs under each H2.
    from linkread_desktop.ai import polish as module
    old = module.group_source_blocks
    module.group_source_blocks = lambda note: old(note,max_chars=150)
    try:
        result = polish_reading(note, Headed(), max_chars=2000)
    finally:
        module.group_source_blocks = old
    assert '<h3>玩法带来的设计启发</h3>' in render_html(result)

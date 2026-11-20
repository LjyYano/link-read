import re

import pytest

from linkread_desktop.ai.models import NoteAnalysis, NoteDocument, ReadingParagraph
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.quotes import QuoteCard, QuoteCards
from linkread_desktop.renderers.extras import ArticleExtras
from linkread_desktop.renderers.formatting import display_title
from linkread_desktop.renderers.html import render_html
from linkread_desktop.renderers.markdown import render_markdown


def document(platform='local'):
    return TranscriptDocument(
        source=SourceInfo(platform=platform, title='读懂软件开发',
                          video_id='1-Fo8McCD9w' if platform == 'youtube' else None),
        transcript_source='local', language='zh-Hans',
        segments=[TranscriptSegment(start_ms=1000, end_ms=2000, text='正文内容必须完整保留')],
    )


def note(*, edited=True):
    return NoteDocument(transcript=document(), provider='codex', analysis=NoteAnalysis(
        summary='文章摘要。', keywords=['软件开发'], reading_paragraphs=[ReadingParagraph(
            start_segment_index=0, end_segment_index=0, text='正文内容，必须完整保留。', edited=edited,
        )],
    ))


@pytest.mark.parametrize('value', [document(), note()])
def test_reader_omits_routine_explanations_and_empty_metadata(value):
    markup = render_html(value)
    for redundant in ('本地文件', '数据版本', '字幕片段', '字幕语言', '本文为',
                      '个人笔记优先', '内容主题', '点击关键词，在完整文稿中查找'):
        assert redundant not in markup
    assert 'class="callout"' not in markup
    assert '<div class="meta">' not in markup
    assert 'class="source"' not in markup
    assert '<nav class="crumb"><span>Library</span><button' in markup
    assert all(f'id="{control}"' in markup for control in (
        'text-search', 'font-increase', 'print-page',
    ))
    assert 'id="notes-area"' not in markup and 'id="tab-notes"' not in markup


def test_failed_proofreading_retains_one_concise_notice_in_html_and_markdown():
    value = note(edited=False)
    message = 'Original wording kept in 1 paragraph(s)'
    markup = render_html(value)
    assert markup.count(message) == 1
    assert "<details class=\"callout reading-fallback\">" in markup
    assert "not factual accuracy" in markup
    assert 'reading-paragraph unpolished' in markup
    assert value.analysis.reading_paragraphs[0].text in markup
    assert '1 段沿用原字幕' in render_markdown(value)


@pytest.mark.parametrize('platform', ['local', 'youtube'])
def test_quotes_keep_one_timestamp_and_only_real_source_links(platform):
    source = document(platform)
    cards = QuoteCards(cards=[QuoteCard(
        title='内容完整', start_segment_index=0, end_segment_index=0,
        front='正文内容，必须完整保留。', back='正文内容必须完整保留。',
    )])
    markup = render_html(source, extras=ArticleExtras(quotes=cards))
    assert markup.count('00:01–00:02') == 1
    assert '原文上下文</div>' not in markup
    assert 'data-copy-side="back"' in markup and 'data-go-cue="0"' in markup
    assert ('class="small-time quote-source"' in markup) == (platform == 'youtube')
    if platform == 'youtube':
        assert '&amp;t=1' in markup and '>Original video ↗</a>' in markup


def test_display_cleanup_preserves_transcript_and_reading_state_key():
    value = note()
    value.transcript.source.title += '_哔哩哔哩_bilibili_BV14uW36uEdD_字幕'
    before = value.model_dump_json()
    markup = render_html(value)
    assert '<h1>读懂软件开发</h1>' in markup
    manual = render_html(value, title='自定义显示标题')
    key = lambda html: re.search(r'data-reading-key="([^"]+)"', html)[1]
    assert key(markup) == key(manual)
    assert value.model_dump_json() == before
    assert render_markdown(value).startswith('# 读懂软件开发\n')
    assert 'AI 辅助整理' not in render_markdown(value)


def test_cleanup_never_removes_source_words_or_guesses_arbitrary_title_suffixes():
    source = document()
    source.source.title = '本地文件与字幕_字幕'
    source.segments[0].text = '本地文件的数据版本和字幕片段，也是本期正文内容。'
    assert display_title(source) == source.source.title
    assert source.segments[0].text in render_html(source)
    assert source.segments[0].text in render_markdown(source)


def test_source_metadata_stays_useful_without_label_prefixes_or_technical_details():
    source = document('youtube')
    source.source.author = '作者 <A>'
    source.source.published_at = '2026-11-15'
    source.source.duration_ms = 62000
    markup = render_html(source)
    assert '作者 &lt;A&gt;' in markup
    assert 'title="Published">2026-11-15</span>' in markup
    assert 'title="Duration">01:02</span>' in markup
    assert '>YouTube</a>' in markup
    assert '数据版本' not in markup and '字幕语言' not in markup

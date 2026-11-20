"""Flexible source-grounded quote selection and Bilibili cover normalization."""
import pytest

from linkread_desktop.ai.models import NoteAnalysis, NoteDocument
from linkread_desktop.ai.pipeline import enrich
from linkread_desktop.ai.provider import ProviderError
from linkread_desktop.ai.publication import _make_quotes, complete_extras
from linkread_desktop.metadata import thumbnail_url, video_metadata
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.quotes import QuoteCards, context_bounds, cue_text
from linkread_desktop.renderers.extras import ArticleExtras
from linkread_desktop.renderers.html import render_html
from test_ai_notes import FakeProvider
from test_metadata import Client, Response


def raw():
    return TranscriptDocument(
        source=SourceInfo(platform='bilibili', title='测试内容', video_id='BV1H4sJ6XEF5',
                          url='https://www.bilibili.com/video/BV1H4sJ6XEF5?p=1'),
        transcript_source='subtitle',
        segments=[TranscriptSegment(start_ms=i*1000, end_ms=(i+1)*1000, text=f'第{i}句有独立观点。')
                  for i in range(60)],
    )


@pytest.mark.parametrize('count', [0, 1, 2, 9, 12])
def test_quotes_do_not_have_a_fixed_count_and_round_trip(count):
    source = raw()
    note = NoteDocument(transcript=source, provider='codex', analysis=NoteAnalysis(summary='正文摘要'))
    spans = [(i*4, i*4+1) for i in range(15)]
    proposals = [{'index': i, 'title': f'观点{i}', 'front': cue_text(source, *spans[i]),
                  'back': cue_text(source, *context_bounds(source, *spans[i])), 'bold': [], 'red': []}
                 for i in range(count)]
    cards = _make_quotes(proposals, note, spans, None)
    cards.verify_source(source)
    assert len(cards.cards) == count
    assert QuoteCards.model_validate_json(cards.model_dump_json()) == cards
    html = render_html(note, extras=ArticleExtras(quotes=cards))
    assert html.count('class="anki-card"') == count
    assert ('id="tab-highlights"' in html) == (count > 0)


@pytest.mark.parametrize('bad', [[{'start_index': 0, 'end_index': 1}, {'start_index': 1, 'end_index': 2}],
                               [{'start_index': 60, 'end_index': 60}]])
def test_flexible_highlights_still_reject_overlap_or_invented_indexes(bad):
    class Invalid(FakeProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            result=super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            result['highlights']=bad
            return result
    with pytest.raises(ProviderError):
        enrich(raw(), Invalid())


def test_one_chunk_can_have_more_than_three_highlights_without_losing_original():
    class Many(FakeProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            result=super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            result['highlights']=[{'start_index': i*4, 'end_index': i*4+1} for i in range(12)]
            return result
    source=raw()
    note=enrich(source, Many())
    assert len(note.analysis.highlights)==12
    assert note.transcript==source


def test_publication_can_return_no_quotes_without_replacing_them_with_highlights():
    note=enrich(raw(), FakeProvider())
    class NoneWorthQuoting(FakeProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            result=super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            if 'quotes' in schema['required']:
                assert '不设固定配额' in prompt
                result['quotes']=[]
            return result
    extras=complete_extras(note, ArticleExtras(), NoneWorthQuoting(), timeout_seconds=20)
    assert extras.quotes is not None and extras.quotes.cards==[]
    assert 'id="tab-highlights"' not in render_html(note, extras=extras)


@pytest.mark.parametrize('value', ['http://i0.hdslb.com/bfs/archive/test.jpg',
                                  '//i0.hdslb.com/bfs/archive/test.jpg',
                                  'https://i0.hdslb.com/bfs/archive/test.jpg'])
def test_bilibili_covers_upgrade_to_https(value):
    expected='https://i0.hdslb.com/bfs/archive/test.jpg'
    assert thumbnail_url(value)==expected
    client=Client([Response({'code':0,'data':{'title':'视频','pic':value,'pages':[{'duration':12}]}})])
    meta=video_metadata('https://www.bilibili.com/video/BV1H4sJ6XEF5', session=client)
    assert meta.thumbnail_url == meta.source.thumbnail_url == expected
    assert meta.public()['thumbnailUrl']==expected


@pytest.mark.parametrize('value', [123, 'http://i0.hdslb.com.evil.test/test.jpg',
                                  'https://user:pass@i0.hdslb.com/a.jpg',
                                  'http://localhost/1.jpg', 'file:///tmp/1.png',
                                  'https://i0.hdslb.com:bad/pic.jpg',
                                  'https://i0.hdslb.com:443/pic.jpg',
                                  'https://i0.hdslb.com\\@evil.test/a.jpg'])
def test_unsafe_thumbnail_inputs_never_crash_or_expand_host_allowlist(value):
    assert thumbnail_url(value) is None

"""User-facing progress must reflect real provider results, not elapsed time."""
import pytest

from linkread_desktop.ai.models import lexical_content
from linkread_desktop.ai.pipeline import enrich
from linkread_desktop.ai.polish import polish_reading
from linkread_desktop.ai.provider import ProviderError
from linkread_desktop.ai.publication import complete_extras
from linkread_desktop.renderers.extras import ArticleExtras
from test_ai_notes import FakeProvider, make_transcript
from test_polish import AccurateProvider, fixture_note


def test_publication_completion_is_emitted_after_validated_result():
    provider = FakeProvider()
    note = enrich(make_transcript(), provider)
    events = []
    result = complete_extras(note, ArticleExtras(), provider, timeout_seconds=30, progress=events.append)
    assert result.reading is not None and result.mindmap is not None
    assert events[0].stage == 'article_ai'
    assert events[-1].stage == 'publication_done'


def test_failed_publication_never_reports_completion():
    provider = FakeProvider()
    note = enrich(make_transcript(), provider)
    events = []

    class FailedProvider:
        name = 'api'
        def generate(self, *args, **kwargs):
            raise ProviderError('model unavailable')

    with pytest.raises(ProviderError):
        complete_extras(note, ArticleExtras(), FailedProvider(), timeout_seconds=30, progress=events.append)
    assert not any(event.stage == 'publication_done' for event in events)


def test_rephrasing_is_explained_and_source_remains_unchanged():
    class Rewrite(AccurateProvider):
        def generate(self, *args, **kwargs):
            response = super().generate(*args, **kwargs)
            response['blocks'][0]['text'] = '模型擅自改写'
            return response

    original = fixture_note()
    events = []
    result = polish_reading(original, Rewrite(), progress=events.append)
    assert any(event.stage == 'polish_verify' for event in events)
    assert any('改动了文字' in event.message for event in events if event.stage == 'polish_retry')
    assert any('沿用原字幕' in event.message for event in events if event.stage == 'polish_warning')
    assert lexical_content(''.join(p.text for p in result.analysis.reading_paragraphs)) == lexical_content(
        ''.join(segment.text for segment in original.transcript.segments))

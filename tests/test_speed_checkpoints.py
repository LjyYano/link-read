import json
from threading import Barrier, Lock
import time
import pytest
from linkread_desktop.ai.checkpoints import RunProvider
from linkread_desktop.ai.early_reading import attach_reading
from linkread_desktop.ai.models import Chapter, NoteAnalysis, NoteDocument, ReadingParagraph, lexical_content
from linkread_desktop.ai.pipeline import CHUNK_SCHEMA, enrich
from linkread_desktop.ai.polish import POLISH_SCHEMA, polish_reading
from linkread_desktop.ai.provider import ProviderError
from linkread_desktop.generation import generate_article
from test_ai_notes import FakeProvider, make_transcript
from test_polish import fixture_note, AccurateProvider


def test_fresh_analysis_and_proofreading_start_together(tmp_path):
    barrier=Barrier(2)
    class ParallelProvider(FakeProvider):
        def generate(self,prompt,schema,*,timeout_seconds):
            if schema in (CHUNK_SCHEMA, POLISH_SCHEMA): barrier.wait(timeout=3)
            return super().generate(prompt,schema,timeout_seconds=timeout_seconds)
    raw=make_transcript(num=10)
    path=tmp_path/'input.json';path.write_text(raw.model_dump_json())
    article=generate_article(str(path), ParallelProvider(), library=tmp_path/'library',jobs=4)
    assert article.is_file()
    metrics=json.loads(article.with_name('.performance.json').read_text())
    assert metrics['model_calls']==3
    assert metrics['concurrency_limit']==4
    assert 2 <= metrics['max_concurrency'] <= 4
    assert metrics['prompt_chars_max'] > 0
    assert metrics['output_json_chars_max'] > 0


def test_early_reading_splits_on_semantic_chapter_without_changing_words():
    note=fixture_note(22)
    early=note.model_copy(deep=True)
    early.analysis.chapters=[]
    early.analysis.reading_paragraphs=[ReadingParagraph(start_segment_index=0,end_segment_index=21,
        text='，'.join(x.text for x in note.transcript.segments)+'。')]
    result=attach_reading(note,early)
    assert [p.start_segment_index for p in result.analysis.reading_paragraphs]==[0,11]
    assert result.analysis.chapters==note.analysis.chapters
    assert lexical_content(''.join(p.text for p in result.analysis.reading_paragraphs))==lexical_content(''.join(s.text for s in note.transcript.segments))


def test_completed_analysis_batches_survive_failed_later_batch(tmp_path):
    raw=make_transcript(num=40,text='完整字幕原文'*20)
    class FailSecond(FakeProvider):
        def generate(self,*a,**kw):
            if len(self.calls)==1: raise ProviderError('interrupted')
            return super().generate(*a,**kw)
    bad=FailSecond()
    with pytest.raises(ProviderError): enrich(raw,bad,max_chars=1000,cache_dir=tmp_path,workers=1)
    good=RunProvider(FakeProvider(),2)
    note=enrich(raw,good,max_chars=1000,cache_dir=tmp_path,workers=2)
    assert good.cache_hits==1
    assert note.transcript==raw


def test_verified_polish_checkpoint_reused_but_changed_source_is_not(tmp_path):
    note=fixture_note(22);provider=AccurateProvider()
    first=polish_reading(note,provider,cache_dir=tmp_path)
    calls=len(provider.calls)
    assert polish_reading(note,provider,cache_dir=tmp_path)==first
    assert len(provider.calls)==calls
    changed=note.model_copy(deep=True);changed.transcript.segments[0].text+='新文字'
    polish_reading(changed,provider,cache_dir=tmp_path)
    assert len(provider.calls)>calls


def test_global_limit_applies_across_stages(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    state={'active':0,'peak':0};lock=Lock()
    class Counter:
        name='api'
        def generate(self,*a,**k):
            with lock:
                state['active']+=1;state['peak']=max(state['peak'],state['active'])
            time.sleep(.015)
            with lock:state['active']-=1
            return {}
    bounded=RunProvider(Counter(),2)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda i: bounded.generate('',{},timeout_seconds=10),range(12)))
    assert state['peak']==2 and bounded.calls==12


def test_model_and_endpoint_changes_do_not_reuse_checkpoint(tmp_path):
    from linkread_desktop.ai.checkpoints import validated_call
    class Provider:
        name='api'
        def __init__(self, model, endpoint):
            self.model=model;self.base_url=endpoint;self.calls=0
        def generate(self,*a,**kw):
            self.calls+=1
            return {'answer': 'verified'}
    def run(p):
        return validated_call(p,'same source',{},lambda value: value['answer'],
                              timeout_seconds=10,cache_dir=tmp_path)
    a=Provider('one','https://example.com/v1');run(a);run(a)
    assert a.calls==1
    b=Provider('two','https://example.com/v1');run(b)
    c=Provider('one','https://other.example.com/v1');run(c)
    assert b.calls==c.calls==1


def test_completed_publication_is_saved_even_when_proofreading_fails(tmp_path):
    from linkread_desktop.renderers.extras import load_extras
    class BrokenProof(FakeProvider):
        def generate(self,prompt,schema,*,timeout_seconds):
            if schema==POLISH_SCHEMA:
                raise ProviderError('proof unavailable')
            return super().generate(prompt,schema,timeout_seconds=timeout_seconds)
    raw=make_transcript(num=10)
    path=tmp_path/'input.json';path.write_text(raw.model_dump_json())
    library=tmp_path/'library'
    with pytest.raises(ProviderError,match='proof unavailable'):
        generate_article(str(path),BrokenProof(),library=library)
    notes=list(library.glob('*/note.json'));assert len(notes)==1
    extras=load_extras(notes[0])
    assert all(getattr(extras,name) is not None for name in ('reading','quotes','mindmap','catalog'))
    good=FakeProvider()
    article=generate_article(str(path),good,library=library)
    assert article.is_file()
    assert all(fields==['blocks'] for fields in good.calls)

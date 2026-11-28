import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from linkread_desktop.ai.models import NoteDocument, lexical_content
from linkread_desktop.ai.pipeline import CHUNK_SCHEMA, enrich, split_chunks
from linkread_desktop.ai.polish import POLISH_SCHEMA, polish_reading
from linkread_desktop.ai.provider import ClaudeProvider, CodexProvider, ProviderError
from linkread_desktop.ai.publication import complete_extras
from linkread_desktop.generation import generate_article
from linkread_desktop.errors import LinkReadError
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.extras import ArticleExtras, load_extras
from linkread_desktop.renderers.html import render_html
from linkread_desktop.renderers.markdown import render_markdown
from linkread_desktop.renderers.site import build_site, load_document


class FakeProvider:
    name = "codex"

    def __init__(self):
        self.prompts = []
        self.calls = []

    def generate(self, prompt, schema, *, timeout_seconds):
        fields = schema["required"]
        self.calls.append(fields)
        if schema == POLISH_SCHEMA:
            blocks = json.loads(prompt.split("字幕块 JSON（index 只在本次输入中有效）：\n")[1])
            return {"blocks": [{"index": item["index"], "text": item["text"] + "。", "subheading": None,
                                "bold": [], "red": []}
                               for item in blocks]}
        if schema != CHUNK_SCHEMA:
            inputs = json.loads(prompt.split("素材 JSON：\n")[1])
            material = {
                "catalog": {"category": "技术与 AI", "tags": ["字幕阅读", "知识整理"]},
                "reading": {"lead": "一段全文导语。", "sections": [
                    {"index": 0, "title": "主题开场", "label": "开场", "summary": "这个主题的引入。"},
                ]},
                "mindmap": {"root": "全篇主题", "branches": [
                    {"topic": "内容", "details": ["开篇观点"]}, {"topic": "总结", "details": ["收束主题"]},
                ]},
            }
            if "quotes" in fields:
                candidate = inputs["quote_candidates"][0]
                material["quotes"] = [{
                    "index": 0, "title": "开篇观点", "front": candidate["front"] + "！", "back": candidate["back"],
                    "bold": ["用于学习"], "red": ["原始字幕"],
                }]
            assert set(fields) <= material.keys()
            if "reading" in fields and len(inputs["section_candidates"]) > 1:
                material["reading"]["sections"].append({
                    "index": len(inputs["section_candidates"]) - 1, "title": "后半段主题", "label": "收束",
                    "summary": "后续内容。",
                })
            return {name: material[name] for name in fields}
        self.prompts.append(prompt)
        subtitles = json.loads(prompt.split("字幕 JSON（每项为 [index, text]，index 仅在当前分块内有效）：\n")[1])
        assert len(subtitles) > 0
        assert all(item[0] == index and isinstance(item[1], str) for index, item in enumerate(subtitles))
        return {
            "summary": "忠实总结这一段的内容。",
            "takeaways": ["重要观点", "重要观点"],
            "chapters": [{"start_index": 0, "title": "第一节：认识内容", "summary": "这是章节概览。"}],
            "highlights": [{"start_index": 0, "end_index": min(1, len(subtitles)-1)}],
            "keywords": ["视频阅读", "知识整理"],
        }


def make_transcript(num=15, text='<script>alert("hacked")</script>字幕内容'):
    return TranscriptDocument(
        source=SourceInfo(platform="youtube", title="重要内容", video_id="1-Fo8McCD9w",
                          url="https://www.youtube.com/watch?v=1-Fo8McCD9w", duration_ms=num*1000),
        transcript_source="subtitle", language="zh-Hans",
        segments=[TranscriptSegment(start_ms=i*1000, end_ms=i*1000+980,
                                    text=f"{text}，第{i}条。") for i in range(num)],
    )


def test_enrich_grounded_chapters_and_exact_quotes_and_original_unchanged():
    source = make_transcript()
    before = source.model_dump_json()
    provider = FakeProvider()
    note = enrich(source, provider)
    assert note.document_type == "linkread_note"
    assert note.analysis.summary == "忠实总结这一段的内容。"
    assert note.analysis.takeaways == ["重要观点"]
    assert len(note.analysis.chapters) == 1
    assert note.analysis.chapters[0].start_ms == source.segments[0].start_ms
    assert note.analysis.highlights[0].text == source.segments[0].text + " " + source.segments[1].text
    assert NoteDocument.model_validate_json(note.model_dump_json()) == note
    assert source.model_dump_json() == before
    assert 'index' in provider.prompts[0]


def test_long_subtitles_are_processed_completely_and_sequentially():
    source = make_transcript(num=173, text="数据用于验证不丢失的字幕。" * 5)
    provider = FakeProvider()
    note = enrich(source, provider, max_chars=1500)
    assert len(provider.prompts) > 1
    assert len(note.analysis.chapters) == len(provider.prompts)
    assert [x.start_segment_index for x in note.analysis.chapters] == sorted(x.start_segment_index for x in note.analysis.chapters)
    assert len(note.transcript.segments) == 173
    for i in (0, 82, 172):
        assert source.segments[i].text in '\n'.join(provider.prompts)


def test_split_keeps_every_original_cue_without_truncation():
    doc = make_transcript(num=51, text="x" * 800)
    chunks = split_chunks(doc.segments, max_chars=1000)
    assert [seg for chunk in chunks for seg in chunk.segments] == doc.segments
    assert [chunk.offset for chunk in chunks] == list(range(51))
    with pytest.raises(LinkReadError, match="--max-chars"):
        split_chunks(doc.segments, max_chars=99)


def test_split_uses_exact_compact_json_boundary():
    segments = [TranscriptSegment(start_ms=i, text=text) for i, text in enumerate([
        "a" * 400, "中" * 585, "下一个分块",
    ])]
    chunks = split_chunks(segments, max_chars=1000)
    assert [len(chunk.segments) for chunk in chunks] == [2, 1]
    payload = json.dumps([[index, cue.text] for index, cue in enumerate(chunks[0].segments)],
                         ensure_ascii=False, separators=(",", ":"))
    assert len(payload) == 1000
    assert chunks[1].offset == 2


def test_split_counts_json_escapes_and_preserves_oversized_cue():
    segments = [TranscriptSegment(start_ms=i, text=text) for i, text in enumerate([
        '中文"\\\n\t' * 100, "正常字幕", "很长的一句" * 500, "最后一句",
    ])]
    chunks = split_chunks(segments, max_chars=1000)
    assert [cue for chunk in chunks for cue in chunk.segments] == segments
    for chunk in chunks:
        pairs = [[index, cue.text] for index, cue in enumerate(chunk.segments)]
        payload = json.dumps(pairs, ensure_ascii=False, separators=(",", ":"))
        assert json.loads(payload) == pairs
        if len(payload) > 1000:
            assert len(chunk.segments) == 1
    assert chunks[-1].segments == (segments[-1],)


def test_compact_prompt_indexes_map_back_to_all_source_cues():
    source = make_transcript(num=250, text='带有引号"和斜线\\的字幕')
    provider = FakeProvider()
    note = enrich(source, provider, max_chars=1000)
    payloads = [json.loads(prompt.split("字幕 JSON（每项为 [index, text]，index 仅在当前分块内有效）：\n")[1])
                for prompt in provider.prompts]
    assert [text for payload in payloads for _, text in payload] == [cue.text for cue in source.segments]
    offset = 0
    for payload, chapter, highlight in zip(payloads, note.analysis.chapters, note.analysis.highlights):
        assert chapter.start_segment_index == offset
        assert highlight.start_segment_index == offset
        assert highlight.text == " ".join(text for _, text in payload[:2])
        offset += len(payload)


@pytest.mark.parametrize("bad", [
    {"chapters": [{"start_index": 999, "title": "x", "summary": "y"}]},
    {"chapters": [{"start_index": 1, "title": "x", "summary": "y"},
                  {"start_index": 0, "title": "z", "summary": "y"}]},
    {"highlights": [{"start_index": 0, "end_index": 999}]},
    {"highlights": [{"start_index": 5, "end_index": 4}]},
    {"highlights": [{"start_index": 0, "end_index": 9}]},
])
def test_invalid_ai_references_fail_without_writing_anything(bad):
    class BadProvider(FakeProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            data = super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            data.update(bad)
            return data
    with pytest.raises(ProviderError, match="索引约束"):
        enrich(make_transcript(), BadProvider())


def test_missing_ai_fields_rejected():
    class Missing(FakeProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            return {"summary": "too small"}
    with pytest.raises(ProviderError):
        enrich(make_transcript(), Missing())


def test_source_reference_validation_on_reload():
    note = enrich(make_transcript(), FakeProvider())
    value = note.model_dump()
    value["analysis"]["highlights"][0]["text"] = "这是模型编出来的一句金句"
    with pytest.raises(ValidationError, match="differs from original"):
        NoteDocument.model_validate(value)
    value = note.model_dump()
    value["analysis"]["chapters"][0]["start_ms"] = 987654
    with pytest.raises(ValidationError, match="not grounded"):
        NoteDocument.model_validate(value)


def test_note_rendered_as_offline_html_and_full_markdown():
    note = enrich(make_transcript(), FakeProvider())
    html = render_html(note, home_href="../../index.html")
    assert 'id="tab-summary"' in html and 'id="tab-chapters"' not in html
    assert 'id="tab-highlights"' in html and 'id="tab-keywords"' in html
    assert 'data-go-cue="0"' in html
    assert '一、认识内容' in html
    assert 'https://www.youtube.com/watch?v=1-Fo8McCD9w&amp;t=0s' in html
    assert '<script>alert("hacked")</script>' not in html
    assert '&lt;script&gt;' in html
    assert '<script src=' not in html and 'https://cdn.' not in html
    assert '<!--LR:' not in html
    markdown = render_markdown(note)
    assert '## 摘要' in markdown and '## 核心观点' in markdown
    assert '## 章节导读' in markdown and '## 金句' in markdown
    assert '## 完整字幕' in markdown
    assert note.transcript.segments[-1].text in markdown


def test_site_prefers_note_over_duplicate_transcript(tmp_path):
    src = tmp_path/'inputs'
    src.mkdir()
    doc = make_transcript()
    note = enrich(doc, FakeProvider())
    (src/'transcript.json').write_text(doc.model_dump_json(), encoding='utf-8')
    (src/'note.json').write_text(note.model_dump_json(), encoding='utf-8')
    (src/'note.article.json').write_text('{"title": "编辑显示标题"}', encoding='utf-8')
    target = tmp_path/'site'
    assert build_site(src, target) == 1
    page = next((target/'pages').glob('*/index.html'))
    assert '第一节：认识内容' in page.read_text(encoding='utf-8')
    home = (target/'index.html').read_text(encoding='utf-8')
    assert '忠实总结这一段' in home and '编辑显示标题' in home
    assert isinstance(load_document(src/'note.json'), NoteDocument)


def test_site_handles_distinct_docs_in_same_folder(tmp_path):
    src=tmp_path/'inputs'; src.mkdir()
    first = make_transcript()
    second = make_transcript(text="不同文本")
    (src/'a.json').write_text(first.model_dump_json(), encoding='utf-8')
    (src/'b.json').write_text(second.model_dump_json(), encoding='utf-8')
    assert build_site(src, tmp_path/'site') == 2


def test_generation_render_and_site_without_real_model(tmp_path):
    source = tmp_path/'transcript.json'
    doc = make_transcript()
    source.write_text(doc.model_dump_json(), encoding='utf-8')
    target = tmp_path/'run'
    article = generate_article(str(source), FakeProvider(), library=tmp_path/'library', output=target)
    note = target/'note.json'
    saved_note = load_document(note)
    assert isinstance(saved_note, NoteDocument)
    assert '忠实总结' in saved_note.analysis.summary
    assert source.read_text(encoding='utf-8') == doc.model_dump_json()
    assert '一段全文导语。' in article.read_text(encoding='utf-8')
    assert build_site(target, tmp_path/'site') == 1
    assert (target/'transcript.json').is_file()
    assert (target/'note.json').is_file()
    assert load_extras(target/'note.json').quotes is not None
    assert 'id="tab-summary"' in (target/'index.html').read_text(encoding='utf-8')


def test_run_keeps_transcript_for_retry_and_loads_video_sidecars(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    doc = make_transcript(text="测试原文")
    extracted = []

    def extract(url, **kwargs):
        extracted.append(url)
        return doc

    monkeypatch.setattr('linkread_desktop.generation.extract_document', extract)
    provider = FakeProvider()
    original_generate = provider.generate

    def fail_model(*args, **kwargs):
        raise ProviderError('model unavailable')

    url = 'https://youtu.be/1-Fo8McCD9w'
    monkeypatch.setattr(provider, 'generate', fail_model)
    with pytest.raises(ProviderError, match='model unavailable'):
        generate_article(url, provider, library=tmp_path/'library')
    folder = tmp_path / 'library' / '重要内容 [youtube-1-Fo8McCD9w]'
    source = folder / 'transcript.json'
    before = source.read_bytes()
    assert not (folder / 'note.json').exists()

    outline = '# 主题\n## 第一节\n### 细节一\n## 第二节\n### 细节二\n'
    (folder / 'mindmap.md').write_text(outline, encoding='utf-8')
    doc.segments[0].text = '平台更新的字幕'
    monkeypatch.setattr(provider, 'generate', original_generate)
    generate_article(url, provider, library=tmp_path/'library')
    assert source.read_bytes() == before
    note = load_document(folder / 'note.json')
    assert note.transcript == TranscriptDocument.model_validate_json(before)
    article = (folder / 'article.html').read_text(encoding='utf-8')
    assert 'id="tab-mindmap"' in article
    assert '平台更新的字幕' not in article
    assert extracted == ['https://youtu.be/1-Fo8McCD9w']
    # Publication and polishing can run concurrently; completion order is not deterministic.
    assert ['reading', 'quotes', 'catalog'] in provider.calls
    assert (folder / 'mindmap.md').read_text(encoding='utf-8') == outline


def test_run_publishes_all_files_and_refreshes_bookshelf_without_repeating_ai(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    doc = make_transcript(num=30, text="这是用于学习的原始字幕")
    provider = FakeProvider()
    monkeypatch.setattr('linkread_desktop.generation.extract_document', lambda url, **kwargs: doc)
    older = tmp_path / 'library' / '已有文章'
    older.mkdir(parents=True)
    (older / 'transcript.json').write_text(doc.model_copy(update={
        'source': SourceInfo(platform='local', title='书架已有文章'),
    }).model_dump_json(), encoding='utf-8')

    url = 'https://www.youtube.com/watch?v=1-Fo8McCD9w'
    generate_article(url, provider, library=tmp_path/'library', site=tmp_path/'site')
    folder = tmp_path / 'library' / '重要内容 [youtube-1-Fo8McCD9w]'
    assert {path.name for path in folder.iterdir() if not path.name.startswith('.')} == {
        'transcript.json', 'note.json', 'reading.json', 'mindmap.md', 'quotes.json', 'catalog.json', 'article.html',
    }
    article = (folder / 'article.html').read_text(encoding='utf-8')
    shelf = tmp_path / 'site' / 'index.html'
    home = shelf.read_text(encoding='utf-8')
    assert 'id="article-count">2</span>' in home and '书架已有文章' in home and doc.source.title in home
    assert 'href="../../site/index.html"' in article
    shelf_articles = [path.read_text(encoding='utf-8') for path in shelf.parent.glob('pages/*/index.html')]
    for marker in ('一段全文导语。', '一、主题开场', '二、后半段主题', 'id="tab-highlights"', 'id="tab-mindmap"'):
        assert marker in article and any(marker in item for item in shelf_articles)
    assert 'id="tab-anki"' not in article
    extras = load_extras(folder / 'note.json')
    note = load_document(folder / 'note.json')
    assert [s.start_segment_index for s in extras.reading.sections] == [
        0, note.analysis.reading_paragraphs[-1].start_segment_index,
    ]
    card = extras.quotes.cards[0]
    assert card.front == ' '.join(item.text for item in doc.segments[:2]) + '！'
    assert card.back == ' '.join(item.text for item in doc.segments[:4])
    assert '00:00–00:01' in article
    body = article.split('<article class="article-body">', 1)[1].split('</article>', 1)[0]
    assert '<strong>用于学习</strong>' in body and '<strong class="red">原始字幕</strong>' in body
    assert provider.calls.count(['reading', 'mindmap', 'quotes', 'catalog']) == 1
    ai_count = len(provider.calls)
    saved = {path.name: path.stat().st_mtime_ns for path in folder.iterdir() if path.name != 'article.html' and not path.name.startswith('.')}

    def fail_extract(*args, **kwargs):
        pytest.fail('已归档的同一视频不应发起网络请求')

    monkeypatch.setattr('linkread_desktop.generation.extract_document', fail_extract)
    shelf.unlink()
    generate_article('https://youtu.be/1-Fo8McCD9w?si=abc', provider,
                     library=tmp_path/'library', site=tmp_path/'site')
    assert len(provider.calls) == ai_count
    assert {name: (folder / name).stat().st_mtime_ns for name in saved} == saved
    assert shelf.read_text(encoding='utf-8') == home


def test_invalid_article_data_preserves_note_and_retry_only_generates_extras(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class BadPublication(FakeProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            result = super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            if 'quotes' in result:
                result['quotes'][0]['index'] = 999
            return result

    raw_path = tmp_path / 'archived.json'
    raw_path.write_text(make_transcript().model_dump_json(), encoding='utf-8')
    bad = BadPublication()
    with pytest.raises(ProviderError, match='金句索引重复或超出'):
        generate_article(str(raw_path), bad, library=tmp_path/'library', site=tmp_path/'site')
    folder = tmp_path / 'library' / '重要内容 [youtube-1-Fo8McCD9w]'
    assert {path.name for path in folder.iterdir() if not path.name.startswith('.')} == {'transcript.json', 'note.json'}
    assert not (tmp_path / 'site' / 'index.html').exists()
    saved_note = (folder / 'note.json').read_bytes()
    working = FakeProvider()
    generate_article(str(raw_path), working, library=tmp_path/'library', site=tmp_path/'site')
    assert (folder / 'note.json').read_bytes() == saved_note
    assert working.calls == [['reading', 'mindmap', 'quotes', 'catalog']]
    assert (tmp_path / 'site' / 'index.html').is_file()


def test_old_run_only_backfills_catalog_once_and_keeps_all_ai_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider()
    monkeypatch.setattr('linkread_desktop.generation.extract_document', lambda *args, **kwargs: make_transcript())
    url = 'https://youtu.be/1-Fo8McCD9w'
    generate_article(url, provider, library=tmp_path/'library', site=tmp_path/'site')
    folder = tmp_path / 'library' / '重要内容 [youtube-1-Fo8McCD9w]'
    (folder / 'catalog.json').unlink()  # Simulate an existing, fully generated older article.
    originals = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in folder.iterdir()
                 if path.name != 'article.html' and not path.name.startswith('.')}
    provider.calls.clear()
    generate_article(url, provider, library=tmp_path/'library', site=tmp_path/'site')
    assert provider.calls == [['catalog']]
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in originals} == originals
    catalog = (folder / 'catalog.json').read_bytes()
    metadata = json.loads(catalog)
    assert metadata['category'] == '技术与 AI' and metadata['tags'] == ['字幕阅读', '知识整理']
    assert metadata['date'] and metadata['date_source'] == 'archived'
    home = (tmp_path / 'site/index.html').read_text()
    assert '技术与 AI' in home and metadata['date'] in home
    assert '技术与 AI' in (folder / 'article.html').read_text()
    generate_article(url, provider, library=tmp_path/'library', site=tmp_path/'site')
    assert provider.calls == [['catalog']]
    assert (folder / 'catalog.json').read_bytes() == catalog


def test_manual_catalog_avoids_extra_ai_and_is_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / 'article'
    folder.mkdir()
    source = tmp_path / 'source.json'
    source.write_text(make_transcript().model_dump_json())
    manual = folder / 'article.json'
    original = '{"category":"用户分类","tags":[],"date":"2025-08-02","custom_field":"保留"}'
    manual.write_text(original)
    provider = FakeProvider()
    generate_article(str(source), provider, library=tmp_path/'library', output=folder)
    assert ['reading', 'mindmap', 'quotes'] in provider.calls
    assert not any('catalog' in fields for fields in provider.calls)
    assert manual.read_text() == original
    assert '用户分类' in (folder / 'index.html').read_text()
    saved = load_extras(folder / 'note.json').catalog
    assert saved.tags == [] and saved.date == '2025-08-02'


@pytest.mark.parametrize('bad_catalog', [
    {'category': '技术', 'tags': []},
    {'category': '技术', 'tags': ['AI', 'ai']},
    {'category': '技术', 'tags': [' ']},
    {'category': '技术', 'tags': ['AI'], 'date': '2099-01-01'},
])
def test_ai_catalog_rejects_empty_tags_duplicates_and_invented_dates(bad_catalog):
    class BadCatalog(FakeProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            result = super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            if 'catalog' in result:
                result['catalog'] = bad_catalog
            return result

    note = enrich(make_transcript(), FakeProvider())
    with pytest.raises(ProviderError):
        complete_extras(note, ArticleExtras(), BadCatalog(), timeout_seconds=30)


def test_generate_quote_without_existing_highlights_for_one_cue():
    doc = make_transcript(num=1, text='一句有时间戳的字幕')
    doc.segments[0].start_ms = 5500
    doc.segments[0].end_ms = None
    provider = FakeProvider()
    note = enrich(doc, provider)
    note.analysis.highlights.clear()
    extras = complete_extras(note, ArticleExtras(), provider, timeout_seconds=30)
    card = extras.quotes.cards[0]
    assert lexical_content(card.front) == lexical_content(card.back) == lexical_content(doc.segments[0].text)
    assert '<span class="small-time quote-time">00:05</span>' in render_html(note, extras=extras)


def test_quote_punctuation_falls_back_on_word_changes():
    class RewrittenQuote(FakeProvider):
        def generate(self, prompt, schema, *, timeout_seconds):
            result = super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            if 'quotes' in result:
                result['quotes'][0].update(front='编造的原句。', red=['编造'])
            return result

    raw = make_transcript(text='原始字幕')
    note = enrich(raw, FakeProvider())
    events = []
    extras = complete_extras(note, ArticleExtras(), RewrittenQuote(), timeout_seconds=30, progress=events.append)
    card = extras.quotes.cards[0]
    assert card.front == ' '.join(segment.text for segment in raw.segments[:2])
    assert not card.red
    assert any(event.stage == 'quote_warning' for event in events)


def test_publication_accepts_english_lengths_and_retries_once_on_validation_error(tmp_path):
    class FlakyPublication(FakeProvider):
        def __init__(self):
            super().__init__()
            self.pub_prompts = []

        def generate(self, prompt, schema, *, timeout_seconds):
            result = super().generate(prompt, schema, timeout_seconds=timeout_seconds)
            if 'reading' in result:
                self.pub_prompts.append(prompt)
                if len(self.pub_prompts) == 1:
                    # First response forgets index 0 on the opening section.
                    result['reading']['sections'][0]['index'] = 99
                    return result
                result['reading']['lead'] = (
                    "Lauren Tan shares how her team at SpaceXSI turned AI-generated code from "
                    "unpredictable slop into 3,000 production pull requests per month by investing "
                    "in automated verification loops, review proofs, and deterministic agent sandboxes."
                )
                result['reading']['sections'][0]['label'] = "Shipping 3,000 PRs with Verification Loops"
                result['reading']['sections'][0]['summary'] = (
                    "Explains why code volume alone is not the bottleneck once automated review proofs, "
                    "deterministic test suites, and isolated agent sandboxes replace slow manual line-by-line inspection."
                )
                result['quotes'][0].pop('bold', None)
                result['quotes'][0].pop('red', None)
            return result

    note = enrich(make_transcript(), FakeProvider())
    provider = FlakyPublication()
    extras = complete_extras(note, ArticleExtras(), provider, timeout_seconds=30, cache_dir=tmp_path)
    assert len(provider.pub_prompts) == 2
    assert '【格式修正重试】' in provider.pub_prompts[1]
    assert '目录索引必须从选项 0 开始' in provider.pub_prompts[1]
    assert len(extras.reading.lead) > 180
    assert len(extras.reading.sections[0].summary) > 160
    assert len(extras.reading.sections[0].label) > 30
    assert len(list(tmp_path.glob('*.json'))) == 1  # Only the valid retry was cached.


def test_run_recovers_after_bookshelf_write_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    doc = make_transcript(num=1)
    provider = FakeProvider()
    monkeypatch.setattr('linkread_desktop.generation.extract_document', lambda url, **kwargs: doc)
    shelf = tmp_path / 'site'
    shelf.mkdir()
    (shelf / 'index.html').write_text('此前的书架', encoding='utf-8')
    blocker = shelf / 'pages'
    blocker.touch()

    url = 'https://youtu.be/1-Fo8McCD9w'
    with pytest.raises(OSError):
        generate_article(url, provider, library=tmp_path/'library', site=shelf)
    assert (shelf / 'index.html').read_text(encoding='utf-8') == '此前的书架'
    assert next((tmp_path / 'library').glob('*/article.html')).is_file()
    ai_count = len(provider.calls)
    blocker.unlink()
    generate_article(url, provider, library=tmp_path/'library', site=shelf)
    assert len(provider.calls) == ai_count
    assert 'id="article-count">1</span>' in (shelf / 'index.html').read_text(encoding='utf-8')


def test_failed_generation_preserves_source_and_avoids_partial_note(tmp_path):
    raw=tmp_path/'transcript.json'
    raw.write_text(make_transcript().model_dump_json(),encoding='utf-8')
    original = raw.read_bytes()
    class FailedProvider:
        name = 'codex'
        def generate(self, *args, **kwargs):
            raise ProviderError('missing AI tool')
    library = tmp_path/'library'
    with pytest.raises(ProviderError, match='missing AI tool'):
        generate_article(str(raw), FailedProvider(), library=library)
    assert not list(library.glob('*/note.json'))
    assert raw.read_bytes() == original


def test_codex_cli_schema_output_contract(monkeypatch):
    monkeypatch.setattr('linkread_desktop.ai.provider.shutil.which', lambda name: '/mock/'+name)
    def fake_run(cmd, *, input, text, encoding, errors, cwd, env, stdout, stderr, timeout, check):
        assert cmd[:2] == ['codex', 'exec']
        assert ['--sandbox','read-only'] == cmd[2:4]
        assert '--output-schema' in cmd and '--output-last-message' in cmd
        assert cmd[-1]=='-'
        assert 'only test prompt' == input
        assert 'LINKREAD_API_TOKEN' not in env and 'LINKREAD_ASR_TOKEN' not in env
        Path(cmd[cmd.index('--output-last-message')+1]).write_text('{"summary":"okay"}',encoding='utf-8')
        return subprocess.CompletedProcess(cmd,0,'','')
    monkeypatch.setattr('linkread_desktop.ai.provider.subprocess.run',fake_run)
    assert CodexProvider(model='custom').generate('only test prompt',CHUNK_SCHEMA,timeout_seconds=60)=={'summary':'okay'}


def test_claude_bare_uses_schema_and_disabled_tools(monkeypatch):
    monkeypatch.setattr('linkread_desktop.ai.provider.shutil.which', lambda name: '/mock/'+name)
    def fake_run(cmd, *, input, text, encoding, errors, cwd, env, stdout, stderr, timeout, check):
        assert cmd[:3]==['claude','--bare','-p']
        assert '--json-schema' in cmd and '--no-session-persistence' in cmd
        assert ['--tools',''] == cmd[cmd.index('--tools'):cmd.index('--tools')+2]
        assert 'LINKREAD_API_TOKEN' not in env and 'LINKREAD_ASR_TOKEN' not in env
        return subprocess.CompletedProcess(cmd,0,json.dumps({'structured_output':{'summary':'okay'}}),'')
    monkeypatch.setattr('linkread_desktop.ai.provider.subprocess.run',fake_run)
    assert ClaudeProvider().generate('prompt',CHUNK_SCHEMA,timeout_seconds=60)=={'summary':'okay'}


def test_cli_failure_and_timeout(monkeypatch):
    monkeypatch.setattr('linkread_desktop.ai.provider.shutil.which', lambda name: '/mock/'+name)
    def fake_timeout(cmd, *, input, text, encoding, errors, cwd, env, stdout, stderr, timeout, check):
        raise subprocess.TimeoutExpired(cmd,timeout)
    monkeypatch.setattr('linkread_desktop.ai.provider.subprocess.run',fake_timeout)
    with pytest.raises(ProviderError,match='超过 23 秒'):
        CodexProvider().generate('test',CHUNK_SCHEMA,timeout_seconds=23)

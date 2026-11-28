import base64
from hashlib import sha256
import json
import re

import pytest

from linkread_desktop import covers
from linkread_desktop.ai.models import Chapter, NoteAnalysis, NoteDocument, lexical_content
from linkread_desktop.desktop import _prepared_reader
from linkread_desktop.errors import ExtractionError, LinkReadError
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.formatting import group_paragraphs, source_link
from linkread_desktop.renderers.extras import load_extras
from linkread_desktop.renderers.html import render_html
from linkread_desktop.renderers.markdown import render_markdown
from linkread_desktop.renderers.site import build_site, load_document, render_home
from test_local_cover import JPEG_BYTES


def make_doc(*, title='示例视频', source='youtube', text='你好。', video_id='1-Fo8McCD9w'):
    return TranscriptDocument(
        source=SourceInfo(platform=source, title=title, video_id=video_id if source == 'youtube' else None,
                          url='https://www.youtube.com/watch?v=1-Fo8McCD9w' if source == 'youtube' else None,
                          duration_ms=300000, published_at='2026-11-12'),
        transcript_source='subtitle', language='zh-Hans',
        segments=[TranscriptSegment(start_ms=0, end_ms=1700, text=text),
                  TranscriptSegment(start_ms=320000, end_ms=321000, text='后面的字幕')],
    )


def test_html_self_contained_metadata_and_cues():
    doc = make_doc()
    rendered = render_html(doc)
    assert rendered.startswith('<!DOCTYPE html>')
    assert '示例视频' in rendered and '后面的字幕' in rendered
    assert 'id="tab-transcript"' in rendered
    assert 'id="tab-raw"' not in rendered
    assert 'id="tab-notes"' not in rendered
    assert 'id="tab-shownotes"' in rendered
    assert '<div class="shell">' in rendered and '<div class="tabs-scroll"' in rendered
    assert 'data-cue-start="1" data-cue-end="1"' in rendered
    assert 'href="https://www.youtube.com/watch?v=1-Fo8McCD9w"' in rendered
    assert 'href="../../index.html"' not in rendered
    assert '<script src=' not in rendered and 'https://cdn.' not in rendered
    assert '<!--LR:' not in rendered
    assert 'fonts.googleapis.com' not in rendered


def test_untrusted_content_escaped_and_urls_validated():
    evil = '<script>alert("x")</script><img src=x onerror=alert(1)>'
    doc = make_doc(title=evil, text=evil)
    rendered = render_html(doc)
    assert '&lt;script&gt;' in rendered
    assert '&lt;img src=x onerror=alert(1)&gt;' in rendered
    assert '<script>alert' not in rendered
    doc.source.url = 'javascript:alert(1)'
    doc.source.video_id = '<bad-id>'
    assert source_link(doc) is None
    assert 'href="javascript:' not in render_html(doc)


def test_group_preserves_all_input_without_false_headings():
    document = make_doc(text='This is plain text.')
    pieces = group_paragraphs(document.segments)
    assert len(pieces) == 2
    assert pieces[0].text == document.segments[0].text
    assert pieces[1].text == document.segments[1].text
    html = render_html(document)
    assert 'class="callout"' not in html
    body = html.split('<article class="article-body">', 1)[1].split('</article>', 1)[0]
    assert '<h2' not in body and '<h3' not in body
    assert all(f'data-tab="{name}"' not in html for name in ('summary', 'chapters', 'highlights', 'mindmap', 'anki'))


def test_saved_document_renders_html_and_markdown_offline(tmp_path):
    path = tmp_path / 'transcript.json'
    path.write_text(make_doc().model_dump_json(), encoding='utf-8')
    document = load_document(path)
    assert '后面的字幕' in render_html(document)
    markdown = render_markdown(document)
    assert '**[05:20]** 后面的字幕' in markdown
    assert 'https://www.youtube.com' in markdown


def test_site_home_multi_articles_with_distinct_links(tmp_path):
    source_dir = tmp_path / 'transcripts'
    source_dir.mkdir()
    first = source_dir / 'a' / 'transcript.json'
    second = source_dir / 'b' / 'transcript.json'
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_text(make_doc(title='中文文章 A').model_dump_json(), encoding='utf-8')
    second.write_text(make_doc(title='中文文章 B').model_dump_json(), encoding='utf-8')
    (first.parent / 'article.json').write_text(json.dumps({'category':'教育', 'tags':['亲子','学习'], 'date':'2026-11-12'}, ensure_ascii=False),encoding='utf-8')
    output = tmp_path / 'site'
    assert build_site(source_dir, output) == 2
    home = (output/'index.html').read_text(encoding='utf-8')
    assert '阅读书架' in home and '中文文章 A' in home and '中文文章 B' in home
    assert '<table aria-label="阅读文章表">' in home
    assert '亲子' in home
    assert '<script type="application/json" id="article-data">' in home
    assert home.count('class="record"') == 2
    article_files = sorted((output/'pages').glob('*/index.html'))
    assert len(article_files) == 2
    for article in article_files:
        content = article.read_text(encoding='utf-8')
        assert 'href="../../index.html"' in content
        assert not content.count('https://cdn.')
        meta = json.loads((article.parent/'article.json').read_text(encoding='utf-8'))
        assert 'title' in meta
        if meta['category']:
            assert f'<span>{meta["category"]}</span>' in content
        else:
            assert '<span></span>' not in content
        assert f'<meta name="description" content="{meta["description"]}">' in content
        assert (output / 'index.html').is_file()


def test_site_single_saved_file(tmp_path):
    transcript = tmp_path/'transcript.json'
    transcript.write_text(make_doc().model_dump_json(), encoding='utf-8')
    target = tmp_path/'output'
    assert build_site(transcript, target) == 1
    assert '<title>阅读书架 · Yano</title>' in (target/'index.html').read_text()


def test_invalid_input_leaves_no_broken_home(tmp_path):
    source_dir = tmp_path/'in'
    source_dir.mkdir()
    (source_dir/'okay.json').write_text(make_doc().model_dump_json(), encoding='utf-8')
    (source_dir/'bad.json').write_text('{"schema_version": "2.0"}', encoding='utf-8')
    target = tmp_path/'target'
    with pytest.raises(ExtractionError, match='不是有效的'):
        build_site(source_dir, target)
    assert not target.exists()
    with pytest.raises(ExtractionError, match='不是有效的'):
        load_document(source_dir/'bad.json')


def test_sidecar_metadata_validation(tmp_path):
    file = tmp_path / 'example.json'
    file.write_text(make_doc().model_dump_json(), encoding='utf-8')
    (tmp_path/'article.json').write_text('{"date":"2026-02-31"}', encoding='utf-8')
    with pytest.raises(ExtractionError, match='date'):
        build_site(file, tmp_path/'site')


def test_large_document_not_truncated():
    doc = make_doc()
    doc.segments = [TranscriptSegment(start_ms=i*1000, end_ms=i*1000+990, text=f'第{i}行。') for i in range(1500)]
    html = render_html(doc)
    paragraphs = re.findall(r'<p class="reading-paragraph"[^>]*>(.*?)</p>', html, re.S)
    assert lexical_content(''.join(paragraphs)) == lexical_content(''.join(cue.text for cue in doc.segments))
    assert 'id="panel-raw"' not in html


def test_stylesheets_match_html_library_source():
    # html-library bca81c0, pages/charlie-munger-etf-trap/index.html and scripts/build.py.
    pages = [
        (render_html(make_doc()), '61dcf57ae9e4f54cf94db1ee6a534c7e1c53734a67edee7915cd1bf82096a5f8'),
        (render_home([]), '190cf22b13f77cd31d872186e7bedca4f9ed92c587d6faba3def56eba67509ff'),
    ]
    for page, expected in pages:
        css = re.search(r'<style>(.*?)</style>', page, re.S)[1]
        assert sha256(css.encode('utf-8')).hexdigest() == expected


def test_metadata_cannot_inject_a_script_in_article_or_home(tmp_path):
    title = '</script><script>alert(1)</script><!--LR:ROWS--><!--LR:RAW_CUES-->'
    source = tmp_path / 'example.json'
    source.write_text(make_doc(title=title, text=title).model_dump_json(), encoding='utf-8')
    target = tmp_path / 'site'
    build_site(source, target)
    article = next((target / 'pages').glob('*/index.html')).read_text(encoding='utf-8')
    assert '<script>alert(1)</script>' not in article
    assert '&lt;!--LR:RAW_CUES--&gt;' in article
    home = (target / 'index.html').read_text(encoding='utf-8')
    assert '<script>alert(1)</script>' not in home
    data = re.search(r'<script type="application/json" id="article-data">(.*?)</script>', home, re.S)[1]
    assert json.loads(data)[0]['title'] == title


def test_render_and_bookshelf_include_both_sidecars(tmp_path):
    source = tmp_path / 'transcript.json'
    source.write_text(make_doc().model_dump_json(), encoding='utf-8')
    outline = '# 测试主题\n## 第一分支\n### 一段话\n## 第二分支\n### 另一段话\n'
    (tmp_path / 'mindmap.md').write_text(outline, encoding='utf-8')
    deck = {'cards': [{
        'title': '第一句', 'start_segment_index': 0, 'end_segment_index': 0,
        'front': '你好！', 'back': '你好，后面的字幕。',
        'red': ['你好'],
    }]}
    (tmp_path / 'quotes.json').write_text(json.dumps(deck), encoding='utf-8')
    (tmp_path / 'anki.json').write_text('旧导出文件', encoding='utf-8')
    (tmp_path / '.venv').mkdir()
    (tmp_path / '.venv' / 'direct_url.json').write_text('{}', encoding='utf-8')

    output = tmp_path / 'article.html'
    output.write_text('original article', encoding='utf-8')
    _prepared_reader(output)
    site = tmp_path / 'site'
    assert build_site(tmp_path, site) == 1

    for file in (output.with_name('.reader.html'), next((site / 'pages').glob('*/index.html'))):
        html = file.read_text(encoding='utf-8')
        assert '<script src=' not in html
        assert 'data-tab="mindmap"' in html and 'data-tab="highlights"' in html
        assert 'data-tab="anki"' not in html
        assert '<p class="anki-front"><strong class="red">你好</strong>！</p>' in html
        assert '00:00–00:01' in html and 'data-go-cue="0"' in html
        assert 'id="markmap-svg"' in html and '<script id="yano-car-cost-mindmap-init">' in html
        assert 'Copyright (C) 2011-2015 by Vitaly Puzrin' in html
        mindmap_data = re.search(r'id="yano-mindmap-data">(.*?)</script>', html, re.S)[1]
        assert json.loads(mindmap_data) == outline
        assert 'data-copy-side="front"' in html and 'data-copy-side="back"' in html
    assert (tmp_path / 'anki.json').read_text(encoding='utf-8') == '旧导出文件'
    assert output.read_text(encoding='utf-8') == 'original article'


def test_invalid_sidecar_does_not_replace_article_or_write_site(tmp_path):
    source = tmp_path / 'transcript.json'
    source.write_text(make_doc().model_dump_json(), encoding='utf-8')
    output = tmp_path / 'article.html'
    output.write_text('previous article', encoding='utf-8')
    (tmp_path / 'quotes.json').write_text(json.dumps({'cards': [{
        'title': '第一句', 'start_segment_index': 0, 'end_segment_index': 0,
        'front': '原文里没有这一句。', 'back': '你好。 后面的字幕',
    }]}), encoding='utf-8')

    with pytest.raises(LinkReadError, match='金句或上下文与原始字幕不一致'):
        _prepared_reader(output)
    assert output.read_text(encoding='utf-8') == 'previous article'
    site = tmp_path / 'site'
    with pytest.raises(LinkReadError, match='金句或上下文与原始字幕不一致'):
        build_site(source, site)
    assert not site.exists()


def test_named_sidecars_take_precedence_and_keep_unsafe_text_inert(tmp_path):
    source = tmp_path / 'video.json'
    attack = '</script><iframe src="https://example.invalid"></iframe>'
    source.write_text(make_doc(text=attack).model_dump_json(), encoding='utf-8')
    deck = {'cards': [{
        'title': attack, 'start_segment_index': 0, 'end_segment_index': 0,
        'front': attack, 'back': f'{attack} 后面的字幕',
    }]}
    (tmp_path / 'video.quotes.json').write_text(json.dumps(deck), encoding='utf-8')
    (tmp_path / 'quotes.json').write_text('bad fallback file', encoding='utf-8')
    (tmp_path / 'video.mindmap.md').write_text(f'# 根\n## 一\n### {attack}\n## 二\n### 文本\n', encoding='utf-8')

    html = render_html(load_document(source), extras=load_extras(source))
    assert attack not in html
    assert '&lt;iframe' in html
    assert '<p class="anki-front">&lt;/script&gt;&lt;iframe' in html
    assert build_site(tmp_path, tmp_path / 'site') == 1


def test_html_caps_legacy_summary_and_numbered_outline():
    transcript = make_doc()
    transcript.segments = [TranscriptSegment(start_ms=i * 1000, text=f'第{i}片段。') for i in range(24)]
    note = NoteDocument(transcript=transcript, provider='codex', analysis=NoteAnalysis(
        summary='文章导语。\n\n分块摘要细节。', takeaways=['额外的分块要点'],
        chapters=[Chapter(start_segment_index=i, start_ms=i * 1000, title=f'{i+1}. 主题{i}', summary=f'要点{i}。')
                  for i in range(24)],
    ))
    html = render_html(note)
    assert html.count('<p class="summary-lead">') == 1
    assert html.count('<a class="summary-line"') == 6
    assert '分块摘要细节' not in html and '额外的分块要点' not in html
    assert 'id="panel-chapters"' not in html
    assert '<h2>一、主题0</h2>' in html and '<h2>六、主题23</h2>' in html
    assert 'data-go-section="chapter-23">六、主题23</button>' in html
    paragraphs = re.findall(r'<p class="reading-paragraph"[^>]*>(.*?)</p>', html, re.S)
    assert lexical_content(''.join(paragraphs)) == lexical_content(''.join(cue.text for cue in transcript.segments))


def test_editorial_summary_and_outline_are_shared_by_render_and_site(tmp_path):
    source = tmp_path / 'transcript.json'
    source.write_text(make_doc().model_dump_json(), encoding='utf-8')
    sidecar = tmp_path / 'transcript.reading.json'
    overview = {
        'lead': '人工整理的导语。', 'source_note': '来源说明。',
        'sections': [
            {'start_segment_index': 0, 'title': '开场话题', 'label': '开场', 'summary': '从问候进入主题。'},
            {'start_segment_index': 1, 'title': '后续话题', 'summary': '补充后续内容。'},
        ],
    }
    sidecar.write_text(json.dumps(overview), encoding='utf-8')
    article = tmp_path / 'article.html'
    article.write_text('original article', encoding='utf-8')
    _prepared_reader(article)
    site = tmp_path / 'site'
    assert build_site(tmp_path, site) == 1
    for path in (article.with_name('.reader.html'), next((site / 'pages').glob('*/index.html'))):
        html = path.read_text(encoding='utf-8')
        assert '<p class="summary-lead">人工整理的导语。</p>' in html
        assert ('<a class="summary-line" href="#chapter-0" data-go-section="chapter-0" '
                'title="Read section: 开场话题"><strong>开场</strong><p>从问候进入主题。</p></a>') in html
        assert '<p class="source-caveat">来源说明。</p>' in html
        assert '<h2>二、后续话题</h2>' in html
    assert overview['lead'] in (site / 'index.html').read_text(encoding='utf-8')

    overview['sections'][-1]['start_segment_index'] = 10
    sidecar.write_text(json.dumps(overview), encoding='utf-8')
    with pytest.raises(LinkReadError, match='字幕索引超出'):
        _prepared_reader(article)


def test_site_pages_carry_the_cover_while_the_bookshelf_stays_text(tmp_path):
    source_dir = tmp_path / 'transcripts'
    (source_dir / 'a').mkdir(parents=True)
    (source_dir / 'a' / 'transcript.json').write_text(make_doc().model_dump_json(), encoding='utf-8')
    assert build_site(source_dir, tmp_path / 'site') == 1
    page = next((tmp_path / 'site' / 'pages').glob('*/index.html')).read_text(encoding='utf-8')
    assert 'class="article-cover" src="https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"' in page
    assert '<img' not in (tmp_path / 'site' / 'index.html').read_text(encoding='utf-8')

    covers.save_cover_bytes(source_dir / 'a', '.jpg', JPEG_BYTES)
    assert build_site(source_dir, tmp_path / 'site2') == 1
    page = next((tmp_path / 'site2' / 'pages').glob('*/index.html')).read_text(encoding='utf-8')
    inline = 'class="article-cover" src="data:image/jpeg;base64,' + base64.b64encode(JPEG_BYTES).decode('ascii') + '"'
    assert inline in page
    assert '<img' not in (tmp_path / 'site2' / 'index.html').read_text(encoding='utf-8')


def test_site_page_of_linked_subtitles_shows_the_linked_cover(tmp_path):
    source_dir = tmp_path / 'subtitles'
    source_dir.mkdir()
    raw = TranscriptDocument(source=SourceInfo(platform='local', title='导入.srt'), transcript_source='local',
                             segments=[TranscriptSegment(start_ms=0, end_ms=1000, text='导入的原文')])
    (source_dir / 'transcript.json').write_text(raw.model_dump_json(), encoding='utf-8')
    (source_dir / 'transcript.reading.json').write_text('{"lead":"导语","sections":[]}', encoding='utf-8')
    assert build_site(source_dir, tmp_path / 'site') == 1
    assert 'article-cover' not in next((tmp_path / 'site' / 'pages').glob('*/index.html')).read_text(encoding='utf-8').split('<body>')[1].split('<script')[0]

    video = 'https://www.bilibili.com/video/BV1BhKf6mEYR?p=2'
    artwork = 'https://i0.hdslb.com/bfs/archive/fixture.jpg'
    details = {'version': 1, 'title': None, 'category': None,
               'linkedSource': {'url': video, 'title': '原视频', 'thumbnailUrl': artwork}}
    (source_dir / '.article-details.json').write_text(json.dumps(details), encoding='utf-8')
    assert build_site(source_dir, tmp_path / 'site2') == 1
    page = next((tmp_path / 'site2' / 'pages').glob('*/index.html')).read_text(encoding='utf-8')
    assert f'class="article-cover" src="{artwork}"' in page

    # A linked podcast episode keeps its square artwork instead of being cropped to 16:9.
    episode_art = 'https://is1-ssl.mzstatic.com/image/thumb/podcast/600x600bb.jpg'
    details['linkedSource'] = {'url': 'https://podcasts.apple.com/us/podcast/id1234567890?i=1000000000001',
                               'title': '单集标题', 'thumbnailUrl': episode_art, 'platform': 'apple_podcasts'}
    (source_dir / '.article-details.json').write_text(json.dumps(details), encoding='utf-8')
    assert build_site(source_dir, tmp_path / 'site3') == 1
    page = next((tmp_path / 'site3' / 'pages').glob('*/index.html')).read_text(encoding='utf-8')
    assert f'class="article-cover is-square" src="{episode_art}"' in page

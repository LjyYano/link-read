import json
from pathlib import Path
import re

import pytest
from pydantic import ValidationError

from linkread_desktop.ai.models import NoteAnalysis, NoteDocument
from linkread_desktop.errors import ExtractionError
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers import catalog as catalog_module
from linkread_desktop.renderers.catalog import CatalogMetadata, load_catalog, load_user_metadata, save_catalog, source_date
from linkread_desktop.renderers.site import article_metadata, build_site


def document(title="AI 与程序员", published_at=None):
    return TranscriptDocument(
        source=SourceInfo(platform="local", title=title, published_at=published_at),
        transcript_source="local", segments=[TranscriptSegment(start_ms=0, text="原始字幕完整保留。")],
    )


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("value", ["2026-02-30", "20261115", "2026-1-2", "2026-11-15T00:00:00Z"])
def test_catalog_rejects_non_iso_or_impossible_calendar_dates(value):
    with pytest.raises(ValidationError, match="date"):
        CatalogMetadata(category="技术", tags=[], date=value)


def test_named_user_fields_override_generated_and_generic_metadata(tmp_path):
    source = tmp_path / "note.json"
    write_json(tmp_path / "catalog.json", {
        "category": "自动分类", "tags": ["自动标签"], "date": "2020-01-01", "date_source": "archived",
    })
    write_json(tmp_path / "article.json", {
        "category": "人工分类", "tags": ["目录标签"], "date": "2021-02-03", "title": "目录标题",
        "editor_custom": {"preserve": True},
    })
    write_json(tmp_path / "note.article.json", {"title": "单篇标题", "tags": []})
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    merged = load_user_metadata(source)
    assert merged["title"] == "单篇标题" and merged["category"] == "人工分类"
    assert merged["editor_custom"] == {"preserve": True}
    actual = load_catalog(source)
    assert actual == CatalogMetadata(category="人工分类", tags=[], date="2021-02-03", date_source="custom")
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_named_catalog_has_priority_and_manual_tag_lists_are_not_truncated(tmp_path):
    source = tmp_path / "note.json"
    write_json(tmp_path / "catalog.json", {"category": "目录分类", "tags": ["目录标签"]})
    tags = [f"标签{i}" for i in range(9)]
    write_json(tmp_path / "note.catalog.json", {"category": "单篇分类", "tags": tags})
    assert load_catalog(source) == CatalogMetadata(category="单篇分类", tags=tags)
    save_catalog(source, CatalogMetadata(category="不能替换", tags=[]), document())
    assert load_catalog(source).category == "单篇分类"
    assert load_catalog(source).tags == tags


@pytest.mark.parametrize("override", [{}, {"category": "技术"}, {"tags": ["AI"]}])
def test_incomplete_user_catalog_remains_missing(tmp_path, override):
    write_json(tmp_path / "article.json", override)
    assert load_catalog(tmp_path / "note.json") is None


def test_explicit_empty_tags_are_a_complete_manual_catalog(tmp_path):
    write_json(tmp_path / "article.json", {"category": "技术", "tags": []})
    assert load_catalog(tmp_path / "note.json") == CatalogMetadata(category="技术", tags=[])


def test_blank_category_override_does_not_hide_generated_category(tmp_path):
    write_json(tmp_path / "catalog.json", {"category": "技术", "tags": ["AI"]})
    write_json(tmp_path / "article.json", {"category": "   "})
    assert article_metadata(document(), tmp_path / "note.json")["category"] == "技术"


def test_invalid_user_date_is_rejected_even_without_complete_topics(tmp_path):
    write_json(tmp_path / "article.json", {"date": "2026-02-31"})
    with pytest.raises(ExtractionError, match="date"):
        article_metadata(document(), tmp_path / "note.json")


def test_save_uses_program_date_and_preserves_source_and_user_files(tmp_path):
    raw = document(published_at="2023-06-12T23:00:00Z")
    source = tmp_path / "transcript.json"
    source.write_text(raw.model_dump_json(), encoding="utf-8")
    write_json(tmp_path / "article.json", {"date": "2024-04-04", "title": "人工标题"})
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    # The model-facing value cannot invent a date; the saved date comes from the source.
    save_catalog(source, CatalogMetadata(category="技术与 AI", tags=["软件开发"], date="1999-01-01"), raw)
    saved = json.loads((tmp_path / "catalog.json").read_text())
    assert saved["date"] == "2023-06-12" and saved["date_source"] == "published"
    assert all(p.read_bytes() == content for p, content in before.items())
    effective = article_metadata(raw, source)
    assert effective["date"] == "2024-04-04" and effective["date_source"] == "custom"


def test_archive_date_comes_from_matching_raw_transcript_and_is_persisted(tmp_path, monkeypatch):
    raw = document()
    note = NoteDocument(transcript=raw, provider="codex", analysis=NoteAnalysis(summary="内容概述"))
    original_path, note_path = tmp_path / "transcript.json", tmp_path / "note.json"
    original_path.write_text(raw.model_dump_json(), encoding="utf-8")
    note_path.write_text(note.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(catalog_module, "_file_date", lambda path: "2020-01-01" if path == original_path else "2025-01-01")
    assert source_date(note, note_path) == ("2020-01-01", "archived")
    save_catalog(note_path, CatalogMetadata(category="技术", tags=["AI"]), note)
    saved = (tmp_path / "catalog.json").read_bytes()
    monkeypatch.setattr(catalog_module, "_file_date", lambda path: "2026-01-01")
    save_catalog(note_path, CatalogMetadata(category="新分类", tags=[]), note)
    assert (tmp_path / "catalog.json").read_bytes() == saved
    actual = article_metadata(note, note_path)
    assert actual["date"] == "2020-01-01" and actual["date_source"] == "archived"


def test_unrelated_transcript_cannot_supply_an_archive_date(tmp_path, monkeypatch):
    note = NoteDocument(transcript=document(), provider="codex", analysis=NoteAnalysis(summary="内容概述"))
    (tmp_path / "transcript.json").write_text(document(title="另一视频").model_dump_json(), encoding="utf-8")
    path = tmp_path / "note.json"
    path.write_text(note.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(catalog_module, "_file_date", lambda candidate: "2020-01-01" if candidate.name == "transcript.json" else "2025-01-01")
    assert source_date(note, path) == ("2025-01-01", "archived")


def test_final_book_dates_are_shared_by_sorting_output_and_display_titles(tmp_path, monkeypatch):
    root, out = tmp_path / "library", tmp_path / "site"
    raw = document()
    note = NoteDocument(transcript=raw, provider="codex", analysis=NoteAnalysis(summary="原概述"))
    a = root / "a"
    write_json(a / "transcript.json", raw.model_dump())
    write_json(a / "note.json", note.model_dump())
    write_json(a / "article.json", {"title": "编辑后的显示标题"})
    write_json(a / "reading.json", {"lead": "短篇导语", "sections": []})
    write_json(root / "b" / "transcript.json", document(title="较新文章", published_at="2025-01-01").model_dump())
    monkeypatch.setattr(catalog_module, "_file_date", lambda path: "2024-01-01" if path.name == "transcript.json" else "2026-01-01")
    original = (a / "transcript.json").read_bytes()
    assert build_site(root, out) == 2
    html = (out / "index.html").read_text()
    records = json.loads(re.search(r'id="article-data">(.*?)</script>', html, re.S)[1])
    assert [(item["title"], item["date"]) for item in records] == [
        ("较新文章", "2025-01-01"), ("编辑后的显示标题", "2024-01-01"),
    ]
    assert 'title="发布日期">2025-01-01' in html
    assert 'title="归档日期">2024-01-01' in html
    assert records[1]["description"] == "短篇导语"
    exported = json.loads((out / records[1]["url"]).with_name("article.json").read_text())
    assert exported["date"] == records[1]["date"]
    assert (a / "transcript.json").read_bytes() == original


def test_generated_topics_are_escaped_and_sidecar_is_not_an_article(tmp_path):
    source = tmp_path / "library" / "transcript.json"
    write_json(source, document().model_dump())
    attack = '</script><script>alert("injected")</script>'
    save_catalog(source, CatalogMetadata(category=attack, tags=[attack]), document())
    out = tmp_path / "site"
    assert build_site(source.parent, out) == 1
    html = (out / "index.html").read_text()
    assert attack not in html and "&lt;script&gt;" in html
    records = json.loads(re.search(r'id="article-data">(.*?)</script>', html, re.S)[1])
    assert records[0]["category"] == attack and records[0]["tags"] == [attack]

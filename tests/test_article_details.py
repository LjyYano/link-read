import json
import os
from pathlib import Path

import pytest

from linkread_desktop import article_details, desktop
from linkread_desktop.ai.models import NoteAnalysis, NoteDocument
from linkread_desktop.article_details import DETAILS_FILENAME
from linkread_desktop.article_state import patch_reading_state
from linkread_desktop.errors import LinkReadError
from test_desktop import invoke
from test_desktop_duplicate import make_archive


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_edit_is_consistent_in_library_reader_and_markdown_without_changing_source(tmp_path):
    folder = make_archive(tmp_path)
    path = folder / "article.html"
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    old_reader = desktop._prepared_reader(path)
    old_record = desktop._library_records(tmp_path)["articles"][0]
    updated = desktop._update_article_details(tmp_path, folder.name, {
        "title": "用户的 <标题>", "category": "学习与思考",
    })
    assert (updated["title"], updated["category"]) == ("用户的 <标题>", "学习与思考")
    assert (updated["id"], updated["readingKey"]) == (old_record["id"], old_record["readingKey"])
    assert desktop._library_records(tmp_path)["articles"] == [updated]
    new_reader = desktop._prepared_reader(path)
    assert new_reader["revision"] != old_reader["revision"]
    assert new_reader["readingKey"] == old_reader["readingKey"]
    assert "用户的 &lt;标题&gt;" in (folder / ".reader.html").read_text()
    assert "学习与思考" in (folder / ".reader.html").read_text()
    md = desktop._article_markdown(tmp_path, folder.name, content="readable", include_timestamped=False)
    assert md.startswith("# 用户的 &lt;标题&gt;\n") and "**分类**：学习与思考" in md
    assert all((folder / name).read_bytes() == data for name, data in before.items())


def test_reset_skips_legacy_manual_values_and_preserves_unknown_fields(tmp_path):
    folder = make_archive(tmp_path, title="源标题")
    write_json(folder / "catalog.json", {"category": "自动分类", "tags": ["标签"]})
    write_json(folder / "article.json", {"title": "目录标题", "category": "目录分类", "custom": {"a": 1}})
    write_json(folder / "transcript.article.json", {"title": "命名标题", "category": "命名分类", "tags": ["人工"]})
    legacy = {p.name: p.read_bytes() for p in folder.glob("*.article.json")}
    legacy["article.json"] = (folder / "article.json").read_bytes()
    original = desktop._get_article_details(tmp_path, folder.name)
    assert original == {"title": "命名标题", "category": "命名分类", "originalTitle": "源标题",
                        "originalCategory": "自动分类", "titleOverridden": True, "categoryOverridden": True}
    desktop._update_article_details(tmp_path, folder.name, {"title": "新标题", "category": ""})
    clear = desktop._get_article_details(tmp_path, folder.name)
    assert clear["category"] == "" and clear["categoryOverridden"] is True
    data = json.loads((folder / DETAILS_FILENAME).read_text())
    data["future"] = {"keep": True}
    write_json(folder / DETAILS_FILENAME, data)
    desktop._update_article_details(tmp_path, folder.name, {"title": None, "category": None})
    reset = desktop._get_article_details(tmp_path, folder.name)
    assert reset["title"] == "源标题" and reset["category"] == "自动分类"
    assert not reset["titleOverridden"] and not reset["categoryOverridden"]
    assert json.loads((folder / DETAILS_FILENAME).read_text())["future"] == {"keep": True}
    assert all((folder / name).read_bytes() == data for name, data in legacy.items())


def test_failed_metadata_save_preserves_previous_value_and_cached_reader(tmp_path, monkeypatch):
    folder = make_archive(tmp_path)
    desktop._update_article_details(tmp_path, folder.name, {"title": "保留标题", "category": "保留分类"})
    prepared = desktop._prepared_reader(folder / "article.html")
    before = {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}
    original_replace = Path.replace
    def fail(self, target):
        if Path(target).name == DETAILS_FILENAME:
            raise OSError("disk full")
        return original_replace(self, target)
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        desktop._update_article_details(tmp_path, folder.name, {"title": "新标题", "category": None})
    assert all((folder / name).read_bytes() == data for name, data in before.items())
    assert not list(folder.glob(".linkread-*"))
    assert desktop._get_article_details(tmp_path, folder.name)["title"] == "保留标题"
    assert desktop._prepared_reader(folder / "article.html") == prepared


@pytest.mark.parametrize("values", [
    {}, {"title": "a"}, {"title": "", "category": None}, {"title": "a\nb", "category": None},
    {"title": "a" * 301, "category": None}, {"title": True, "category": None},
    {"title": "a", "category": "b" * 81}, {"title": None, "category": None, "unknown": 1},
])
def test_invalid_edit_does_not_write_metadata(tmp_path, values):
    folder = make_archive(tmp_path)
    with pytest.raises(LinkReadError):
        desktop._update_article_details(tmp_path, folder.name, values)
    assert not (folder / DETAILS_FILENAME).exists()


@pytest.mark.parametrize("contents", ["{bad", "[]", '{"version":1,"title":"x"}',
    '{"version":2,"title":null,"category":null}', '{"version":1,"title":false,"category":null}',
    '{"version":1,"title":null,"category":null,"nested":' + '[' * 2000 + 'null' + ']' * 2000 + '}'])
def test_corrupt_overlay_is_not_replaced_by_an_edit(tmp_path, contents):
    folder = make_archive(tmp_path)
    path = folder / DETAILS_FILENAME
    path.write_text(contents)
    with pytest.raises(LinkReadError):
        desktop._update_article_details(tmp_path, folder.name, {"title": "new", "category": None})
    assert path.read_text() == contents


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_unsafe_overlay_is_not_read_or_replaced(tmp_path, kind):
    folder = make_archive(tmp_path)
    path = folder / DETAILS_FILENAME
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    if kind == "symlink":
        path.symlink_to(outside)
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    with pytest.raises(LinkReadError):
        article_details.read_article_details(folder)
    with pytest.raises(LinkReadError):
        desktop._update_article_details(tmp_path, folder.name, {"title": "new", "category": None})
    assert outside.read_text() == "keep"


def test_regeneration_preserves_gui_and_legacy_overrides_even_when_active_source_changes(tmp_path):
    old = make_archive(tmp_path, title="Old source")
    write_json(old / "article.json", {"description": "人工简介", "custom": {"preserve": True}})
    write_json(old / "transcript.article.json", {"title": "原人工标题", "tags": ["人工标签"]})
    write_json(old / "note.article.json", {"future": {"preserve": True}, "title": "未生效的旧标题"})
    desktop._update_article_details(tmp_path, old.name, {"title": "GUI 标题", "category": "GUI 分类"})
    patch_reading_state(old, is_read=True, is_starred=True)
    stage = make_archive(tmp_path / ".stage", title="New source", html="new html")
    raw = desktop.load_transcript(stage / "transcript.json")
    note = NoteDocument(transcript=raw, provider="codex", analysis=NoteAnalysis(summary="新概述"))
    (stage / "note.json").write_text(note.model_dump_json())
    before = {p.name: p.read_bytes() for p in old.iterdir() if p.name in {DETAILS_FILENAME, "article.json", "transcript.article.json"}}
    result = desktop._publish_replacement(stage / "article.html", tmp_path)
    assert result.parent == old
    assert all((old / name).read_bytes() == data for name, data in before.items())
    assert json.loads((old / "note.article.json").read_text()) == {
        "future": {"preserve": True}, "title": "原人工标题", "tags": ["人工标签"],
    }
    record = desktop._article_record(result)
    assert record["title"] == "GUI 标题" and record["category"] == "GUI 分类"
    assert record["isRead"] and record["isStarred"]
    assert desktop._get_article_details(tmp_path, old.name)["originalTitle"] == "New source"


def test_failed_metadata_inheritance_does_not_publish_or_damage_old_article(tmp_path, monkeypatch):
    old = make_archive(tmp_path)
    desktop._update_article_details(tmp_path, old.name, {"title": "保留", "category": None})
    stage = make_archive(tmp_path / ".stage", title="New source", html="new html")
    before = {p.name: p.read_bytes() for p in old.iterdir() if p.is_file()}
    def fail(*args, **kwargs):
        raise OSError("metadata copy failed")
    monkeypatch.setattr(desktop, "write_text", fail)
    with pytest.raises(OSError, match="metadata copy failed"):
        desktop._publish_replacement(stage / "article.html", tmp_path)
    assert all((old / name).read_bytes() == data for name, data in before.items())
    assert not (tmp_path / ".linkread-backups" / old.name).exists()
    assert (stage / "article.html").read_text() == "new html"


@pytest.mark.parametrize("name", ["article.json", "transcript.article.json", DETAILS_FILENAME])
def test_corrupt_manual_metadata_blocks_regeneration_before_replacement(tmp_path, name):
    old = make_archive(tmp_path)
    (old / name).write_text("{broken")
    stage = make_archive(tmp_path / ".stage", title="New source", html="new html")
    with pytest.raises(LinkReadError, match="已保留旧文章"):
        desktop._publish_replacement(stage / "article.html", tmp_path)
    assert (old / "article.html").read_text() == "old"
    assert (old / name).read_text() == "{broken"


def test_replacement_cannot_operate_on_original_archive_as_its_own_stage(tmp_path):
    old = make_archive(tmp_path)
    with pytest.raises(LinkReadError, match="独立的暂存目录"):
        desktop._publish_replacement(old / "article.html", tmp_path)
    assert (old / "article.html").read_text() == "old"


def test_metadata_edit_rejects_outside_id_and_symlink_library(tmp_path):
    library = tmp_path / "library"
    old = make_archive(library)
    alias = tmp_path / "alias"
    alias.symlink_to(library, target_is_directory=True)
    for root, article_id in [(library, "../" + old.name), (alias, old.name)]:
        with pytest.raises(LinkReadError):
            desktop._update_article_details(root, article_id, {"title": "new", "category": None})
    assert not (old / DETAILS_FILENAME).exists()
    assert not (library / ".article-state.lock").exists()


def test_sidecar_details_and_export_options_round_trip(tmp_path, monkeypatch, capsys):
    folder = make_archive(tmp_path)
    status, events = invoke(monkeypatch, capsys, tmp_path, "update-article-details", "--values",
        json.dumps({"title": "新显示标题", "category": None}), "--", folder.name)
    assert status == 0 and events[0]["title"] == "新显示标题" and events[0]["id"] == folder.name
    status, events = invoke(monkeypatch, capsys, tmp_path, "article-details", "--", folder.name)
    assert status == 0 and events[0]["originalTitle"] == "Video"
    status, events = invoke(monkeypatch, capsys, tmp_path, "markdown", "--content", "readable",
        "--include-timestamped", "false", "--", folder.name)
    assert status == 0 and "## 完整字幕" not in events[0]["text"]
    assert events[0]["text"].startswith("# 新显示标题\n")

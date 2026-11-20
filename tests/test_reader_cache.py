import json
import os
from pathlib import Path

import pytest

from linkread_desktop import desktop, reader_cache
from linkread_desktop.article_state import patch_reading_state
from linkread_desktop.errors import LinkReadError
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment


def archive(root: Path, name="one") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    raw = TranscriptDocument(source=SourceInfo(platform="youtube", title=name,
        video_id="1-Fo8McCD9w", url="https://www.youtube.com/watch?v=1-Fo8McCD9w"), transcript_source="subtitle",
        segments=[TranscriptSegment(start_ms=0, end_ms=10000, text="保留每一段原文。")])
    (folder / "transcript.json").write_text(raw.model_dump_json(), encoding="utf-8")
    (folder / "article.html").write_text("<!doctype html><title>Original archived HTML</title>", encoding="utf-8")
    return folder / "article.html"


def track_records(monkeypatch):
    calls = []
    original = desktop._article_record
    def tracked(path):
        calls.append(path.parent.name)
        return original(path)
    monkeypatch.setattr(desktop, "_article_record", tracked)
    return calls


def test_repeat_reader_reuses_validated_materialization_without_parsing_or_writing(tmp_path, monkeypatch):
    article = archive(tmp_path)
    original = article.read_bytes()
    prepared = desktop._prepared_reader(article)
    cached = article.with_name(".reader.html")
    timestamp = cached.stat().st_mtime_ns
    monkeypatch.setattr(desktop, "_reader_source", lambda _: pytest.fail("unchanged document reparsed"))
    assert desktop._prepared_reader(article) == prepared
    assert cached.stat().st_mtime_ns == timestamp
    assert article.read_bytes() == original
    assert prepared["readingKey"] and len(prepared["revision"]) == 20


@pytest.mark.parametrize("change", ["transcript", "metadata", "reading-state", "shownotes", "template", "license"])
def test_reader_invalidates_actual_inputs_and_implementation(tmp_path, monkeypatch, change):
    article = archive(tmp_path)
    if change == "license":
        resources = tmp_path / "shipped-resources"
        resources.mkdir()
        license_file = resources / "js-yaml.LICENSE.txt"
        license_file.write_text("Original third-party notice", encoding="utf-8")
        monkeypatch.setattr(reader_cache, "files", lambda _: resources)
        # Model a fresh process after a license-only asset update in a frozen build.
        monkeypatch.setattr(reader_cache, "implementation_signature", reader_cache.implementation_signature.__wrapped__)
    first = desktop._prepared_reader(article)
    if change == "transcript":
        path = article.with_name("transcript.json")
        before = path.stat()
        path.write_text(path.read_text().replace("保留每一段原文", "逐段阅读原文呀"))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == "metadata":
        article.with_name("article.json").write_text(json.dumps({"title": "A new title"}))
    elif change == "reading-state":
        patch_reading_state(article.parent, is_read=True)
    elif change == "shownotes":
        from linkread_desktop.workspace import _source_key
        raw = desktop.load_transcript(article.with_name("transcript.json"))
        article.with_name(".source-metadata.json").write_text(json.dumps({"version": 1,
            "sourceKey": _source_key(raw), "description": "New source description", "descriptionFormat": "text"}))
    elif change == "license":
        license_file.write_text("Updated third-party notice", encoding="utf-8")
    else:
        monkeypatch.setattr(reader_cache, "implementation_signature", lambda: "updated-renderer")
    calls = []
    original = desktop.render_html
    monkeypatch.setattr(desktop, "render_html", lambda *a, **kw: (calls.append(True), original(*a, **kw))[1])
    current = desktop._prepared_reader(article)
    assert calls == [True]
    if change not in {"template", "license"}:
        assert current["revision"] != first["revision"]
    assert desktop._prepared_reader(article) == current
    assert calls == [True]


@pytest.mark.parametrize("filename,content", [(".reader-cache.json", "{broken"), (".reader.html", "truncated")])
def test_damaged_reader_cache_rebuilds_without_touching_archives(tmp_path, filename, content):
    article = archive(tmp_path)
    original = article.read_bytes()
    prepared = desktop._prepared_reader(article)
    article.with_name(filename).write_text(content)
    assert desktop._prepared_reader(article) == prepared
    assert article.read_bytes() == original


def test_reader_refuses_changed_invalid_source_and_symlink_cache(tmp_path):
    article = archive(tmp_path)
    desktop._prepared_reader(article)
    transcript = article.with_name("transcript.json")
    saved = transcript.read_text()
    transcript.write_text("{bad")
    with pytest.raises(LinkReadError):
        desktop._prepared_reader(article)
    transcript.write_text(saved)
    cached = article.with_name(".reader.html")
    cached.unlink()
    external = tmp_path / "outside.html"
    external.write_text("do not overwrite")
    cached.symlink_to(external)
    with pytest.raises(LinkReadError, match="缓存"):
        desktop._prepared_reader(article)
    assert external.read_text() == "do not overwrite"


def test_incremental_library_only_reparses_changed_archives_and_removes_deleted(tmp_path, monkeypatch):
    one, two = archive(tmp_path, "one"), archive(tmp_path, "two")
    calls = track_records(monkeypatch)
    first = desktop._library_records(tmp_path)
    assert sorted(calls) == ["one", "two"]
    calls.clear()
    assert desktop._library_records(tmp_path) == first
    assert not calls
    patch_reading_state(one.parent, is_starred=True)
    second = desktop._library_records(tmp_path)
    assert calls == ["one"]
    assert next(a for a in second["articles"] if a["id"] == "one")["isStarred"]
    calls.clear()
    two.unlink()
    archive(tmp_path, "three")
    result = desktop._library_records(tmp_path)
    assert calls == ["three"]
    assert {a["id"] for a in result["articles"]} == {"one", "three"}
    index = json.loads((tmp_path / reader_cache.LIBRARY_INDEX).read_text())
    assert set(index["entries"]) == {"one", "three"}


@pytest.mark.parametrize("damage", ["json", "nested-json", "shape", "record", "platform", "checksum", "implementation"])
def test_library_index_damage_or_new_renderer_rebuilds_from_archives(tmp_path, monkeypatch, damage):
    archive(tmp_path)
    expected = desktop._library_records(tmp_path)
    target = tmp_path / reader_cache.LIBRARY_INDEX
    data = json.loads(target.read_text())
    if damage == "json":
        target.write_text("{")
    elif damage == "nested-json":
        target.write_text('[' * 1200 + '0' + ']' * 1200)
    elif damage == "implementation":
        monkeypatch.setattr(desktop, "implementation_signature", lambda: "changed-code")
    else:
        if damage == "shape": data["entries"] = []
        elif damage == "record": data["entries"]["one"]["record"]["id"] = "../external"
        elif damage == "platform": data["entries"]["one"]["record"]["platform"] = []
        else: data["entries"]["one"]["digest"] = "bad"
        target.write_text(json.dumps(data))
    calls = track_records(monkeypatch)
    assert desktop._library_records(tmp_path) == expected
    assert calls == ["one"]


def test_reader_replaces_non_regular_disposable_cache_without_blocking(tmp_path):
    article = archive(tmp_path)
    prepared = desktop._prepared_reader(article)
    cached = article.with_name(".reader.html")
    cached.unlink()
    os.mkfifo(cached)
    assert desktop._prepared_reader(article) == prepared
    assert cached.is_file()


def test_index_does_not_hide_bad_archives_and_recovers_after_repair(tmp_path):
    article = archive(tmp_path)
    desktop._library_records(tmp_path)
    source = article.with_name("transcript.json")
    saved = source.read_text()
    source.write_text("broken")
    assert desktop._library_records(tmp_path) == {"articles": [], "unavailable": 1}
    source.write_text(saved)
    assert len(desktop._library_records(tmp_path)["articles"]) == 1


def test_optional_index_write_failure_does_not_block_library(tmp_path, monkeypatch):
    archive(tmp_path)
    monkeypatch.setattr(reader_cache, "write_text", lambda *_: (_ for _ in ()).throw(OSError("read only")))
    result = desktop._library_records(tmp_path)
    assert len(result["articles"]) == 1 and result["unavailable"] == 0


def test_read_only_library_is_listed_without_writing_index_or_lock(tmp_path, monkeypatch):
    archive(tmp_path)
    monkeypatch.setattr(desktop, "archive_mutation", lambda *_: (_ for _ in ()).throw(PermissionError("read only")))
    result = desktop._library_records(tmp_path)
    assert len(result["articles"]) == 1 and result["unavailable"] == 0
    assert not (tmp_path / reader_cache.LIBRARY_INDEX).exists()


def test_index_cache_and_reader_manifest_never_overwrite_symlink_targets(tmp_path):
    article = archive(tmp_path)
    target = tmp_path / "external.json"
    target.write_text("do not change")
    (tmp_path / reader_cache.LIBRARY_INDEX).symlink_to(target)
    article.with_name(reader_cache.READER_MANIFEST).symlink_to(target)
    assert len(desktop._library_records(tmp_path)["articles"]) == 1
    desktop._prepared_reader(article)
    assert target.read_text() == "do not change"


@pytest.mark.parametrize("platform,source_id,url", [
    ("youtube", "1-Fo8McCD9w", "https://www.youtube.com/watch?v=1-Fo8McCD9w"),
    ("apple_podcasts", "1552904790:1000801308527", "https://podcasts.apple.com/cn/podcast/id1552904790?i=1000801308527"),
    ("local", "local-import", None),
])
def test_continue_reading_identity_matches_reader_without_materializing_html(tmp_path, monkeypatch, platform, source_id, url):
    from linkread_desktop.renderers.reading_state import reading_state_key

    path = archive(tmp_path)
    transcript = path.with_name("transcript.json")
    raw = desktop.load_transcript(transcript).model_copy(update={"source": SourceInfo(
        platform=platform, title="Saved article", video_id=source_id, url=url)})
    transcript.write_text(raw.model_dump_json(), encoding="utf-8")
    original = transcript.read_bytes(), path.read_bytes()
    monkeypatch.setattr(desktop, "render_html", lambda *a, **kw: pytest.fail("Listing must not render readers"))
    record = desktop._library_records(tmp_path)["articles"][0]
    assert record["readingKey"] == reading_state_key(raw)
    assert desktop._valid_cached_record(record, record["id"])
    assert not path.with_name(".reader.html").exists()
    assert (transcript.read_bytes(), path.read_bytes()) == original


def test_continue_reading_rebuilds_old_index_missing_source_identity(tmp_path, monkeypatch):
    archive(tmp_path)
    expected = desktop._library_records(tmp_path)
    index_path = tmp_path / reader_cache.LIBRARY_INDEX
    index = json.loads(index_path.read_text())
    entry = index["entries"]["one"]
    del entry["record"]["readingKey"]
    entry["digest"] = desktop._record_digest(entry["record"])
    index_path.write_text(json.dumps(index), encoding="utf-8")
    calls = track_records(monkeypatch)
    assert desktop._library_records(tmp_path) == expected
    assert calls == ["one"]

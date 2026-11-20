from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from linkread_desktop import desktop
from linkread_desktop.article_state import patch_reading_state, reading_state
from linkread_desktop.errors import LinkReadError
from linkread_desktop.models import SourceInfo
from test_desktop import invoke
from test_desktop_duplicate import make_archive


def test_reading_flags_default_to_unread_and_survive_partial_updates(tmp_path, monkeypatch, capsys):
    archive = make_archive(tmp_path)
    originals = {path.name: path.read_bytes() for path in archive.iterdir()}
    assert reading_state(archive) == {"isRead": False, "isStarred": False}
    status, events = invoke(monkeypatch, capsys, tmp_path, "reading-state", "--starred", "true", archive.name)
    assert status == 0 and events == [{"type": "reading_state", "isRead": False, "isStarred": True}]
    status, _ = invoke(monkeypatch, capsys, tmp_path, "reading-state", "--read", "true", archive.name)
    assert status == 0
    status, events = invoke(monkeypatch, capsys, tmp_path, "library")
    assert status == 0
    assert events[0]["articles"][0]["isRead"] and events[0]["articles"][0]["isStarred"]
    assert patch_reading_state(archive, is_read=False) == {"isRead": False, "isStarred": True}
    assert {name: (archive / name).read_bytes() for name in originals} == originals


def test_concurrent_partial_state_updates_preserve_both_flags(tmp_path):
    archive = make_archive(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        read = pool.submit(patch_reading_state, archive, is_read=True)
        star = pool.submit(patch_reading_state, archive, is_starred=True)
        read.result()
        star.result()
    assert reading_state(archive) == {"isRead": True, "isStarred": True}


def test_failed_atomic_state_write_keeps_previous_flags(tmp_path, monkeypatch):
    archive = make_archive(tmp_path)
    patch_reading_state(archive, is_starred=True)
    before = (archive / ".reading-state.json").read_bytes()
    original_replace = Path.replace

    def fail(self, target):
        if Path(target).name == ".reading-state.json":
            raise OSError("disk unavailable")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        patch_reading_state(archive, is_read=True)
    assert (archive / ".reading-state.json").read_bytes() == before
    assert not list(archive.glob(".linkread-*"))


@pytest.mark.parametrize("payload", ['invalid', '[]', '{"version":1,"isRead":"true","isStarred":false}'])
def test_invalid_state_is_not_silently_overwritten(tmp_path, payload):
    archive = make_archive(tmp_path)
    state = archive / ".reading-state.json"
    state.write_text(payload)
    with pytest.raises(LinkReadError, match="阅读状态"):
        patch_reading_state(archive, is_read=True)
    assert state.read_text() == payload


def test_reading_state_rejects_symlink_and_outside_archive(tmp_path, monkeypatch, capsys):
    archive = make_archive(tmp_path)
    target = tmp_path / "outside.json"
    target.write_text("unchanged")
    (archive / ".reading-state.json").symlink_to(target)
    with pytest.raises(LinkReadError):
        patch_reading_state(archive, is_starred=True)
    assert target.read_text() == "unchanged"
    status, events = invoke(monkeypatch, capsys, tmp_path, "reading-state", "--read", "true", "../outside")
    assert status == 1 and events[0]["type"] == "error"


def test_article_replacement_preserves_manual_flags_and_refreshed_shownotes(tmp_path):
    archive = make_archive(tmp_path, title="Original")
    raw_path = archive / "transcript.json"
    raw = json.loads(raw_path.read_text())
    raw["source"]["description"] = "old cached description"
    raw_path.write_text(json.dumps(raw))
    patch_reading_state(archive, is_read=True, is_starred=True)
    overlay = {"version": 1, "sourceKey": "youtube-1-Fo8McCD9w", "description": "fresh source notes", "descriptionFormat": "text"}
    (archive / ".source-metadata.json").write_text(json.dumps(overlay))
    staged = make_archive(tmp_path / ".stage", title="New")
    (staged / "transcript.json").write_text(json.dumps(raw))
    desktop._publish_replacement(staged / "article.html", tmp_path)
    assert reading_state(archive) == {"isRead": True, "isStarred": True}
    assert json.loads((archive / ".source-metadata.json").read_text()) == overlay


def test_prepare_reader_upgrades_existing_archive_offline_without_mutating_originals(tmp_path, monkeypatch, capsys):
    archive = make_archive(tmp_path, html='<body><button data-tab="notes">笔记</button></body>')
    originals = {path.name: path.read_bytes() for path in archive.iterdir()}
    monkeypatch.setattr(desktop, "shownotes_metadata", lambda *_: pytest.fail("ordinary reading must remain offline"))
    monkeypatch.setattr(desktop, "make_provider", lambda *_a, **_k: pytest.fail("must not call AI"))
    status, events = invoke(monkeypatch, capsys, tmp_path, "reader", archive.name)
    assert status == 0 and events[0]["revision"]
    page = (archive / ".reader.html").read_text()
    assert 'data-tab="transcript"' in page and "Transcript" in page
    assert 'data-tab="shownotes"' in page and 'data-tab="notes"' not in page
    assert "Mark Complete" in page and 'data-refresh-shownotes' in page
    assert {name: (archive / name).read_bytes() for name in originals} == originals


def test_explicit_shownotes_refresh_saves_exact_source_description_only(tmp_path, monkeypatch, capsys):
    archive = make_archive(tmp_path)
    originals = {path.name: path.read_bytes() for path in archive.iterdir()}
    source = SourceInfo(platform="youtube", title="Changed external title", video_id="1-Fo8McCD9w",
                        description='<p>Source notes <a href="https://example.com/">reference</a></p>',
                        description_format="html")
    calls = []
    monkeypatch.setattr(desktop, "shownotes_metadata", lambda url: calls.append(url) or source)
    status, events = invoke(monkeypatch, capsys, tmp_path, "refresh-shownotes", archive.name)
    assert status == 0 and events[0]["revision"]
    assert calls == ["https://www.youtube.com/watch?v=1-Fo8McCD9w"]
    page = (archive / ".reader.html").read_text()
    assert "Source notes" in page and 'https://example.com/' in page
    assert "Changed external title" not in page
    assert {name: (archive / name).read_bytes() for name in originals} == originals
    status, events = invoke(monkeypatch, capsys, tmp_path, "markdown", archive.name)
    assert status == 0 and "Source notes" in events[0]["text"]


@pytest.mark.parametrize("wrong_id", [False, True])
def test_refresh_does_not_erase_saved_notes_on_empty_or_wrong_source(tmp_path, monkeypatch, wrong_id):
    archive = make_archive(tmp_path)
    overlay = archive / ".source-metadata.json"
    original = '{"version":1,"sourceKey":"youtube-1-Fo8McCD9w","description":"keep","descriptionFormat":"text"}'
    overlay.write_text(original)
    source = SourceInfo(platform="youtube", title="Source", video_id="N4jBc7_ThnE" if wrong_id else "1-Fo8McCD9w",
                        description="different" if wrong_id else None)
    monkeypatch.setattr(desktop, "shownotes_metadata", lambda _: source)
    with pytest.raises(LinkReadError):
        desktop._refresh_shownotes(archive / "article.html")
    assert overlay.read_text() == original


def test_reader_cache_symlink_is_not_followed(tmp_path):
    archive = make_archive(tmp_path)
    target = tmp_path / "outside.html"
    target.write_text("unchanged")
    (archive / ".reader.html").symlink_to(target)
    with pytest.raises(LinkReadError, match="阅读缓存"):
        desktop._prepare_reader(archive / "article.html")
    assert target.read_text() == "unchanged"


def test_oversized_refresh_preserves_existing_notes(tmp_path, monkeypatch):
    archive = make_archive(tmp_path)
    overlay = archive / ".source-metadata.json"
    original = '{"version":1,"sourceKey":"youtube-1-Fo8McCD9w","description":"keep","descriptionFormat":"text"}'
    overlay.write_text(original)
    source = SourceInfo(platform="youtube", title="Source", video_id="1-Fo8McCD9w", description="字" * 800_000)
    monkeypatch.setattr(desktop, "shownotes_metadata", lambda _: source)
    with pytest.raises(LinkReadError, match="过大"):
        desktop._refresh_shownotes(archive / "article.html")
    assert overlay.read_text() == original


def test_static_bookshelf_includes_refreshed_notes_and_reading_flags(tmp_path):
    from linkread_desktop.renderers.site import build_site

    library = tmp_path / "library"
    archive = make_archive(library)
    original = (archive / "transcript.json").read_bytes()
    patch_reading_state(archive, is_read=True, is_starred=True)
    (archive / ".source-metadata.json").write_text(json.dumps({
        "version": 1, "sourceKey": "youtube-1-Fo8McCD9w",
        "description": "Refreshed public source notes", "descriptionFormat": "text",
    }))
    output = tmp_path / "site"
    assert build_site(library, output) == 1
    home = (output / "index.html").read_text()
    page = next((output / "pages").glob("*/index.html")).read_text()
    assert "Refreshed public source notes" in page
    assert 'data-is-read="true"' in page and 'data-is-starred="true"' in page
    assert 'data-read aria-pressed="true"' in home and 'data-star aria-pressed="true"' in home
    assert (archive / "transcript.json").read_bytes() == original

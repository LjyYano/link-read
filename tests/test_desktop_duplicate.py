import json
from pathlib import Path

import pytest

from linkread_desktop.desktop import _duplicate, _publish_replacement, _generate_with_replacement
from linkread_desktop.errors import LinkReadError
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.workspace import workspace_dir


def transcript(title="Video"):
    return TranscriptDocument(
        source=SourceInfo(platform="youtube", title=title, video_id="1-Fo8McCD9w",
                          url="https://www.youtube.com/watch?v=1-Fo8McCD9w"),
        transcript_source="subtitle",
        segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="Hello")],
    )


def make_archive(library, title="Video", html="old"):
    raw = transcript(title)
    folder = workspace_dir(library, raw)
    folder.mkdir(parents=True)
    (folder / "transcript.json").write_text(raw.model_dump_json(), encoding="utf-8")
    (folder / "article.html").write_text(html, encoding="utf-8")
    return folder


def test_duplicate_detects_same_video_even_with_tracking_query(tmp_path):
    old = make_archive(tmp_path)
    # _article_record requires a full note? It can read raw transcript directly.
    found = _duplicate("https://youtu.be/1-Fo8McCD9w?si=abc", tmp_path, local_file=False)
    assert found["key"] == "youtube-1-Fo8McCD9w"
    assert found["article"]["id"] == old.name


def test_default_rejects_existing_completed_article(tmp_path):
    make_archive(tmp_path)
    with pytest.raises(LinkReadError, match="已存在"):
        _generate_with_replacement(
            "https://youtu.be/1-Fo8McCD9w", tmp_path, lambda event: None,
            local_file=False, replace=False, provider=None)


def test_replacement_preserves_old_if_not_yet_ready(tmp_path):
    old = make_archive(tmp_path, html="keep me")
    with pytest.raises(LinkReadError):
        _publish_replacement(tmp_path / "missing" / "article.html", tmp_path)
    assert (old / "article.html").read_text(encoding="utf-8") == "keep me"


def test_successful_replace_keeps_original_id_and_removes_backup(tmp_path):
    old = make_archive(tmp_path, title="Old title")
    stage = tmp_path / ".linkread-stage-tests"
    candidate = make_archive(stage, title="New title", html="new content")
    result = _publish_replacement(candidate / "article.html", tmp_path)
    assert result == old / "article.html"
    assert result.read_text(encoding="utf-8") == "new content"
    assert not candidate.exists()
    assert not (tmp_path / ".linkread-backups" / old.name).exists()


def test_failed_swap_restores_old_article(tmp_path, monkeypatch):
    # Simulate a target file rename failing after old version was backed up.
    old = make_archive(tmp_path)
    stage = tmp_path / ".linkread-stage-tests"
    candidate = make_archive(stage, title="New title", html="new")
    original_rename = Path.rename
    def fail_publish(self, dest):
        if self == candidate:
            raise OSError("publish failed")
        return original_rename(self, dest)
    monkeypatch.setattr(Path, "rename", fail_publish)
    with pytest.raises(OSError, match="publish failed"):
        _publish_replacement(candidate / "article.html", tmp_path)
    assert (old / "article.html").read_text(encoding="utf-8") == "old"

def test_short_bilibili_link_resolves_before_duplicate_check(tmp_path, monkeypatch):
    class Response:
        url = "https://www.bilibili.com/video/BV1rxqmBhE91?p=2"
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def raise_for_status(self): pass

    monkeypatch.setattr("linkread_desktop.desktop.requests.get", lambda *a, **k: Response())
    result = _duplicate("https://b23.tv/abcdef", tmp_path, local_file=False)
    assert result["key"] == "bilibili-BV1rxqmBhE91-p2"
    assert result["article"] is None

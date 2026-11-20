"""Archive identities and transcript contracts used by desktop generation."""
import json

import pytest
from pydantic import ValidationError

from linkread_desktop.errors import UnsupportedSourceError
from linkread_desktop.extractors.local import extract_local
from linkread_desktop.generation import _run_input, _save_json
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.pipeline import extract
from linkread_desktop.renderers.html import render_html
from linkread_desktop.renderers.site import build_site


def test_local_subtitle_keeps_source_title_and_text(tmp_path):
    source = tmp_path / "test.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:02,000\n你好，世界\n", encoding="utf-8")
    body = extract_local(source)
    assert body.source.title == "test"
    assert body.transcript_source == "local"
    assert body.segments[0].text == "你好，世界"


def test_saved_transcript_keeps_v1_contract(tmp_path):
    source = tmp_path / "subtitle.vtt"
    source.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nhello\n", encoding="utf-8")
    target = tmp_path / "out" / "transcript.json"
    _save_json(extract_local(source), target)
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["schema_version"] == "1.0"
    assert data["segments"] == [{"start_ms": 0, "end_ms": 1000, "text": "hello"}]


def test_video_uses_title_directory_and_reuses_saved_transcript(tmp_path, monkeypatch):
    raw = TranscriptDocument(
        source=SourceInfo(platform="youtube", title='  读书 / 笔记: "上集"  ', video_id="1-Fo8McCD9w"),
        transcript_source="subtitle", segments=[TranscriptSegment(start_ms=0, text="原字幕。")],
    )
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *args, **kwargs: raw)
    url = "https://www.youtube.com/watch?v=1-Fo8McCD9w"
    library = tmp_path / "library"
    folder, saved, reused = _run_input(url, library, None, None, {})
    assert not reused
    assert folder == library / "读书 笔记 上集 [youtube-1-Fo8McCD9w]"
    target = folder / "transcript.json"
    before = target.read_bytes()

    raw.source.title = "修改后的标题"
    raw.segments[0].text = "之后的不同版本。"
    repeated, saved, reused = _run_input(url, library, None, None, {})
    assert reused and repeated == folder
    assert list(library.iterdir()) == [folder]
    assert target.read_bytes() == before
    assert "原字幕。" in render_html(saved)
    assert build_site(library, tmp_path / "site") == 1
    assert 'id="article-count">1</span>' in (tmp_path / "site" / "index.html").read_text(encoding="utf-8")


def test_title_collisions_and_bilibili_parts_get_distinct_directories(tmp_path, monkeypatch):
    raw = TranscriptDocument(
        source=SourceInfo(platform="bilibili", title="相同标题", video_id="BV1rxqmBhE91"),
        transcript_source="subtitle", segments=[TranscriptSegment(start_ms=0, text="测试")],
    )
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *args, **kwargs: raw)
    for part in (1, 2):
        raw.source.url = f"https://www.bilibili.com/video/BV1rxqmBhE91/?p={part}"
        _run_input(raw.source.url, tmp_path, None, None, {})
    raw.source.video_id = "BV14uW36uEdD"
    raw.source.url = "https://www.bilibili.com/video/BV14uW36uEdD/"
    _run_input(raw.source.url, tmp_path, None, None, {})
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "相同标题 [bilibili-BV14uW36uEdD-p1]", "相同标题 [bilibili-BV1rxqmBhE91-p1]",
        "相同标题 [bilibili-BV1rxqmBhE91-p2]",
    ]


def test_missing_local_file_is_readable_error():
    with pytest.raises(UnsupportedSourceError, match="文件不存在"):
        extract("/not-a-file.srt")


def test_model_invalid_end_and_chronology():
    with pytest.raises(ValidationError):
        TranscriptSegment(start_ms=100, end_ms=99, text="bad")
    with pytest.raises(ValidationError):
        TranscriptDocument(
            source=SourceInfo(platform="local", title="x"),
            transcript_source="local",
            segments=[TranscriptSegment(start_ms=2, text="b"), TranscriptSegment(start_ms=1, text="a")],
        )

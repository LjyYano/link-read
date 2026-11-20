import pytest

from linkread_desktop.errors import ExtractionError, UnsupportedSourceError
from linkread_desktop.models import TranscriptDocument
from linkread_desktop.pipeline import extract, video_source_key


@pytest.mark.parametrize("url", [
    "https://www.bilibili.com.evil.example/video/BV1rxqmBhE91",
    "https://127.0.0.1/video/BV1rxqmBhE91",
    "file:///etc/passwd",
    "http://www.youtube.com/watch?v=abc",
])
def test_rejects_unsafe_or_unsupported_urls(url):
    with pytest.raises(UnsupportedSourceError):
        extract(url)


def test_local_bilibili_json(tmp_path):
    path = tmp_path / "my-subtitle.json"
    path.write_text('{"body": [{"from": 1, "to": 2, "content": "测试"}]}', encoding="utf-8")
    doc = extract(path)
    assert doc.source.platform == "local"
    assert doc.segments[0].text == "测试"


def test_extract_archive_preserves_metadata_and_segments(tmp_path):
    path = tmp_path / "archive.json"
    path.write_text(
        '{"schema_version":"1.0","source":{"platform":"youtube","title":"原视频",'
        '"video_id":"1-Fo8McCD9w"},"language":"zh-Hans","transcript_source":"subtitle",'
        '"segments":[{"start_ms":200,"text":"原文"}]}', encoding="utf-8",
    )
    assert extract(path) == TranscriptDocument.model_validate_json(path.read_bytes())
    path.write_text('{"schema_version":"1.0","segments":[]}', encoding="utf-8")
    with pytest.raises(ExtractionError, match="TranscriptDocument"):
        extract(path)


@pytest.mark.parametrize(("url", "key"), [
    ("https://youtu.be/1-Fo8McCD9w?si=test", "youtube-1-Fo8McCD9w"),
    (r"https\://www\.youtube.com/shorts/1-Fo8McCD9w", "youtube-1-Fo8McCD9w"),
    ("https://www.bilibili.com/video/BV1rxqmBhE91/?p=2", "bilibili-BV1rxqmBhE91-p2"),
    ("https://b23.tv/example", None),
    ("https://www.youtube.com.example/watch?v=1-Fo8McCD9w", None),
])
def test_video_source_key_without_network(url, key):
    assert video_source_key(url) == key

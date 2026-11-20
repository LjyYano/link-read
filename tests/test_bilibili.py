import json

import pytest

from linkread_desktop.errors import SubtitleUnavailableError, UnsupportedSourceError, ExtractionError
from linkread_desktop.extractors.bilibili import extract_bilibili


class Response:
    def __init__(self, *, payload=None, text=None, url=None):
        self._payload = payload
        self.text = text or ""
        self.url = url or ""

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class Session:
    def __init__(self, *, subtitles=True, subtitle_host="i0.hdslb.com"):
        self.calls = []
        self.subtitles = subtitles
        self.subtitle_host = subtitle_host

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        if "x/web-interface/view" in url:
            return Response(payload={"code": 0, "data": {
                "title": "示例视频", "owner": {"name": "作者"}, "pubdate": 1700000000,
                "pages": [{"cid": 101, "duration": 100, "part": "上"}, {"cid": 202, "duration": 75, "part": "下"}],
            }})
        if "x/player/v2" in url:
            subtitles = ([{"lan": "en", "subtitle_url": "//i0.hdslb.com/en.json"},
                          {"lan": "zh-CN", "subtitle_url": f"//{self.subtitle_host}/zh.json"}]
                         if self.subtitles else [])
            return Response(payload={"code": 0, "data": {"subtitle": {"subtitles": subtitles}}})
        if url.startswith("https://i0.hdslb.com/"):
            return Response(text=json.dumps({"body": [{"from": 0, "to": 1.5, "content": "你好"}]}))
        raise AssertionError(f"Unexpected request {url}")


def test_bilibili_chooses_chinese_and_supports_page_2():
    session = Session()
    doc = extract_bilibili("https://www.bilibili.com/video/BV1rxqmBhE91/?p=2", session=session)
    assert doc.source.platform == "bilibili"
    assert "P2 下" in doc.source.title
    assert doc.source.duration_ms == 75000
    assert doc.source.author == "作者"
    assert doc.language == "zh-CN"
    assert doc.segments[0].end_ms == 1500
    assert any(params == {"bvid": "BV1rxqmBhE91", "cid": 202} for _, params in session.calls)


def test_bilibili_no_subtitles_explains_next_stage():
    with pytest.raises(SubtitleUnavailableError, match="ASR"):
        extract_bilibili("https://www.bilibili.com/video/BV1rxqmBhE91", session=Session(subtitles=False))


def test_bilibili_rejects_out_of_range_page():
    with pytest.raises(UnsupportedSourceError):
        extract_bilibili("https://www.bilibili.com/video/BV1rxqmBhE91?p=3", session=Session())


def test_bilibili_blocks_untrusted_subtitle_host():
    with pytest.raises(ExtractionError, match="受信任"):
        extract_bilibili("https://www.bilibili.com/video/BV1rxqmBhE91", session=Session(subtitle_host="evil.example"))

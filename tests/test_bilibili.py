import json

import pytest
import requests

from linkread_desktop.errors import SubtitleUnavailableError, UnsupportedSourceError, ExtractionError
from linkread_desktop.asr.config import AsrConfig
from linkread_desktop.extractors.bilibili import extract_bilibili
from linkread_desktop.pipeline import extract


class Response:
    def __init__(self, *, payload=None, text=None, url=None, status_code=200):
        self._payload = payload
        self.text = text or ""
        self.url = url or ""
        self.status_code = status_code
        self.headers = {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, size):
        yield self.text.encode()


class Session:
    def __init__(self, *, subtitles=True, player_data=None,
                 video_data=None, dm_data=None, caption=None):
        self.calls = []
        self.subtitles = subtitles
        self.player_data = player_data
        self.video_data = video_data or {}
        self.dm_data = {"subtitle": {"subtitles": None}} if dm_data is None else dm_data
        self.caption = caption if caption is not None else Response(
            text=json.dumps({"body": [{"from": 0, "to": 1.5, "content": "你好"}]})
        )

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        if "x/web-interface/view" in url:
            return Response(payload={"code": 0, "data": {
                "title": "示例视频", "owner": {"name": "作者"}, "pubdate": 1700000000,
                "aid": 42, "cid": 101,
                "pages": [{"cid": 101, "duration": 100, "part": "上"}, {"cid": 202, "duration": 75, "part": "下"}],
                **self.video_data,
            }})
        if "x/player/v2" in url:
            if self.player_data is not None:
                return Response(payload={"code": 0, "data": self.player_data})
            subtitles = ([{"lan": "en", "subtitle_url": "//i0.hdslb.com/en.json"},
                          {"lan": "zh-CN", "subtitle_url": "//i0.hdslb.com/zh.json"}]
                         if self.subtitles else [])
            return Response(payload={"code": 0, "data": {"subtitle": {"subtitles": subtitles}}})
        if "x/v2/dm/view" in url:
            if isinstance(self.dm_data, Exception):
                raise self.dm_data
            return Response(payload={"code": 0, "data": self.dm_data})
        if url.startswith(("https://i0.hdslb.com/", "https://aisubtitle.hdslb.com/")):
            assert kwargs.get("allow_redirects") is False and kwargs.get("stream") is True
            return self.caption
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


@pytest.mark.parametrize("player_data, code", [
    ({"need_login_subtitle": True, "subtitle": {"subtitles": []}}, "subtitle_login_required"),
    ({"need_login_subtitle": True, "subtitle": {"subtitles": [{"lan": "en", "subtitle_url": ""}]}}, "subtitle_login_required"),
    ({"need_login_subtitle": False, "subtitle": {"subtitles": []}}, "subtitles_unavailable"),
    ({}, "subtitle_response_invalid"),
    ({"subtitle": {"subtitles": [{"lan": "en"}]}}, "subtitle_response_invalid"),
    ({"need_login_subtitle": True}, "subtitle_response_invalid"),
    ({"need_login_subtitle": True, "subtitle": {"subtitles": ["malformed"]}}, "subtitle_response_invalid"),
])
def test_bilibili_distinguishes_login_and_invalid_lists_from_missing_subtitles(player_data, code, monkeypatch):
    session = Session(player_data=player_data)
    share = r"https://www.bilibili.com/video/BV1rxqmBhE91/?share_medium=iphone\&p=2\&spm_id_from=333.788"
    monkeypatch.setattr("linkread_desktop.pipeline.extract_bilibili",
                        lambda link, **options: extract_bilibili(link, session=session, **options))
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video",
                        lambda *args, **kwargs: pytest.fail("ASR must not run for a login or subtitle list error"))
    asr = AsrConfig() if code == "subtitle_response_invalid" else None
    with pytest.raises((ExtractionError, SubtitleUnavailableError)) as failure:
        extract(share, asr=asr)
    assert failure.value.code == code
    assert len(session.calls) == 3
    assert session.calls[1][1] == {"bvid": "BV1rxqmBhE91", "cid": 202}
    assert session.calls[2][1] == {"aid": 42, "oid": 202, "type": 1}


@pytest.mark.parametrize("video_data", [
    {"is_upower_exclusive": True}, {"is_upower_preview": True},
    {"rights": {"pay": 1}}, {"rights": {"ugc_pay_preview": 1}},
])
def test_login_required_subtitles_do_not_trigger_asr_for_paid_media(video_data, monkeypatch):
    session = Session(video_data=video_data, player_data={"need_login_subtitle": True, "subtitle": {"subtitles": []}})
    monkeypatch.setattr("linkread_desktop.pipeline.extract_bilibili",
                        lambda link, **options: extract_bilibili(link, session=session, **options))
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video",
                        lambda *args, **kwargs: pytest.fail("Paid media must not trigger ASR"))
    with pytest.raises(ExtractionError) as failure:
        extract("https://www.bilibili.com/video/BV1rxqmBhE91/?p=2", asr=AsrConfig())
    assert failure.value.code == "protected_media"


def test_available_bilibili_captions_take_priority_over_login_flag(monkeypatch):
    session = Session(player_data={"need_login_subtitle": True, "subtitle": {
        "subtitles": [{"lan": "zh-CN", "subtitle_url": "//i0.hdslb.com/zh.json"}],
    }})
    monkeypatch.setattr("linkread_desktop.pipeline.extract_bilibili",
                        lambda link, **options: extract_bilibili(link, session=session, **options))
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video",
                        lambda *args, **kwargs: pytest.fail("Available subtitles must win"))
    transcript = extract("https://www.bilibili.com/video/BV1rxqmBhE91/?p=2", asr=AsrConfig())
    assert transcript.transcript_source == "subtitle"


def test_bilibili_uses_public_track_when_another_track_is_missing_its_url():
    session = Session(player_data={"subtitle": {"subtitles": [
        {"lan": "zh-CN", "subtitle_url": ""}, {"lan": "en", "subtitle_url": "//i0.hdslb.com/en.json"},
    ]}})
    doc = extract_bilibili("https://www.bilibili.com/video/BV1rxqmBhE91?p=2", session=session)
    assert doc.language == "en"
    assert session.calls[-1][0] == "https://i0.hdslb.com/en.json"


def test_bilibili_rejects_out_of_range_page():
    with pytest.raises(UnsupportedSourceError):
        extract_bilibili("https://www.bilibili.com/video/BV1rxqmBhE91?p=3", session=Session())


def test_bilibili_blocks_untrusted_subtitle_host():
    session = Session(player_data={"subtitle": {"subtitles": [
        {"lan": "zh-CN", "subtitle_url": "//evil.example/zh.json"},
    ]}})
    with pytest.raises(ExtractionError, match="受信任"):
        extract_bilibili("https://www.bilibili.com/video/BV1rxqmBhE91", session=session)
    assert not any("evil.example" in url for url, _ in session.calls)


def public_track(*, prefix="42202", host="aisubtitle.hdslb.com"):
    return {"lan": "ai-zh", "subtitle_url": f"http://{host}/bfs/ai_subtitle/prod/{prefix}{'a' * 32}?auth_key=test"}


@pytest.mark.parametrize("login", [True, False])
def test_public_bilibili_track_wins_before_asr_even_when_player_is_empty(monkeypatch, login):
    session = Session(player_data={"need_login_subtitle": login, "subtitle": {"subtitles": []}},
                      dm_data={"subtitle": {"subtitles": [public_track(prefix="42101"), public_track()]}})
    monkeypatch.setattr("linkread_desktop.pipeline.extract_bilibili",
                        lambda link, **options: extract_bilibili(link, session=session, **options))
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video",
                        lambda *args, **kwargs: pytest.fail("Public subtitles must win before ASR"))
    result = extract("https://www.bilibili.com/video/BV1rxqmBhE91?p=2", asr=AsrConfig())
    assert result.transcript_source == "subtitle" and result.language == "ai-zh"
    assert result.segments[0].text == "你好"
    assert session.calls[-2][1] == {"aid": 42, "oid": 202, "type": 1}
    assert session.calls[-1][0] == public_track()["subtitle_url"].replace("http://", "https://", 1)


def test_public_manual_cc_uses_publisher_track_id_for_exact_page():
    track = {"id_str": "1533287192412254208", "lan": "zh-CN"}
    listed = {**track, "subtitle_url": f"http://i0.hdslb.com/bfs/subtitle/{'b' * 40}.json"}
    session = Session(subtitles=False, video_data={"subtitle": {"list": [track]}},
                      dm_data={"subtitle": {"subtitles": [listed]}})
    result = extract_bilibili("https://www.bilibili.com/video/BV1rxqmBhE91?p=1", session=session)
    assert result.transcript_source == "subtitle" and result.language == "zh-CN"
    assert session.calls[-1][0] == listed["subtitle_url"].replace("http://", "https://", 1)


def test_verified_public_track_replaces_wrong_player_track_for_another_page():
    session = Session(player_data={"subtitle": {"subtitles": [public_track(prefix="42101")]}},
                      dm_data={"subtitle": {"subtitles": [public_track()]}})
    extract_bilibili("https://www.bilibili.com/video/BV1rxqmBhE91?p=2", session=session)
    assert session.calls[-1][0] == public_track()["subtitle_url"].replace("http://", "https://", 1)
    assert not any("42101" in url for url, _ in session.calls)


@pytest.mark.parametrize("dm_data", [
    {}, {"subtitle": {}}, requests.Timeout(),
    {"subtitle": {"subtitles": [public_track(prefix="422020")]}},
    {"subtitle": {"subtitles": [public_track(host="evil.example")]}},
    {"cid": 101, "subtitle": {"subtitles": [public_track()]}},
])
def test_uncertain_public_subtitle_checks_never_trigger_asr(monkeypatch, dm_data):
    session = Session(player_data={"need_login_subtitle": True, "subtitle": {"subtitles": []}},
                      dm_data=dm_data)
    monkeypatch.setattr("linkread_desktop.pipeline.extract_bilibili",
                        lambda link, **options: extract_bilibili(link, session=session, **options))
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video",
                        lambda *args, **kwargs: pytest.fail("Unverified subtitles must not trigger ASR"))
    with pytest.raises(ExtractionError):
        extract("https://www.bilibili.com/video/BV1rxqmBhE91?p=2", asr=AsrConfig())
    assert len(session.calls) == 3


@pytest.mark.parametrize("caption", [
    Response(text='{"body": []}'),
    Response(text='{"body": [{"from": "bad", "to": 1, "content": "字幕"}]}'),
    Response(status_code=302),
    Response(status_code=206, text='{"body": []}'),
])
def test_invalid_caption_download_never_triggers_asr(monkeypatch, caption):
    session = Session(subtitles=False, dm_data={"subtitle": {"subtitles": [public_track()]}}, caption=caption)
    monkeypatch.setattr("linkread_desktop.pipeline.extract_bilibili",
                        lambda link, **options: extract_bilibili(link, session=session, **options))
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video",
                        lambda *args, **kwargs: pytest.fail("A bad caption response must not trigger ASR"))
    with pytest.raises(ExtractionError):
        extract("https://www.bilibili.com/video/BV1rxqmBhE91?p=2", asr=AsrConfig())

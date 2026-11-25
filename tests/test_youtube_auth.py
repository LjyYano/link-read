"""YouTube bot verification diagnostics and explicitly opted-in cookie authentication."""

import io
import sys
import types

import pytest

from linkread_desktop.generation import generate_article
from linkread_desktop.errors import ExtractionError, LinkReadError, UnsupportedSourceError
from linkread_desktop.extractors.youtube import extract_youtube
from linkread_desktop.pipeline import extract


class FakeYoutubeDL:
    options: list[dict] = []
    fail_auth = False

    def __init__(self, opts):
        self.options.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def extract_info(self, url, download=False):
        assert not download
        if self.fail_auth:
            raise RuntimeError(
                "ERROR: [youtube] Sign in to confirm you’re not a bot. "
                "Use --cookies-from-browser or --cookies for authentication."
            )
        return {
            "title": "Video", "id": "1-Fo8McCD9w",
            "duration": 60,
            "subtitles": {"en": [{"url": "https://captions.example/en.vtt", "ext": "vtt"}]},
        }

    def urlopen(self, url):
        return io.BytesIO(b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello world\n")


@pytest.fixture(autouse=True)
def fake_ytdlp(monkeypatch):
    FakeYoutubeDL.options = []
    FakeYoutubeDL.fail_auth = False
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))


def test_no_implicit_browser_cookie_access():
    doc = extract_youtube("https://www.youtube.com/watch?v=1-Fo8McCD9w")
    assert doc.segments[0].text == "Hello world"
    assert "cookiesfrombrowser" not in FakeYoutubeDL.options[-1]
    assert "cookiefile" not in FakeYoutubeDL.options[-1]


@pytest.mark.parametrize("browser", ["chrome", "safari", "firefox", "edge", "brave"])
def test_explicit_browser_cookie_forwarded(browser):
    extract_youtube("https://www.youtube.com/watch?v=1-Fo8McCD9w", cookies_from_browser=browser)
    opts = FakeYoutubeDL.options[-1]
    assert opts["cookiesfrombrowser"] == (browser, None, None, None)
    assert "cookiefile" not in opts
    assert "logger" in opts


def test_browser_normalized_case():
    extract_youtube("https://www.youtube.com/watch?v=1-Fo8McCD9w", cookies_from_browser="Chrome")
    assert FakeYoutubeDL.options[-1]["cookiesfrombrowser"][0] == "chrome"


def test_cookie_file_explicitly_forwarded(tmp_path):
    cookie = tmp_path / "youtube-cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    extract_youtube("https://www.youtube.com/watch?v=1-Fo8McCD9w", cookies_file=cookie)
    assert FakeYoutubeDL.options[-1]["cookiefile"] == str(cookie.resolve())
    assert "cookiesfrombrowser" not in FakeYoutubeDL.options[-1]


def test_rejects_missing_or_unsupported_cookies(tmp_path):
    with pytest.raises(ExtractionError, match="只能选择一种"):
        extract_youtube(
            "https://www.youtube.com/watch?v=1-Fo8McCD9w",
            cookies_file=tmp_path / "missing.txt", cookies_from_browser="chrome",
        )
    with pytest.raises(ExtractionError, match="不支持该浏览器"):
        extract_youtube("https://www.youtube.com/watch?v=1-Fo8McCD9w", cookies_from_browser="unsupported")
    with pytest.raises(ExtractionError, match="Cookie 文件不存在"):
        extract_youtube("https://www.youtube.com/watch?v=1-Fo8McCD9w", cookies_file=tmp_path / "missing.txt")
    assert FakeYoutubeDL.options == []


def test_youtube_antibot_diagnostic_does_not_dump_raw_error_or_start_asr(capsys, monkeypatch):
    from linkread_desktop.asr.config import AsrConfig

    FakeYoutubeDL.fail_auth = True
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video",
                        lambda *args, **kwargs: pytest.fail("Login verification must not trigger ASR"))
    with pytest.raises(ExtractionError, match="反机器人风控") as ex:
        extract("https://www.youtube.com/watch?v=1-Fo8McCD9w", asr=AsrConfig())
    assert ex.value.code == "youtube_verification_required"
    assert "--cookies-from-browser chrome" in str(ex.value)
    assert "Sign in to confirm" not in str(ex.value)
    assert capsys.readouterr().err == ""


def test_pipeline_forwards_auth_explicitly(monkeypatch, tmp_path):
    captured = {}
    def fake_extract(url, **kwargs):
        captured.update(kwargs)
        return "ok"
    monkeypatch.setattr("linkread_desktop.pipeline.extract_youtube", fake_extract)
    assert extract("https://youtu.be/1-Fo8McCD9w?si=xxx", cookies_from_browser="firefox") == "ok"
    assert captured["cookies_from_browser"] == "firefox"
    assert captured["progress"] is None
    assert "cookies_file" not in captured

    with pytest.raises(UnsupportedSourceError, match="仅适用于 YouTube"):
        extract("https://www.bilibili.com/video/BV1rxqmBhE91", cookies_from_browser="firefox")
    local = tmp_path / "test.srt"
    local.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n")
    with pytest.raises(UnsupportedSourceError, match="仅适用于 YouTube"):
        extract(str(local), cookies_file=tmp_path / "anything.txt")


def test_generation_forwards_explicit_auth(monkeypatch, tmp_path):
    seen = {}
    def fake_extract(source, *, progress, **opts):
        seen.update(opts)
        raise LinkReadError("stop before AI")
    monkeypatch.setattr("linkread_desktop.generation.extract_document", fake_extract)
    with pytest.raises(LinkReadError, match="stop before AI"):
        generate_article("https://www.youtube.com/watch?v=1-Fo8McCD9w",
                         types.SimpleNamespace(name="codex"), library=tmp_path,
                         cookies_from_browser="edge")
    assert seen == {"cookies_from_browser": "edge"}

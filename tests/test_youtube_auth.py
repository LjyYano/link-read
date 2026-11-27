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
    assert ex.value.action == "settings"
    assert "--cookies-from-browser chrome" in str(ex.value)
    assert "Sign in to confirm" not in str(ex.value)
    assert capsys.readouterr().err == ""


def test_youtube_cookie_read_error_prompts_settings_and_never_starts_asr(monkeypatch):
    from linkread_desktop.asr.config import AsrConfig

    class BrokenCookieYoutubeDL(FakeYoutubeDL):
        def extract_info(self, url, download=False):
            raise RuntimeError("ERROR: Could not copy Chrome cookie database: Operation not permitted")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=BrokenCookieYoutubeDL))
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video",
                        lambda *args, **kwargs: pytest.fail("Cookie read error must not trigger ASR"))
    with pytest.raises(ExtractionError) as ex:
        extract("https://www.youtube.com/watch?v=1-Fo8McCD9w", cookies_from_browser="chrome", asr=AsrConfig())
    assert ex.value.code == "youtube_cookies_permission_denied"
    assert ex.value.action == "settings"


def test_youtube_cookie_error_classifications_and_no_false_positive(monkeypatch, tmp_path):
    class SafariBlockedYoutubeDL(FakeYoutubeDL):
        def extract_info(self, url, download=False):
            raise RuntimeError("ERROR: could not find safari cookies database")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=SafariBlockedYoutubeDL))
    with pytest.raises(ExtractionError) as ex:
        extract_youtube("https://www.youtube.com/watch?v=1-Fo8McCD9w", cookies_from_browser="safari")
    assert ex.value.code == "youtube_cookies_safari_permission"
    assert "完全磁盘访问权限" in str(ex.value)

    class MissingChromeYoutubeDL(FakeYoutubeDL):
        def extract_info(self, url, download=False):
            raise RuntimeError("ERROR: could not find chrome cookies database in '/tmp/none'")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=MissingChromeYoutubeDL))
    with pytest.raises(ExtractionError) as ex:
        extract_youtube("https://www.youtube.com/watch?v=1-Fo8McCD9w", cookies_from_browser="chrome")
    assert ex.value.code == "youtube_cookies_not_found"

    # Remote YouTube error merely mentioning --cookies must not be misclassified as local cookie load error.
    class RemoteYoutubeDL(FakeYoutubeDL):
        def extract_info(self, url, download=False):
            raise RuntimeError("ERROR: [youtube] Video unavailable. See docs on cookies for details.")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=RemoteYoutubeDL))
    with pytest.raises(ExtractionError) as ex:
        extract_youtube("https://www.youtube.com/watch?v=1-Fo8McCD9w", cookies_from_browser="chrome")
    assert ex.value.code == "extraction_failed"

    # Explicit test_youtube_cookies verifies youtube.com session without leaking cookie values.
    from linkread_desktop.extractors.youtube import test_youtube_cookies

    class CookieJarYoutubeDL(FakeYoutubeDL):
        jar = []

        @property
        def cookiejar(self):
            return self.jar

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=CookieJarYoutubeDL))
    CookieJarYoutubeDL.jar = [types.SimpleNamespace(domain=".example.com", value="secret-val")]
    with pytest.raises(ExtractionError) as ex:
        test_youtube_cookies(cookies_from_browser="chrome")
    assert ex.value.code == "youtube_cookies_empty"
    assert "secret-val" not in str(ex.value)

    CookieJarYoutubeDL.jar = [
        types.SimpleNamespace(domain=".youtube.com", name="VISITOR_INFO1_LIVE", value="anon-val"),
        types.SimpleNamespace(domain=".youtube.com", name="PREF", value="pref-val"),
    ]
    with pytest.raises(ExtractionError) as ex:
        test_youtube_cookies(cookies_from_browser="chrome")
    assert ex.value.code == "youtube_cookies_empty"
    assert "anon-val" not in str(ex.value)

    CookieJarYoutubeDL.jar = [
        types.SimpleNamespace(domain=".youtube.com", name="LOGIN_INFO", value="secret-yt"),
        types.SimpleNamespace(domain=".youtube.com", name="SAPISID", value="secret-sid"),
    ]
    result = test_youtube_cookies(cookies_from_browser="chrome")
    assert result["ok"] is True
    assert "secret-yt" not in str(result)
    assert "secret-sid" not in str(result)


def test_missing_cookie_file_returns_cookie_unavailable_code(tmp_path):
    with pytest.raises(ExtractionError) as ex:
        extract_youtube("https://www.youtube.com/watch?v=1-Fo8McCD9w", cookies_file=tmp_path / "absent.txt")
    assert ex.value.code == "youtube_cookies_unavailable"
    assert ex.value.action == "settings"


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


def test_desktop_download_and_export_subtitles_with_explicit_cookies(tmp_path, monkeypatch, capsys):
    import json
    from linkread_desktop import desktop

    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "linkread", "--library", str(tmp_path / "library"),
        "download-subtitles", "--cookies", str(cookie_file),
        "--", "https://www.youtube.com/watch?v=1-Fo8McCD9w",
    ])
    assert desktop.main() == 0
    downloaded = json.loads(capsys.readouterr().out)
    assert downloaded["type"] == "subtitles_downloaded"
    assert downloaded["segmentCount"] == 1
    assert downloaded["metadata"]["title"] == "Video"
    assert FakeYoutubeDL.options[-1]["cookiefile"] == str(cookie_file.resolve())

    # Export SRT directly from the downloaded preview without touching YouTube again.
    FakeYoutubeDL.fail_auth = True
    monkeypatch.setattr(sys, "argv", [
        "linkread", "--library", str(tmp_path / "library"),
        "export-subtitles", "--format", "srt", "--preview",
        "--", downloaded["previewId"],
    ])
    assert desktop.main() == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["type"] == "subtitles"
    assert "00:00:01,000 --> 00:00:02,000\nHello world" in exported["text"]


def test_swallowed_permission_error_on_browser_root_classified_as_permission_denied(monkeypatch, tmp_path):
    import os
    from linkread_desktop.extractors.youtube import test_youtube_cookies

    fake_edge = tmp_path / "Microsoft Edge"
    fake_edge.mkdir()

    class SwallowedRootPermissionYoutubeDL(FakeYoutubeDL):
        @property
        def cookiejar(self):
            raise FileNotFoundError(f'could not find edge cookies database in "{fake_edge}"')

        def extract_info(self, url, download=False):
            raise FileNotFoundError(f'could not find edge cookies database in "{fake_edge}"')

    real_scandir = os.scandir

    def fake_scandir(path):
        if os.fspath(path) == str(fake_edge):
            raise PermissionError(1, "Operation not permitted")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=SwallowedRootPermissionYoutubeDL))

    for call in (
        lambda: test_youtube_cookies(cookies_from_browser="edge"),
        lambda: extract_youtube("https://www.youtube.com/watch?v=1-Fo8McCD9w", cookies_from_browser="edge"),
    ):
        with pytest.raises(ExtractionError) as ex:
            call()
        assert ex.value.code == "youtube_cookies_permission_denied"
        assert ex.value.action == "settings"
        assert "Microsoft Edge" in str(ex.value)
        assert str(fake_edge) not in str(ex.value)


def test_swallowed_permission_error_on_profile_subdir_classified_as_permission_denied(monkeypatch, tmp_path):
    import os
    from linkread_desktop.extractors.youtube import test_youtube_cookies

    fake_edge = tmp_path / "Microsoft Edge"
    default_dir = fake_edge / "Default"
    default_dir.mkdir(parents=True)

    class SwallowedProfilePermissionYoutubeDL(FakeYoutubeDL):
        @property
        def cookiejar(self):
            raise FileNotFoundError(f'could not find edge cookies database in "{fake_edge}"')

    real_scandir = os.scandir

    def fake_scandir(path):
        if os.fspath(path) == str(default_dir):
            raise PermissionError(1, "Operation not permitted")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=SwallowedProfilePermissionYoutubeDL))

    with pytest.raises(ExtractionError) as ex:
        test_youtube_cookies(cookies_from_browser="edge")
    assert ex.value.code == "youtube_cookies_permission_denied"
    assert ex.value.action == "settings"
    assert str(fake_edge) not in str(ex.value)


def test_missing_or_empty_readable_browser_dir_remains_not_found(monkeypatch, tmp_path):
    from linkread_desktop.extractors.youtube import test_youtube_cookies

    absent_edge = tmp_path / "AbsentEdge"
    empty_edge = tmp_path / "EmptyEdge"
    empty_edge.mkdir()

    for target in (absent_edge, empty_edge):
        class MissingDirYoutubeDL(FakeYoutubeDL):
            @property
            def cookiejar(self):
                raise FileNotFoundError(f'could not find edge cookies database in "{target}"')

        monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=MissingDirYoutubeDL))
        with pytest.raises(ExtractionError) as ex:
            test_youtube_cookies(cookies_from_browser="edge")
        assert ex.value.code == "youtube_cookies_not_found"
        assert ex.value.action == "settings"
        assert str(target) not in str(ex.value)


def test_keychain_denial_during_cookie_test_not_misclassified_as_empty(monkeypatch):
    from linkread_desktop.extractors.youtube import test_youtube_cookies

    class KeychainDeniedYoutubeDL(FakeYoutubeDL):
        def __init__(self, opts):
            super().__init__(opts)
            self._logger = opts["logger"]

        @property
        def cookiejar(self):
            self._logger.warning("find-generic-password failed")
            self._logger.warning("cannot decrypt v10 cookies: no key found")
            return []

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=KeychainDeniedYoutubeDL))
    with pytest.raises(ExtractionError) as ex:
        test_youtube_cookies(cookies_from_browser="edge")
    assert ex.value.code == "youtube_cookies_permission_denied"
    assert ex.value.action == "settings"
    assert "Microsoft Edge" in str(ex.value)


def test_english_permission_denied_message_mentions_disk_and_keychain(monkeypatch):
    from linkread_desktop.messages import localize_event

    monkeypatch.setenv("LINKREAD_UI_LANGUAGE", "en")
    localized = localize_event("error", {
        "code": "youtube_cookies_permission_denied",
        "message": "raw error",
    })
    assert "Full Disk Access" in localized["message"]
    assert "Keychain" in localized["message"]


def test_unencrypted_youtube_cookie_does_not_mask_keychain_denial(monkeypatch):
    from linkread_desktop.extractors.youtube import test_youtube_cookies

    class PartialDecryptYoutubeDL(FakeYoutubeDL):
        def __init__(self, opts):
            super().__init__(opts)
            self._logger = opts["logger"]

        @property
        def cookiejar(self):
            self._logger.warning("find-generic-password failed")
            self._logger.warning("cannot decrypt v10 cookies: no key found")
            return [types.SimpleNamespace(domain=".youtube.com", name="VISITOR_INFO1_LIVE", value="plain")]

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=PartialDecryptYoutubeDL))
    with pytest.raises(ExtractionError) as ex:
        test_youtube_cookies(cookies_from_browser="edge")
    assert ex.value.code == "youtube_cookies_permission_denied"
    assert ex.value.action == "settings"
    assert "Microsoft Edge" in str(ex.value)


def test_expired_chromium_webkit_auth_cookies_rejected_and_future_accepted(monkeypatch):
    import time
    from linkread_desktop.extractors.youtube import test_youtube_cookies

    class JarYoutubeDL(FakeYoutubeDL):
        jar = []

        @property
        def cookiejar(self):
            return self.jar

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=JarYoutubeDL))
    # 13_300_000_000_000_000 in Chromium WebKit microseconds is ~2022 (expired).
    JarYoutubeDL.jar = [
        types.SimpleNamespace(domain=".youtube.com", name="LOGIN_INFO", value="old", expires=13_300_000_000_000_000),
        types.SimpleNamespace(domain=".youtube.com", name="SAPISID", value="old-sid", expires=13_300_000_000_000_000),
    ]
    with pytest.raises(ExtractionError) as ex:
        test_youtube_cookies(cookies_from_browser="edge")
    assert ex.value.code == "youtube_cookies_empty"

    future_webkit = int((time.time() + 86400 + 11_644_473_600) * 1_000_000)
    JarYoutubeDL.jar = [
        types.SimpleNamespace(domain=".youtube.com", name="LOGIN_INFO", value="fresh-tok", expires=future_webkit),
        types.SimpleNamespace(domain=".youtube.com", name="__Secure-3PAPISID", value="fresh-sid", expires=future_webkit),
    ]
    result = test_youtube_cookies(cookies_from_browser="edge")
    assert result["ok"] is True
    assert "fresh-tok" not in str(result)


def test_extract_youtube_distinguishes_configured_cookie_failures(monkeypatch):
    class BotCheckYoutubeDL(FakeYoutubeDL):
        scenario = "keychain"

        def __init__(self, opts):
            super().__init__(opts)
            self._logger = opts["logger"]

        def extract_info(self, url, download=False):
            if self.scenario == "keychain":
                self._logger.warning("find-generic-password failed")
                self._logger.warning("cannot decrypt v10 cookies: no key found")
                self.cookiejar = []
            elif self.scenario == "empty":
                self.cookiejar = [
                    types.SimpleNamespace(domain=".youtube.com", name="VISITOR_INFO1_LIVE", value="anon"),
                ]
            elif self.scenario == "expired":
                self.cookiejar = [
                    types.SimpleNamespace(domain=".youtube.com", name="LOGIN_INFO", value="tok"),
                    types.SimpleNamespace(domain=".youtube.com", name="SAPISID", value="sid"),
                ]
            elif self.scenario == "rotated":
                self.cookiejar = [
                    types.SimpleNamespace(domain=".youtube.com", name="LOGIN_INFO", value="tok"),
                    types.SimpleNamespace(domain=".youtube.com", name="SAPISID", value="sid"),
                ]
                self._logger.warning(
                    "The provided YouTube account cookies are no longer valid. "
                    "They have likely been rotated in the browser as a security measure."
                )
            raise RuntimeError("ERROR: [youtube] O_zcc6Xl12g: Sign in to confirm you’re not a bot.")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=BotCheckYoutubeDL))

    BotCheckYoutubeDL.scenario = "keychain"
    with pytest.raises(ExtractionError) as ex:
        extract_youtube("https://www.youtube.com/watch?v=O_zcc6Xl12g", cookies_from_browser="edge")
    assert ex.value.code == "youtube_cookies_permission_denied"
    assert ex.value.action == "settings"

    BotCheckYoutubeDL.scenario = "empty"
    with pytest.raises(ExtractionError) as ex:
        extract_youtube("https://www.youtube.com/watch?v=O_zcc6Xl12g", cookies_from_browser="edge")
    assert ex.value.code == "youtube_cookies_empty"
    assert ex.value.action == "settings"

    for scenario in ("expired", "rotated"):
        BotCheckYoutubeDL.scenario = scenario
        with pytest.raises(ExtractionError) as ex:
            extract_youtube("https://www.youtube.com/watch?v=O_zcc6Xl12g", cookies_from_browser="edge")
        assert ex.value.code == "youtube_cookies_expired"
        assert ex.value.action == "settings"


def test_english_expired_cookie_message_in_error_and_source_check(monkeypatch):
    from linkread_desktop.messages import localize_event

    monkeypatch.setenv("LINKREAD_UI_LANGUAGE", "en")
    for kind, extra in (("error", {}), ("source_check", {"status": "unknown"})):
        localized = localize_event(kind, {
            "code": "youtube_cookies_expired",
            "message": "raw error",
            **extra,
        })
        assert "YouTube rejected the configured cookies" in localized["message"]
        assert "proxy/network" in localized["message"]

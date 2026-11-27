"""YouTube subtitle extraction via yt-dlp (no audio/video download)."""

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import time
from urllib.parse import parse_qs, urlsplit

from ..errors import ExtractionError, SubtitleUnavailableError
from ..links import canonical_link
from ..models import SourceInfo, TranscriptDocument
from ..parsers import has_rolling_caption_markers, parse_srt_vtt
from ..progress import ProgressCallback, report


# Authentication is explicitly opt-in. Never silently read local browser sessions.
_SUPPORTED_BROWSERS = frozenset({
    "brave", "chrome", "chromium", "edge", "firefox", "opera",
    "safari", "vivaldi", "whale",
})
_BROWSER_LABELS = {
    "brave": "Brave",
    "chrome": "Google Chrome",
    "chromium": "Chromium",
    "edge": "Microsoft Edge",
    "firefox": "Firefox",
    "opera": "Opera",
    "safari": "Safari",
    "vivaldi": "Vivaldi",
    "whale": "Whale",
}
_CAPTION_FIELDS = ("subtitles", "automatic_captions")
_YOUTUBE_AUTH_SID_COOKIES = frozenset({"SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"})
_CHROME_EPOCH_OFFSET_SECONDS = 11_644_473_600
_CHROME_TIMESTAMP_THRESHOLD = 10**15


class _QuietLogger:
    """Prevent yt-dlp from printing duplicate raw errors (or sensitive paths)."""

    def debug(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


class _CookieDiagnosticLogger(_QuietLogger):
    """Record boolean cookie-access failure signals without storing paths or values."""

    def __init__(self) -> None:
        self.permission_denied = False
        self.keychain_denied = False
        self.decrypt_failed = False
        self.cookies_invalidated = False

    def _inspect_cookie_message(self, message: str) -> None:
        reason = str(message).lower()
        if any(marker in reason for marker in (
            "find-generic-password", "keychain", "keyring", "secretstorage",
        )):
            self.keychain_denied = True
        if "cannot decrypt" in reason or "failed to decrypt" in reason:
            self.decrypt_failed = True
        if "operation not permitted" in reason or "permission denied" in reason:
            self.permission_denied = True
        if "cookies are no longer valid" in reason or "likely been rotated" in reason:
            self.cookies_invalidated = True

    def warning(self, message: str) -> None:
        self._inspect_cookie_message(message)

    def error(self, message: str) -> None:
        self._inspect_cookie_message(message)


class _CaptionLookupLogger(_CookieDiagnosticLogger):
    """Retain failure signals when yt-dlp omits tracks from its result."""

    def __init__(self) -> None:
        super().__init__()
        self.incomplete = False

    def debug(self, message: str) -> None:
        reason = message.lower()
        if any(marker in reason for marker in (
            "subtitles require a po token", "missing subtitles languages",
            "no supported javascript runtime", "signature solving failed", "challenge solving failed",
        )):
            self.incomplete = True

    def warning(self, message: str) -> None:
        super().warning(message)
        self.debug(message)

    def error(self, message: str) -> None:
        super().error(message)


def _cookie_options(
    cookies_from_browser: str | None, cookies_file: Path | None,
) -> dict:
    if cookies_from_browser is not None and cookies_file is not None:
        raise ExtractionError("--cookies-from-browser 和 --cookies 只能选择一种")
    if cookies_from_browser is not None:
        browser = cookies_from_browser.strip().lower()
        if browser not in _SUPPORTED_BROWSERS:
            raise ExtractionError(
                "不支持该浏览器；可选 chrome、edge、firefox、safari、brave 等",
                code="youtube_cookies_unavailable", action="settings",
            )
        # yt-dlp's Python API expects (browser, profile, keyring, container).
        return {"cookiesfrombrowser": (browser, None, None, None)}
    if cookies_file is not None:
        path = cookies_file.expanduser().resolve()
        if not path.is_file():
            raise ExtractionError(
                "Cookie 文件不存在或不可读取；请检查 --cookies 路径",
                code="youtube_cookies_unavailable", action="settings",
            )
        return {"cookiefile": str(path)}
    return {}


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _browser_label(cookies_from_browser: str | None) -> str:
    browser = cookies_from_browser.strip().lower() if cookies_from_browser else None
    if not browser:
        return "所选浏览器"
    return _BROWSER_LABELS.get(browser, browser.capitalize())


def _cookie_permission_error(cookies_from_browser: str | None) -> ExtractionError:
    browser_label = _browser_label(cookies_from_browser)
    return ExtractionError(
        f"系统拒绝读取 {browser_label} 的 Cookie（macOS 权限受限）。"
        "请在「系统设置 → 隐私与安全性 → 完全磁盘访问权限 / App 数据」中授权 LinkRead（或运行 dev.sh 的终端，授权后需重启该终端），"
        "并允许钥匙串访问，或在设置中切换浏览器 / 使用 cookies.txt。",
        code="youtube_cookies_permission_denied", action="settings",
    )


def _cookie_empty_error() -> ExtractionError:
    return ExtractionError(
        "已读取 Cookie，但其中未找到有效的 youtube.com 登录会话。请先在该浏览器登录 YouTube（或完全退出并重启浏览器以刷新本地 Cookie），或重新导出 cookies.txt。",
        code="youtube_cookies_empty", action="settings",
    )


def _cookie_expired_error() -> ExtractionError:
    return ExtractionError(
        "YouTube 拒绝了当前配置的 Cookie（浏览器会话可能已过期/被轮换，或当前代理网络触发了人机验证）。请重启浏览器确认已登录 YouTube，或在隐私窗口重新导出 cookies.txt / 切换代理节点后重试。",
        code="youtube_cookies_expired", action="settings",
    )


def _is_youtube_cookie_domain(domain: str | None) -> bool:
    host = (domain or "").strip().lower().lstrip(".")
    return host == "youtube.com" or host.endswith(".youtube.com")


def _cookie_expired(cookie: object, *, now: float) -> bool:
    expires = getattr(cookie, "expires", None)
    if expires is None or isinstance(expires, bool):
        return False
    try:
        numeric = float(expires)
    except (TypeError, ValueError):
        return False
    if numeric <= 0:
        return False
    if numeric > _CHROME_TIMESTAMP_THRESHOLD:
        numeric = numeric / 1_000_000.0 - _CHROME_EPOCH_OFFSET_SECONDS
    return numeric <= now


def _has_youtube_auth_cookies(jar: object, *, now: float | None = None) -> bool:
    """Match yt-dlp's YoutubeBaseInfoExtractor._has_auth_cookies for unexpired login cookies."""
    if jar is None:
        return False
    current = time.time() if now is None else now
    names: set[str] = set()
    for cookie in jar:
        if not _is_youtube_cookie_domain(getattr(cookie, "domain", None)):
            continue
        name = getattr(cookie, "name", None)
        value = getattr(cookie, "value", None)
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(value, (str, bytes)) or not value:
            continue
        if _cookie_expired(cookie, now=current):
            continue
        names.add(name)
    return "LOGIN_INFO" in names and bool(names & _YOUTUBE_AUTH_SID_COOKIES)


def _configured_cookie_auth_error(
    lookup: _CookieDiagnosticLogger, loaded_jar: object, cookies_from_browser: str | None,
) -> ExtractionError:
    if lookup.permission_denied or lookup.keychain_denied or lookup.decrypt_failed:
        return _cookie_permission_error(cookies_from_browser)
    if lookup.cookies_invalidated:
        return _cookie_expired_error()
    if loaded_jar is not None and not _has_youtube_auth_cookies(loaded_jar):
        return _cookie_empty_error()
    return _cookie_expired_error()


def _extract_cookie_search_dirs(chain: list[BaseException]) -> list[Path]:
    marker = "cookies database in "
    dirs: list[Path] = []
    seen: set[Path] = set()
    for item in chain:
        text = str(item)
        index = text.lower().find(marker)
        if index < 0:
            continue
        tail = text[index + len(marker):]
        for _, raw_path in re.findall(r"""(["'])([^"']+)\1""", tail):
            candidate = Path(raw_path).expanduser()
            if candidate not in seen:
                seen.add(candidate)
                dirs.append(candidate)
    return dirs


def _has_permission_error_in_search_dirs(dirs: list[Path], *, browser: str | None) -> bool:
    """Detect OS directory-listing denials swallowed by yt-dlp's os.walk/glob without reading files."""
    for root in dirs:
        try:
            with os.scandir(root) as entries:
                for count, entry in enumerate(entries):
                    if count >= 256:
                        break
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except PermissionError:
                        return True
                    except OSError:
                        continue
                    if not is_dir:
                        continue
                    is_profile = (
                        browser == "firefox"
                        or entry.name in {"Default", "Network", "Guest Profile", "System Profile"}
                        or entry.name.startswith("Profile ")
                    )
                    if not is_profile:
                        continue
                    try:
                        with os.scandir(entry.path):
                            pass
                    except PermissionError:
                        return True
                    except OSError:
                        continue
                    if browser != "firefox" and entry.name != "Network":
                        try:
                            with os.scandir(Path(entry.path) / "Network"):
                                pass
                        except PermissionError:
                            return True
                        except OSError:
                            pass
        except PermissionError:
            return True
        except OSError:
            continue
    return False


def _classify_cookie_load_error(
    exc: BaseException, *, cookies_from_browser: str | None = None, cookies_file: Path | None = None,
    logger: _CookieDiagnosticLogger | None = None,
) -> ExtractionError | None:
    """Distinguish local cookie-loading failures safely without leaking paths or values."""
    chain = _exception_chain(exc)
    names = {type(item).__name__ for item in chain}
    combined = " ".join(str(item).lower() for item in chain if str(item))
    has_logger_permission = logger is not None and (
        logger.permission_denied or logger.keychain_denied or logger.decrypt_failed
    )
    is_cookie_load = (
        "CookieLoadError" in names
        or "LoadError" in names
        or any(isinstance(item, (PermissionError, FileNotFoundError)) for item in chain)
        or any(phrase in combined for phrase in (
            "failed to load cookies",
            "could not find ",
            "could not copy ",
            "netscape format cookies",
            "operation not permitted",
            "permission denied",
            "find-generic-password",
            "keychain",
            "keyring",
            "secretstorage",
            "cannot decrypt",
            "failed to decrypt",
        ))
    )
    if not is_cookie_load:
        return None
    browser = cookies_from_browser.strip().lower() if cookies_from_browser else None
    browser_label = _browser_label(cookies_from_browser)
    is_permission = (
        any(isinstance(item, PermissionError) for item in chain)
        or "operation not permitted" in combined
        or "permission denied" in combined
        or has_logger_permission
    )
    is_not_found = (
        any(isinstance(item, FileNotFoundError) for item in chain)
        or "could not find " in combined
    )
    if browser == "safari" and (is_permission or is_not_found):
        return ExtractionError(
            "无法读取 Safari Cookie（macOS 默认限制读取 Safari 容器目录）。"
            "请在「系统设置 → 隐私与安全性 → 完全磁盘访问权限」授权应用或启动终端，或在设置中改选 Chrome / 使用 cookies.txt。",
            code="youtube_cookies_safari_permission", action="settings",
        )
    if not is_permission and is_not_found and browser and browser != "safari":
        search_dirs = _extract_cookie_search_dirs(chain)
        if search_dirs and _has_permission_error_in_search_dirs(search_dirs, browser=browser):
            is_permission = True
    if is_permission:
        return _cookie_permission_error(cookies_from_browser)
    if cookies_file is not None and ("netscape" in combined or "LoadError" in names):
        return ExtractionError(
            "所选文件不是有效的 Netscape 格式 cookies.txt，请重新导出后在设置中选择。",
            code="youtube_cookies_format_invalid", action="settings",
        )
    if is_not_found:
        return ExtractionError(
            f"未在本机找到 {browser_label} 的 Cookie 数据库，请确认已安装并登录过该浏览器，或在设置中改选其他浏览器 / cookies.txt。",
            code="youtube_cookies_not_found", action="settings",
        )
    return ExtractionError(
        "无法读取所配置的 YouTube Cookie（数据库可能被占用或格式异常），请稍后重试、切换浏览器或改用 cookies.txt。",
        code="youtube_cookies_unavailable", action="settings",
    )


def test_youtube_cookies(
    *, cookies_from_browser: str | None = None, cookies_file: Path | None = None,
) -> dict:
    """Explicitly test reading local YouTube cookies without network calls or leaking values."""
    if cookies_from_browser is None and cookies_file is None:
        raise ExtractionError(
            "请先选择浏览器或 cookies.txt 文件",
            code="youtube_cookies_unavailable", action="settings",
        )
    cookie_options = _cookie_options(cookies_from_browser, cookies_file)
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise ExtractionError("读取 YouTube Cookie 需要 yt-dlp，请检查桌面版后端是否完整安装") from exc
    diagnostic_logger = _CookieDiagnosticLogger()
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "logger": diagnostic_logger,
        **cookie_options,
    }
    try:
        with YoutubeDL(options) as ydl:
            jar = ydl.cookiejar
            has_youtube_auth = _has_youtube_auth_cookies(jar)
    except ExtractionError:
        raise
    except Exception as exc:
        classified = _classify_cookie_load_error(
            exc, cookies_from_browser=cookies_from_browser, cookies_file=cookies_file,
            logger=diagnostic_logger,
        )
        if classified is not None:
            raise classified from exc
        raise ExtractionError(
            "无法读取所配置的 YouTube Cookie，请在设置中检查浏览器访问权限或重新选择 cookies.txt 文件。",
            code="youtube_cookies_unavailable", action="settings",
        ) from exc
    if not has_youtube_auth:
        if (
            diagnostic_logger.permission_denied
            or diagnostic_logger.keychain_denied
            or diagnostic_logger.decrypt_failed
        ):
            raise _cookie_permission_error(cookies_from_browser)
        raise _cookie_empty_error()
    return {"ok": True, "message": "已读取到 YouTube 登录 Cookie（仅本地检查，实际有效性以 YouTube 验证为准）"}


def _language_rank(language: str) -> int:
    key = language.lower()
    if key in {"zh-hans", "zh-cn", "zh"}:
        return 0
    if key.startswith("zh"):
        return 1
    if key in {"en", "en-us", "en-gb"}:
        return 2
    if key.startswith("en"):
        return 3
    return 4


def _choose_caption(info: dict) -> tuple[str, dict, bool] | None:
    candidates = []
    declared = info.get("language")
    source = declared.lower().split("-")[0] if isinstance(declared, str) else ""
    for is_auto, field in enumerate(_CAPTION_FIELDS):
        catalog = info.get(field)
        if not isinstance(catalog, dict):
            continue
        for language, tracks in catalog.items():
            if not isinstance(language, str) or language == "live_chat" or not isinstance(tracks, list):
                continue
            for track in tracks:
                if not isinstance(track, dict) or not isinstance(track.get("ext"), str):
                    continue
                ext = track["ext"].lower()
                if ext in {"vtt", "srt"} and isinstance(track.get("url"), str) and track["url"].strip():
                    code = language.removesuffix("-orig")
                    query = parse_qs(urlsplit(track["url"]).query)
                    translated = bool(query.get("tlang")) and query.get("tlang") != query.get("lang")
                    original = language.endswith("-orig") or (source and code.lower().split("-")[0] == source)
                    # Keep the previous preference where there is no original
                    # language signal. A known machine-translated track is last.
                    rank = (translated, not original, _language_rank(code), is_auto, ext != "vtt")
                    candidates.append((rank, code, track, bool(is_auto)))
    if not candidates:
        return None
    _, language, track, is_auto = min(candidates, key=lambda item: item[0])
    return language, track, is_auto


def _source_info(info: dict, url: str) -> SourceInfo:
    from ..metadata import thumbnail_url
    upload_date = info.get("upload_date")
    published_at = None
    if upload_date:
        try:
            published_at = datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=timezone.utc).date().isoformat()
        except (TypeError, ValueError):
            pass
    duration = info.get("duration")
    description = info.get("description")
    description = description if isinstance(description, str) and description.strip() else None
    return SourceInfo(
        platform="youtube", title=info.get("title") or info.get("id") or "Untitled",
        url=info.get("webpage_url") or url, video_id=info.get("id"),
        author=info.get("channel") or info.get("uploader"), published_at=published_at,
        duration_ms=round(float(duration) * 1000) if duration is not None else None,
        thumbnail_url=thumbnail_url(info.get("thumbnail")), description=description,
        description_format="text" if description is not None else None,
    )


def youtube_metadata(url: str) -> SourceInfo:
    """Explicit description refresh; metadata only, never captions or media."""
    url = canonical_link(url)
    expected_id = url.rsplit("=", 1)[1]
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise ExtractionError("获取 YouTube 简介需要 yt-dlp，请检查桌面版后端是否完整安装") from exc
    options = {"quiet": True, "no_warnings": True, "noplaylist": True,
               "skip_download": True, "writesubtitles": False, "writeautomaticsub": False,
               "writethumbnail": False, "socket_timeout": 15, "retries": 1,
               "extractor_retries": 1, "logger": _QuietLogger()}
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if (not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}
                or info.get("id") != expected_id):
            raise ExtractionError("YouTube 返回的简介与当前视频不匹配")
        result = _source_info(info, url)
        # The known selected video, rather than a response-provided redirect,
        # remains the identity of this explicit metadata refresh.
        return result.model_copy(update={"url": url})
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError("暂时无法读取 YouTube 简介，请检查网络或稍后重试") from exc


def extract_youtube(
    url: str, *, progress: ProgressCallback | None = None,
    cookies_from_browser: str | None = None,
    cookies_file: Path | None = None,
) -> TranscriptDocument:
    url = canonical_link(url)
    expected_id = url.rsplit("=", 1)[1]
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise ExtractionError("提取 YouTube 字幕需要 yt-dlp，请检查桌面版后端是否完整安装") from exc
    cookie_options = _cookie_options(cookies_from_browser, cookies_file)
    lookup = _CaptionLookupLogger()
    loaded_jar = None
    try:
        if cookies_from_browser:
            report(progress, "authentication", f"已选择从 {cookies_from_browser.lower()} 读取 Cookie（仅本次请求）")
        elif cookies_file:
            report(progress, "authentication", "已选择本地 Cookie 文件（仅本次请求）")
        report(progress, "metadata", "正在获取 YouTube 视频元数据（首次访问可能较慢）")
        options = {
            "quiet": True,
            "verbose": True,  # yt-dlp reports some skipped subtitle tracks only at debug level.
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            # Only captions are read here. A signed-in session may receive no downloadable
            # media format, and yt-dlp's default format selection would then abort videos
            # whose subtitle tracks are available.
            "ignore_no_formats_error": True,
            "socket_timeout": 20,
            "retries": 2,
            "extractor_retries": 2,
            "logger": lookup,
        }
        options.update(cookie_options)
        with YoutubeDL(options) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            finally:
                if cookie_options and "cookiejar" in getattr(ydl, "__dict__", {}):
                    loaded_jar = ydl.__dict__.get("cookiejar")
            if (not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}
                    or info.get("id") != expected_id):
                raise ExtractionError("YouTube 返回的字幕来源与当前视频不匹配")
            source = _source_info(info, url).model_copy(update={"url": url})
            report(progress, "metadata_done", f"已获取视频：{source.title}", source=source)
            report(progress, "captions", "正在检查字幕列表")
            selected = _choose_caption(info)
            if selected is None:
                if info.get("availability") == "needs_auth":
                    if cookie_options:
                        raise _configured_cookie_auth_error(lookup, loaded_jar, cookies_from_browser)
                    raise ExtractionError(
                        "YouTube 视频需要登录访问，请在设置中配置 YouTube Cookie 后重试。",
                        code="youtube_verification_required", action="settings",
                    )
                if info.get("availability") in ("private", "premium_only", "subscriber_only"):
                    raise ExtractionError("YouTube 视频需要授权访问，请打开原视频查看。", code="source_permission_denied")
                catalog_empty = all(isinstance(info.get(field), dict) and not (info[field].keys() - {"live_chat"})
                                    for field in _CAPTION_FIELDS)
                if lookup.incomplete or not catalog_empty:
                    raise ExtractionError("YouTube 字幕列表不完整或包含无法读取的字幕轨，请重新检查来源。",
                                          code="youtube_subtitles_unconfirmed", retryable=True, action="retry")
                raise SubtitleUnavailableError("该 YouTube 视频没有公开字幕；可启用 ASR", source=source)
            language, track, is_auto = selected
            caption_type = "自动字幕" if is_auto else "人工字幕"
            report(progress, "captions_done", f"选定字幕：{language}（{caption_type} / {track['ext'].upper()}）")
            report(progress, "download", "正在下载字幕文本（不下载视频或音频）")
            with ydl.urlopen(track["url"]) as response:
                content = response.read().decode("utf-8-sig", errors="replace")
    except (ExtractionError, SubtitleUnavailableError):
        raise
    except Exception as exc:
        reason = str(exc).lower()
        is_verification = (
            "not a bot" in reason or "sign in to confirm" in reason
            or "sign in to verify" in reason or "confirm your age" in reason
            or "login required" in reason
        )
        if cookie_options and is_verification:
            raise _configured_cookie_auth_error(lookup, loaded_jar, cookies_from_browser) from exc
        if is_verification:
            raise ExtractionError(
                "YouTube 要求验证登录状态或触发了反机器人风控。"
                "请先前往设置配置 YouTube Cookie（选择已登录浏览器或 cookies.txt），或导入已有字幕。"
                "命令行可显式添加 --cookies-from-browser chrome（或 edge/firefox/safari）授权读取本机浏览器会话。",
                code="youtube_verification_required", action="settings",
            ) from exc
        if cookie_options:
            classified = _classify_cookie_load_error(
                exc, cookies_from_browser=cookies_from_browser, cookies_file=cookies_file,
                logger=lookup,
            )
            if classified is not None:
                raise classified from exc
        raise ExtractionError("YouTube 字幕提取失败，请检查来源或稍后重试。") from exc
    report(progress, "parse", "正在解析字幕时间戳和文本")
    try:
        segments = parse_srt_vtt(content, rolling=is_auto or has_rolling_caption_markers(content))
    except (ExtractionError, SubtitleUnavailableError) as exc:
        raise ExtractionError("YouTube 已列出字幕轨，但返回的字幕正文为空或格式无效。请重新检查来源。",
                              code="youtube_subtitle_invalid", retryable=True, action="retry") from exc
    report(progress, "parsed", f"字幕解析完成：{len(segments)} 个片段")
    return TranscriptDocument(
        source=source,
        transcript_source="subtitle",
        language=language,
        segments=segments,
    )

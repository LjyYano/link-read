"""YouTube subtitle extraction via yt-dlp (no audio/video download)."""

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ..errors import ExtractionError, SubtitleUnavailableError
from ..links import canonical_link
from ..models import SourceInfo, TranscriptDocument
from ..parsers import parse_srt_vtt
from ..progress import ProgressCallback, report


# Authentication is explicitly opt-in. Never silently read local browser sessions.
_SUPPORTED_BROWSERS = frozenset({
    "brave", "chrome", "chromium", "edge", "firefox", "opera",
    "safari", "vivaldi", "whale",
})
_CAPTION_FIELDS = ("subtitles", "automatic_captions")


class _QuietLogger:
    """Prevent yt-dlp from printing duplicate raw errors (or sensitive paths)."""

    def debug(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


class _CaptionLookupLogger(_QuietLogger):
    """Retain failure signals when yt-dlp omits tracks from its result."""

    def __init__(self) -> None:
        self.incomplete = False

    def debug(self, message: str) -> None:
        reason = message.lower()
        if any(marker in reason for marker in (
            "subtitles require a po token", "missing subtitles languages",
            "no supported javascript runtime", "signature solving failed", "challenge solving failed",
        )):
            self.incomplete = True

    def warning(self, message: str) -> None:
        self.debug(message)


def _cookie_options(
    cookies_from_browser: str | None, cookies_file: Path | None,
) -> dict:
    if cookies_from_browser is not None and cookies_file is not None:
        raise ExtractionError("--cookies-from-browser 和 --cookies 只能选择一种")
    if cookies_from_browser is not None:
        browser = cookies_from_browser.strip().lower()
        if browser not in _SUPPORTED_BROWSERS:
            raise ExtractionError(
                "不支持该浏览器；可选 chrome、edge、firefox、safari、brave 等"
            )
        # yt-dlp's Python API expects (browser, profile, keyring, container).
        return {"cookiesfrombrowser": (browser, None, None, None)}
    if cookies_file is not None:
        path = cookies_file.expanduser().resolve()
        if not path.is_file():
            raise ExtractionError("Cookie 文件不存在或不可读取；请检查 --cookies 路径")
        return {"cookiefile": str(path)}
    return {}


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
            "socket_timeout": 20,
            "retries": 2,
            "extractor_retries": 2,
            "logger": lookup,
        }
        options.update(cookie_options)
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
            if (not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"}
                    or info.get("id") != expected_id):
                raise ExtractionError("YouTube 返回的字幕来源与当前视频不匹配")
            source = _source_info(info, url).model_copy(update={"url": url})
            report(progress, "metadata_done", f"已获取视频：{source.title}", source=source)
            report(progress, "captions", "正在检查字幕列表")
            selected = _choose_caption(info)
            if selected is None:
                if info.get("availability") in ("needs_auth", "private", "premium_only", "subscriber_only"):
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
        if ("not a bot" in reason or "sign in to confirm" in reason
                or "sign in to verify" in reason):
            raise ExtractionError(
                "YouTube 要求验证登录状态或触发了反机器人风控。"
                "请打开原视频确认访问权限，或导入已有字幕。"
                "命令行可显式添加 --cookies-from-browser chrome（或 edge/firefox/safari）授权读取本机浏览器会话。",
                code="youtube_verification_required",
            ) from exc
        raise ExtractionError("YouTube 字幕提取失败，请检查来源或稍后重试。") from exc
    report(progress, "parse", "正在解析字幕时间戳和文本")
    try:
        segments = parse_srt_vtt(content)
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

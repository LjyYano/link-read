"""Bilibili public metadata and subtitle API integration (no login bypass)."""

from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
import re

import requests

from ..errors import ExtractionError, SubtitleUnavailableError, UnsupportedSourceError
from ..links import bilibili_part, canonical_link
from ..models import SourceInfo, TranscriptDocument
from ..parsers import parse_bilibili_json
from ..progress import ProgressCallback, report

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LinkRead/0.1)", "Referer": "https://www.bilibili.com/"}
_BVID = re.compile(r"BV[0-9A-Za-z]{10}")
BILIBILI_VIDEO_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
BILIBILI_SHORT_HOSTS = {"b23.tv", "www.b23.tv"}


def _fetch_json(session: requests.Session, url: str, *, params: dict | None = None) -> dict:
    try:
        response = session.get(url, params=params, headers=_HEADERS, timeout=20)
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ExtractionError(f"B 站接口请求失败：{urlparse(url).path}") from exc
    if not isinstance(body, dict) or body.get("code") != 0 or not isinstance(body.get("data"), dict):
        raise ExtractionError(f"B 站接口返回错误：{body.get('message', 'unknown') if isinstance(body, dict) else 'unknown'}")
    return body["data"]


def _select_subtitle(entries: list[dict]) -> dict:
    def priority(entry: dict) -> int:
        language = (entry.get("lan") or "").lower()
        if language.startswith("zh") or language.startswith("ai-zh"):
            return 0
        if language.startswith("en") or language.startswith("ai-en"):
            return 1
        return 2

    return sorted(entries, key=priority)[0]


def _subtitle_url(raw: str) -> str:
    url = "https:" + raw if raw.startswith("//") else raw
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "bilibili.com" or host.endswith(".bilibili.com") or host == "hdslb.com" or host.endswith(".hdslb.com")):
        raise ExtractionError("字幕下载地址不是受信任的 B 站 HTTPS 域名")
    return url


def bilibili_video_part(url: str) -> tuple[str, int]:
    return bilibili_part(url)


def _source_info(info: dict, url: str, bvid: str, page: int, pages: list, page_info: dict) -> SourceInfo:
    from ..metadata import thumbnail_url
    title = info.get("title") or bvid
    if pages and len(pages) > 1:
        title = f"{title} - P{page} {page_info.get('part') or ''}".strip()
    timestamp = info.get("pubdate")
    published_at = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat() if timestamp else None
    duration = page_info.get("duration") or info.get("duration")
    description = info.get("desc")
    description = description if isinstance(description, str) and description.strip() else None
    return SourceInfo(
        platform="bilibili", thumbnail_url=thumbnail_url(info.get("pic")),
        title=title, url=url, video_id=bvid, author=(info.get("owner") or {}).get("name"),
        published_at=published_at,
        duration_ms=round(float(duration) * 1000) if duration is not None else None,
        description=description, description_format="text" if description is not None else None,
    )


def extract_bilibili(
    url: str, *, session: requests.Session | None = None, progress: ProgressCallback | None = None,
) -> TranscriptDocument:
    session = session or requests.Session()
    url = canonical_link(url)
    if urlparse(url).hostname in BILIBILI_SHORT_HOSTS:
        report(progress, "redirect", "正在解析 B 站短链接")
        try:
            redirect = session.get(url, headers=_HEADERS, timeout=20)
            redirect.raise_for_status()
            url = redirect.url
        except requests.RequestException as exc:
            raise ExtractionError("无法解析 B 站短链接") from exc
    bvid, page = bilibili_video_part(url)
    report(progress, "metadata", "正在获取 B 站视频元数据")
    info = _fetch_json(session, "https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid})
    pages = info.get("pages") or []
    if pages and page > len(pages):
        raise UnsupportedSourceError(f"该视频没有第 {page} P")
    page_info = pages[page - 1] if pages else info
    cid = page_info.get("cid") or info.get("cid")
    if not cid:
        raise ExtractionError("无法获取视频 CID")
    source = _source_info(info, url, bvid, page, pages, page_info)
    report(progress, "metadata_done", f"已获取视频：{source.title}（P{page}）", source=source)
    report(progress, "captions", "正在查询 B 站字幕列表")
    player = _fetch_json(session, "https://api.bilibili.com/x/player/v2", params={"bvid": bvid, "cid": cid})
    subtitle = player.get("subtitle")
    tracks = subtitle.get("subtitles") if isinstance(subtitle, dict) else None
    subtitles = [
        track for track in tracks
        if isinstance(track, dict) and isinstance(track.get("subtitle_url"), str) and track["subtitle_url"].strip()
    ] if isinstance(tracks, list) else []
    if not subtitles:
        # The player can suppress tracks behind login while returning HTTP 200.
        # Only an explicit empty list without a login requirement permits ASR.
        if player.get("need_login_subtitle") is True:
            raise ExtractionError("B 站要求登录后查看字幕。可打开原视频，或导入已有字幕文件。",
                                  code="subtitle_login_required")
        if tracks != []:
            raise ExtractionError("B 站返回的字幕列表不完整，请重试检查来源。",
                                  code="subtitle_response_invalid", retryable=True, action="retry")
        raise SubtitleUnavailableError("该视频没有可获取的字幕；可启用 ASR", source=source)
    selected = _select_subtitle(subtitles)
    report(progress, "captions_done", f"选定字幕：{selected.get('lan') or '未知语言'}")
    subtitle_url = _subtitle_url(selected.get("subtitle_url") or "")
    report(progress, "download", "正在下载字幕文本（不下载视频或音频）")
    try:
        response = session.get(subtitle_url, headers=_HEADERS, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ExtractionError("B 站字幕下载失败") from exc
    report(progress, "parse", "正在解析字幕时间戳和文本")
    segments = parse_bilibili_json(response.text)
    report(progress, "parsed", f"字幕解析完成：{len(segments)} 个片段")
    return TranscriptDocument(
        source=source,
        transcript_source="subtitle",
        language=selected.get("lan"),
        segments=segments,
    )

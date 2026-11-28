"""Bilibili public metadata and subtitle API integration (no login bypass)."""

from datetime import datetime, timezone
import re
import time
from urllib.parse import urlsplit

import requests

from ..errors import ExtractionError, SubtitleLoginRequiredError, SubtitleUnavailableError, UnsupportedSourceError
from ..links import bilibili_part, canonical_link
from ..models import SourceInfo, TranscriptDocument
from ..parsers import parse_bilibili_json
from ..progress import ProgressCallback, report

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LinkRead/0.1)", "Referer": "https://www.bilibili.com/"}
BILIBILI_VIDEO_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
BILIBILI_SHORT_HOSTS = {"b23.tv", "www.b23.tv"}
_AI_TRACK = re.compile(r"/bfs/ai_subtitle/prod/([0-9]*)([a-fA-F0-9]{32})(?:\.json)?$")
_CC_TRACK = re.compile(r"/bfs/subtitle/[a-fA-F0-9]{40}\.json$")
_MAX_SUBTITLE_BYTES = 16 * 1024 * 1024


def _invalid_subtitles(message: str) -> ExtractionError:
    return ExtractionError(message, code="subtitle_response_invalid", retryable=True, action="retry")


def _fetch_json(session: requests.Session, url: str, *, params: dict | None = None) -> dict:
    try:
        response = session.get(url, params=params, headers=_HEADERS, timeout=20, allow_redirects=False)
        response.raise_for_status()
        if response.status_code != 200:
            raise _invalid_subtitles("B 站接口返回了意外的跳转，请重新检查来源。")
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ExtractionError(f"B 站接口请求失败：{urlsplit(url).path}") from exc
    if not isinstance(body, dict) or type(body.get("code")) is not int or body["code"] != 0 or not isinstance(body.get("data"), dict):
        code = body.get("code") if isinstance(body, dict) else None
        detail = f"（{code}）" if type(code) is int else ""
        raise ExtractionError(f"B 站接口返回错误{detail}：{urlsplit(url).path}")
    return body["data"]


def _select_subtitle(entries: list[dict]) -> dict:
    def priority(entry: dict) -> int:
        language = entry["lan"].lower()
        if language.startswith("zh") or language.startswith("ai-zh"):
            return 0
        if language.startswith("en") or language.startswith("ai-en"):
            return 1
        return 2

    return sorted(entries, key=priority)[0]


def _subtitle_url(raw: str) -> str:
    try:
        if "\\" in raw or any(ord(char) < 33 for char in raw):
            raise ValueError
        parsed = urlsplit("https:" + raw if raw.startswith("//") else raw)
        host = parsed.hostname or ""
        if parsed.username is not None or parsed.password is not None or parsed.port is not None or parsed.fragment:
            raise ValueError
        cdn = host.endswith(".hdslb.com")
        if parsed.scheme == "http" and cdn:
            parsed = parsed._replace(scheme="https")
        if parsed.scheme != "https" or not (cdn or host == "hdslb.com" or host in BILIBILI_VIDEO_HOSTS or host.endswith(".bilibili.com")):
            raise ValueError
        return parsed.geturl()
    except ValueError as exc:
        raise _invalid_subtitles("字幕下载地址不是受信任的 B 站 HTTPS 域名") from exc


def _track_entries(tracks: object) -> list[dict]:
    if not isinstance(tracks, list):
        return []
    return [
        track for track in tracks
        if isinstance(track, dict) and isinstance(track.get("lan"), str) and track["lan"].strip()
        and isinstance(track.get("subtitle_url"), str) and track["subtitle_url"].strip()
    ]


def _player_subtitles(tracks: object, aid: object, cid: int) -> tuple[list[dict], ExtractionError | None]:
    valid = []
    rejected = None
    for track in _track_entries(tracks):
        try:
            url = _subtitle_url(track["subtitle_url"])
            auto = _AI_TRACK.fullmatch(urlsplit(url).path)
            if auto and auto[1] and auto[1] != f"{aid}{cid}":
                raise _invalid_subtitles("B 站播放器返回了其他视频或分 P 的字幕地址。")
        except ExtractionError as exc:
            rejected = exc
            continue
        valid.append({**track, "subtitle_url": url})
    return valid, rejected


def _track_id(track: dict) -> str:
    value = track.get("id_str") or track.get("id")
    if type(value) is int and value > 0:
        return str(value)
    return value if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) else ""


def _published_subtitles(info: dict, cid: int) -> list:
    subtitle = info.get("subtitle")
    tracks = subtitle.get("list") if info.get("cid") == cid and isinstance(subtitle, dict) else None
    return tracks if isinstance(tracks, list) else []


def _public_subtitles(session, info: dict, cid: int, published: list) -> list[dict]:
    aid = info.get("aid")
    if type(aid) is not int or aid <= 0:
        raise _invalid_subtitles("无法确认 B 站视频编号，暂不能判断公开字幕是否可用。")
    data = _fetch_json(session, "https://api.bilibili.com/x/v2/dm/view", params={"aid": aid, "oid": cid, "type": 1})
    if data.get("aid", aid) != aid or data.get("cid", cid) != cid or data.get("oid", cid) != cid:
        raise _invalid_subtitles("B 站公开字幕响应属于其他视频或分 P。")
    subtitle = data.get("subtitle")
    if not isinstance(subtitle, dict) or "subtitles" not in subtitle:
        raise _invalid_subtitles("B 站公开字幕列表不完整，请重新检查来源。")
    tracks = subtitle["subtitles"]
    # The public view returns an explicit null for a page with no tracks, or
    # empty-url placeholders when subtitles require login.
    if tracks is None or tracks == []:
        return []
    if not isinstance(tracks, list):
        raise _invalid_subtitles("B 站公开字幕列表格式错误，请重新检查来源。")
    if all(isinstance(track, dict) and isinstance(track.get("lan"), str)
           and isinstance(track.get("subtitle_url"), str) and not track["subtitle_url"].strip()
           for track in tracks):
        return []

    published_tracks = {(_track_id(entry), entry.get("lan")) for entry in published
                        if isinstance(entry, dict) and isinstance(entry.get("lan"), str)}
    verified = []
    for track in _track_entries(tracks):
        try:
            url = _subtitle_url(track["subtitle_url"])
        except ExtractionError:
            continue
        path = urlsplit(url).path
        auto = _AI_TRACK.fullmatch(path)
        track_id = _track_id(track)
        matches = (auto is not None and auto[1] == f"{aid}{cid}") or (
            _CC_TRACK.fullmatch(path) and track_id and (
                (track_id, track["lan"]) in published_tracks or info.get("cid") != cid
            )
        )
        if matches:
            verified.append({**track, "subtitle_url": url})
    if not verified:
        raise _invalid_subtitles("B 站返回了字幕轨，但无法确认它属于当前分 P，请重新检查来源。")
    return verified


def _download_subtitle(session, url: str) -> str:
    try:
        started = time.monotonic()
        with session.get(url, headers=_HEADERS, timeout=(8, 15), stream=True, allow_redirects=False) as response:
            response.raise_for_status()
            if response.status_code != 200 or response.headers.get("Content-Range"):
                raise _invalid_subtitles("B 站未返回完整的字幕文件，请重新检查来源。")
            body = bytearray()
            for chunk in response.iter_content(64 * 1024):
                body.extend(chunk)
                if len(body) > _MAX_SUBTITLE_BYTES or time.monotonic() - started > 30:
                    raise _invalid_subtitles("B 站字幕下载超过大小或时间上限，请重试。")
        return body.decode("utf-8-sig")
    except (requests.RequestException, UnicodeError) as exc:
        raise ExtractionError("B 站字幕下载失败") from exc


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
    if urlsplit(url).hostname in BILIBILI_SHORT_HOSTS:
        from ..metadata import canonical_video_url
        report(progress, "redirect", "正在解析 B 站短链接")
        url = canonical_video_url(url, session)
    bvid, page = bilibili_video_part(url)
    report(progress, "metadata", "正在获取 B 站视频元数据")
    info = _fetch_json(session, "https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid})
    if info.get("bvid", bvid) != bvid:
        raise ExtractionError("B 站返回了其他视频的资料，已停止提取")
    pages = info.get("pages") or []
    if not isinstance(pages, list):
        raise ExtractionError("B 站返回了无效的分 P 列表")
    if page > max(len(pages), 1):
        raise UnsupportedSourceError(f"该视频没有第 {page} P")
    page_info = pages[page - 1] if pages else info
    cid = page_info.get("cid") if isinstance(page_info, dict) else None
    if type(cid) is not int or cid <= 0 or page_info.get("page", page) != page:
        raise ExtractionError("无法确认请求分 P 的视频 CID")
    source = _source_info(info, url, bvid, page, pages, page_info)
    report(progress, "metadata_done", f"已获取视频：{source.title}（P{page}）", source=source)
    report(progress, "captions", "正在查询 B 站字幕列表")
    player = _fetch_json(session, "https://api.bilibili.com/x/player/v2", params={"bvid": bvid, "cid": cid})
    if player.get("bvid", bvid) != bvid or player.get("cid", cid) != cid:
        raise _invalid_subtitles("B 站播放器响应属于其他视频或分 P。")
    subtitle = player.get("subtitle")
    tracks = subtitle.get("subtitles") if isinstance(subtitle, dict) else None
    subtitles, rejected = _player_subtitles(tracks, info.get("aid"), cid)
    if not subtitles:
        published = _published_subtitles(info, cid)
        subtitles, publisher_rejection = _player_subtitles(published, info.get("aid"), cid)
        rejected = rejected or publisher_rejection
        if not subtitles:
            subtitles = _public_subtitles(session, info, cid, published)
    if not subtitles:
        if rejected:
            raise rejected
        rights = info.get("rights")
        upower_restricted = any(
            info.get(key) == 1 for key in ("is_upower_exclusive", "is_upower_exclusive_with_qa", "is_upower_preview")
        )
        paid = isinstance(rights, dict) and any(
            rights.get(key) == 1 for key in ("pay", "arc_pay", "ugc_pay", "ugc_pay_preview")
        )
        if upower_restricted or paid:
            raise ExtractionError("此视频的音轨受访问限制，无法转写", code="protected_media")
        # The player can suppress tracks behind login while returning HTTP 200.
        login_required = player.get("need_login_subtitle") is True
        placeholders = login_required and isinstance(tracks, list) and all(
            isinstance(track, dict) and isinstance(track.get("lan"), str)
            and isinstance(track.get("subtitle_url"), str) and not track["subtitle_url"].strip()
            for track in tracks
        )
        if tracks != [] and not placeholders:
            raise _invalid_subtitles("B 站返回的字幕列表不完整，请重试检查来源。")
        if login_required:
            raise SubtitleLoginRequiredError("B 站要求登录后查看字幕。可打开原视频，或导入已有字幕文件。",
                                             source=source)
        raise SubtitleUnavailableError("该视频没有可获取的字幕；可启用 ASR", source=source)
    selected = _select_subtitle(subtitles)
    report(progress, "captions_done", f"选定字幕：{selected.get('lan') or '未知语言'}")
    subtitle_url = selected["subtitle_url"]
    report(progress, "download", "正在下载字幕文本（不下载视频或音频）")
    content = _download_subtitle(session, subtitle_url)
    report(progress, "parse", "正在解析字幕时间戳和文本")
    try:
        segments = parse_bilibili_json(content)
    except SubtitleUnavailableError as exc:
        raise _invalid_subtitles("B 站返回了字幕地址，但下载的字幕为空。") from exc
    report(progress, "parsed", f"字幕解析完成：{len(segments)} 个片段")
    return TranscriptDocument(
        source=source,
        transcript_source="subtitle",
        language=selected.get("lan"),
        segments=segments,
    )

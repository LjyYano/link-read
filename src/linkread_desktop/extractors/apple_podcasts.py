"""Apple Podcasts episode metadata, public RSS transcripts and audio enclosures.

Apple IDs identify the selected episode; a matching RSS GUID identifies the
publisher's transcript. No title matching, latest-episode fallback, Apple login,
subscription APIs, or automatic ASR requests are used here.
"""
from dataclasses import dataclass
from html.parser import HTMLParser
from itertools import chain
import json
import math
import os
from pathlib import Path
from queue import Empty, Queue
import re
from threading import Thread
import time
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

import requests

from ..errors import ExtractionError, SubtitleUnavailableError
from ..models import SourceInfo, TranscriptDocument, TranscriptSegment
from ..parsers import parse_srt_vtt
from ..progress import report
from .podcast_audio import check_audio_source, is_hls_content_type, reject_hls_audio
from .podcast_http import chunks, get_bytes, public_response, public_url

_PODCAST = "https://podcastindex.org/namespace/1.0"
_ATOM = "http://www.w3.org/2005/Atom"
_ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
_CONTENT = "http://purl.org/rss/1.0/modules/content/"
_TIMED_TYPES = {"text/vtt": 0, "application/x-subrip": 1, "application/srt": 1,
                "text/srt": 1, "application/json": 2}
_MAX_METADATA = 6 * 1024**2
_MAX_FEED = 12 * 1024**2
_MAX_TRANSCRIPT = 12 * 1024**2
_PREVIEW_SECONDS = 8.0


class _PageFormatError(ExtractionError):
    """A public page has changed shape; generation may try Apple's catalog API."""


class _VerifiedEmptyFeed(ExtractionError):
    """An identified, complete but empty feed cannot supply episode captions."""


@dataclass(frozen=True)
class AppleEpisode:
    source: SourceInfo
    show_id: str
    episode_id: str
    guid: str | None
    feed_url: str | None
    audio_url: str | None
    # Only the exact, freely playable Apple page may grant this provenance.
    # Catalog fallback data alone cannot authorize missing/empty-feed recovery.
    page_verified_free_audio: bool = False
    # Explicit null fields on the exact page are different from omitted fields
    # after a schema change. Neither flag alone grants permission to run ASR.
    page_declares_no_feed: bool = False
    page_declares_no_transcript: bool = False


def _string(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _description(value) -> str | None:
    # Keep publisher line breaks and markup; the renderer sanitizes HTML.
    return value if isinstance(value, str) and value.strip() else None


def _description_format(value: str | None) -> str | None:
    if value is None:
        return None
    return "html" if re.search(r"</?[a-zA-Z][^>]*>", value) else "text"


def _duration(value, *, multiplier=1) -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
        return round(value * multiplier)
    return None


def _image(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.replace("{w}", "600").replace("{h}", "600").replace("{f}", "jpg")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not (parsed.hostname or "").endswith(".mzstatic.com")
                or parsed.username is not None or parsed.password is not None or parsed.port is not None
                or "\\" in value or any(ord(c) < 33 for c in value)):
            return None
        return value
    except ValueError:
        return None


class _ServerData(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.inside = False
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "serialized-server-data":
            self.inside = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.parts.append(data)


def _page_episode(content: bytes, *, show_id: str, episode_id: str, url: str) -> AppleEpisode:
    """Fallback for episodes outside Apple's latest-200 lookup window."""
    parser = _ServerData()
    try:
        parser.feed(content.decode("utf-8"))
        payload = json.loads("".join(parser.parts))
        pages = payload["data"]
        page = next(row["data"] for row in pages if
                    row.get("intent", {}).get("$kind") == "EpisodePageIntent"
                    and str(row["intent"].get("adamId")) == episode_id
                    and str(row["intent"].get("showAdamId")) == show_id)
        candidates = [entry.get("model", {}) for entry in page.get("headerButtonItems", [])]
        # Only the exact page's header is examined. Recommendations are ignored.
        model = next(row for row in candidates if str(row.get("id")) == episode_id
                     and str(row.get("showAdamId")) == show_id)
        if model.get("entitlementState") != "free":
            raise ExtractionError("该播客单集需要订阅或登录，无法读取公开音源")
        offer = model.get("playAction", {}).get("episodeOffer", {})
        show = offer.get("showOffer", {})
        if str(show.get("adamId")) != show_id or str(offer.get("contentId")) != episode_id:
            raise ValueError("mismatched episode offer")
        enclosures = model.get("mediaEnclosures", [])
        audio = next((row.get("streamUrl") for row in enclosures
                      if "audio" in row.get("mediaKinds", []) and row.get("streamUrl")), None)
        title = _string(model.get("title"))
        if not title or not audio:
            raise ValueError("no public audio episode")
        artwork = (model.get("episodeArtwork") or model.get("icon") or {}).get("template")
        description = _description(model.get("summary")) or _description(model.get("description"))
        source = SourceInfo(platform="apple_podcasts", title=title, url=url, video_id=episode_id,
                            author=_string(model.get("showTitle")) or _string(show.get("title")),
                            published_at=_string(model.get("releaseDate")),
                            duration_ms=_duration(model.get("duration"), multiplier=1000),
                            thumbnail_url=_image(artwork), description=description,
                            description_format=_description_format(description))
        return AppleEpisode(source, show_id, episode_id, _string(offer.get("guid")),
                            _string(show.get("feedUrl")), public_url(audio, resolve=False),
                            page_verified_free_audio=True,
                            page_declares_no_feed="feedUrl" in show and show["feedUrl"] is None,
                            page_declares_no_transcript=bool(enclosures) and all(
                                isinstance(row, dict) and "transcriptInfo" in row
                                and row["transcriptInfo"] is None for row in enclosures))
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration) as exc:
        raise _PageFormatError("未能在 Apple Podcasts 页面确认该单集，请检查链接或稍后重试") from exc


def _lookup_episode(url: str, *, session=None) -> AppleEpisode:
    """Catalog fallback for generation when a public episode page changes shape."""
    from ..links import apple_podcast_ids, canonical_link
    url = canonical_link(url)
    show_id, episode_id, country = apple_podcast_ids(url)
    client = session or requests.Session()
    lookup = "https://itunes.apple.com/lookup?" + urlencode({
        "id": show_id, "entity": "podcastEpisode", "limit": 200, "country": country,
    })
    content = get_bytes(client, lookup, max_bytes=_MAX_METADATA, accept="application/json")
    try:
        payload = json.loads(content)
        rows = payload["results"]
        if not isinstance(rows, list) or not isinstance(payload.get("resultCount"), int):
            raise ValueError
        shows = [row for row in rows if isinstance(row, dict) and row.get("kind") == "podcast"
                 and str(row.get("collectionId")) == show_id]
        episodes = [row for row in rows if isinstance(row, dict) and row.get("kind") == "podcast-episode"
                    and str(row.get("trackId")) == episode_id and str(row.get("collectionId")) == show_id]
        if len(episodes) > 1 or len(shows) > 1:
            raise ValueError
        if not episodes:
            raise ExtractionError("Apple Podcasts 目录中未能确认该单集，请检查链接或稍后重试")
        item = episodes[0]
        show = shows[0] if shows else {}
        title = _string(item.get("trackName"))
        audio = _string(item.get("episodeUrl"))
        if not title or not audio or item.get("episodeContentType") != "audio":
            raise ValueError
        description = _description(item.get("description"))
        source = SourceInfo(platform="apple_podcasts", title=title, url=url, video_id=episode_id,
                            author=_string(show.get("artistName")) or _string(item.get("collectionName")),
                            published_at=_string(item.get("releaseDate")),
                            duration_ms=_duration(item.get("trackTimeMillis")),
                            thumbnail_url=_image(item.get("artworkUrl600") or show.get("artworkUrl600")),
                            description=description, description_format=_description_format(description))
        return AppleEpisode(source, show_id, episode_id, _string(item.get("episodeGuid")),
                            _string(show.get("feedUrl")), public_url(audio, resolve=False))
    except (ValueError, KeyError, TypeError) as exc:
        raise ExtractionError("Apple Podcasts 返回了无效的单集信息") from exc


def _direct_episode(url: str, *, session=None,
                    timeout: tuple[float, float] = (10, 30), max_seconds: float = 90) -> AppleEpisode:
    from ..links import apple_podcast_ids, canonical_link
    url = canonical_link(url)
    show_id, episode_id, _ = apple_podcast_ids(url)
    content = get_bytes(session or requests, url, max_bytes=_MAX_METADATA,
                        accept="text/html", timeout=timeout, max_seconds=max_seconds)
    return _page_episode(content, show_id=show_id, episode_id=episode_id, url=url)


def preview_episode(url: str, *, session=None) -> SourceInfo:
    """Read one exact episode page with a wall-clock budget, including DNS.

    requests' per-socket timeouts do not bound DNS or a trickling response. A
    daemon worker lets the short-lived preview sidecar return within its budget;
    the native process deadline is the final cleanup boundary. It never starts
    catalog, feed, subtitle, or media downloads after a preview timeout.
    """
    from ..links import canonical_link
    url = canonical_link(url)
    results: Queue[SourceInfo | Exception] = Queue(maxsize=1)

    def read_page():
        try:
            # A preview must return quickly; generation keeps the normal network
            # budget so a slow preview need not also prevent generating the article.
            results.put(_direct_episode(url, session=session, timeout=(3, 5),
                                        max_seconds=_PREVIEW_SECONDS).source)
        except Exception as exc:
            results.put(exc)

    worker = Thread(target=read_page, name="linkread-podcast-preview", daemon=True)
    worker.start()
    try:
        result = results.get(timeout=_PREVIEW_SECONDS)
    except Empty as exc:
        raise ExtractionError("播客预览暂时超时，可重试预览或直接开始生成") from exc
    if isinstance(result, Exception):
        raise result
    return result


def resolve_episode(url: str, *, session=None) -> AppleEpisode:
    """Resolve the selected page first; do not wait for 200 unrelated episodes."""
    try:
        return _direct_episode(url, session=session)
    except _PageFormatError:
        # A page schema change is distinct from an explicit subscription or
        # HTTP permission error; those propagate and never trigger this fallback.
        return _lookup_episode(url, session=session)


def _same_feed_url(declared: str | None, actual: str | None) -> bool:
    """Compare declared feed identity without discarding queries or paths."""
    try:
        expected = urlsplit(public_url(declared, resolve=False))
        candidate = urlsplit(public_url(actual, resolve=False))
    except ExtractionError:
        return False
    return (expected.netloc, expected.path, expected.query) == (
        candidate.netloc, candidate.path, candidate.query
    ) and (candidate.scheme == expected.scheme or (
        expected.scheme == "http" and candidate.scheme == "https"
    ))


def _check_empty_feed(root, episode: AppleEpisode) -> None:
    """Recover only the observed case: a complete feed has no episode items.

    Apple still identifies a free public audio enclosure for this exact episode.
    This is distinct from a failed fetch, a truncated/broken feed, an unmatched
    non-empty catalog, or an ambiguous identity; none of those permit this path.
    """
    channels = root.findall("channel")
    if root.get("version") != "2.0" or len(channels) != 1 or next(root.iter("item"), None) is not None:
        raise ExtractionError("播客 RSS 结构不完整，无法确认该集字幕")
    channel = channels[0]
    if not all(_string(channel.findtext(tag)) for tag in ("title", "link", "description")):
        raise ExtractionError("播客 RSS 频道信息不完整，无法确认该集字幕")
    public_url(_string(channel.findtext("link")), resolve=False)
    # itunes:block controls Apple directory inclusion, not HTTP access. Exclude
    # it only from this new empty-directory recovery; ordinary matched feeds
    # retain their existing public-caption behavior.
    if (_string(channel.findtext(f"{{{_ITUNES}}}block")) or "").lower() == "yes":
        raise ExtractionError("该 RSS 当前未列出单集，并设置了 Apple 目录屏蔽标记；未自动切换语音识别")
    links = [row for row in channel.findall(f"{{{_ATOM}}}link") if row.get("rel") == "self"]
    if len(links) != 1 or not _same_feed_url(episode.feed_url, links[0].get("href")):
        raise ExtractionError("播客 RSS 来源发生变化，无法确认该集字幕")
    if _string(channel.findtext(f"{{{_ITUNES}}}new-feed-url")):
        raise ExtractionError("发布方声明了新的 RSS 地址，请使用更新后的节目单集链接")
    if not episode.page_verified_free_audio or not episode.audio_url:
        raise ExtractionError("节目 RSS 暂未列出单集，且未能核验该集的公开音源")
    raise _VerifiedEmptyFeed("发布方 RSS 当前未列出单集，无法从订阅源读取该集字幕")


def _feed_item(content: bytes, episode: AppleEpisode):
    declarations = content.replace(b"\x00", b"").lower()
    if b"<!doctype" in declarations or b"<!entity" in declarations:
        raise ExtractionError("播客 RSS 含有不支持的 XML 声明")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ExtractionError("播客 RSS 内容无效，无法确认该集字幕") from exc
    if root.tag != "rss" or root.find("channel") is None:
        raise ExtractionError("未获取到有效的播客 RSS 订阅源")
    items = root.findall("./channel/item")
    if not items:
        _check_empty_feed(root, episode)
    if episode.guid:
        matches = [item for item in items if _string(item.findtext("guid")) == episode.guid]
    else:
        matches = [item for item in items if any(
            row.get("url") == episode.audio_url for row in item.findall("enclosure"))]
    if len(matches) != 1:
        raise ExtractionError("公开 RSS 中未能唯一定位该单集；不会改为读取其他集数")
    return root, matches[0]


def _rss_source_description(source: SourceInfo, item) -> SourceInfo:
    """Use only the already identity-matched item's original Shownotes."""
    for tag in (f"{{{_CONTENT}}}encoded", "description", f"{{{_ITUNES}}}summary"):
        element = item.find(tag)
        if element is None:
            continue
        value = (element.text or "") + "".join(
            ElementTree.tostring(child, encoding="unicode") for child in element
        )
        description = _description(value)
        if description:
            return source.model_copy(update={
                "description": description, "description_format": _description_format(description),
            })
    return source


def episode_shownotes(url: str, *, session=None) -> SourceInfo:
    """Explicit metadata-only refresh for an already archived exact episode.

    The current exact page normally contains the complete publisher summary.
    RSS is read only if that page omitted it, with the usual exact GUID checks;
    neither subtitles nor audio nor an AI/ASR service is contacted.
    """
    client = session or requests.Session()
    episode = resolve_episode(url, session=client)
    if episode.source.description or not episode.feed_url:
        return episode.source
    content = get_bytes(client, episode.feed_url, max_bytes=_MAX_FEED,
                        accept="application/rss+xml, application/xml, text/xml")
    try:
        _, item = _feed_item(content, episode)
    except _VerifiedEmptyFeed:
        return episode.source
    return _rss_source_description(episode.source, item)


def _json_transcript(content: bytes) -> list[TranscriptSegment]:
    try:
        payload = json.loads(content)
        rows = payload["segments"]
        if not isinstance(rows, list) or not rows:
            raise ValueError
        segments = []
        for row in rows:
            body = _string(row.get("body"))
            start, end = row["startTime"], row.get("endTime")
            if (not body or any(not isinstance(value, (int, float)) or isinstance(value, bool)
                                or not math.isfinite(value) or value < 0
                                for value in (start,) + ((end,) if end is not None else ()))
                    or (end is not None and end < start)):
                raise ValueError
            segments.append(TranscriptSegment(start_ms=round(start * 1000),
                            end_ms=round(end * 1000) if end is not None else None, text=body))
        return sorted(segments, key=lambda row: row.start_ms)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ExtractionError("播客 JSON 字幕缺少有效的逐句文本或时间戳") from exc


def _timed_transcript(content: bytes) -> list[TranscriptSegment]:
    """Require every non-metadata block to be a cue before using the shared parser.

    The generic import parser tolerates unrelated prose. For an advertised
    podcast transcript that would silently omit a damaged block and risk
    producing an incomplete article, so this extractor fails explicitly.
    """
    text = content.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").strip()
    cues = []
    for index, block in enumerate(re.split(r"\n\s*\n", text)):
        lines = block.splitlines()
        if not lines:
            continue
        first = lines[0].strip()
        if index == 0 and (first == "WEBVTT" or first.startswith(("WEBVTT ", "WEBVTT\t"))):
            if any("-->" in line for line in lines):
                raise ExtractionError("播客 VTT 头部与字幕之间缺少分隔，无法完整解析")
            continue
        if (first in {"NOTE", "STYLE", "REGION"}
                or first.startswith(("NOTE ", "NOTE\t"))):
            continue
        position = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if position not in {0, 1} or any("-->" in line for line in lines[position + 1:]):
            raise ExtractionError("播客字幕含有缺失时间戳的片段，未生成不完整文章")
        # Discard only the optional cue identifier; preserve all timed text.
        cues.append("\n".join(lines[position:]))
    if not cues:
        raise ExtractionError("播客公开字幕没有可读取的句子")
    # Parse each actual cue separately. The generic parser's cross-cue YouTube
    # rolling-caption deduplication must not remove overlapping podcast speech.
    segments = [segment for cue in cues for segment in parse_srt_vtt(cue)]
    return sorted(segments, key=lambda segment: segment.start_ms)


def extract_apple_podcasts(url: str, *, session=None, progress=None) -> TranscriptDocument:
    client = session or requests.Session()
    report(progress, "metadata", "正在获取 Apple Podcasts 单集信息")
    episode = resolve_episode(url, session=client)
    report(progress, "metadata_done", f"已获取播客：{episode.source.title}", source=episode.source)
    if not episode.feed_url:
        if (episode.page_verified_free_audio and episode.audio_url
                and episode.page_declares_no_feed and episode.page_declares_no_transcript):
            report(progress, "captions", "该单集未提供公开 RSS，正在检查对应音源")
            # Free playback can still use protected HLS. Identify that limit
            # before suggesting ASR; never pass a playlist off as an MP3 file.
            check_audio_source(episode.audio_url, session=client)
            raise SubtitleUnavailableError(
                "该单集未提供公开 RSS 或字幕，可在设置中启用语音识别处理对应的免费音源",
                source=episode.source,
            )
        raise ExtractionError("已识别该单集，但未能确认可读取的公开字幕来源；请查看来源或导入已有字幕")
    report(progress, "captions", "正在检查该单集发布的公开字幕")
    content = get_bytes(client, episode.feed_url, max_bytes=_MAX_FEED,
                        accept="application/rss+xml, application/xml, text/xml")
    try:
        feed, item = _feed_item(content, episode)
    except _VerifiedEmptyFeed as exc:
        check_audio_source(episode.audio_url, session=client)
        report(progress, "captions", "发布方 RSS 当前未列出单集，无法读取该集字幕；已核验 Apple 对应单集的公开音源")
        # The main dispatcher alone decides whether ASR is explicitly enabled.
        # Keep the selected Apple episode URL; do not select another feed item.
        raise SubtitleUnavailableError(
            "发布方 RSS 当前未列出单集，无法读取该集字幕；可在设置中启用语音识别处理该单集",
            source=episode.source,
        ) from exc
    source = _rss_source_description(episode.source, item)
    resources = item.findall(f"{{{_PODCAST}}}transcript")
    candidates = []
    for row in resources:
        kind = row.get("type", "").split(";", 1)[0].strip().lower()
        if kind in _TIMED_TYPES:
            if not row.get("url"):
                raise ExtractionError("该集的公开字幕地址缺失，请稍后重试")
            language = row.get("language", "").lower()
            candidates.append((0 if language.startswith("zh") else 1 if language.startswith("en") else 2,
                               _TIMED_TYPES[kind], row, kind))
    if not candidates:
        check_audio_source(episode.audio_url, session=client)
        raise SubtitleUnavailableError("该播客单集未发布可读取的带时间戳字幕，可在设置中启用语音识别", source=source)
    _, _, selected, kind = min(candidates, key=lambda row: row[:2])
    report(progress, "captions_done", "已找到该单集的公开字幕")
    report(progress, "download", "正在下载播客字幕（不下载音频）")
    content = get_bytes(client, selected.get("url"), max_bytes=_MAX_TRANSCRIPT, accept=kind)
    report(progress, "parse", "正在解析播客字幕时间戳和文本")
    try:
        segments = _json_transcript(content) if kind == "application/json" else _timed_transcript(content)
    except (SubtitleUnavailableError, UnicodeError) as exc:
        # A declared subtitle that is broken/empty is not proof of its absence.
        raise ExtractionError("该集公开字幕内容无效，请重试；未自动提交语音识别") from exc
    report(progress, "parsed", f"字幕解析完成：{len(segments)} 个片段")
    return TranscriptDocument(source=source, transcript_source="subtitle", segments=segments,
                              language=selected.get("language") or feed.findtext("./channel/language"))


def download_episode_audio(url, folder: Path, config, *, progress=None, session=None):
    """Download the public publisher enclosure for the existing opt-in ASR flow."""
    from ..asr.bailian import MAX_AUDIO_BYTES
    from ..asr.config import AsrError
    from ..asr.segments import ffmpeg_binary
    client = session or requests.Session()
    episode = resolve_episode(url, session=client)
    if not episode.audio_url:
        raise AsrError("该单集没有可下载的公开音源")
    check_audio_source(episode.audio_url, session=client)
    duration = episode.source.duration_ms
    if not duration or duration > config.max_minutes * 60_000:
        raise AsrError(f"无法确认播客时长，或超过 {config.max_minutes} 分钟转写上限；未下载或提交转写")
    if config.chunk_minutes and duration > config.chunk_minutes * 60_000:
        ffmpeg_path = getattr(config, "ffmpeg_path", "")
        ffmpeg_binary(ffmpeg_path) if ffmpeg_path else ffmpeg_binary()
    limit = 2 * 1024**3 if config.chunk_minutes else MAX_AUDIO_BYTES
    # Never take a filename from a remote header or URL path.
    extension = Path(urlsplit(episode.audio_url).path).suffix.lower()
    if extension not in {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}:
        extension = ".mp3"
    target = folder / ("audio" + extension)
    temporary = folder / ("audio" + extension + ".part")
    if folder.is_symlink() or target.is_symlink() or temporary.is_symlink():
        raise AsrError("播客音频缓存不可使用符号链接")
    report(progress, "audio_download", "正在下载该播客单集的公开音频")
    try:
        with public_response(client, episode.audio_url, accept="audio/*, application/octet-stream") as response:
            kind = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if is_hls_content_type(kind):
                # Read only enough manifest text for an accurate explanation;
                # no media segments, keys or licenses are requested.
                manifest = b"".join(chunks(response, max_bytes=256 * 1024, max_seconds=8))
                reject_hls_audio(manifest)
            if kind and not (kind.startswith("audio/") or kind in {"application/octet-stream", "video/mp4"}):
                raise AsrError("播客音源未返回可用的音频文件")
            stream = chunks(response, max_bytes=limit, max_seconds=1800)
            prefix = []
            prefix_size = 0
            for chunk in stream:
                prefix.append(chunk)
                prefix_size += len(chunk)
                if prefix_size >= 16:
                    break
            first = b"".join(prefix)
            if first.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"#EXTM3U"):
                reject_hls_audio(first)
            total = 0
            last = 0.0
            with temporary.open("wb") as output:
                os.chmod(temporary, 0o600)
                for chunk in chain(prefix, stream):
                    output.write(chunk)
                    total += len(chunk)
                    if time.monotonic() - last > 1:
                        report(progress, "audio_download", f"下载播客音频：{total / 1048576:.1f} MB")
                        last = time.monotonic()
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    report(progress, "audio_download_done", "播客音频下载完成")
    return target, episode.source

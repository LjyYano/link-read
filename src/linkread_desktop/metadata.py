"""Lightweight metadata previews. No AI, subtitle checks or media downloads."""
from urllib.parse import urlsplit, urljoin, urlunsplit

import requests
from pydantic import BaseModel, ConfigDict

from .errors import ExtractionError, UnsupportedSourceError
from .extractors.bilibili import (
    BILIBILI_SHORT_HOSTS, BILIBILI_VIDEO_HOSTS, _fetch_json, _HEADERS, bilibili_video_part,
    _source_info as bilibili_source_info,
)
from .models import SourceInfo
from .links import APPLE_PODCAST_HOSTS, canonical_link
from .pipeline import _canonical_youtube_url, _normalize_video_url, _YOUTUBE_DOMAINS


class VideoMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: SourceInfo
    thumbnail_url: str | None = None

    def public(self) -> dict:
        s = self.source
        return {"title": s.title, "platform": s.platform, "sourceUrl": s.url,
                "author": s.author, "publishedAt": s.published_at,
                "durationMs": s.duration_ms, "thumbnailUrl": self.thumbnail_url}


def thumbnail_url(value: str | None) -> str | None:
    """Normalize only known public image CDNs; Bilibili often returns HTTP pic URLs."""
    if not isinstance(value, str) or not value:
        return None
    value = value.strip()
    if any(ord(c) < 32 for c in value) or "\\" in value:
        return None
    value = "https:" + value if value.startswith("//") else value
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme not in {"http", "https"} or parsed.username is not None
                or parsed.password is not None or parsed.port is not None
                or not (host == "i.ytimg.com" or host.endswith(".hdslb.com") or host.endswith(".mzstatic.com"))):
            return None
        return urlunsplit(("https", host, parsed.path, parsed.query, ""))
    except ValueError:
        return None


def canonical_video_url(source: str, session=None) -> str:
    """Bounded redirects for Bilibili short links; never follow arbitrary hosts."""
    url = _normalize_video_url(source)
    client = session or requests
    for _ in range(5):
        p = urlsplit(url)
        if p.scheme != "https" or p.username or p.password or p.port:
            raise UnsupportedSourceError("请输入支持的视频或播客单集 HTTPS 链接")
        if p.hostname in APPLE_PODCAST_HOSTS:
            return canonical_link(url)
        if p.hostname in _YOUTUBE_DOMAINS:
            return _canonical_youtube_url(url)
        if p.hostname in BILIBILI_VIDEO_HOSTS:
            bvid, part = bilibili_video_part(url)
            return f"https://www.bilibili.com/video/{bvid}?p={part}"
        if p.hostname not in BILIBILI_SHORT_HOSTS:
            raise UnsupportedSourceError("仅支持 B 站、YouTube 视频和 Apple Podcasts 单集链接")
        try:
            response = client.get(url, headers=_HEADERS, timeout=10, allow_redirects=False)
            if response.status_code not in {301, 302, 303, 307, 308}:
                raise ExtractionError("B 站短链接没有返回可用的视频跳转")
            url = urljoin(url, response.headers.get("Location", ""))
        except requests.RequestException as exc:
            raise ExtractionError("暂时无法解析 B 站短链接") from exc
    raise ExtractionError("B 站短链接跳转次数过多")


def video_metadata(source: str, *, session=None) -> VideoMetadata:
    client = session or requests
    url = canonical_video_url(source, client)
    if urlsplit(url).hostname in APPLE_PODCAST_HOSTS:
        from .extractors.apple_podcasts import preview_episode
        episode = preview_episode(url, session=client)
        cover = thumbnail_url(episode.thumbnail_url)
        return VideoMetadata(source=episode.model_copy(update={"thumbnail_url": cover}), thumbnail_url=cover)
    if urlsplit(url).hostname in BILIBILI_VIDEO_HOSTS:
        bvid, part = bilibili_video_part(url)
        info = _fetch_json(client, "https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid})
        pages = info.get("pages") or []
        if info.get("bvid", bvid) != bvid or not isinstance(pages, list):
            raise ExtractionError("B 站返回了不匹配的视频信息")
        if part > max(1, len(pages)):
            raise UnsupportedSourceError(f"视频不存在第 {part} P")
        page = pages[part - 1] if pages else info
        if not isinstance(page, dict) or page.get("page", part) != part:
            raise ExtractionError("B 站返回了不匹配的分 P 信息")
        cover = thumbnail_url(info.get("pic"))
        return VideoMetadata(source=bilibili_source_info(info, url, bvid, part, pages, page),
                             thumbnail_url=cover)
    # oEmbed is substantially lighter than probing all yt-dlp formats. It does
    # not provide duration: leave it unknown rather than inventing a value.
    try:
        response = client.get("https://www.youtube.com/oembed", params={"url": url, "format": "json"},
                              timeout=12, allow_redirects=False)
        response.raise_for_status()
        info = response.json()
        title = info.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title missing")
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise ExtractionError("暂未获取到视频信息，仍可直接开始生成") from exc
    video_id = url.rsplit("=", 1)[1]
    cover = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return VideoMetadata(source=SourceInfo(platform="youtube", title=title.strip(), url=url,
                        video_id=video_id, author=info.get("author_name"), thumbnail_url=cover),
                         thumbnail_url=cover)


def shownotes_metadata(source: str, *, session=None) -> SourceInfo:
    """Explicit refresh of original publisher descriptions, without generation.

    Kept separate from the lightweight preview: YouTube descriptions need a
    metadata extractor, and a podcast missing its page summary may need RSS.
    Ordinary previews never gain either of those requests.
    """
    url = canonical_video_url(source, session=session)
    host = urlsplit(url).hostname
    if host in APPLE_PODCAST_HOSTS:
        from .extractors.apple_podcasts import episode_shownotes
        return episode_shownotes(url, session=session)
    if host in BILIBILI_VIDEO_HOSTS:
        return video_metadata(url, session=session).source
    from .extractors.youtube import youtube_metadata
    return youtube_metadata(url)

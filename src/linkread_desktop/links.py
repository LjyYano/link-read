"""Pure, network-free parsing of one media URL or a share/Markdown text.

The same fixtures are run against the Python, web and native implementations.
Never extract a BV identifier from an untrusted host or follow nested URLs.
"""
import html
import re
from urllib.parse import parse_qs, urlsplit, unquote

from .errors import UnsupportedSourceError

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
BILIBILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
SHORT_HOSTS = {"b23.tv", "www.b23.tv"}
APPLE_PODCAST_HOSTS = {"podcasts.apple.com"}
BVID = re.compile(r"BV[0-9A-Za-z]{10}\Z")
_URL = re.compile(r'https?://[^\s<>"\[\]【】「」]+', re.IGNORECASE)
_ESCAPES = re.compile(r'\\+([:/?.=&_#%\-])')


def pasted_url(source: str) -> str:
    """Leave local paths alone; normalize escapes only when a URL is present."""
    text = source.strip()
    if not re.search(r"https?\\*:", text, re.IGNORECASE):
        return text
    text = html.unescape(_ESCAPES.sub(r"\1", text))
    matches = list(dict.fromkeys(m.group().rstrip(").,;!，。；！）'") for m in _URL.finditer(text)))
    if len(matches) > 1:
        raise UnsupportedSourceError("检测到多个链接，请一次提交一个视频或播客单集")
    return matches[0] if matches else text


def _parsed(source: str):
    try:
        parsed = urlsplit(pasted_url(source))
        if (parsed.scheme.lower() != "https" or parsed.username is not None or parsed.password is not None
                or ":" in parsed.netloc or "@" in parsed.netloc
                or parsed.port is not None or parsed.hostname not in YOUTUBE_HOSTS | BILIBILI_HOSTS | SHORT_HOSTS | APPLE_PODCAST_HOSTS):
            raise ValueError()
        return parsed
    except ValueError as exc:
        raise UnsupportedSourceError("请输入 YouTube、B 站视频或 Apple Podcasts 单集 HTTPS 链接") from exc


def apple_podcast_ids(source: str) -> tuple[str, str, str]:
    """Return show ID, episode ID and storefront; never guess an episode from a show."""
    p = _parsed(source)
    if any(unquote(part) in {".", ".."} for part in p.path.split("/")):
        raise UnsupportedSourceError("播客链接不能包含相对路径")
    match = re.fullmatch(r"/(?:(?P<country>[A-Za-z]{2})/)?podcast/(?:[^/]+/)?id(?P<show>[1-9][0-9]{0,19})/?", p.path)
    episodes = parse_qs(p.query, keep_blank_values=True).get("i", [])
    if (p.hostname not in APPLE_PODCAST_HOSTS or match is None or len(episodes) != 1
            or not re.fullmatch(r"[1-9][0-9]{0,19}", episodes[0])):
        raise UnsupportedSourceError("请分享 Apple Podcasts 的单集链接（包含 i=单集编号），暂不支持整档节目")
    return match.group("show"), episodes[0], (match.group("country") or "us").lower()


def bilibili_part(source: str) -> tuple[str, int]:
    p = _parsed(source)
    if p.hostname not in BILIBILI_HOSTS:
        raise UnsupportedSourceError("不是受支持的 B 站视频链接")
    query = parse_qs(p.query, keep_blank_values=True)
    path = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10})/?", p.path)
    ids = query.get("bvid", [])
    if path:
        bvid = path.group(1)
        if ids and (len(ids) != 1 or ids[0] != bvid):
            raise UnsupportedSourceError("B 站链接包含冲突的视频编号")
    elif p.path.rstrip("/") in {"/list/watchlater", "/medialist/play/watchlater"}:
        if len(ids) != 1 or not BVID.fullmatch(ids[0]):
            raise UnsupportedSourceError("稍后再看链接需要包含唯一且有效的 bvid 参数")
        bvid = ids[0]
    else:
        raise UnsupportedSourceError("需要 B 站 BV 视频链接，或带 bvid 参数的稍后再看链接")
    pages = query.get("p", ["1"])
    if len(pages) != 1 or not re.fullmatch(r"[0-9]{1,4}", pages[0]) or not 1 <= int(pages[0]) <= 1000:
        raise UnsupportedSourceError("B 站分 P 参数需为 1–1000 的整数")
    return bvid, int(pages[0])


def canonical_link(source: str) -> str:
    """Canonicalize direct links; short URLs are resolved separately with a host allowlist."""
    p = _parsed(source)
    if p.hostname in APPLE_PODCAST_HOSTS:
        show_id, episode_id, country = apple_podcast_ids(source)
        return f"https://podcasts.apple.com/{country}/podcast/id{show_id}?i={episode_id}"
    if p.hostname in BILIBILI_HOSTS:
        bvid, part = bilibili_part(source)
        return f"https://www.bilibili.com/video/{bvid}?p={part}"
    if p.hostname in SHORT_HOSTS:
        if not re.fullmatch(r"/[A-Za-z0-9_-]+/?", p.path):
            raise UnsupportedSourceError("B 站短链接格式不正确")
        return f"https://b23.tv/{p.path.strip('/')}"
    path = p.path.strip("/").split("/")
    if p.hostname in {"youtu.be", "www.youtu.be"} and len(path) == 1:
        video = path[0]
    elif p.path.rstrip("/") == "/watch":
        values = parse_qs(p.query, keep_blank_values=True).get("v", [])
        video = values[0] if len(values) == 1 else ""
    elif len(path) == 2 and path[0] in {"shorts", "live", "embed"}:
        video = path[1]
    else:
        video = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
        raise UnsupportedSourceError("YouTube 需要单个视频链接，不能是整张播放列表")
    return f"https://www.youtube.com/watch?v={video}"

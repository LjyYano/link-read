"""Validated source dispatch; URLs are normalized before platform extractors run."""

import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .errors import ExtractionError, SubtitleLoginRequiredError, SubtitleUnavailableError, UnsupportedSourceError
from .asr.config import AsrConfig
from .extractors.bilibili import BILIBILI_SHORT_HOSTS, BILIBILI_VIDEO_HOSTS, bilibili_video_part, extract_bilibili
from .extractors.local import extract_local
from .extractors.youtube import extract_youtube
from .models import SourceInfo, TranscriptDocument
from .links import pasted_url, canonical_link, apple_podcast_ids, APPLE_PODCAST_HOSTS
from .progress import ProgressCallback, report

_YOUTUBE_DOMAINS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be",
}
_BILIBILI_DOMAINS = BILIBILI_VIDEO_HOSTS | BILIBILI_SHORT_HOSTS
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")


def _normalize_video_url(raw: str) -> str:
    return pasted_url(raw)


def _canonical_youtube_url(url: str) -> str:
    return canonical_link(url)


def video_source_key(source: str) -> str | None:
    """Identify a video or podcast episode without contacting the platform."""
    text = _normalize_video_url(source)
    parsed = urlsplit(text)
    if parsed.scheme == "https" and parsed.hostname in _YOUTUBE_DOMAINS | _BILIBILI_DOMAINS | APPLE_PODCAST_HOSTS:
        text = canonical_link(text)
        parsed = urlsplit(text)
        if parsed.hostname in APPLE_PODCAST_HOSTS:
            show_id, episode_id, _ = apple_podcast_ids(text)
            return f"apple-podcasts-{show_id}-{episode_id}"
        if parsed.hostname in _YOUTUBE_DOMAINS:
            video_id = _canonical_youtube_url(text).rsplit("=", 1)[1]
            return f"youtube-{video_id}"
        if parsed.hostname in BILIBILI_VIDEO_HOSTS:
            video_id, part = bilibili_video_part(text)
            return f"bilibili-{video_id}-p{part}"
    return None


def extract(
    source: str | Path, *, progress: ProgressCallback | None = None,
    cookies_from_browser: str | None = None,
    cookies_file: Path | None = None,
    asr: AsrConfig | None = None, asr_cache_dir: Path | None = None,
) -> TranscriptDocument:
    text = _normalize_video_url(str(source))
    def with_fallback(call, url):
        try:
            return call()
        except (SubtitleUnavailableError, SubtitleLoginRequiredError) as exc:
            subtitle_login = isinstance(exc, SubtitleLoginRequiredError)
            if subtitle_login and (asr is None or urlsplit(url).hostname not in _BILIBILI_DOMAINS):
                raise
            if asr is None:
                raise SubtitleUnavailableError("没有可获取的字幕；可在设置中开启语音识别，或导入字幕文件") from exc
            metadata = exc.source if isinstance(exc.source, SourceInfo) else None
            requested_key = video_source_key(url)
            metadata_key = video_source_key(metadata.url or "") if metadata is not None else None
            if metadata is not None and requested_key and metadata_key != requested_key:
                raise ExtractionError("已获取的来源信息与当前单集不匹配，未启动语音识别") from exc
            audio_url = url
            if subtitle_login:
                # Bind the audio request to the video and part that returned
                # the flag, even if the original input was a short link.
                if not metadata or not metadata.url or not metadata_key or not metadata_key.startswith("bilibili-"):
                    raise ExtractionError("无法确认当前 B 站视频的分 P，未启动语音识别") from exc
                audio_url = metadata.url
            from .asr.pipeline import transcribe_video
            result = transcribe_video(audio_url, asr, cache_dir=asr_cache_dir or Path("library/.asr"),
                                      progress=progress, cookies_from_browser=cookies_from_browser,
                                      cookies_file=cookies_file)
            if metadata is not None and metadata.description:
                if not metadata_key or metadata_key != video_source_key(result.source.url or ""):
                    raise ExtractionError("语音识别结果与已获取的单集信息不匹配，未合并简介")
                # RSS descriptions can be more complete than the audio download
                # metadata. Do not replace ASR timestamps or other source fields.
                result = result.model_copy(update={"source": result.source.model_copy(update={
                    "description": metadata.description,
                    "description_format": metadata.description_format,
                })})
            return result
    parsed = urlsplit(text)
    if (cookies_from_browser is not None or cookies_file is not None) and (
        parsed.scheme != "https" or (parsed.hostname or "").lower() not in _YOUTUBE_DOMAINS
    ):
        raise UnsupportedSourceError("Cookie 参数仅适用于 YouTube HTTPS 视频")
    if parsed.scheme in {"http", "https"}:
        if parsed.scheme != "https":
            raise UnsupportedSourceError("仅支持 HTTPS 链接")
        host = (parsed.hostname or "").lower()
        if host in APPLE_PODCAST_HOSTS:
            from .extractors.apple_podcasts import extract_apple_podcasts
            canonical = canonical_link(text)
            report(progress, "source", "识别为 Apple Podcasts 单集")
            return with_fallback(lambda: extract_apple_podcasts(canonical, progress=progress), canonical)
        if host in _BILIBILI_DOMAINS:
            report(progress, "source", "识别为 B 站视频")
            return with_fallback(lambda: extract_bilibili(text, progress=progress), text)
        if host in _YOUTUBE_DOMAINS:
            canonical = _canonical_youtube_url(text)
            report(progress, "source", "识别为 YouTube 单个视频，已规范化链接")
            auth_options = {}
            if cookies_from_browser is not None:
                auth_options["cookies_from_browser"] = cookies_from_browser
            if cookies_file is not None:
                auth_options["cookies_file"] = cookies_file
            return with_fallback(lambda: extract_youtube(canonical, progress=progress, **auth_options), canonical)
        raise UnsupportedSourceError("当前支持 Bilibili、YouTube、Apple Podcasts 单集链接以及本地 SRT/VTT/JSON 文件")
    if parsed.scheme and len(parsed.scheme) > 1:
        raise UnsupportedSourceError("不支持的链接协议")
    path = Path(str(source)).expanduser()
    if not path.is_file():
        raise UnsupportedSourceError(f"文件不存在：{path}")
    report(progress, "source", f"识别为本地字幕：{path.name}")
    return extract_local(path, progress=progress)

"""Download a single authorized audio track, transcribe and cache the transcript."""
from hashlib import sha256
import json
import os
import shutil
from pathlib import Path
import time
from urllib.parse import urlsplit

from ..errors import LinkReadError, error_operation
from ..links import APPLE_PODCAST_HOSTS
from ..extractors.youtube import _QuietLogger, _cookie_options
from ..metadata import canonical_video_url, thumbnail_url
from ..models import SourceInfo, TranscriptDocument
from ..pipeline import video_source_key
from ..progress import report
from ..storage import write_text
from .bailian import BailianAsr, MAX_AUDIO_BYTES
from .config import AsrConfig, AsrError
from .segments import ffmpeg_binary, split_audio, transcribe_parts, _manifest_parts


@error_operation("audio")
def download_audio(url, folder, config, *, progress=None, auth_options=None):
    if urlsplit(url).hostname in APPLE_PODCAST_HOSTS:
        from ..extractors.apple_podcasts import download_episode_audio
        return download_episode_audio(url, folder, config, progress=progress)
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise AsrError("下载音频需要 yt-dlp，请检查桌面版后端是否完整安装") from exc
    download_limit = 2 * 1024**3 if config.chunk_minutes else MAX_AUDIO_BYTES
    last = 0.0
    def hook(event):
        nonlocal last
        size = event.get("downloaded_bytes", 0)
        if size > download_limit:
            raise AsrError(f"音轨超过下载上限 {download_limit // 1048576} MB，已停止下载")
        if event.get("status") == "downloading" and time.monotonic() - last > 1:
            total = event.get("total_bytes") or event.get("total_bytes_estimate")
            text = f"下载音频：{size / 1048576:.1f} MB"
            if total:
                text += f" / {total / 1048576:.1f} MB"
            report(progress, "audio_download", text)
            last = time.monotonic()
    opts = {"format": "bestaudio[ext=m4a]/bestaudio", "noplaylist": True,
            "quiet": True, "no_warnings": True, "logger": _QuietLogger(),
            "socket_timeout": 20, "retries": 2, "fragment_retries": 2,
            "outtmpl": str(folder / "audio.%(ext)s"), "max_filesize": download_limit,
            "progress_hooks": [hook], "overwrites": False}
    opts.update(auth_options or {})
    report(progress, "audio_download", "正在获取可访问的音轨（不下载视频画面）")
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not isinstance(info, dict) or info.get("_type") in {"playlist", "multi_video"} or info.get("is_live"):
                raise AsrError("ASR 仅支持单个已结束的视频，不支持直播和播放列表")
            duration = info.get("duration")
            if not isinstance(duration, (int, float)) or not 0 < duration <= config.max_minutes * 60:
                raise AsrError(f"无法确认视频时长，或超过 {config.max_minutes} 分钟转写上限；未下载或提交转写")
            if config.chunk_minutes and duration > config.chunk_minutes * 60:
                # Check the selected local tool before downloading or charging.
                ffmpeg_binary(config.ffmpeg_path) if config.ffmpeg_path else ffmpeg_binary()
            expected = ydl.prepare_filename(info)
            ydl.process_info(info)
            audio = Path(info.get("filepath") or expected)
            if audio.is_symlink() or audio.parent.resolve() != folder.resolve() or not audio.is_file():
                raise AsrError("音频下载未完成，请重试")
            if not 0 < audio.stat().st_size <= download_limit:
                raise AsrError("下载的音频为空或超过下载上限")
            # Audio-only selection never falls back to downloading a full video.
            description = info.get("description")
            description = description if isinstance(description, str) and description.strip() else None
            source = SourceInfo(platform="bilibili" if "bilibili.com" in url else "youtube",
                                title=info.get("title") or "未命名视频", url=url,
                                video_id=video_source_key(url).split("-", 1)[1] if video_source_key(url) else None,
                                author=info.get("uploader") or info.get("channel"),
                                duration_ms=round(duration * 1000), thumbnail_url=thumbnail_url(info.get("thumbnail")),
                                description=description, description_format="text" if description is not None else None)
            if source.platform == "bilibili":
                source.video_id = source.video_id.rsplit("-p", 1)[0]
            report(progress, "audio_download_done", "音频下载完成")
            return audio, source
    except LinkReadError:
        raise
    except Exception as exc:
        # yt-dlp's error can contain signed media URLs or local Cookie paths.
        raise AsrError("音轨下载失败，请检查访问权限、网络或更新 yt-dlp；不会绕过登录或付费限制") from exc


@error_operation("asr")
def transcribe_video(source, config: AsrConfig, *, cache_dir: Path, progress=None,
                     cookies_from_browser=None, cookies_file=None) -> TranscriptDocument:
    token = config.credential()
    url = canonical_video_url(source)
    key = video_source_key(url)
    account = sha256(token.encode()).hexdigest()
    fingerprint = sha256(f"{key}\n{config.base_url}\n{config.model}\n{account}".encode()).hexdigest()
    folder = cache_dir / fingerprint
    if cache_dir.is_symlink() or folder.is_symlink():
        raise AsrError("ASR 缓存目录不可使用符号链接")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    for path in folder.iterdir():
        if path.is_symlink():
            raise AsrError("ASR 缓存含有不安全的符号链接")
    result_file = folder / "transcript.json"
    if result_file.is_file():
        raw = TranscriptDocument.model_validate_json(result_file.read_text())
        if video_source_key(raw.source.url or "") != key or raw.transcript_source != "asr":
            raise AsrError("ASR 缓存与当前视频或播客单集不匹配")
        report(progress, "asr_reuse", "已复用语音识别字幕，无需再次上传或计费")
        return raw
    report(progress, "asr_start", "没有可读取的公开字幕，正在准备语音转写")
    metadata = folder / "audio.json"
    if metadata.is_file():
        data = json.loads(metadata.read_text())
        audio = folder / data["filename"]
        if audio.parent != folder or not audio.is_file():
            raise AsrError("音频缓存不可用，请检查 ASR 缓存目录")
        info = SourceInfo.model_validate(data["source"])
        report(progress, "audio_download_done", "已复用下载的音频")
    else:
        auth = _cookie_options(cookies_from_browser, cookies_file)
        audio, info = download_audio(url, folder, config, progress=progress, auth_options=auth)
        write_text(metadata, json.dumps({"filename": audio.name, "source": info.model_dump()}, ensure_ascii=False))
    parts = None
    # An already submitted legacy whole-audio job always resumes; do not create
    # extra chunk jobs merely because the new default is 5-minute segmentation.
    pending_parts = [path for path in sorted(folder.glob("parts-v1-*"))
                     if path.is_dir() and (list(path.glob("*.task.json")) or list(path.glob("*.result.json")))]
    if pending_parts and not (folder / "task.json").exists():
        report(progress, "asr_resume", "继续上次的分片计划，避免修改分片时长后重复计费")
        parts = _manifest_parts(pending_parts[0])
        segments = transcribe_parts(parts, config, progress=progress, workers=2)
    elif (config.chunk_minutes and (info.duration_ms or 0) > config.chunk_minutes * 60_000
            and not (folder / "task.json").exists()):
        parts = split_audio(audio, folder, minutes=config.chunk_minutes,
                            duration_ms=info.duration_ms, progress=progress, ffmpeg_path=config.ffmpeg_path)
        segments = transcribe_parts(parts, config, progress=progress, workers=2)
    else:
        client = BailianAsr(config)
        segments = client.transcribe(audio, folder / "task.json", progress=progress)
    raw = TranscriptDocument(schema_version="1.1", source=info, transcript_source="asr", segments=segments)
    write_text(result_file, raw.model_dump_json(indent=2, exclude_none=True))
    # Keep only the reusable transcript after success; no raw audio kept long-term.
    if parts:
        shutil.rmtree(parts[0].path.parent)
    audio.unlink(missing_ok=True)
    metadata.unlink(missing_ok=True)
    (folder / "task.json").unlink(missing_ok=True)
    return raw

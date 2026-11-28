"""Lossless PCM slicing and resumable, bounded ASR jobs.

Offsets come from actual WAV sample counts, never from `chunk_index * 5 min`.
No audio overlap => repeated real speech is never removed by text deduplication.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import wave

from ..ai.batches import ordered_batches
from ..models import TranscriptSegment
from ..progress import report
from ..errors import error_operation
from ..storage import write_text
from .bailian import BailianAsr
from .config import AsrError

RATE = 16000


def ffmpeg_binary(custom: str = "") -> str:
    if custom:
        if (not isinstance(custom, str) or len(custom) > 4096
                or any(ord(char) < 32 or ord(char) == 127 for char in custom)
                or not Path(custom).is_absolute()):
            raise AsrError("FFmpeg 路径无效，请在设置中重新选择程序", code="configuration_invalid", stage="audio", action="setup")
        candidate = Path(custom)
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise AsrError("所选 FFmpeg 不存在或没有执行权限，请在设置中重新选择程序", code="dependency_missing", stage="audio", action="setup")
        return str(candidate.resolve())
    candidate = shutil.which("ffmpeg")
    if candidate:
        return candidate
    for value in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if os.path.isfile(value) and os.access(value, os.X_OK):
            return value
    raise AsrError("长音频分片需要 FFmpeg。请安装后重试，或在语音识别高级设置中选择整段转写",
                   code="dependency_missing", stage="audio", action="setup")


def digest(path: Path) -> str:
    result = sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


@dataclass(frozen=True)
class AudioPart:
    path: Path
    offset_ms: int
    duration_ms: int
    checksum: str


def _manifest_parts(folder: Path) -> list[AudioPart]:
    try:
        if folder.is_symlink() or (folder / "manifest.json").is_symlink():
            raise ValueError()
        data = json.loads((folder / "manifest.json").read_text())
        if data.get("version") != 1 or not data.get("parts"):
            raise ValueError()
        parts = []
        samples = 0
        for entry in data["parts"]:
            filename = entry["filename"]
            if Path(filename).name != filename or not filename.endswith(".wav"):
                raise ValueError()
            file = folder / filename
            if file.is_symlink() or not file.is_file():
                raise ValueError()
            with wave.open(str(file), "rb") as wav:
                if wav.getframerate() != RATE or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                    raise ValueError()
                frames = wav.getnframes()
            if frames != entry["frames"] or frames <= 0 or digest(file) != entry["sha256"]:
                raise ValueError()
            start, end = round(samples * 1000 / RATE), round((samples + frames) * 1000 / RATE)
            parts.append(AudioPart(file, start, end - start, entry["sha256"]))
            samples += frames
        return parts
    except (ValueError, TypeError, KeyError, OSError, wave.Error) as exc:
        raise AsrError("音频分片缓存不完整或已被改动；已停止，避免将旧转写错误地关联到新音频") from exc


@error_operation("audio")
def split_audio(audio: Path, folder: Path, *, minutes: int, duration_ms: int, progress=None, ffmpeg_path: str = "") -> list[AudioPart]:
    target = folder / f"parts-v1-{minutes * 60}"
    if target.exists() or target.is_symlink():
        return _manifest_parts(target)
    binary = ffmpeg_binary(ffmpeg_path) if ffmpeg_path else ffmpeg_binary()
    # PCM 16kHz mono s16 costs 32 kB/s. Bound local disk expansion before decoding.
    expected_bytes = duration_ms * 32
    if shutil.disk_usage(folder).free < expected_bytes * 1.2 + 20 * 1024 * 1024:
        raise AsrError("磁盘剩余空间不足以进行音频分片，请清理空间后重试", code="storage_full")
    report(progress, "audio_split", f"正在将音轨按约 {minutes} 分钟分片，不下载视频画面")
    with TemporaryDirectory(prefix=".split-", dir=folder) as tmp:
        stage = Path(tmp)
        ffmpeg_env = {
            key: value for key, value in os.environ.items()
            if key not in {"LINKREAD_API_TOKEN", "LINKREAD_ASR_TOKEN", "ANTHROPIC_API_KEY"}
        }
        try:
            subprocess.run([
                binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-i", str(audio), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(RATE),
                "-c:a", "pcm_s16le", "-f", "segment", "-segment_time", str(minutes * 60),
                "-reset_timestamps", "1", str(stage / "part-%04d.wav"),
            ], check=True, env=ffmpeg_env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=max(120, min(1800, duration_ms // 1000)))
        except FileNotFoundError as exc:
            raise AsrError("没有找到 FFmpeg，请安装后重试", code="dependency_missing", action="setup") from exc
        except (subprocess.SubprocessError, OSError) as exc:
            raise AsrError("音频分片失败或超时；原音轨已保留，未提交收费转写") from exc
        entries = []
        for file in sorted(stage.glob("part-*.wav")):
            with wave.open(str(file), "rb") as wav:
                frames = wav.getnframes()
            if frames:
                entries.append({"filename": file.name, "frames": frames, "sha256": digest(file)})
        if not entries:
            raise AsrError("音频分片结果为空，未提交转写")
        write_text(stage / "manifest.json", json.dumps({"version": 1, "parts": entries}))
        stage.rename(target)
    parts = _manifest_parts(target)
    report(progress, "audio_split_done", f"音频已分成 {len(parts)} 片，每片约 {minutes} 分钟")
    return parts


def transcribe_parts(parts: list[AudioPart], config, *, progress=None, workers=2) -> list[TranscriptSegment]:
    total = len(parts)
    report(progress, "asr_chunks_start", f"开始分片转写：{total} 片，最多同时处理 {min(workers, total)} 片", completed=0, total=total)

    def process(index: int, part: AudioPart):
        result_file = part.path.with_suffix(".result.json")
        state_file = part.path.with_suffix(".task.json")
        if result_file.is_symlink() or state_file.is_symlink():
            raise AsrError("分片转写缓存不可使用符号链接")
        if result_file.is_file():
            try:
                data = json.loads(result_file.read_text())
                if data["sha256"] != part.checksum:
                    raise ValueError()
                local = [TranscriptSegment.model_validate(row) for row in data["segments"]]
            except (ValueError, KeyError, TypeError) as exc:
                raise AsrError("分片识别结果与音频不匹配，未重新提交以避免重复计费") from exc
            report(progress, "asr_chunk_reuse", f"第 {index}/{total} 片已复用，无需再次转写")
        else:
            def feedback(event):
                # A single chunk's asr_done/upload_done must not complete the entire lane.
                report(progress, "asr_chunk_progress", f"第 {index}/{total} 片 · {event.message}")
            report(progress, "asr_chunk_progress", f"正在处理第 {index}/{total} 片")
            local = BailianAsr(config).transcribe(part.path, state_file, progress=feedback, allow_empty=True)
            # Store completed silent chunks too: silence is not a reason to recharge.
            write_text(result_file, json.dumps({"sha256": part.checksum,
                "segments": [row.model_dump() for row in local]}, ensure_ascii=False))
        shifted = []
        for sentence in local:
            if sentence.start_ms > part.duration_ms + 1000 or (sentence.end_ms or 0) > part.duration_ms + 1000:
                raise AsrError("分片识别返回超出音频范围的时间戳")
            shifted.append(TranscriptSegment(start_ms=part.offset_ms + sentence.start_ms,
                end_ms=part.offset_ms + sentence.end_ms if sentence.end_ms is not None else None,
                text=sentence.text))
        return shifted

    def done(index, count):
        report(progress, "asr_chunk_done", f"已完成 {count}/{total} 片（第 {index} 片完成）", completed=count, total=total)
    results = ordered_batches(parts, process, workers=workers, on_complete=done)
    sentences = sorted([item for result in results for item in result], key=lambda x: x.start_ms)
    if not sentences:
        raise AsrError("音频分片全部完成，但未识别到可用语句")
    report(progress, "asr_done", f"分片转写完成：{total} 片，{len(sentences)} 条语句", completed=total, total=total)
    return sentences

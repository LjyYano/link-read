from dataclasses import replace
from pathlib import Path
import shutil

import pytest

from linkread_desktop.asr.config import AsrError
from linkread_desktop.asr.segments import ffmpeg_binary, split_audio


def test_selected_ffmpeg_path_is_exact_and_does_not_fall_back(tmp_path, monkeypatch):
    tool = tmp_path / "Audio Tools" / "ffmpeg"
    tool.parent.mkdir()
    tool.write_text("offline fixture")
    tool.chmod(0o700)
    monkeypatch.setattr(shutil, "which", lambda _: pytest.fail("explicit path must not fall back to PATH"))
    assert ffmpeg_binary(str(tool)) == str(tool.resolve())
    tool.chmod(0o600)
    with pytest.raises(AsrError, match="执行权限"):
        ffmpeg_binary(str(tool))
    with pytest.raises(AsrError, match="不存在"):
        ffmpeg_binary(str(tmp_path / "missing"))


@pytest.mark.parametrize("path", ["ffmpeg", "../ffmpeg", "/tmp/ffmpeg\n-version", "/" + "x" * 4096])
def test_invalid_selected_tool_is_rejected_before_any_process(path, monkeypatch):
    monkeypatch.setattr("linkread_desktop.asr.segments.subprocess.run", lambda *a, **kw: pytest.fail("must not launch invalid path"))
    with pytest.raises(AsrError, match="路径无效"):
        ffmpeg_binary(path)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg integration requires the binary")
def test_explicit_ffmpeg_outside_path_runs_the_real_pcm_splitter(tmp_path, monkeypatch):
    import wave
    executable = tmp_path / "program with spaces"
    executable.symlink_to(Path(shutil.which("ffmpeg")).resolve())
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 8000)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    parts = split_audio(audio, tmp_path, minutes=5, duration_ms=500, ffmpeg_path=str(executable))
    assert len(parts) == 1
    assert parts[0].offset_ms == 0
    assert parts[0].duration_ms == 500


def test_explicit_tool_reaches_asr_split_without_changing_cache_identity(tmp_path, monkeypatch):
    from tests.test_asr_segments import _audio_cache
    from linkread_desktop.asr.pipeline import transcribe_video
    from linkread_desktop.models import TranscriptSegment
    config, _ = _audio_cache(tmp_path)
    path = "/Users/yano/Tools/ffmpeg"
    seen = []
    def split(audio, folder, **kwargs):
        seen.append(kwargs["ffmpeg_path"])
        return []
    monkeypatch.setattr("linkread_desktop.asr.pipeline.split_audio", split)
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_parts", lambda *args, **kwargs: [TranscriptSegment(start_ms=0, end_ms=500, text="原文")])
    result = transcribe_video("https://youtu.be/1-Fo8McCD9w", replace(config, ffmpeg_path=path), cache_dir=tmp_path)
    assert seen == [path]
    assert result.segments[0].text == "原文"

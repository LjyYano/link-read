import json
from pathlib import Path
import shutil
import wave
import pytest

from linkread_desktop.asr.config import AsrConfig, AsrError
from linkread_desktop.asr.segments import AudioPart, split_audio, transcribe_parts, digest
from linkread_desktop.asr.bailian import BailianAsr, segments_from_result
from linkread_desktop.models import TranscriptSegment


def wav(path, samples):
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
        stream.writeframes(b'\0\0' * samples)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg integration requires the binary')
def test_real_ffmpeg_split_preserves_every_audio_sample_and_tail(tmp_path):
    path = tmp_path/'audio.wav'; samples = 16000 * 605 + 8000
    wav(path, samples)
    events=[]
    parts = split_audio(path, tmp_path, minutes=5, duration_ms=605500, progress=events.append)
    assert len(parts) == 3
    frames=[]
    for item in parts:
        with wave.open(str(item.path), 'rb') as f: frames.append(f.getnframes())
    assert sum(frames) == samples
    assert [p.offset_ms for p in parts] == [round(sum(frames[:i])*1000/16000) for i in range(3)]
    assert parts[-1].offset_ms + parts[-1].duration_ms == 605500
    assert abs(parts[0].duration_ms-300000) < 100
    assert split_audio(path, tmp_path, minutes=5, duration_ms=605500) == parts


def test_chunk_retry_reuses_only_verified_completed_parts(tmp_path, monkeypatch):
    parts=[]
    for index in range(3):
        path=tmp_path/f'part-{index}.wav';wav(path,16000)
        parts.append(AudioPart(path,index*1000,1000,digest(path)))
    calls=[]
    def first(self, audio, state, **kw):
        calls.append(audio.name)
        if audio.name == 'part-1.wav': raise AsrError('network')
        return [TranscriptSegment(start_ms=100,end_ms=800,text='真实句子')]
    monkeypatch.setattr(BailianAsr,'transcribe',first)
    config=AsrConfig(token='test-only')
    with pytest.raises(AsrError): transcribe_parts(parts,config,workers=1)
    assert calls == ['part-0.wav','part-1.wav']
    calls.clear()
    def second(self, audio, state, **kw):
        calls.append(audio.name)
        return [] if audio.name=='part-1.wav' else [TranscriptSegment(start_ms=100,end_ms=800,text='真实句子')]
    monkeypatch.setattr(BailianAsr,'transcribe',second)
    result=transcribe_parts(parts,config,workers=1)
    assert calls==['part-1.wav','part-2.wav']
    # Same text at different times is real repeated speech, not dropped as duplicate.
    assert [(x.start_ms,x.end_ms) for x in result]==[(100,800),(2100,2800)]
    calls.clear();assert transcribe_parts(parts,config,workers=1)==result;assert not calls


def test_silent_part_is_allowed_but_empty_whole_audio_is_not():
    empty={'transcripts':[{'channel_id':0,'sentences':[]}]}
    assert segments_from_result(empty,allow_empty=True)==[]
    with pytest.raises(AsrError): segments_from_result(empty)


def test_missing_ffmpeg_stops_before_cloud_jobs(tmp_path,monkeypatch):
    monkeypatch.setattr('linkread_desktop.asr.segments.ffmpeg_binary',lambda: (_ for _ in ()).throw(AsrError('需要 FFmpeg')))
    with pytest.raises(AsrError,match='FFmpeg'):
        split_audio(tmp_path/'none',tmp_path,minutes=5,duration_ms=600000)
    assert not list(tmp_path.rglob('*.task.json'))


def test_invalid_chunk_options_rejected():
    for minutes in [-1,1,7,True]:
        with pytest.raises(AsrError): AsrConfig(token='test',chunk_minutes=minutes).credential()


def _audio_cache(tmp_path):
    from hashlib import sha256
    from linkread_desktop.models import SourceInfo
    config = AsrConfig(token='test-only')
    key='youtube-1-Fo8McCD9w'
    account=sha256(config.token.encode()).hexdigest()
    fingerprint=sha256(f'{key}\n{config.base_url}\n{config.model}\n{account}'.encode()).hexdigest()
    folder=tmp_path/fingerprint;folder.mkdir()
    audio=folder/'audio.wav';wav(audio,16000)
    info=SourceInfo(platform='youtube',title='长音频',video_id='1-Fo8McCD9w',
        url='https://www.youtube.com/watch?v=1-Fo8McCD9w',duration_ms=601000)
    (folder/'audio.json').write_text(json.dumps({'filename':audio.name,'source':info.model_dump()}))
    return config, folder


def test_legacy_whole_job_resumes_before_new_default_chunks(tmp_path, monkeypatch):
    from linkread_desktop.asr.pipeline import transcribe_video
    config,folder=_audio_cache(tmp_path)
    (folder/'task.json').write_text('{"task_id":"previous-paid-job"}')
    monkeypatch.setattr('linkread_desktop.asr.pipeline.split_audio', lambda *a,**k: pytest.fail('must not create new paid chunk jobs'))
    seen=[]
    def resume(self,audio,state,**kw):
        seen.append(json.loads(state.read_text())['task_id'])
        return [TranscriptSegment(start_ms=0,end_ms=500,text='已有云任务')]
    monkeypatch.setattr(BailianAsr,'transcribe',resume)
    result=transcribe_video('https://youtu.be/1-Fo8McCD9w',config,cache_dir=tmp_path)
    assert seen==['previous-paid-job']
    assert result.transcript_source=='asr'
    assert (folder/'transcript.json').is_file()


def test_changed_chunk_size_keeps_previous_paid_plan(tmp_path, monkeypatch):
    from dataclasses import replace
    from linkread_desktop.asr.pipeline import transcribe_video
    config,folder=_audio_cache(tmp_path)
    previous=folder/'parts-v1-300';previous.mkdir()
    audio=previous/'part-0000.wav';wav(audio,16000)
    (previous/'part-0000.task.json').write_text('{"task_id":"existing"}')
    part=AudioPart(audio,0,1000,digest(audio))
    monkeypatch.setattr('linkread_desktop.asr.pipeline._manifest_parts',lambda path: [part] if path==previous else pytest.fail('changed plan'))
    monkeypatch.setattr('linkread_desktop.asr.pipeline.split_audio',lambda *a,**k: pytest.fail('must reuse prior plan'))
    seen=[]
    def resume(parts,config,**kw):
        seen.extend(parts)
        return [TranscriptSegment(start_ms=0,end_ms=500,text='已保存的计划')]
    monkeypatch.setattr('linkread_desktop.asr.pipeline.transcribe_parts',resume)
    result=transcribe_video('https://youtu.be/1-Fo8McCD9w',replace(config,chunk_minutes=10),cache_dir=tmp_path)
    assert seen==[part]
    assert result.segments[0].text=='已保存的计划'
    assert not previous.exists()

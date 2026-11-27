import json
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import pytest
import requests

from linkread_desktop.asr.bailian import BailianAsr, MultipartBody, segments_from_result
from linkread_desktop.asr.config import AsrConfig, AsrError
from linkread_desktop.asr.pipeline import download_audio, transcribe_video
from linkread_desktop.errors import ExtractionError, SubtitleUnavailableError
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.pipeline import extract

URL = "https://www.youtube.com/watch?v=1-Fo8McCD9w"
BURL = "https://www.bilibili.com/video/BV1rxqmBhE91?p=2"
CONFIG = AsrConfig(token="private-test-token")
RESULT = {"transcripts": [{"channel_id": 0, "sentences": [{"begin_time": 125, "end_time": 1420, "text": "完整的识别文本。"}]}]}


def raw():
    return TranscriptDocument(source=SourceInfo(platform="youtube", title="test", url=URL),
                              transcript_source="subtitle", segments=[TranscriptSegment(start_ms=0, text="字幕")])


def test_existing_subtitles_never_download_or_call_asr(monkeypatch, tmp_path):
    monkeypatch.setattr("linkread_desktop.pipeline.extract_youtube", lambda *a, **k: raw())
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video", lambda *a, **k: pytest.fail("ASR must not run"))
    assert extract(URL, asr=CONFIG, asr_cache_dir=tmp_path).transcript_source == "subtitle"


def test_network_error_never_triggers_paid_asr(monkeypatch, tmp_path):
    def fail(*a, **k): raise ExtractionError("network/permission")
    monkeypatch.setattr("linkread_desktop.pipeline.extract_bilibili", fail)
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video", lambda *a, **k: pytest.fail("ASR must not run"))
    with pytest.raises(ExtractionError, match="network"): extract(BURL, asr=CONFIG, asr_cache_dir=tmp_path)


def test_disabled_asr_never_downloads(monkeypatch):
    def missing(*a, **k): raise SubtitleUnavailableError("no subtitles")
    monkeypatch.setattr("linkread_desktop.pipeline.extract_bilibili", missing)
    with pytest.raises(SubtitleUnavailableError, match="开启语音识别"): extract(BURL)


def test_no_subtitles_uses_explicit_asr_configuration(monkeypatch, tmp_path):
    def missing(*a, **k): raise SubtitleUnavailableError("no subtitles")
    monkeypatch.setattr("linkread_desktop.pipeline.extract_bilibili", missing)
    seen = []
    def fallback(url, config, **kwargs):
        seen.append((url, config, kwargs))
        result = raw(); result.transcript_source = "asr"; result.schema_version = "1.1"
        return result
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video", fallback)
    assert extract(BURL, asr=CONFIG, asr_cache_dir=tmp_path).transcript_source == "asr"
    assert seen[0][0] == BURL and seen[0][1] is CONFIG
    assert seen[0][2]["cache_dir"] == tmp_path


def test_sentence_times_are_real_not_fabricated():
    got = segments_from_result(RESULT)
    assert (got[0].start_ms, got[0].end_ms) == (125, 1420)
    with pytest.raises(AsrError): segments_from_result({"transcripts": [{"text": "text without timestamps"}]})


def test_multipart_is_streamed_and_has_exact_length(tmp_path):
    f = tmp_path / "audio.m4a"; f.write_bytes(b"hello" * 10000)
    body = MultipartBody({"key": "file", "policy": "secret-policy"}, f)
    chunks=[]
    while c := body.read(997): chunks.append(c)
    assert len(b"".join(chunks)) == len(body)
    assert max(map(len, chunks)) == 997
    assert b"hello" * 10000 in b"".join(chunks)
    body.close()


class Response:
    def __init__(self, data=None, status=200): self.data, self.status_code = data, status
    def json(self): return self.data
    def close(self): pass


class Client:
    def __init__(self, responses): self.responses, self.calls = iter(responses), []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(self.responses)


def test_upload_submit_poll_and_result_keep_token_on_api_host_only(tmp_path):
    policy={"upload_host": "https://dashscope-file-mgr.oss-cn-beijing.aliyuncs.com", "upload_dir":"test", "oss_access_key_id":"id", "signature":"sig", "policy":"policy", "x_oss_object_acl":"private", "x_oss_forbid_overwrite":"true"}
    client=Client([Response({"data":policy}),Response(),Response({"output":{"task_id":"test-task-123"}}),
                   Response({"output":{"task_status":"SUCCEEDED","results":[{"subtask_status":"SUCCEEDED","transcription_url":"https://dashscope-result-bj.oss-cn-beijing.aliyuncs.com/file.json?Signature=private"}]}}), Response(RESULT)])
    path=tmp_path/"audio.m4a"; path.write_bytes(b"dummy")
    api=BailianAsr(CONFIG, session=client)
    got=api.transcribe(path,tmp_path/"task.json")
    assert got[0].text == "完整的识别文本。"
    for _,url,kwargs in client.calls:
        if ".oss-" in url: assert "Authorization" not in kwargs["headers"]
        else: assert kwargs["headers"]["Authorization"] == "Bearer private-test-token"
        assert kwargs["allow_redirects"] is False
    assert client.calls[2][2]["headers"]["X-DashScope-Async"] == "enable"
    assert client.calls[2][2]["json"]["model"] == "paraformer-v2"
    assert "private-test-token" not in (tmp_path/"task.json").read_text()


def test_retry_resumes_task_without_upload_or_new_paid_submission(tmp_path, monkeypatch):
    state=tmp_path/"task.json";state.write_text(json.dumps({"task_id":"saved-task-123"}))
    api=BailianAsr(CONFIG)
    monkeypatch.setattr(api,"upload",lambda *a,**k:pytest.fail("must not upload again"))
    monkeypatch.setattr(api,"submit",lambda *a,**k:pytest.fail("must not resubmit"))
    monkeypatch.setattr(api,"wait",lambda *a,**k:RESULT)
    assert api.transcribe(tmp_path/"unused.m4a",state)[0].start_ms == 125


def test_ambiguous_submission_is_not_automatically_recharged(tmp_path):
    state=tmp_path/"task.json";state.write_text('{"submitting":true}')
    with pytest.raises(AsrError,match="重复计费"): BailianAsr(CONFIG).transcribe(tmp_path/"none",state)


def test_successful_transcription_reuses_cache_and_cleans_audio(tmp_path, monkeypatch):
    downloads=[];transcriptions=[]
    def download(url,folder,config,**kw):
        downloads.append(url); f=folder/"audio.m4a";f.write_bytes(b"test")
        return f,SourceInfo(platform="youtube",title="test",url=URL,duration_ms=2000)
    def transcribe(self,path,state_file,**kw):
        transcriptions.append(1);return segments_from_result(RESULT)
    monkeypatch.setattr("linkread_desktop.asr.pipeline.download_audio",download)
    monkeypatch.setattr(BailianAsr,"transcribe",transcribe)
    result=transcribe_video(URL,CONFIG,cache_dir=tmp_path)
    assert result.transcript_source == "asr" and result.schema_version == "1.1"
    assert not list(tmp_path.rglob("audio.m4a"))
    assert transcribe_video(URL,CONFIG,cache_dir=tmp_path) == result
    assert len(downloads)==len(transcriptions)==1


@pytest.mark.parametrize('base',["http://dashscope.aliyuncs.com/api/v1", "https://evil.example/api/v1", "https://dashscope.aliyuncs.com/compatible-mode/v1"])
def test_reject_wrong_asr_hosts_before_token_sent(base):
    with pytest.raises(AsrError): AsrConfig(base_url=base,token="private").credential()


@pytest.mark.parametrize(("source", "platform"), [(URL, "youtube"), (BURL, "bilibili")])
def test_full_desktop_asr_to_article_and_saved_transcript(monkeypatch, tmp_path, source, platform):
    from linkread_desktop.desktop import _generate_with_replacement
    from linkread_desktop.renderers.site import load_document
    from test_ai_notes import FakeProvider
    from test_bilibili import Session, extract_bilibili
    def no_caption(*args, **kwargs): raise SubtitleUnavailableError("no subtitle")
    monkeypatch.setattr("linkread_desktop.pipeline.extract_youtube", no_caption)
    session = Session(player_data={"need_login_subtitle": True, "subtitle": {"subtitles": []}},
                      video_data={"desc": "视频原始简介"})
    monkeypatch.setattr("linkread_desktop.pipeline.extract_bilibili",
                        lambda link, **options: extract_bilibili(link, session=session, **options))
    def download(url, folder, config, **kw):
        assert url == source
        assert not kw.get("auth_options")
        audio = folder / "audio.m4a"; audio.write_bytes(b"sample")
        return audio, SourceInfo(platform=platform, title="ASR sample", url=url, duration_ms=2000)
    monkeypatch.setattr("linkread_desktop.asr.pipeline.download_audio", download)
    monkeypatch.setattr(BailianAsr, "transcribe", lambda *a, **k: segments_from_result(RESULT))
    article = _generate_with_replacement(source, tmp_path, lambda e: None, local_file=False,
                                         replace=False, provider=FakeProvider(), asr=CONFIG)
    assert article.is_file()
    note = load_document(article.with_name("note.json"))
    assert note.transcript.transcript_source == "asr"
    assert note.transcript.segments[0].start_ms == 125
    if platform == "bilibili":
        assert note.transcript.source.description == "视频原始简介"
        assert session.calls[-1][1] == {"aid": 42, "oid": 202, "type": 1}
    assert "This transcript was generated from audio" not in article.read_text()


def test_explicit_rejection_allows_user_to_retry_but_timeout_does_not(tmp_path, monkeypatch):
    from linkread_desktop.asr.bailian import AsrHttpError
    api = BailianAsr(CONFIG)
    state = tmp_path / "task.json"
    state.write_text(json.dumps({"audio_url":"oss://audio", "uploaded_at":__import__('time').time()}))
    def reject(*args): raise AsrHttpError("quota", 429)
    monkeypatch.setattr(api, "submit", reject)
    with pytest.raises(AsrHttpError): api.transcribe(tmp_path/"unused", state)
    assert json.loads(state.read_text())["submitting"] is False
    def uncertain(*args): raise AsrError("network")
    monkeypatch.setattr(api, "submit", uncertain)
    with pytest.raises(AsrError): api.transcribe(tmp_path/"unused", state)
    assert json.loads(state.read_text())["submitting"] is True


class AudioYoutubeDL:
    """Mirror yt-dlp for an account that YouTube serves no media URL to: with the account
    cookies only storyboard images remain, while the public session still gets audio.
    yt-dlp raises on that unless ignore_no_formats_error is set, then returns no format."""
    created = []
    audio = {"format_id": "140", "ext": "m4a", "url": "https://media.example/audio.m4a",
             "acodec": "mp4a.40.2", "vcodec": "none"}
    storyboard = {"format_id": "sb0", "ext": "mhtml", "url": "https://i.ytimg.com/sb/storyboard",
                  "acodec": "none", "vcodec": "none"}
    signed_in_error = None
    public_audio = True

    def __init__(self, options):
        self.options = options
        self.created.append(options)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def extract_info(self, url, download=False):
        assert not download
        if "cookiesfrombrowser" in self.options:
            if self.signed_in_error:
                raise RuntimeError(self.signed_in_error)
            media = []
        else:
            media = [self.audio] if self.public_audio else []
        info = {"id": "1-Fo8McCD9w", "title": "Video", "duration": 31, "uploader": "Creator",
                "formats": [self.storyboard, *media]}
        if media:
            return {**info, **media[0]}
        if not self.options.get("ignore_no_formats_error"):
            raise RuntimeError("ERROR: [youtube] 1-Fo8McCD9w: Requested format is not available. "
                               "Use --list-formats for a list of available formats")
        return {**info, "format_id": None}

    def prepare_filename(self, info):
        return self.options["outtmpl"] % {"ext": info["ext"]}

    def process_info(self, info):
        info["filepath"] = self.prepare_filename(info)
        Path(info["filepath"]).write_bytes(b"audio-bytes")


@pytest.fixture
def audio_downloader(monkeypatch):
    AudioYoutubeDL.created = []
    AudioYoutubeDL.signed_in_error = None
    AudioYoutubeDL.public_audio = True
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=AudioYoutubeDL))
    return AudioYoutubeDL


EDGE_COOKIES = {"cookiesfrombrowser": ("edge", None, None, None)}


def test_signed_in_session_without_media_downloads_public_audio_without_cookies(audio_downloader, tmp_path):
    events = []
    audio, source = download_audio(URL, tmp_path, CONFIG, progress=events.append, auth_options=EDGE_COOKIES)
    assert audio == tmp_path / "audio.m4a"
    assert audio.read_bytes() == b"audio-bytes"
    assert (source.platform, source.title, source.author, source.duration_ms) == ("youtube", "Video", "Creator", 31000)
    # The explicitly configured cookies are tried first; only the public retry carries none.
    assert ["cookiesfrombrowser" in options for options in AudioYoutubeDL.created] == [True, False]
    assert [event.stage for event in events if event.stage == "audio_retry"] == ["audio_retry"]


@pytest.mark.parametrize("signed_in_error", [
    "ERROR: Could not copy Edge cookie database: Operation not permitted",
    "ERROR: [youtube] 1-Fo8McCD9w: Sign in to confirm you're not a bot",
])
def test_other_signed_in_download_errors_never_fall_back_to_public_audio(audio_downloader, tmp_path, signed_in_error):
    audio_downloader.signed_in_error = signed_in_error
    with pytest.raises(AsrError, match="音轨下载失败") as failure:
        download_audio(URL, tmp_path, CONFIG, auth_options=EDGE_COOKIES)
    assert failure.value.code == "asr_failed"
    assert len(AudioYoutubeDL.created) == 1
    assert not list(tmp_path.iterdir())


def test_no_audio_in_either_session_reports_unavailable_without_downloading(audio_downloader, tmp_path):
    audio_downloader.public_audio = False
    with pytest.raises(AsrError) as failure:
        download_audio(URL, tmp_path, CONFIG, auth_options=EDGE_COOKIES)
    assert failure.value.code == "audio_unavailable"
    assert failure.value.stage == "audio"
    assert failure.value.retryable is False
    assert len(AudioYoutubeDL.created) == 2
    assert not list(tmp_path.iterdir())


def test_english_retry_progress_and_unavailable_error_have_their_own_messages(monkeypatch):
    from linkread_desktop.messages import localize_event

    monkeypatch.setenv("LINKREAD_UI_LANGUAGE", "en")
    progress = localize_event("progress", {"stage": "audio_retry", "message": "原始消息"})
    assert "cookies" in progress["message"].lower()
    error = localize_event("error", {"code": "audio_unavailable", "message": "原始消息"})
    assert "audio track" in error["message"].lower()

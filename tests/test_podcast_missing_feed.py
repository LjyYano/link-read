"""No RSS is not proof that free playback is a downloadable audio file.

The exact episode identifiers and nullable field shape reproduce the reported
Apple episode. Media/transcript data are local fixtures; no paid calls are made.
"""
import base64
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
import requests

from linkread_desktop import pipeline
from linkread_desktop.asr import pipeline as asr_pipeline
from linkread_desktop.asr.config import AsrConfig
from linkread_desktop.errors import ExtractionError, SubtitleUnavailableError, error_details
from linkread_desktop.extractors import apple_podcasts as apple
from linkread_desktop.extractors import podcast_http as http
from linkread_desktop.models import TranscriptDocument, TranscriptSegment
from test_apple_podcasts import Response, Session


URL = "https://podcasts.apple.com/cn/podcast/id6788661772?i=1000775971124"
TITLE = "《人间悲喜》001 瘦老头（一）"
GUID = "f073047f-7878-403a-94e5-1eac1d161b92"
AUDIO = "https://media.example.com/episode.m4a"
HLS = "https://play.itunes.apple.com/podcast/playlist.m3u8?episode=1000775971124"
FEED = "https://feeds.example.com/episode.xml"
DESCRIPTION = "<p>本单集原始简介。</p>"


def episode_page(*, audio=AUDIO, feed=None, transcript=None, omit=(), entitlement="free"):
    show = {"adamId": "6788661772", "feedUrl": feed, "title": "人间悲喜"}
    enclosure = {"streamUrl": audio, "mediaKinds": ["audio"], "transcriptInfo": transcript,
                 "priceType": "PLUS", "duration": 387.542}
    if "feedUrl" in omit:
        del show["feedUrl"]
    if "transcriptInfo" in omit:
        del enclosure["transcriptInfo"]
    offer = {"contentId": "1000775971124", "guid": GUID, "showOffer": show,
             "isEntitledOffer": True, "priceType": "PLUS"}
    model = {"id": "1000775971124", "showAdamId": "6788661772", "title": TITLE,
             "showTitle": "人间悲喜", "entitlementState": entitlement,
             "showDisplayType": "paid", "hasFreeVersion": True, "duration": 387.542,
             "summary": DESCRIPTION, "playAction": {"episodeOffer": offer},
             "episodeArtwork": {"template": "https://is1-ssl.mzstatic.com/image/cover.jpg"},
             "mediaEnclosures": [enclosure]}
    payload = {"data": [{"intent": {"$kind": "EpisodePageIntent", "adamId": "1000775971124",
                                    "showAdamId": "6788661772"},
                         "data": {"headerButtonItems": [{"model": model}]}}]}
    return '<script id="serialized-server-data">' + json.dumps(payload, ensure_ascii=False) + '</script>'


def protected_master():
    descriptor = [{"method": "SAMPLE-AES", "keyformat": "com.apple.streamingkeydelivery",
                   "uri": "skd://itunes.apple.com/example", "keyformatversion": ["1"]}]
    encoded = base64.b64encode(json.dumps(descriptor).encode()).decode()
    return (f'#EXTM3U\n#EXT-X-SESSION-DATA:DATA-ID="com.apple.hls.keys",VALUE="{encoded}"\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=128000\nhttps://media.example.com/variant.m3u8\n')


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])


def parsed_episode():
    return apple._page_episode(episode_page().encode(), show_id="6788661772",
                               episode_id="1000775971124", url=URL)


def wire_pipeline(monkeypatch, session):
    original = apple.extract_apple_podcasts
    monkeypatch.setattr(apple, "extract_apple_podcasts", lambda url, **options:
                        original(url, session=session, **options))
    calls = []
    source = parsed_episode().source.model_copy(update={"description": None, "description_format": None})
    result = TranscriptDocument(schema_version="1.1", source=source, transcript_source="asr",
                                segments=[TranscriptSegment(start_ms=1000, end_ms=2200, text="离线转写结果")])
    def transcribe(url, config, **options):
        calls.append((url, config, options))
        return result
    monkeypatch.setattr(asr_pipeline, "transcribe_video", transcribe)
    return calls


def test_free_episode_in_paid_show_keeps_exact_metadata_and_null_evidence():
    episode = parsed_episode()
    assert episode.source.title == TITLE and episode.source.duration_ms == 387542
    assert episode.source.description == DESCRIPTION and episode.source.thumbnail_url
    assert episode.page_verified_free_audio and episode.page_declares_no_feed
    assert episode.page_declares_no_transcript and episode.guid == GUID


def test_reported_free_hls_episode_is_protected_not_missing_subtitles(monkeypatch):
    session = Session(Response(episode_page(audio=HLS)), Response(protected_master()))
    calls = wire_pipeline(monkeypatch, session)
    events = []
    with pytest.raises(ExtractionError) as failure:
        pipeline.extract(URL, asr=AsrConfig(token="offline-token"), progress=events.append)
    assert not isinstance(failure.value, SubtitleUnavailableError)
    details = error_details(failure.value)
    assert details["code"] == "protected_media" and details["action"] == "source"
    assert details["retryable"] is False
    assert calls == [] and [url for url, _ in session.calls] == [URL, HLS]
    assert next(event.source for event in events if event.stage == "metadata_done").title == TITLE
    assert not any(event.stage == "captions_done" for event in events)


def test_free_file_without_feed_requires_explicit_asr(monkeypatch):
    session = Session(Response(episode_page()))
    calls = wire_pipeline(monkeypatch, session)
    with pytest.raises(SubtitleUnavailableError, match="开启语音识别"):
        pipeline.extract(URL)
    assert calls == [] and [url for url, _ in session.calls] == [URL]


def test_opted_in_missing_feed_uses_exact_episode_cache_and_shownotes(monkeypatch, tmp_path):
    session = Session(Response(episode_page()))
    calls = wire_pipeline(monkeypatch, session)
    config = AsrConfig(token="offline-token")
    result = pipeline.extract(URL, asr=config, asr_cache_dir=tmp_path)
    assert result.source.video_id == "1000775971124" and result.source.description == DESCRIPTION
    assert len(calls) == 1 and calls[0][0] == URL and calls[0][1] is config
    assert calls[0][2]["cache_dir"] == tmp_path and [url for url, _ in session.calls] == [URL]


@pytest.mark.parametrize("options", [
    {"omit": ("feedUrl",)}, {"omit": ("transcriptInfo",)}, {"feed": ""},
    {"transcript": {}}, {"transcript": {"url": "https://media.example.com/declared.vtt"}},
])
def test_unknown_or_declared_transcript_is_not_proof_of_absence(options, monkeypatch):
    session = Session(Response(episode_page(**options)))
    calls = wire_pipeline(monkeypatch, session)
    with pytest.raises(ExtractionError) as failure:
        pipeline.extract(URL, asr=AsrConfig(token="offline-token"))
    assert not isinstance(failure.value, SubtitleUnavailableError) and calls == []


def test_lookup_only_cannot_grant_missing_feed_recovery(monkeypatch):
    episode = replace(parsed_episode(), page_verified_free_audio=False)
    monkeypatch.setattr(apple, "resolve_episode", lambda *a, **k: episode)
    calls = wire_pipeline(monkeypatch, Session())
    with pytest.raises(ExtractionError):
        pipeline.extract(URL, asr=AsrConfig(token="offline-token"))
    assert calls == []


@pytest.mark.parametrize("response", [Response("Forbidden", 403), requests.Timeout(), Response("bad RSS")])
def test_declared_feed_errors_do_not_fall_back_to_missing_feed(response, monkeypatch):
    session = Session(Response(episode_page(feed=FEED)), response)
    calls = wire_pipeline(monkeypatch, session)
    with pytest.raises(ExtractionError):
        pipeline.extract(URL, asr=AsrConfig(token="offline-token"))
    assert calls == [] and [url for url, _ in session.calls] == [URL, FEED]


@pytest.mark.parametrize("empty", [False, True])
def test_no_timed_captions_still_checks_stream_when_rss_is_valid(empty, monkeypatch):
    item = "" if empty else f"<item><guid>{GUID}</guid></item>"
    feed = (f'<rss version="2.0" xmlns:atom="{apple._ATOM}"><channel>'
            f'<title>人间悲喜</title><link>https://feeds.example.com/show</link><description>节目</description>'
            f'<atom:link rel="self" href="{FEED}"/>{item}</channel></rss>')
    session = Session(Response(episode_page(audio=HLS, feed=FEED)), Response(feed), Response(protected_master()))
    calls = wire_pipeline(monkeypatch, session)
    with pytest.raises(ExtractionError) as failure:
        pipeline.extract(URL, asr=AsrConfig(token="offline-token"))
    assert failure.value.code == "protected_media" and calls == []
    assert [url for url, _ in session.calls] == [URL, FEED, HLS]


def test_existing_public_captions_work_without_fetching_protected_audio():
    caption = "https://media.example.com/captions.vtt"
    feed = (f'<rss xmlns:podcast="{apple._PODCAST}"><channel><item><guid>{GUID}</guid>'
            f'<podcast:transcript url="{caption}" type="text/vtt"/></item></channel></rss>')
    session = Session(Response(episode_page(audio=HLS, feed=FEED)), Response(feed),
                      Response("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n公开字幕。\n"))
    document = apple.extract_apple_podcasts(URL, session=session)
    assert document.segments[0].text == "公开字幕。"
    assert [url for url, _ in session.calls] == [URL, FEED, caption]


def test_protected_preview_does_not_fetch_manifests_or_infer_subtitles():
    session = Session(Response(episode_page(audio=HLS)))
    source = apple.preview_episode(URL, session=session)
    assert source.title == TITLE and [url for url, _ in session.calls] == [URL]


def test_protected_audio_is_rejected_before_ffmpeg_or_file_creation(tmp_path, monkeypatch):
    from linkread_desktop.asr import segments
    monkeypatch.setattr(segments, "ffmpeg_binary", lambda *a: pytest.fail("Protection precedes FFmpeg"))
    session = Session(Response(episode_page(audio=HLS)), Response(protected_master()))
    with pytest.raises(ExtractionError) as failure:
        apple.download_episode_audio(URL, tmp_path, SimpleNamespace(max_minutes=120, chunk_minutes=5), session=session)
    assert failure.value.code == "protected_media" and not list(tmp_path.iterdir())
    assert [url for url, _ in session.calls] == [URL, HLS]


@pytest.mark.parametrize("content_type", ["application/vnd.apple.mpegurl", "audio/x-mpegurl", "application/octet-stream"])
def test_playlist_body_never_becomes_an_audio_file(tmp_path, content_type):
    session = Session(Response(episode_page()), Response(protected_master(), headers={"Content-Type": content_type}))
    with pytest.raises(ExtractionError) as failure:
        apple.download_episode_audio(URL, tmp_path, SimpleNamespace(max_minutes=120, chunk_minutes=0), session=session)
    assert failure.value.code == "protected_media" and not list(tmp_path.iterdir())


def test_split_manifest_prefix_cannot_evade_audio_sniffing(tmp_path):
    class TinyChunks(Response):
        def iter_content(self, chunk_size):
            for byte in self.body:
                yield bytes((byte,))
    session = Session(Response(episode_page()), TinyChunks(protected_master(), headers={"Content-Type": "audio/mp4"}))
    with pytest.raises(ExtractionError):
        apple.download_episode_audio(URL, tmp_path, SimpleNamespace(max_minutes=120, chunk_minutes=0), session=session)
    assert not list(tmp_path.iterdir())


def test_public_audio_file_remains_byte_exact(tmp_path):
    audio = b"offline file audio" * 9000
    session = Session(Response(episode_page()), Response(audio, headers={"Content-Type": "audio/mp4"}))
    path, source = apple.download_episode_audio(URL, tmp_path, SimpleNamespace(max_minutes=120, chunk_minutes=0), session=session)
    assert path.read_bytes() == audio and source.video_id == "1000775971124"

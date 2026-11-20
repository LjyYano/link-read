"""A verified public episode remains usable when the feed has no episode items.

The minimal fixture retains the actual identity and empty RSS shape observed for
Meme Radio episode 087 on 2026-11-18. No network or paid model calls are made.
"""
from dataclasses import replace
from html import escape
import json
from types import SimpleNamespace

import pytest
import requests

from linkread_desktop import pipeline
from linkread_desktop.asr import pipeline as asr_pipeline
from linkread_desktop.asr.config import AsrConfig
from linkread_desktop.errors import ExtractionError, SubtitleUnavailableError
from linkread_desktop.extractors import apple_podcasts as apple
from linkread_desktop.extractors import podcast_http as http
from linkread_desktop.models import TranscriptDocument, TranscriptSegment
from test_apple_podcasts import Session, Response, lookup_response


URL = "https://podcasts.apple.com/cn/podcast/id1603392589?i=1000757475500"
FEED = "http://www.ximalaya.com/album/56087690.xml"
SELF = "https://www.ximalaya.com/album/56087690.xml"
GUID = "xmly_track_965731037"
TITLE = "087. 计算器、火炮与登月：一场被低估的算力革命"
AUDIO = "https://media.example.com/verified-087.m4a?track_id=965731037"


def episode_page(*, entitlement="free"):
    offer = {"contentId": "1000757475500", "guid": GUID, "showOffer": {
        "adamId": "1603392589", "title": "迷因电波", "feedUrl": FEED}}
    model = {"id": "1000757475500", "showAdamId": "1603392589", "title": TITLE,
             "showTitle": "迷因电波", "duration": 2542, "entitlementState": entitlement,
             "playAction": {"episodeOffer": offer},
             "mediaEnclosures": [{"mediaKinds": ["audio"], "streamUrl": AUDIO}]}
    payload = {"data": [{"intent": {"$kind": "EpisodePageIntent", "adamId": "1000757475500",
                                    "showAdamId": "1603392589"},
                         "data": {"headerButtonItems": [{"model": model}]}}]}
    return '<script id="serialized-server-data">' + json.dumps(payload, ensure_ascii=False) + '</script>'


def empty_feed(*, self_url=SELF, title="迷因电波", link="https://www.ximalaya.com/album/56087690",
               description="频道信息仍在，但目录当前没有单集。", extra="", version="2.0"):
    return (f'<rss version="{version}" xmlns:atom="{apple._ATOM}" xmlns:itunes="{apple._ITUNES}">'
            '<channel>'
            f'<atom:link href="{escape(self_url, quote=True)}" rel="self" type="application/rss+xml"/>'
            f'<title>{escape(title)}</title><link>{escape(link)}</link>'
            f'<description>{escape(description)}</description>{extra}</channel></rss>').encode()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])


def parsed_episode():
    return apple._page_episode(episode_page().encode(), show_id="1603392589",
                               episode_id="1000757475500", url=URL)


def wire_pipeline(monkeypatch, session):
    original = apple.extract_apple_podcasts
    monkeypatch.setattr(apple, "extract_apple_podcasts", lambda url, **options:
                        original(url, session=session, **options))
    calls = []
    source = parsed_episode().source
    result = TranscriptDocument(schema_version="1.1", source=source, transcript_source="asr",
                                segments=[TranscriptSegment(start_ms=1200, end_ms=2400, text="真实转写占位")])
    def transcribe(url, config, **options):
        calls.append((url, config, options))
        return result
    monkeypatch.setattr(asr_pipeline, "transcribe_video", transcribe)
    return calls, result


@pytest.mark.parametrize("self_url", [FEED, SELF])
def test_valid_empty_publisher_feed_is_distinct_from_failed_matching(self_url):
    events = []
    session = Session(Response(episode_page()), Response(empty_feed(self_url=self_url)))
    with pytest.raises(SubtitleUnavailableError, match="RSS 当前未列出单集"):
        apple.extract_apple_podcasts(URL, session=session, progress=events.append)
    assert [url for url, _ in session.calls] == [URL, FEED]
    assert next(event.source for event in events if event.stage == "metadata_done").video_id == "1000757475500"
    assert any(event.stage == "captions" and "已核验 Apple 对应单集" in event.message for event in events)
    assert not any(event.stage == "captions_done" for event in events)


def test_empty_feed_does_not_start_asr_without_explicit_setting(monkeypatch):
    session = Session(Response(episode_page()), Response(empty_feed()))
    calls, _ = wire_pipeline(monkeypatch, session)
    with pytest.raises(SubtitleUnavailableError, match="开启语音识别"):
        pipeline.extract(URL)
    assert calls == [] and [url for url, _ in session.calls] == [URL, FEED]


def test_opted_in_asr_keeps_the_exact_apple_episode_and_existing_cache(monkeypatch, tmp_path):
    session = Session(Response(episode_page()), Response(empty_feed()))
    calls, expected = wire_pipeline(monkeypatch, session)
    config = AsrConfig(token="offline-token")
    result = pipeline.extract(URL, asr=config, asr_cache_dir=tmp_path)
    assert result == expected and result.source.video_id == "1000757475500"
    assert len(calls) == 1 and calls[0][0] == URL and calls[0][1] is config
    assert calls[0][2]["cache_dir"] == tmp_path
    assert [url for url, _ in session.calls] == [URL, FEED]


@pytest.mark.parametrize("payload", [
    b"", b"not XML", b"<rss><channel>", b"<rss><channel/></rss>",
    empty_feed(version="0.91"), empty_feed(title=""), empty_feed(description=""), empty_feed(link=""),
    empty_feed(self_url="https://other.example.com/album/56087690.xml"),
    empty_feed(self_url=SELF.replace("56087690", "999")), empty_feed(self_url=SELF + "?different=1"),
    empty_feed(extra=f'<atom:link rel="self" href="{SELF}"/>'),
    empty_feed().replace(b'rel="self"', b'rel="alternate"'),
    empty_feed(extra="<itunes:block>Yes</itunes:block>"),
    empty_feed(extra="<itunes:new-feed-url>https://new.example.com/feed.xml</itunes:new-feed-url>"),
    empty_feed(extra="<item><guid>another-episode</guid></item>"),
    empty_feed(extra=f"<item><guid>{GUID}</guid></item><item><guid>{GUID}</guid></item>"),
    empty_feed().replace(b"</rss>", b"<channel><title>another</title></channel></rss>"),
    empty_feed().replace(b"</rss>", b"<item><guid>outside-channel</guid></item></rss>"),
    empty_feed().replace(b"</rss>", b"<item/></rss>"),
])
def test_bad_missing_ambiguous_or_withdrawn_feed_never_enters_asr(payload, monkeypatch):
    session = Session(Response(episode_page()), Response(payload))
    calls, _ = wire_pipeline(monkeypatch, session)
    with pytest.raises(ExtractionError) as error:
        pipeline.extract(URL, asr=AsrConfig(token="offline-token"))
    assert not isinstance(error.value, SubtitleUnavailableError)
    assert calls == []


@pytest.mark.parametrize("response", [Response("Forbidden", 403), requests.Timeout(),
                                      Response(empty_feed(), status=206),
                                      Response(empty_feed(), headers={"Content-Length": "99999"})])
def test_transport_failure_is_never_converted_to_empty_feed_asr(response, monkeypatch):
    session = Session(Response(episode_page()), response)
    calls, _ = wire_pipeline(monkeypatch, session)
    with pytest.raises(ExtractionError) as error:
        pipeline.extract(URL, asr=AsrConfig(token="offline-token"))
    assert not isinstance(error.value, SubtitleUnavailableError) and calls == []


def test_directory_block_does_not_disable_an_exact_public_transcript():
    # Apple's block tag controls listing, not authorization to an HTTP resource.
    caption = "https://media.example.com/087.vtt"
    item = (f'<itunes:block>Yes</itunes:block><item><guid>{GUID}</guid>'
            '<itunes:block>Yes</itunes:block>'
            f'<podcast:transcript xmlns:podcast="{apple._PODCAST}" url="{caption}" type="text/vtt"/>'
            '</item>')
    session = Session(Response(episode_page()), Response(empty_feed(extra=item)),
                      Response("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n公开字幕。\n"))
    document = apple.extract_apple_podcasts(URL, session=session)
    assert document.source.video_id == "1000757475500"
    assert document.segments[0].text == "公开字幕。" and session.calls[-1][0] == caption


def test_empty_feed_cannot_grant_provenance_to_catalog_or_manual_metadata(monkeypatch):
    episode = parsed_episode()
    assert episode.page_verified_free_audio is True
    without_proof = replace(episode, page_verified_free_audio=False)
    monkeypatch.setattr(apple, "resolve_episode", lambda *a, **k: without_proof)
    calls, _ = wire_pipeline(monkeypatch, Session(Response(empty_feed())))
    with pytest.raises(ExtractionError, match="未能核验") as error:
        pipeline.extract(URL, asr=AsrConfig(token="offline-token"))
    assert not isinstance(error.value, SubtitleUnavailableError) and calls == []


def test_catalog_fallback_does_not_inherit_exact_free_page_provenance():
    from test_apple_podcasts import URL as other_url
    episode = apple.resolve_episode(other_url, session=Session(Response("changed markup"), lookup_response()))
    assert episode.page_verified_free_audio is False


def test_subscription_page_remains_a_hard_failure_before_feed_or_asr(monkeypatch):
    session = Session(Response(episode_page(entitlement="subscription")))
    calls, _ = wire_pipeline(monkeypatch, session)
    with pytest.raises(ExtractionError, match="订阅或登录"):
        pipeline.extract(URL, asr=AsrConfig(token="offline-token"))
    assert calls == [] and len(session.calls) == 1


def test_empty_feed_still_rejects_https_to_http_identity_downgrade():
    episode = replace(parsed_episode(), feed_url=SELF)
    with pytest.raises(ExtractionError, match="来源发生变化"):
        apple._feed_item(empty_feed(self_url=FEED), episode)


def test_audio_download_uses_original_exact_episode_not_another_rss_item(tmp_path):
    session = Session(Response(episode_page()), Response(b"offline-audio", headers={"Content-Type": "audio/mp4"}))
    path, source = apple.download_episode_audio(URL, tmp_path, SimpleNamespace(max_minutes=120, chunk_minutes=0),
                                               session=session)
    assert path.read_bytes() == b"offline-audio" and source.video_id == "1000757475500"
    assert source.title == TITLE and [url for url, _ in session.calls] == [URL, AUDIO]

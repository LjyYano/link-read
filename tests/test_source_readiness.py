"""The user-triggered source check must not become implicit generation or ASR."""
import json
import sys
from threading import Event
import time

import pytest
import requests

from linkread_desktop import desktop, source_readiness as readiness
from linkread_desktop.errors import SubtitleUnavailableError
from linkread_desktop.extractors import apple_podcasts as apple, podcast_http as http
from test_apple_podcasts import Response, Session, feed
from test_podcast_missing_feed import URL, HLS, FEED, GUID, episode_page, protected_master


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])


def with_session(monkeypatch, session):
    original = apple.extract_apple_podcasts
    monkeypatch.setattr(apple, "extract_apple_podcasts", lambda source, **options:
                        original(source, session=session, **options))


def test_exact_reported_episode_is_blocked_after_explicit_public_check(monkeypatch):
    session = Session(Response(episode_page(audio=HLS)), Response(protected_master()))
    with_session(monkeypatch, session)
    result = readiness.check_source(URL)
    assert result["source"] == URL and result["status"] == "blocked"
    assert result["code"] == "protected_media" and result["checkedAt"] > 0
    # No variants, audio segments, keys, licenses or ASR endpoints.
    assert [url for url, _ in session.calls] == [URL, HLS]


def test_protected_audio_cannot_preempt_public_captions(monkeypatch):
    caption = "https://feeds.example.com/transcript.vtt"
    resource = f'<podcast:transcript url="{caption}" type="text/vtt" language="zh-CN"/>'
    session = Session(Response(episode_page(audio=HLS, feed=FEED)),
                      Response(feed(guid=GUID, transcript=resource, enclosure=HLS)),
                      Response("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n来自准确单集的原文。\n"))
    with_session(monkeypatch, session)
    result = readiness.check_source(URL)
    assert result["status"] == "captions"
    assert [url for url, _ in session.calls] == [URL, FEED, caption]


@pytest.mark.parametrize("omit", [("feedUrl",), ("transcriptInfo",)])
def test_unknown_caption_fields_do_not_prove_a_blocked_source(monkeypatch, omit):
    session = Session(Response(episode_page(audio=HLS, omit=omit)))
    with_session(monkeypatch, session)
    result = readiness.check_source(URL)
    assert result["status"] == "unknown"
    assert [url for url, _ in session.calls] == [URL]


def test_no_captions_never_submits_asr_or_downloads_audio(monkeypatch):
    session = Session(Response(episode_page()))
    with_session(monkeypatch, session)
    result = readiness.check_source(URL)
    assert result["status"] == "needs_asr"
    assert [url for url, _ in session.calls] == [URL]
    assert "仍会检查音频" in result["message"]


@pytest.mark.parametrize("response", [requests.Timeout(), Response("broken RSS"), Response("Forbidden", status=403)])
def test_failed_rss_is_unknown_not_ready_or_asr(monkeypatch, response):
    session = Session(Response(episode_page(feed=FEED)), response)
    with_session(monkeypatch, session)
    result = readiness.check_source(URL)
    assert result["status"] == "unknown"
    assert [url for url, _ in session.calls] == [URL, FEED]


@pytest.mark.parametrize("source", ["https://www.youtube.com/watch?v=1-Fo8McCD9w",
                                    "https://www.bilibili.com/video/BV1H4sJ6XEF5?p=1"])
def test_video_checks_share_extractor_but_never_asr_or_cookie_options(monkeypatch, source):
    calls = []
    def extract(value, **options):
        calls.append((value, options))
        raise SubtitleUnavailableError("none")
    monkeypatch.setattr(readiness, "extract", extract)
    result = readiness.check_source(source)
    assert result["status"] == "needs_asr"
    assert calls == [(source, {"asr": None})]


def test_wall_clock_timeout_is_unknown_and_has_no_late_result(monkeypatch):
    release = Event()
    monkeypatch.setattr(readiness, "extract", lambda *a, **k: release.wait(2))
    started = time.monotonic()
    try:
        result = readiness.check_source(URL, timeout=.01)
        assert time.monotonic() - started < 1
        assert result["status"] == "unknown" and result["code"] == "source_check_timeout"
    finally:
        release.set()


def test_unexpected_dependency_error_does_not_leak_output(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("signed-url-or-remote-content")
    monkeypatch.setattr(readiness, "extract", broken)
    result = readiness.check_source(URL)
    assert result["status"] == "unknown" and "signed-url" not in result["message"]


def test_sidecar_check_is_explicit_and_does_not_write_library(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "argv", ["linkread", "--library", str(tmp_path), "check-source", "--", URL])
    monkeypatch.setattr(readiness, "extract", lambda *a, **k: object())
    monkeypatch.setattr(desktop, "make_provider", lambda *a, **k: pytest.fail("No AI in source checks"))
    assert desktop.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["type"] == "source_check" and result["status"] == "captions"
    assert list(tmp_path.iterdir()) == []

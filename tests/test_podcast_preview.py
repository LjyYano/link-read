"""Fast preview remains exact, anonymous and bounded even before socket connect."""
from threading import Event
import time

import pytest

from linkread_desktop.errors import ExtractionError
from linkread_desktop.extractors import apple_podcasts as apple
from linkread_desktop.extractors import podcast_http as http
from linkread_desktop.metadata import video_metadata
from test_apple_podcasts import Response, Session, page_payload, URL


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34",443))])


def test_preview_reads_one_exact_page_not_a_catalog_feed_or_audio():
    session = Session(Response(page_payload()))
    source = apple.preview_episode(URL, session=session)
    assert source.title == "用户指定的单集" and source.video_id == "1000801308527"
    assert source.thumbnail_url == "https://is1-ssl.mzstatic.com/image/600x600bb.jpg"
    assert source.duration_ms == 2467000
    assert len(session.calls) == 1 and session.calls[0][0] == URL
    assert session.calls[0][1]["timeout"] == (3,5)


def test_public_metadata_adapter_uses_the_fast_preview_path(monkeypatch):
    monkeypatch.setattr(apple, "resolve_episode", lambda *a, **k: pytest.fail("Preview waited for full resolver"))
    session = Session(Response(page_payload()))
    metadata = video_metadata(URL, session=session).public()
    assert metadata["title"] == "用户指定的单集" and metadata["platform"] == "apple_podcasts"
    assert metadata["sourceUrl"] == URL and len(session.calls) == 1


def test_generation_still_allows_a_slower_public_metadata_response():
    session = Session(Response(page_payload()))
    episode = apple.resolve_episode(URL, session=session)
    assert episode.source.title == "用户指定的单集"
    assert len(session.calls) == 1 and session.calls[0][1]["timeout"] == (10, 30)


@pytest.mark.parametrize("payload", [page_payload(actual_id="222222"), page_payload(entitlement="subscription"),
                                      "<html>login</html>"])
def test_preview_cannot_fallback_to_other_episodes_or_expand_requests(payload):
    session = Session(Response(payload))
    with pytest.raises(ExtractionError):
        apple.preview_episode(URL, session=session)
    assert len(session.calls) == 1


def test_preview_wall_clock_budget_includes_stalled_dns(monkeypatch):
    # Socket timeouts do not cover getaddrinfo. Do not wait for this fake resolver.
    release = Event()
    started = Event()
    def stalled(*a, **k):
        started.set()
        release.wait(timeout=2)
        return [(2,1,6,"",("93.184.216.34",443))]
    monkeypatch.setattr(http.socket,"getaddrinfo",stalled)
    monkeypatch.setattr(apple,"_PREVIEW_SECONDS",0.04)
    session = Session(Response(page_payload()))
    before = time.monotonic()
    try:
        with pytest.raises(ExtractionError,match="预览暂时超时"):
            apple.preview_episode(URL,session=session)
        assert started.is_set() and not session.calls
        assert time.monotonic()-before < 0.5
    finally:
        release.set()


def test_preview_wall_clock_budget_includes_slow_body(monkeypatch):
    release = Event()
    class SlowBody(Response):
        def iter_content(self, chunk_size):
            yield b"<html>"
            release.wait(timeout=2)
            yield b"</html>"
    monkeypatch.setattr(apple,"_PREVIEW_SECONDS",0.04)
    before = time.monotonic()
    try:
        with pytest.raises(ExtractionError,match="预览暂时超时"):
            apple.preview_episode(URL,session=Session(SlowBody()))
        assert time.monotonic()-before < 0.5
    finally:
        release.set()


def test_redirects_share_one_deadline_instead_of_restarting_budget(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(http.time,"monotonic",lambda:clock[0])
    class DelayedRedirect(Session):
        def get(self,url,**kwargs):
            response = super().get(url,**kwargs)
            clock[0] += 9
            return response
    session = DelayedRedirect(Response(status=302,headers={"Location":URL+"&next=1"}))
    with pytest.raises(ExtractionError,match="超时"):
        http.get_bytes(session,URL,max_bytes=1000,accept="text/html",timeout=(3,5),max_seconds=8)
    assert len(session.calls) == 1

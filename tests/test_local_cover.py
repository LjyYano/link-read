"""Offline tests for local cover caching during conversion and archive updates."""

import base64
import json
import threading
import time

import pytest

from linkread_desktop import covers, desktop, reader_cache
from linkread_desktop.generation import generate_article
from linkread_desktop.metadata import VideoMetadata
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.workspace import workspace_dir
from test_ai_notes import FakeProvider, make_transcript


JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP_BYTES = b"RIFF\x24\x00\x00\x00WEBP" + b"\x00" * 32
GIF_BYTES = b"GIF89a" + b"\x00" * 32


class FakeResponse:
    def __init__(self, body=b"", *, status=200, headers=None):
        self._body = body
        self.status_code = status
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for offset in range(0, len(self._body), chunk_size):
            yield self._body[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        assert kwargs.get("allow_redirects") is False
        self.calls.append((url, kwargs))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_download_cover_saves_valid_images_follows_trusted_redirects_and_replaces_stale(tmp_path):
    session = FakeSession([
        FakeResponse(status=302, headers={"Location": "https://i0.hdslb.com/bfs/archive/final.png"}),
        FakeResponse(PNG_BYTES, status=200),
    ])
    saved = covers.download_cover("http://i0.hdslb.com/bfs/archive/orig.jpg", tmp_path, session=session)
    assert saved == tmp_path / "cover.png"
    assert saved.read_bytes() == PNG_BYTES
    assert [url for url, _ in session.calls] == [
        "https://i0.hdslb.com/bfs/archive/orig.jpg",
        "https://i0.hdslb.com/bfs/archive/final.png",
    ]
    assert covers.local_cover_url(tmp_path).startswith("linkread-reader://localhost/cover/")

    # Overwriting with a different image type removes the stale cover extension.
    session2 = FakeSession([FakeResponse(WEBP_BYTES, status=200)])
    replaced = covers.download_cover("https://is1-ssl.mzstatic.com/image/thumb/cover.webp", tmp_path,
                                     session=session2, overwrite=True)
    assert replaced == tmp_path / "cover.webp"
    assert not (tmp_path / "cover.png").exists()


@pytest.mark.parametrize("responses", [
    [FakeResponse(b"<html>not an image</html>", status=200)],
    [FakeResponse(b"<svg xmlns='http://www.w3.org/2000/svg'/>", status=200)],
    [FakeResponse(status=302, headers={"Location": "https://evil.example/cover.jpg"})],
    [FakeResponse(JPEG_BYTES, status=404)],
    [FakeResponse(JPEG_BYTES + b"\x00" * (covers.MAX_COVER_BYTES + 1), status=200)],
])
def test_download_cover_rejects_untrusted_redirects_oversize_and_non_images(tmp_path, responses):
    session = FakeSession(responses)
    assert covers.download_cover("https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg", tmp_path, session=session) is None
    assert covers.find_local_cover(tmp_path) is None


def test_download_cover_never_overwrites_symlink_target(tmp_path):
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"keep-me")
    folder = tmp_path / "article"
    folder.mkdir()
    (folder / "cover.jpg").symlink_to(outside)
    session = FakeSession([FakeResponse(JPEG_BYTES, status=200)])
    assert covers.download_cover("https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg", folder,
                                 session=session, overwrite=True) is None
    assert outside.read_bytes() == b"keep-me"


def test_generation_caches_cover_locally_and_library_index_tracks_it(tmp_path, monkeypatch):
    raw = make_transcript(text="带封面的离线文章")
    raw.source.thumbnail_url = "https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *a, **k: raw)
    monkeypatch.setattr(covers, "fetch_cover_bytes", lambda url, session=None: (".jpg", JPEG_BYTES))

    library = tmp_path / "library"
    article = generate_article(raw.source.url, FakeProvider(), library=library)
    folder = article.parent
    assert (folder / "cover.jpg").read_bytes() == JPEG_BYTES

    records = desktop._library_records(library)["articles"]
    assert len(records) == 1
    assert records[0]["localCover"].startswith("linkread-reader://localhost/cover/")
    assert desktop._valid_cached_record(records[0], records[0]["id"])

    # Removing cover.jpg invalidates the library index automatically.
    sig_before = reader_cache.archive_signature(folder)
    (folder / "cover.jpg").unlink()
    assert reader_cache.archive_signature(folder) != sig_before
    records_after = desktop._library_records(library)["articles"]
    assert records_after[0]["localCover"] is None
    # The archived page carries the cover too, so opening the file outside the App shows it.
    assert "data:image/jpeg;base64," + base64.b64encode(JPEG_BYTES).decode("ascii") in (folder / "article.html").read_text(encoding="utf-8")


def test_generation_without_a_local_cover_writes_the_online_thumbnail(tmp_path, monkeypatch):
    raw = make_transcript(text="没有本地封面的文章")
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *a, **k: raw)
    monkeypatch.setattr(covers, "fetch_cover_bytes", lambda url, session=None: None)
    article = generate_article(raw.source.url, FakeProvider(), library=tmp_path / "library")
    html = article.read_text(encoding="utf-8")
    assert 'src="https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"' in html
    assert "data:image" not in html and not (article.parent / "cover.jpg").exists()


def test_generation_waits_for_a_late_cover_before_writing_the_article(tmp_path, monkeypatch):
    raw = make_transcript(text="封面在整理阶段结束后才到")
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *a, **k: raw)
    rendering = threading.Event()

    def late_cover(url, session=None):
        assert rendering.wait(5), "the render stage must start first"
        time.sleep(.5)       # lands after the page would already have been written
        return (".jpg", JPEG_BYTES)

    monkeypatch.setattr(covers, "fetch_cover_bytes", late_cover)

    def progress(event):
        if event.stage == "render":
            rendering.set()

    article = generate_article(raw.source.url, FakeProvider(), library=tmp_path / "library", progress=progress)
    assert (article.parent / "cover.jpg").read_bytes() == JPEG_BYTES
    assert "data:image/jpeg;base64," + base64.b64encode(JPEG_BYTES).decode("ascii") in article.read_text(encoding="utf-8")


def test_generation_reports_render_time_without_the_cover_wait(tmp_path, monkeypatch):
    raw = make_transcript(text="封面等待不计入渲染耗时")
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *a, **k: raw)
    rendering = threading.Event()

    def late_cover(url, session=None):
        assert rendering.wait(5), "the render stage must start first"
        time.sleep(.5)       # waiting for the cover is not time spent rendering
        return (".jpg", JPEG_BYTES)

    monkeypatch.setattr(covers, "fetch_cover_bytes", late_cover)

    def progress(event):
        if event.stage == "render":
            rendering.set()

    article = generate_article(raw.source.url, FakeProvider(), library=tmp_path / "library", progress=progress)
    metrics = json.loads((article.parent / ".performance.json").read_text(encoding="utf-8"))
    assert (article.parent / "cover.jpg").exists() and metrics["html_seconds"] < .4


def test_offline_replacement_preserves_existing_local_cover(tmp_path):
    raw = make_transcript(text="离线重新整理保留本地封面")
    library = tmp_path / "library"
    old_folder = workspace_dir(library, raw)
    old_folder.mkdir(parents=True)
    (old_folder / "transcript.json").write_text(raw.model_dump_json(), encoding="utf-8")
    (old_folder / "article.html").write_text("old", encoding="utf-8")
    covers.save_cover_bytes(old_folder, ".png", PNG_BYTES)

    stage = tmp_path / "stage" / old_folder.name
    stage.mkdir(parents=True)
    (stage / "transcript.json").write_text(raw.model_dump_json(), encoding="utf-8")
    (stage / "article.html").write_text("new", encoding="utf-8")

    published = desktop._publish_replacement(stage / "article.html", library)
    assert (published.parent / "cover.png").read_bytes() == PNG_BYTES


def test_linking_and_unlinking_source_updates_local_cover(tmp_path, monkeypatch):
    raw = TranscriptDocument(
        source=SourceInfo(platform="local", title="导入.srt"),
        transcript_source="local",
        segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="正文")],
    )
    library = tmp_path / "library"
    folder = workspace_dir(library, raw)
    folder.mkdir(parents=True)
    (folder / "transcript.json").write_text(raw.model_dump_json(), encoding="utf-8")
    (folder / "article.html").write_text("html", encoding="utf-8")

    video = "https://www.bilibili.com/video/BV1BhKf6mEYR?p=1"
    artwork = "https://i0.hdslb.com/bfs/archive/local-cover.jpg"
    monkeypatch.setattr(
        desktop, "video_metadata",
        lambda url: VideoMetadata(
            source=SourceInfo(platform="bilibili", video_id="BV1BhKf6mEYR", title="原视频", url=video),
            thumbnail_url=artwork,
        ),
    )
    monkeypatch.setattr(desktop, "fetch_cover_bytes", lambda url: (".jpg", JPEG_BYTES))

    linked = desktop._update_article_details(library, folder.name, {"title": None, "category": None, "sourceUrl": video})
    assert linked["localCover"].startswith("linkread-reader://localhost/cover/")
    assert (folder / "cover.jpg").read_bytes() == JPEG_BYTES

    cleared = desktop._update_article_details(library, folder.name, {"title": None, "category": None, "sourceUrl": None})
    assert cleared["localCover"] is None
    assert not (folder / "cover.jpg").exists()


@pytest.mark.parametrize("payload,ext,mime", [
    (JPEG_BYTES, ".jpg", "image/jpeg"), (PNG_BYTES, ".png", "image/png"),
    (WEBP_BYTES, ".webp", "image/webp"), (GIF_BYTES, ".gif", "image/gif"),
])
def test_embedded_cover_inlines_a_small_local_cover_as_a_data_uri(tmp_path, payload, ext, mime):
    covers.save_cover_bytes(tmp_path, ext, payload)
    assert covers.embedded_cover(tmp_path) == f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")


def test_embedded_cover_skips_oversized_missing_and_symlinked_covers(tmp_path, monkeypatch):
    assert covers.embedded_cover(tmp_path) is None
    covers.save_cover_bytes(tmp_path, ".jpg", JPEG_BYTES)
    monkeypatch.setattr(covers, "MAX_EMBEDDED_COVER_BYTES", 16)
    assert covers.embedded_cover(tmp_path) is None
    monkeypatch.undo()
    article = tmp_path / "article"
    article.mkdir()
    (article / "cover.jpg").symlink_to(tmp_path / "cover.jpg")
    assert covers.embedded_cover(article) is None


def test_cover_source_prefers_the_local_cover_then_the_allowlisted_thumbnail(tmp_path):
    thumb = "https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"
    assert covers.cover_source(tmp_path, thumb) == thumb
    assert covers.cover_source(tmp_path, "https://evil.example/cover.jpg") is None
    covers.save_cover_bytes(tmp_path, ".jpg", JPEG_BYTES)
    assert covers.cover_source(tmp_path, "https://evil.example/cover.jpg") == covers.embedded_cover(tmp_path)
    assert covers.cover_source(None, thumb) == thumb


def test_valid_cover_source_accepts_only_inlined_raster_images_and_allowlisted_thumbnails():
    inline = "data:image/png;base64,iVBORw0KGgo="
    url = "https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"
    assert covers.valid_cover_source(inline) == inline
    assert covers.valid_cover_source(url) == url
    for bad in (None, "", 7, "http://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg", "//i.ytimg.com/a.jpg",
                "data:image/svg+xml,<svg onload=alert(1)>", "data:text/html;base64,PGh0bWw+",
                'data:image/png;base64,iVBORw0KGgo=" onerror="alert(1)', "data:image/png;base64,",
                "data:image/png;base64," + "A" * (2 * 1024 * 1024)):
        assert covers.valid_cover_source(bad) is None, bad


def test_valid_cover_source_bounds_inline_size_without_limiting_urls(monkeypatch):
    monkeypatch.setattr(covers, "MAX_EMBEDDED_COVER_BYTES", 16)
    url = "https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"
    assert covers.valid_cover_source(url) == url                 # an online cover has its own bound
    small = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" * 4).decode("ascii")    # 12 bytes
    large = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff" * 8).decode("ascii")    # 24 bytes
    assert covers.valid_cover_source(small) == small and covers.valid_cover_source(large) is None
    assert covers.valid_cover_source("https://i.ytimg.com/" + "a" * 8200) is None


def test_display_thumbnail_uses_a_stable_youtube_frame_then_the_saved_cover():
    youtube = SourceInfo(platform="youtube", title="t", video_id="1-Fo8McCD9w",
                         url="https://www.youtube.com/watch?v=1-Fo8McCD9w",
                         thumbnail_url="https://i.ytimg.com/vi/1-Fo8McCD9w/maxresdefault.jpg")
    assert covers.display_thumbnail(youtube) == "https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg"
    show = SourceInfo(platform="apple_podcasts", title="t", url="https://podcasts.apple.com/podcast/id1",
                      thumbnail_url="https://is1-ssl.mzstatic.com/image/thumb/cover.jpg")
    assert covers.display_thumbnail(show) == "https://is1-ssl.mzstatic.com/image/thumb/cover.jpg"
    assert covers.display_thumbnail(SourceInfo(platform="local", title="t")) is None

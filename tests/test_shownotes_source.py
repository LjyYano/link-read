"""Original publisher descriptions survive every extraction route; no network."""
from hashlib import sha256
import json
import sys
import types

import pytest

from linkread_desktop.asr.config import AsrConfig
from linkread_desktop.errors import ExtractionError, SubtitleUnavailableError
from linkread_desktop.extractors import apple_podcasts as apple
from linkread_desktop.extractors import podcast_http as http
from linkread_desktop.metadata import shownotes_metadata, video_metadata
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.pipeline import extract
from linkread_desktop.workspace import _source_key
from test_apple_podcasts import AUDIO, CAPTION, FEED, URL, Response, Session, feed, lookup, page_payload
from test_bilibili import Session as BiliSession
from test_youtube import FakeYoutubeDL

YOUTUBE_URL = "https://www.youtube.com/watch?v=1-Fo8McCD9w"
ORIGINAL = '<p>本期内容 &amp; 资料</p><ul><li>第一段</li><li><a href="https://example.com/book">参考书</a></li></ul>'


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])


def page(summary=None, **changes):
    text = page_payload()
    payload = json.loads(text.split('>', 1)[1].rsplit('</script>', 1)[0])
    model = payload["data"][0]["data"]["headerButtonItems"][0]["model"]
    if summary is not None:
        model["summary"] = summary
    model.update(changes)
    return '<script id="serialized-server-data">' + json.dumps(payload) + '</script>'


def test_legacy_local_archive_identity_and_duplicate_detection_are_unchanged(tmp_path):
    from linkread_desktop.desktop import _duplicate
    legacy = {"schema_version": "1.0", "source": {"platform": "local", "title": "旧字幕"},
              "transcript_source": "local", "segments": [{"start_ms": 0, "text": "完整原文"}]}
    serialized = json.dumps(legacy, ensure_ascii=False, separators=(",", ":"))
    raw = TranscriptDocument.model_validate(legacy)
    assert raw.model_dump_json(exclude_none=True) == serialized
    key = "local-" + sha256(serialized.encode()).hexdigest()[:12]
    assert _source_key(raw) == key
    library = tmp_path / "library"
    folder = library / f"旧字幕 [{key}]"
    folder.mkdir(parents=True)
    (folder / "transcript.json").write_text(serialized)
    (folder / "article.html").write_text("old article")
    imported = tmp_path / "original.json"
    imported.write_text(serialized)
    assert _duplicate(str(imported), library, local_file=True)["article"]["id"] == folder.name


def test_exact_apple_page_summary_is_kept_without_a_new_preview_request():
    session = Session(Response(page(ORIGINAL)))
    metadata = video_metadata(URL, session=session)
    assert metadata.source.description == ORIGINAL
    assert metadata.source.description_format == "html"
    assert len(session.calls) == 1
    assert "description" not in metadata.public()  # Preview IPC stays lightweight.


def test_apple_description_refresh_uses_exact_page_and_never_downloads_rss_or_audio():
    session = Session(Response(page(ORIGINAL)))
    source = shownotes_metadata(URL, session=session)
    assert source.description == ORIGINAL
    assert [url for url, _ in session.calls] == [URL]


def test_exact_lookup_description_is_preserved():
    payload = lookup()
    payload["results"][1]["description"] = ORIGINAL
    source = apple.resolve_episode(URL, session=Session(Response("<html>changed</html>"),
                                                      Response(json.dumps(payload)))).source
    assert source.description == ORIGINAL and source.description_format == "html"


def test_rss_content_encoded_wins_over_short_description_and_page_summary():
    tags = ('<description>简短简介</description>'
            f'<content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/"><![CDATA[{ORIGINAL}]]></content:encoded>'
            f'<podcast:transcript url="{CAPTION}" type="text/vtt"/>')
    session = Session(Response(page("网页简介")), Response(feed(transcript=tags)),
                      Response("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n完整原文\n"))
    result = apple.extract_apple_podcasts(URL, session=session)
    assert result.source.description == ORIGINAL
    assert result.source.description_format == "html"
    assert [url for url, _ in session.calls] == [URL, FEED, CAPTION]


@pytest.mark.parametrize("tag, content, expected_format", [
    ("description", "第一段\n第二段", "text"),
    ("description", "<p>完整原文</p>", "html"),
    ('itunes:summary xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"', "保留原简介", "text"),
])
def test_refresh_uses_exact_rss_description_if_page_has_none(tag, content, expected_format):
    closing = tag.split()[0]
    row = f"<{tag}><![CDATA[{content}]]></{closing}>"
    session = Session(Response(page()), Response(feed(transcript=row)))
    source = shownotes_metadata(URL, session=session)
    assert source.description == content and source.description_format == expected_format
    assert [url for url, _ in session.calls] == [URL, FEED]


def test_rss_only_shownotes_survive_the_opt_in_asr_path(monkeypatch, tmp_path):
    original_extractor = apple.extract_apple_podcasts
    session = Session(Response(page()), Response(feed(transcript=f'<description><![CDATA[{ORIGINAL}]]></description>')))
    monkeypatch.setattr(apple, "extract_apple_podcasts", lambda url, **kw: original_extractor(url, session=session, **kw))
    asr_doc = TranscriptDocument(schema_version="1.1", source=SourceInfo(
        platform="apple_podcasts", title="音源标题", url=URL, video_id="1000801308527"),
        transcript_source="asr", segments=[TranscriptSegment(start_ms=1234, end_ms=2345, text="完整原文")])
    calls = []
    def transcribe(url, *a, **kw):
        calls.append(url)
        return asr_doc
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video", transcribe)
    result = extract(URL, asr=AsrConfig(token="fake"), asr_cache_dir=tmp_path)
    assert calls == [URL]
    assert result.source.description == ORIGINAL and result.source.description_format == "html"
    assert result.source.title == "音源标题" and result.segments == asr_doc.segments
    assert asr_doc.source.description is None  # Metadata merge does not mutate a cached document.


def test_wrong_episode_metadata_is_rejected_before_asr(monkeypatch, tmp_path):
    wrong = SourceInfo(platform="apple_podcasts", title="另一集", url=URL.replace("1000801308527", "1000801308528"),
                       description="不应该使用")
    def missing(*a, **kw):
        raise SubtitleUnavailableError("no captions", source=wrong)
    monkeypatch.setattr(apple, "extract_apple_podcasts", missing)
    monkeypatch.setattr("linkread_desktop.asr.pipeline.transcribe_video", lambda *a, **kw: pytest.fail("must not submit paid ASR"))
    with pytest.raises(ExtractionError, match="不匹配"):
        extract(URL, asr=AsrConfig(token="fake"), asr_cache_dir=tmp_path)


def test_missing_description_never_prevents_existing_subtitle_extraction():
    tags = f'<podcast:transcript url="{CAPTION}" type="text/vtt"/>'
    result = apple.extract_apple_podcasts(URL, session=Session(Response(page()), Response(feed(transcript=tags)),
                                        Response("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n字幕\n")))
    assert result.source.description is None and result.segments[0].text == "字幕"


class BiliDescriptionSession(BiliSession):
    def get(self, url, **kwargs):
        result = super().get(url, **kwargs)
        if "x/web-interface/view" in url:
            result._payload["data"]["desc"] = "原始简介\n00:30 第二部分\nhttps://example.com/reference"
        return result


def test_bilibili_description_uses_existing_metadata_response_for_both_paths():
    from linkread_desktop.extractors.bilibili import extract_bilibili
    url = "https://www.bilibili.com/video/BV1rxqmBhE91?p=2"
    session = BiliDescriptionSession()
    doc = extract_bilibili(url, session=session)
    assert doc.source.description.startswith("原始简介\n00:30")
    assert doc.source.description_format == "text" and len(session.calls) == 3
    refresh = BiliDescriptionSession()
    assert shownotes_metadata(url, session=refresh).description == doc.source.description
    assert len(refresh.calls) == 1
    with pytest.raises(SubtitleUnavailableError) as error:
        extract_bilibili(url, session=BiliDescriptionSession(subtitles=False))
    assert error.value.source.description == doc.source.description


def test_youtube_original_description_is_kept_and_refresh_never_reads_subtitles(monkeypatch):
    from linkread_desktop.extractors.youtube import extract_youtube
    description = "第一行 < 原样\n\n00:30 章节\nhttps://example.com/source"
    FakeYoutubeDL.info = {"id": "1-Fo8McCD9w", "title": "视频", "description": description,
                          "subtitles": {"zh": [{"ext": "vtt", "url": "https://captions.example/zh"}]}}
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    doc = extract_youtube(YOUTUBE_URL)
    assert doc.source.description == description and doc.source.description_format == "text"
    monkeypatch.setattr(FakeYoutubeDL, "urlopen", lambda *a: pytest.fail("refresh must not fetch subtitles or media"))
    assert shownotes_metadata(YOUTUBE_URL).description == description


def test_youtube_refresh_rejects_metadata_for_another_video(monkeypatch):
    FakeYoutubeDL.info = {"id": "2-Fo8McCD9w", "title": "另一集", "description": "wrong"}
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    with pytest.raises(ExtractionError, match="不匹配"):
        shownotes_metadata(YOUTUBE_URL)


def test_audio_download_source_description_survives_cached_asr(monkeypatch, tmp_path):
    from linkread_desktop.asr.bailian import BailianAsr
    from linkread_desktop.asr.pipeline import transcribe_video
    def download(url, folder, config, **kw):
        path = folder / "audio.m4a"
        path.write_bytes(b"fake-audio")
        return path, SourceInfo(platform="youtube", title="视频", url=url, description="原始简介\n下一行",
                                description_format="text", duration_ms=2000)
    monkeypatch.setattr("linkread_desktop.asr.pipeline.download_audio", download)
    monkeypatch.setattr(BailianAsr, "transcribe", lambda *a, **kw: [TranscriptSegment(start_ms=0, text="字幕")])
    config = AsrConfig(token="fake", chunk_minutes=0)
    result = transcribe_video(YOUTUBE_URL, config, cache_dir=tmp_path)
    monkeypatch.setattr("linkread_desktop.asr.pipeline.download_audio", lambda *a, **kw: pytest.fail("cached ASR must not redownload"))
    cached = transcribe_video(YOUTUBE_URL, config, cache_dir=tmp_path)
    assert result == cached and cached.source.description == "原始简介\n下一行"

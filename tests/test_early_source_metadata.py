"""Generation can identify its source before long subtitle or ASR work completes."""
from linkread_desktop import desktop
from linkread_desktop.errors import ExtractionError
from linkread_desktop.models import SourceInfo
from linkread_desktop.progress import report
from test_ai_notes import FakeProvider
from test_desktop import invoke


def test_metadata_is_emitted_before_transcript_and_survives_later_failure(tmp_path, monkeypatch, capsys):
    source = SourceInfo(
        platform="apple_podcasts", title="已确认的播客单集", author="节目名称",
        video_id="1000801308527", duration_ms=2467000,
        url="https://podcasts.apple.com/cn/podcast/id1552904790?i=1000801308527",
        thumbnail_url="https://is1-ssl.mzstatic.com/image/600x600bb.jpg",
    )
    def extract(url, *, progress, **options):
        report(progress, "metadata_done", "已获取播客信息", source=source)
        report(progress, "captions", "正在检查公开字幕")
        raise ExtractionError("模拟后续网络失败")
    monkeypatch.setattr("linkread_desktop.generation.extract_document", extract)
    monkeypatch.setattr(desktop, "make_provider", lambda *a, **k: FakeProvider())
    status, events = invoke(monkeypatch, capsys, tmp_path, "generate", source.url)
    assert status == 1
    metadata_index = next(i for i, event in enumerate(events) if "metadata" in event)
    assert events[metadata_index]["metadata"] == {
        "title": source.title, "platform": "apple_podcasts", "sourceUrl": source.url,
        "author": source.author, "publishedAt": None, "durationMs": source.duration_ms,
        "thumbnailUrl": source.thumbnail_url,
    }
    assert metadata_index < next(i for i, e in enumerate(events) if e.get("stage") == "captions")
    assert not any(e.get("stage") == "saved_transcript" for e in events)
    assert not any("source" in event or "work_dir" in event for event in events)
    assert events[-1]["message"] == "模拟后续网络失败"


def test_early_source_metadata_does_not_publish_untrusted_links():
    source = SourceInfo(platform="apple_podcasts", title="标题", video_id="1000801308527",
                        url="https://evil.example/cn/podcast/id1552904790?i=1000801308527",
                        thumbnail_url="https://evil.example/cover.jpg")
    metadata = desktop._source_info_metadata(source)
    assert metadata["sourceUrl"] is None and metadata["thumbnailUrl"] is None

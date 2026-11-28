import json

from linkread_desktop import desktop
from linkread_desktop.archive_manifest import MANIFEST_FILENAME, published_article_marker
from linkread_desktop.generation import generate_article
from linkread_desktop.reading_preferences import (
    default_reading_preferences,
    normalize_reading_preferences,
    reading_preferences_signature,
    valid_custom_font,
)
from linkread_desktop.renderers.html import render_html
from test_ai_notes import FakeProvider, make_transcript
from test_desktop import invoke


def test_reading_preferences_validation_and_css_safety():
    defaults = default_reading_preferences()
    assert normalize_reading_preferences(None) == defaults
    assert normalize_reading_preferences("bad") == defaults
    assert valid_custom_font("PingFang SC")
    assert valid_custom_font("LXGW WenKai")
    for malicious in [
        "",
        "   ",
        "Font; } body { display: none",
        'Font" onclick="alert(1)',
        "Font</style><script>alert(1)</script>",
        "Font\ncolor:red",
        "a" * 129,
    ]:
        assert not valid_custom_font(malicious)

    sanitized = normalize_reading_preferences({
        "theme": "sepia",
        "fontFamily": "custom",
        "customFont": "Font</style><script>",
        "fontSize": 99,
        "lineWidth": "huge",
        "lineSpacing": "standard",
        "defaultTab": "summary",
        "showCover": False,
        "showHighlights": False,
    })
    assert sanitized == {
        "theme": "sepia",
        "fontFamily": "sans",  # custom without valid customFont falls back cleanly
        "customFont": "",
        "fontSize": 16,
        "lineWidth": "standard",
        "lineSpacing": "standard",
        "defaultTab": "summary",
        "showCover": False,
        "showHighlights": False,
    }


def test_render_html_applies_reading_preferences_and_default_tab():
    raw = make_transcript(text="这是一段用于验证阅读偏好的字幕内容。")
    html_raw = render_html(
        raw,
        language="zh-CN",
        cover_url="https://i.ytimg.com/vi/1-Fo8McCD9w/hqdefault.jpg",
        reading_preferences={
            "theme": "sepia",
            "fontFamily": "custom",
            "customFont": "PingFang SC",
            "fontSize": 20,
            "lineWidth": "narrow",
            "lineSpacing": "standard",
            "defaultTab": "summary",
            "showCover": False,
            "showHighlights": False,
        },
    )
    assert 'data-theme="sepia"' in html_raw
    assert 'data-reading-font="custom"' in html_raw
    assert 'data-reading-width="narrow"' in html_raw
    assert 'data-reading-spacing="standard"' in html_raw
    assert 'data-reading-highlights="off"' in html_raw
    assert 'data-reading-cover="hide"' in html_raw
    assert "--reading-font-size:20px" in html_raw
    assert '--font-reading-custom:"PingFang SC",var(--font)' in html_raw
    assert "article-cover" not in html_raw.split("<header", 1)[1].split("</header>", 1)[0]
    # Raw transcript has no summary panel, so transcript stays active.
    assert 'class="tab active" id="tab-transcript"' in html_raw


def test_pure_json_archive_renders_on_demand_and_invalidates_cache_on_reading_change(
    tmp_path, monkeypatch, capsys
):
    raw = make_transcript(num=6, text="纯数据归档按需渲染测试内容。")
    monkeypatch.setattr("linkread_desktop.generation.extract_document", lambda *a, **k: raw)
    monkeypatch.setattr(desktop, "make_provider", lambda *a, **k: FakeProvider())
    library = tmp_path / "library"

    status, events = invoke(monkeypatch, capsys, library, "generate", raw.source.url)
    assert status == 0, events[-1]
    article_id = events[-1]["article"]["id"]
    folder = library / article_id
    assert (folder / MANIFEST_FILENAME).is_file()
    assert not (folder / "article.html").exists()
    assert not (folder / ".reader.html").exists()
    assert published_article_marker(folder) == folder / MANIFEST_FILENAME

    # First reader call renders .reader.html with default preferences.
    status, first = invoke(monkeypatch, capsys, library, "reader", article_id)
    assert status == 0
    assert (folder / ".reader.html").is_file()
    html_default = (folder / ".reader.html").read_text(encoding="utf-8")
    assert 'class="tab active" id="tab-transcript"' in html_default
    assert 'class="panel active" id="panel-transcript"' in html_default

    # Repeating with same reading prefs hits cache without changing revision.
    status, cached = invoke(monkeypatch, capsys, library, "reader", article_id)
    assert status == 0 and cached[0]["revision"] == first[0]["revision"]

    # Changing reading prefs (defaultTab=summary, customFont, fontSize=22) rebuilds .reader.html.
    custom = json.dumps({
        "theme": "dark",
        "fontFamily": "custom",
        "customFont": "Songti SC",
        "fontSize": 22,
        "lineWidth": "wide",
        "lineSpacing": "compact",
        "defaultTab": "summary",
        "showCover": False,
        "showHighlights": False,
    })
    status, updated = invoke(
        monkeypatch, capsys, library, "reader", "--reading-preferences", custom, article_id,
    )
    assert status == 0
    assert updated[0]["revision"] != first[0]["revision"]
    html_custom = (folder / ".reader.html").read_text(encoding="utf-8")
    assert 'data-theme="dark"' in html_custom
    assert '--font-reading-custom:"Songti SC",var(--font)' in html_custom
    assert "--reading-font-size:22px" in html_custom
    assert 'class="tab active" id="tab-summary"' in html_custom
    assert 'class="panel active" id="panel-summary"' in html_custom
    assert 'class="panel" hidden id="panel-transcript"' in html_custom

    # Reading state and details updates work cleanly on pure-JSON archives.
    status, patched = invoke(
        monkeypatch, capsys, library, "reading-state", "--read", "true", "--starred", "true", article_id,
    )
    assert status == 0 and patched[0]["isRead"] is True and patched[0]["isStarred"] is True

    # Signature changes when preferences change.
    assert reading_preferences_signature(None) != reading_preferences_signature({"fontSize": 22})


def test_legacy_article_html_archive_remains_readable_and_stateful(tmp_path, monkeypatch, capsys):
    raw = make_transcript(text="旧归档兼容性测试。")
    library = tmp_path / "library"
    folder = library / "legacy-article"
    folder.mkdir(parents=True)
    (folder / "transcript.json").write_text(raw.model_dump_json(), encoding="utf-8")
    (folder / "article.html").write_text("<!doctype html><title>Legacy</title>", encoding="utf-8")

    assert published_article_marker(folder) == folder / "article.html"
    status, listed = invoke(monkeypatch, capsys, library, "library")
    assert status == 0 and len(listed[0]["articles"]) == 1
    assert listed[0]["articles"][0]["id"] == "legacy-article"

    status, prepared = invoke(monkeypatch, capsys, library, "reader", "legacy-article")
    assert status == 0 and (folder / ".reader.html").is_file()

    status, state = invoke(monkeypatch, capsys, library, "reading-state", "--read", "true", "legacy-article")
    assert status == 0 and state[0]["isRead"] is True

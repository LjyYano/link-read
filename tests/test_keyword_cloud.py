"""Offline keyword-cloud rendering, source preservation and injection safety."""

from html.parser import HTMLParser
import re

import pytest

from linkread_desktop.ai.models import NoteAnalysis, NoteDocument
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.html import render_html


class CloudParser(HTMLParser):
    def __init__(self, markup):
        super().__init__(convert_charrefs=True)
        self.elements = []
        self.words = []
        self.current = None
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        if tag == "button" and "cloud-word" in attributes.get("class", "").split():
            self.current = {"attrs": attributes, "text": ""}
            self.words.append(self.current)

    def handle_data(self, value):
        if self.current is not None:
            self.current["text"] += value

    def handle_endtag(self, tag):
        if tag == "button":
            self.current = None


def make_note(keywords):
    transcript = TranscriptDocument(
        source=SourceInfo(platform="local", title="程序员与 AI"),
        transcript_source="local",
        segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="AI 改变了程序员的工作。")],
    )
    return NoteDocument(
        transcript=transcript, provider="codex",
        analysis=NoteAnalysis(summary="讨论 AI 和程序员。", keywords=keywords),
    )


@pytest.mark.parametrize("keywords", [
    ["程序员"],
    ["AI 辅助编码", "人月神话", "软件架构", "Java 后端求职", "Larry"],
    ["需要完整显示而不截断的长主题" * 15, "C++ / C#", "AI\n与开发"],
    [f"原文主题 {index}" for index in range(61)],
])
def test_cloud_preserves_every_keyword_as_accessible_search_button(keywords):
    page = CloudParser(render_html(make_note(keywords)))
    assert [word["text"] for word in page.words] == keywords
    assert [word["attrs"]["data-keyword"] for word in page.words] == keywords
    assert all(word["attrs"].get("type") == "button" for word in page.words)
    assert any("keyword-cloud" in attrs.get("class", "").split() for _, attrs in page.elements)
    assert any(attrs.get("id") == "panel-keywords" for _, attrs in page.elements)


def test_keyword_text_and_attributes_cannot_create_markup_or_execute_code():
    attacks = [
        '<img src="https://example.invalid/pixel" onerror="alert(1)">',
        '</button><script>alert("injected")</script><button>',
        '" autofocus onfocus="alert(2)" data-spoof="yes',
        "';background:url(https://example.invalid/leak);--weight:999",
        '<!--LR:READER_SCRIPT--> & "quoted"',
    ]
    page = CloudParser(render_html(make_note(attacks)))
    assert [word["text"] for word in page.words] == attacks
    assert [word["attrs"]["data-keyword"] for word in page.words] == attacks
    assert not any(tag in {"img", "iframe"} for tag, _ in page.elements)
    baseline = CloudParser(render_html(make_note(["安全关键词"])))
    assert sum(tag == "script" for tag, _ in page.elements) == sum(
        tag == "script" for tag, _ in baseline.elements
    )
    assert not any(name.startswith("on") or name in {"autofocus", "data-spoof"}
                   for _, attrs in page.elements for name in attrs)
    assert all("url(" not in word["attrs"].get("style", "") for word in page.words)


@pytest.mark.parametrize("keywords", [[], ["", " ", "\t\n"]])
def test_empty_keywords_do_not_create_a_panel_or_tab(keywords):
    page = CloudParser(render_html(make_note(keywords)))
    assert not page.words
    assert not any(attrs.get("id") in {"panel-keywords", "tab-keywords"}
                   for _, attrs in page.elements)


def test_raw_transcript_does_not_invent_a_keyword_cloud():
    page = CloudParser(render_html(make_note(["仅在 AI 笔记中"]).transcript))
    assert not page.words
    assert not any(attrs.get("id") in {"panel-keywords", "tab-keywords"}
                   for _, attrs in page.elements)


def test_keyword_cloud_is_self_contained_and_deterministic_without_mutating_note():
    note = make_note(["AI 辅助编码", "人月神话", "软件架构"])
    original = note.model_dump_json()
    markup = render_html(note)
    assert markup == render_html(note)
    assert note.model_dump_json() == original
    page = CloudParser(markup)
    assert not any(tag == "script" and "src" in attrs for tag, attrs in page.elements)
    assert not any(tag == "link" and attrs.get("rel") in {"stylesheet", "preload"}
                   for tag, attrs in page.elements)
    assert not re.search(r"@import\s|url\(\s*['\"]?(?:https?:)?//", markup, re.I)


def test_keyword_cloud_ships_the_layout_module_inline():
    markup = render_html(make_note(["关键词"]))
    assert "LinkReadKeywordLayout" in markup
    assert not any(tag == "script" and "src" in attrs for tag, attrs in CloudParser(markup).elements)


def test_keyword_weights_keep_the_saved_rank_order_within_range():
    page = CloudParser(render_html(make_note(["短词", "第二个词", "第三个主题词", "第四个关键词", "很长的第五个关键词主题"])))
    weights = [float(re.fullmatch(r"--weight:([0-9.]+)", word["attrs"]["style"])[1]) for word in page.words]
    assert weights[0] == 1 and weights == sorted(weights, reverse=True)
    assert all(0 < weight <= 1 for weight in weights) and weights[0] > weights[-1]

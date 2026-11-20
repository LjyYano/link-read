"""Mindmap structure follows content instead of a fixed three-level template."""

import pytest

from linkread_desktop.ai.publication import _FIELDS, _INSTRUCTIONS, _make_mindmap
from linkread_desktop.models import SourceInfo, TranscriptDocument, TranscriptSegment
from linkread_desktop.renderers.extras import load_extras, validate_mindmap
from linkread_desktop.renderers.html import render_html


def test_mindmap_can_follow_five_levels_and_mixed_branch_depths():
    tree = {"root": "计算改变社会", "nodes": [
        {"parent": 0, "text": "计算需求"},
        {"parent": 1, "text": "炮兵的弹道问题"},
        {"parent": 2, "text": "大量重复计算"},
        {"parent": 3, "text": "推动电子计算机"},
        {"parent": 0, "text": "能力扩散"},
        {"parent": 5, "text": "航天与商业应用"},
        {"parent": 0, "text": "算力成为基础设施"},
    ]}
    markdown = _make_mindmap(tree)
    assert "##### 推动电子计算机" in markdown
    assert markdown.endswith("## 算力成为基础设施\n")
    assert markdown.count("算力成为基础设施") == 1
    validate_mindmap(markdown)


def test_parent_order_does_not_require_depth_first_model_output():
    markdown = _make_mindmap({"root": "Root", "nodes": [
        {"parent": 0, "text": "First"}, {"parent": 0, "text": "Second"},
        {"parent": 1, "text": "First detail"}, {"parent": 2, "text": "Second detail"},
    ]})
    assert markdown == "# Root\n## First\n### First detail\n## Second\n### Second detail\n"


def test_short_content_does_not_need_artificial_branches_or_details():
    assert _make_mindmap({"root": "One idea", "nodes": [{"parent": 0, "text": "Its consequence"}]}) == (
        "# One idea\n## Its consequence\n"
    )
    assert "nodes" in _FIELDS["mindmap"]["properties"]
    assert "不固定为三级" in _INSTRUCTIONS["mindmap"]


@pytest.mark.parametrize("nodes", [
    [{"parent": 1, "text": "Self"}],
    [{"parent": 2, "text": "Future"}, {"parent": 1, "text": "Cycle"}],
    [{"parent": -1, "text": "Negative"}],
    [{"parent": True, "text": "Not an integer parent"}],
    [{"parent": 0, "text": "Two\nlines"}],
    [{"parent": 0, "text": " "}],
    [{"parent": 0, "text": "x" * 101}],
    [{"parent": n, "text": f"Nested {n}"} for n in range(6)],
    [{"parent": 0, "text": f"Item {n}"} for n in range(81)],
])
def test_malformed_or_unbounded_proposals_fail_without_discarding_nodes(nodes):
    with pytest.raises(ValueError):
        _make_mindmap({"root": "Root", "nodes": nodes})


@pytest.mark.parametrize("markdown", ["# Root\n### Skipped\n", "# One\n# Two\n", "## Missing root\n", "# Only root\n", "# Root\nbody text\n"])
def test_manual_mindmaps_keep_strict_tree_validation(markdown):
    with pytest.raises(ValueError):
        validate_mindmap(markdown)


def test_previous_three_level_proposals_remain_readable():
    old = {"root": "Root", "branches": [
        {"topic": "A", "details": ["A detail"]}, {"topic": "B", "details": ["B detail"]},
    ]}
    assert "### A detail" in _make_mindmap(old)


def test_existing_large_heading_map_still_loads_and_renders_without_regeneration(tmp_path):
    outline = "# Existing topic\n## First branch\n" + "".join(
        f"### Legacy detail {index}\n" for index in range(90)
    ) + "## Second branch\n### Final point\n"
    stored = tmp_path / "mindmap.md"
    stored.write_text(outline, encoding="utf-8")
    extras = load_extras(tmp_path / "transcript.json")
    assert extras.mindmap == outline
    transcript = TranscriptDocument(
        source=SourceInfo(platform="local", title="Existing article"), transcript_source="local",
        segments=[TranscriptSegment(start_ms=0, end_ms=1000, text="Original transcript remains readable.")],
    )
    markup = render_html(transcript, extras=extras)
    assert "Legacy detail 89" in markup and "Final point" in markup
    assert 'id="tab-mindmap"' in markup
    assert stored.read_text(encoding="utf-8") == outline

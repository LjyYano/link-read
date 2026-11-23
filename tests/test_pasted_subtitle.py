import json
import sys

import pytest

from linkread_desktop import desktop


@pytest.mark.parametrize(("suffix", "text", "expected"), [
    ("srt", "1\n00:00:01,000 --> 00:00:02,000\nOriginal text\n", "subtitle_validated"),
    ("vtt", "WEBVTT\n\n00:01.000 --> 00:02.000\nOriginal text\n", "subtitle_validated"),
    ("json", '{"body":[{"from":1,"to":2,"content":"Original text"}]}', "subtitle_validated"),
    ("srt", "Private subtitle text without timestamps\n", "error"),
    ("json", '{"body":[{"from":2,"to":1,"content":"Private invalid cue"}]}', "error"),
])
def test_pasted_subtitles_are_validated_without_generating_or_logging_content(
    suffix, text, expected, tmp_path, monkeypatch, capsys,
):
    file = tmp_path / f"pasted.{suffix}"
    file.write_text(text, encoding="utf-8")
    library = tmp_path / "library"
    monkeypatch.setenv("LINKREAD_UI_LANGUAGE", "en")
    monkeypatch.setattr(sys, "argv", ["sidecar", "--library", str(library), "validate-subtitle", "--", str(file)])
    monkeypatch.setattr(desktop, "make_provider", lambda *a, **kw: pytest.fail("Validation must not start AI"))
    result = desktop.main()
    output = capsys.readouterr().out
    event = json.loads(output)
    assert event["type"] == expected
    assert not library.exists()
    assert "Original text" not in output and "Private" not in output and str(file) not in output
    if expected == "error":
        assert result == 1 and event["code"] == "invalid_pasted_subtitle"
    else:
        assert result == 0

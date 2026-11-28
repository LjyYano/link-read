"""The Rust app supervises every sidecar through its stdin pipe.

These tests spawn the real backend process the way `desktop/src-tauri/src/sidecar.rs`
does (stdin piped and held open, LINKREAD_WATCH_PARENT=1): the sidecar must exit
cleanly while the app holds the pipe, and must stop when the app closes it.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from linkread_desktop.article_state import file_lock

REPO_ROOT = Path(__file__).resolve().parents[1]


def spawn_sidecar(library: Path) -> subprocess.Popen:
    environment = dict(os.environ,
                       PYTHONPATH=str(REPO_ROOT / "src"),
                       PYTHONUTF8="1",
                       PYTHONDONTWRITEBYTECODE="1",
                       LINKREAD_WATCH_PARENT="1")
    return subprocess.Popen(
        [sys.executable, "-m", "linkread_desktop.desktop", "--library", str(library), "library"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
    )


def test_sidecar_exits_cleanly_while_the_app_holds_stdin_open(tmp_path):
    """The app keeps stdin open for the whole request, so the sidecar must exit 0."""
    process = spawn_sidecar(tmp_path / "资料库")
    output = process.stdout.read()  # the app reads the full answer before the child exits
    status = process.wait(timeout=60)
    process.stdin.close()
    stderr = process.stderr.read()
    assert status == 0, f"sidecar exited with {status}: {stderr.decode(errors='replace')}"
    assert stderr == b""
    event = json.loads(output)
    assert event == {"type": "library", "articles": [], "unavailable": 0}


def test_closing_stdin_stops_a_sidecar_waiting_on_the_library(tmp_path):
    """Closing the pipe must stop a sidecar even while it waits for the library lock."""
    library = tmp_path / "资料库"
    library.mkdir()
    with file_lock(library / ".article-state.lock"):
        process = spawn_sidecar(library)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.05)
            # The lock is ours, so an alive sidecar is blocked on it, not finishing.
            assert process.poll() is None, "sidecar finished while the library lock was held"
            process.stdin.close()
            assert process.wait(timeout=30) == 1
            assert process.stdout.read() == b""
        finally:
            if process.poll() is None:
                process.kill()

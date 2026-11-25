"""Explicit source checks, separate from fast metadata previews.

Use the same exact-episode caption rules as generation, with ASR disabled. A
successful check verifies captions, not future AI availability. No result here
grants permission to transcribe, and no temporary media/archive is written.
"""
from queue import Empty, Queue
from threading import Thread
import time

from .errors import LinkReadError, SubtitleLoginRequiredError, SubtitleUnavailableError, error_details
from .links import canonical_link
from .pipeline import extract

CHECK_SECONDS = 30.0
_BLOCKED_CODES = frozenset({"protected_media", "unsupported_audio_stream"})


def _check(source: str) -> dict:
    try:
        # Explicit None is intentional: this path must never submit paid ASR,
        # read credentials, or switch to downloading audio when captions lack.
        extract(source, asr=None)
    except SubtitleUnavailableError:
        return {"status": "needs_asr", "code": "subtitles_unavailable",
                "message": "未找到可读取的公开字幕；可启用语音识别。生成时仍会检查音频是否可用，本次检查未下载音频。"}
    except SubtitleLoginRequiredError:
        return {"status": "needs_asr", "code": "subtitle_login_required",
                "message": "B 站要求登录后查看字幕。生成时可使用语音识别转写公开音轨。"}
    except LinkReadError as exc:
        failure = error_details(exc)
        return {"status": "blocked" if failure["code"] in _BLOCKED_CODES else "unknown",
                "code": failure["code"], "message": failure["message"]}
    return {"status": "captions", "code": "public_captions",
            "message": "已读取并校验该内容的公开字幕，可以开始生成。"}


def check_source(source: str, *, timeout: float = CHECK_SECONDS) -> dict:
    """Bound DNS, extractors and trickling responses with one wall-clock budget.

    The desktop runs this in a dedicated sidecar with its own process-group
    deadline. The daemon worker lets the short-lived sidecar exit on timeout;
    the native deadline also cleans up a frozen bootloader and its children.
    """
    source = canonical_link(source)
    results: Queue[dict | Exception] = Queue(maxsize=1)

    def run():
        try:
            results.put(_check(source))
        except Exception as exc:
            results.put(exc)

    Thread(target=run, name="linkread-source-check", daemon=True).start()
    try:
        result = results.get(timeout=timeout)
    except Empty:
        result = {"status": "unknown", "code": "source_check_timeout",
                  "message": "来源检查暂时超时，尚未确认内容是否可用；可以重试检查或查看来源。"}
    if isinstance(result, Exception):
        # Extractors normally provide a safe LinkReadError. Unknown exceptions
        # must not leak remote bodies, signed media URLs or dependency output.
        result = {"status": "unknown", "code": "source_check_failed",
                  "message": "暂时无法完成来源检查，请重试检查或查看来源。"}
    return {**result, "source": source, "checkedAt": int(time.time() * 1000)}

"""Identify unsupported podcast streams without obtaining media or playback keys.

Only the public top-level HLS manifest is inspected. This module never follows
variant, segment, key, or license URLs and does not add HLS download support.
"""
import base64
import binascii
import json
import re
from urllib.parse import unquote, urlsplit

from ..errors import ExtractionError
from .podcast_http import get_bytes

_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_DESCRIPTOR_CHARS = 16 * 1024
_HLS_CONTENT_TYPES = frozenset({
    "application/vnd.apple.mpegurl", "application/x-mpegurl", "application/mpegurl",
    "audio/mpegurl", "audio/x-mpegurl",
})
_ATTRIBUTE = re.compile(r'([A-Z0-9-]+)=("[^"\r\n]*"|[^,\s]+)(?:,|$)')
_ENCRYPTED_METHODS = frozenset({"AES-128", "SAMPLE-AES", "SAMPLE-AES-CTR"})
_DRM_KEY_FORMATS = frozenset({
    "com.apple.streamingkeydelivery", "com.microsoft.playready",
    "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed",  # Widevine
    "urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95",  # PlayReady
})


def is_hls_url(url: str | None) -> bool:
    try:
        return unquote(urlsplit(url).path).lower().endswith(".m3u8")
    except (TypeError, ValueError, AttributeError):
        return False


def is_hls_content_type(content_type: str | None) -> bool:
    return (isinstance(content_type, str)
            and content_type.split(";", 1)[0].strip().lower() in _HLS_CONTENT_TYPES)


def _attributes(value: str) -> dict[str, str] | None:
    """Read a complete HLS attribute list, including commas inside quotes."""
    result = {}
    position = 0
    while position < len(value):
        match = _ATTRIBUTE.match(value, position)
        if match is None or match[1] in result:
            return None
        item = match[2]
        result[match[1]] = item[1:-1] if item.startswith('"') else item
        position = match.end()
    return result


def _protected_descriptor(method, key_format, uri) -> bool:
    return ((isinstance(method, str) and method.upper() in _ENCRYPTED_METHODS)
            or (isinstance(key_format, str) and key_format.lower() in _DRM_KEY_FORMATS)
            or (isinstance(uri, str) and uri.lower().startswith("skd://")))


def _apple_session_keys(value: str | None) -> bool:
    # Apple's com.apple.hls.keys VALUE contains a base64-encoded JSON array.
    # Do not recursively scan arbitrary metadata or execute/fetch any URI.
    if not isinstance(value, str) or not value or len(value) > _MAX_DESCRIPTOR_CHARS:
        return False
    try:
        records = json.loads(base64.b64decode(value, validate=True))
    except (ValueError, UnicodeError, binascii.Error, RecursionError):
        return False
    if not isinstance(records, list) or len(records) > 64:
        return False
    return any(isinstance(record, dict) and _protected_descriptor(
        record.get("method"), record.get("keyformat"), record.get("uri"),
    ) for record in records)


def reject_hls_audio(content: bytes) -> None:
    """Always reject HLS; identify protection only from explicit key metadata.

    Absence of a protection tag in the master does not prove its variants are
    unencrypted. Such a manifest remains an unsupported stream, never a file.
    """
    try:
        if not isinstance(content, bytes) or not content or len(content) > _MAX_MANIFEST_BYTES:
            raise ValueError
        lines = content.decode("utf-8-sig").splitlines()
        if not lines or lines[0].strip() != "#EXTM3U":
            raise ValueError
    except (ValueError, UnicodeError) as exc:
        raise ExtractionError(
            "音源未返回有效的 HLS 清单，无法确认可转写的音频；请查看来源或稍后重试",
            code="audio_manifest_invalid", retryable=False, action="source",
        ) from exc
    protected = False
    for raw in lines[1:]:
        line = raw.strip()
        if line.startswith(("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")):
            attributes = _attributes(line.split(":", 1)[1])
            if attributes and _protected_descriptor(
                attributes.get("METHOD"), attributes.get("KEYFORMAT"), attributes.get("URI"),
            ):
                protected = True
                break
        elif line.startswith("#EXT-X-SESSION-DATA:"):
            attributes = _attributes(line.split(":", 1)[1])
            if (attributes and attributes.get("DATA-ID") == "com.apple.hls.keys"
                    and _apple_session_keys(attributes.get("VALUE"))):
                protected = True
                break
    if protected:
        raise ExtractionError(
            "已识别该单集，但音频受播放保护，暂不支持直接转写；可查看来源或导入已有字幕",
            code="protected_media", retryable=False, action="source",
        )
    raise ExtractionError(
        "已识别该单集，但音源是流式播放清单，暂不支持直接转写；可查看来源或导入已有字幕",
        code="unsupported_audio_stream", retryable=False, action="source",
    )


def check_audio_source(url: str | None, *, session) -> None:
    """Ordinary file URLs require no probe; HLS gets one bounded manifest read."""
    if is_hls_url(url):
        content = get_bytes(
            session, url, max_bytes=_MAX_MANIFEST_BYTES,
            accept="application/vnd.apple.mpegurl, application/x-mpegurl, text/plain",
            timeout=(3, 5), max_seconds=8,
        )
        reject_hls_audio(content)

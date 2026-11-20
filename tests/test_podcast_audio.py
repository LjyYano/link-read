"""Public playlist diagnosis only: never request keys, variants or audio bytes."""
import base64
import json

import pytest
import requests

from linkread_desktop.errors import ExtractionError, SubtitleUnavailableError
from linkread_desktop.extractors import podcast_audio as audio


def classify(content: bytes) -> ExtractionError:
    with pytest.raises(ExtractionError) as raised:
        audio.reject_hls_audio(content)
    error = raised.value
    assert not isinstance(error, SubtitleUnavailableError)
    assert error.action == "source" and error.retryable is False
    return error


def apple_descriptor(value, *, data_id="com.apple.hls.keys") -> bytes:
    encoded = base64.b64encode(json.dumps(value).encode()).decode()
    return (f'#EXTM3U\n#EXT-X-SESSION-DATA:DATA-ID="{data_id}",VALUE="{encoded}"\n'
            '#EXT-X-STREAM-INF:BANDWIDTH=96000\nvariant.m3u8\n').encode()


@pytest.mark.parametrize("tag", [
    '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://example.invalid/episode",KEYFORMAT="com.apple.streamingkeydelivery"',
    '#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,KEYFORMAT="com.apple.streamingkeydelivery",URI="skd://example.invalid/key"',
    '#EXT-X-KEY:METHOD=AES-128,URI="https://example.invalid/key"',
    '#EXT-X-KEY:METHOD=SAMPLE-AES-CTR,URI="https://example.invalid/key,with-comma"',
    '#EXT-X-KEY:KEYFORMAT="com.apple.streamingkeydelivery",URI="skd://example.invalid/key"',
])
def test_standard_key_metadata_identifies_playback_protection(tag):
    error = classify(("#EXTM3U\n" + tag + "\n#EXTINF:6,\nsegment.m4a\n").encode())
    assert error.code == "protected_media"
    assert "受播放保护" in str(error)
    assert "付费" not in str(error) and "登录" not in str(error)
    assert "skd://" not in str(error) and "example.invalid" not in str(error)


def test_actual_apple_master_descriptor_shape_is_protected():
    # Minimized shape of the observed public master; no actual IDs or media URLs.
    content = apple_descriptor([{"keyformatversion": ["1"], "method": "SAMPLE-AES",
                                 "keyformat": "com.apple.streamingkeydelivery",
                                 "uri": "skd://example.invalid/episode/key"}])
    assert classify(content).code == "protected_media"


@pytest.mark.parametrize("content", [
    b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=96000\nvariant.m3u8\n",
    b"#EXTM3U\n#EXT-X-KEY:METHOD=NONE\n#EXTINF:6,\nsegment.m4a\n",
    b"#EXTM3U\n# a comment about skd://example.invalid and SAMPLE-AES\nvariant.m3u8\n",
    b"#EXTM3U\n# comment #EXT-X-KEY:METHOD=SAMPLE-AES\nvariant.m3u8\n",
    b'#EXTM3U\n#EXT-X-SESSION-DATA:DATA-ID="ordinary-comment",VALUE="skd://example.invalid"\n',
    apple_descriptor([{"comment": "skd://example.invalid and SAMPLE-AES"}]),
    apple_descriptor({"nested": [{"method": "SAMPLE-AES"}]}),
    apple_descriptor([{"method": "SAMPLE-AES"}], data_id="unrelated.metadata"),
])
def test_unverified_master_is_unsupported_not_mislabeled_protected_or_clear(content):
    assert classify(content).code == "unsupported_audio_stream"


@pytest.mark.parametrize("value", ["%%invalid%%", "", "e30=", "W10=", "WzFd", "A" * 20000,
                                  base64.b64encode(b"not-json").decode(),
                                  base64.b64encode(b"[" * 2000).decode()])
def test_bad_or_oversized_apple_descriptor_never_proves_protection(value):
    content = f'#EXTM3U\n#EXT-X-SESSION-DATA:DATA-ID="com.apple.hls.keys",VALUE="{value}"\n'.encode()
    assert classify(content).code == "unsupported_audio_stream"


@pytest.mark.parametrize("line", [
    '#EXT-X-KEY:METHOD=NONE,METHOD=SAMPLE-AES',
    '#EXT-X-KEY:METHOD="SAMPLE-AES',
    '#EXT-X-SESSION-DATA:DATA-ID="com.apple.hls.keys",VALUE="bad",VALUE="bad"',
])
def test_malformed_attributes_do_not_establish_drm(line):
    assert classify(("#EXTM3U\n" + line).encode()).code == "unsupported_audio_stream"


@pytest.mark.parametrize("content", [b"", b"<html>Access denied</html>", b"not a playlist", b"\xff\xfe",
                                      b"#EXTM3U\n" + b"a" * (256 * 1024)])
def test_bad_response_is_a_manifest_error_not_protection_or_subtitle_absence(content):
    assert classify(content).code == "audio_manifest_invalid"


@pytest.mark.parametrize("url,expected", [
    ("https://media.example.com/master.m3u8?token=opaque", True),
    ("https://media.example.com/MASTER.M3U8", True),
    ("https://media.example.com/master%2Em3u8", True),
    ("https://media.example.com/file.m4a?next=master.m3u8", False),
    ("https://media.example.com/master.m3u8/file.mp3", False),
])
def test_playlist_detection_uses_path_not_tracking_query(url, expected):
    assert audio.is_hls_url(url) is expected


@pytest.mark.parametrize("value,expected", [
    ("Application/Vnd.Apple.MpegURL; charset=utf-8", True),
    ("audio/x-mpegurl", True), ("application/x-mpegurl", True),
    ("audio/mpeg", False), ("text/plain", False), (None, False),
])
def test_playlist_content_types(value, expected):
    assert audio.is_hls_content_type(value) is expected


def test_source_check_reads_only_one_bounded_public_master(monkeypatch):
    calls = []
    session = object()
    url = "https://media.example.com/master.m3u8?opaque=value"
    content = apple_descriptor([{"method": "SAMPLE-AES", "uri": "skd://example.invalid/key"}])

    def fetch(client, requested, **options):
        calls.append((client, requested, options))
        return content

    monkeypatch.setattr(audio, "get_bytes", fetch)
    with pytest.raises(ExtractionError) as raised:
        audio.check_audio_source(url, session=session)
    assert raised.value.code == "protected_media"
    assert len(calls) == 1 and calls[0][:2] == (session, url)
    assert calls[0][2]["max_bytes"] == 256 * 1024
    assert calls[0][2]["max_seconds"] == 8 and calls[0][2]["timeout"] == (3, 5)


def test_regular_audio_url_does_not_trigger_probe_or_download(monkeypatch):
    monkeypatch.setattr(audio, "get_bytes", lambda *a, **k: pytest.fail("Unexpected media request"))
    assert audio.check_audio_source("https://media.example.com/episode.m4a", session=object()) is None
    assert audio.check_audio_source(None, session=object()) is None


def test_network_error_is_preserved_and_never_becomes_subtitle_unavailability(monkeypatch):
    try:
        raise requests.Timeout("fixture timeout")
    except requests.Timeout as cause:
        failure = ExtractionError("播客资源请求失败")
        failure.__cause__ = cause

    def failed(*args, **kwargs):
        raise failure

    monkeypatch.setattr(audio, "get_bytes", failed)
    with pytest.raises(ExtractionError) as raised:
        audio.check_audio_source("https://media.example.com/master.m3u8", session=object())
    assert raised.value is failure
    assert raised.value.code == "extraction_failed"
    assert isinstance(raised.value.__cause__, requests.Timeout)
    assert not isinstance(raised.value, SubtitleUnavailableError)

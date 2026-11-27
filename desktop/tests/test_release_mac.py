"""Offline regression checks for the macOS release script."""

from base64 import b64encode
from pathlib import Path
import runpy

import pytest


release = runpy.run_path(str(Path(__file__).parents[1] / "scripts/release_mac.py"))


def test_keychain_profile_can_notarize_without_plaintext_credentials(monkeypatch):
    for name in (*release["NOTARY_API"], *release["NOTARY_ACCOUNT"]):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LINKREAD_NOTARY_PROFILE", "LinkRead")

    assert release["notary_options"]() == ["--keychain-profile", "LinkRead"]


def test_update_feed_rejects_a_signature_bound_to_a_different_version(tmp_path):
    archive = tmp_path / "LinkRead-macos-arm64.app.tar.gz"
    key_id = b"key-1234"
    public_data = b"Ed" + key_id + bytes(32)
    signed_data = b"ED" + key_id + bytes(64)
    signature = b64encode(b"untrusted comment: test\n" + b64encode(signed_data)
                          + b"\ntrusted comment: timestamp:0\tversion:0.1.0\n").decode("ascii")
    archive.with_suffix(".gz.sig").write_text(signature)
    assets = "https://example.test/releases/v0.1.0"

    metadata = release["manifest"]("0.1.0", "aarch64", assets, archive, public_data)
    assert metadata["platforms"]["darwin-aarch64"] == {
        "url": f"{assets}/LinkRead-macos-arm64.app.tar.gz", "signature": signature,
    }
    with pytest.raises(ValueError, match="版本与发布版本不匹配"):
        release["manifest"]("0.2.0", "aarch64", assets, archive, public_data)

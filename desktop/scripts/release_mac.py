"""Build signed macOS distribution assets without uploading them to a release host."""

import argparse
from base64 import b64decode
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlsplit


DESKTOP = Path(__file__).resolve().parents[1]
TARGETS = {"arm64": ("aarch64", "aarch64"), "x86_64": ("x86_64", "x64")}


def https_url(value):
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username is not None
            or url.password is not None or url.fragment or url.query):
        raise argparse.ArgumentTypeError("请使用不含凭据、查询参数或片段的公开 HTTPS 地址")
    return value


def notary_options():
    api = ("APPLE_API_KEY", "APPLE_API_ISSUER", "APPLE_API_KEY_PATH")
    account = ("APPLE_ID", "APPLE_TEAM_ID", "APPLE_PASSWORD")
    if all(os.environ.get(key) for key in api):
        options = ["--key-id", os.environ[api[0]], "--issuer", os.environ[api[1]], "--key", os.environ[api[2]]]
    elif all(os.environ.get(key) for key in account):
        options = ["--apple-id", os.environ[account[0]], "--team-id", os.environ[account[1]],
                   "--password", os.environ[account[2]]]
    else:
        raise ValueError("请先配置 Apple 公证凭据，见 docs/macos-release.md。")
    profile = os.environ.get("LINKREAD_NOTARY_PROFILE")
    return ["--keychain-profile", profile] if profile else options


def minisign_data(encoded):
    lines = b64decode(encoded, validate=True).splitlines()
    if len(lines) < 2:
        raise ValueError("Tauri 更新签名或公钥格式无效。")
    return b64decode(lines[1], validate=True)


def manifest(version, arch, assets_base, archive, public_data):
    signature = archive.with_suffix(".gz.sig").read_text(encoding="ascii").strip()
    signed_data = minisign_data(signature)
    if len(signed_data) != 74 or public_data[2:10] != signed_data[2:10]:
        raise ValueError("更新包所用的签名私钥与内置更新公钥不匹配。")
    signed_comment = b64decode(signature, validate=True).splitlines()[2:3]
    version_field = f"version:{version}".encode("ascii")
    if not signed_comment or version_field not in signed_comment[0].split(b"\t"):
        raise ValueError("更新包签名中的版本与发布版本不匹配。")
    return {"version": version, "platforms": {f"darwin-{arch}": {
        "url": f"{assets_base.rstrip('/')}/{quote(archive.name)}",
        "signature": signature,
    }}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", required=True, type=https_url, help="用户长期访问的更新清单地址")
    parser.add_argument("--assets-base", required=True, type=https_url, help="本版本下载文件所在的 HTTPS 目录")
    parser.add_argument("--public-key", required=True, type=Path, help="Tauri 更新公钥 .pub 文件")
    options = parser.parse_args()

    if sys.platform != "darwin" or platform.machine() not in TARGETS:
        raise ValueError("请在目标架构对应的 macOS 环境运行发布构建。")
    identity = os.environ.get("APPLE_SIGNING_IDENTITY", "")
    if not identity.startswith("Developer ID Application:"):
        raise ValueError("APPLE_SIGNING_IDENTITY 必须指向已安装的 Developer ID Application 证书。")
    if not os.environ.get("TAURI_SIGNING_PRIVATE_KEY"):
        raise ValueError("请设置 TAURI_SIGNING_PRIVATE_KEY。")
    credentials = notary_options()
    arch, dmg_arch = TARGETS[platform.machine()]
    target = f"{arch}-apple-darwin"
    native = DESKTOP / "src-tauri"
    base = json.loads((native / "tauri.conf.json").read_text())
    config = json.loads((native / "tauri.macos-bundle.json").read_text())
    public_key = options.public_key.expanduser().read_text(encoding="ascii").strip()
    public_data = minisign_data(public_key)
    if len(public_data) != 42 or public_data[:2] != b"Ed":
        raise ValueError("--public-key 必须指向 Tauri 生成的 .pub 公钥文件。")
    config["bundle"].update(createUpdaterArtifacts=True)
    config["bundle"]["macOS"].update(signingIdentity=identity, hardenedRuntime=True)
    config["plugins"] = {"updater": {"pubkey": public_key, "endpoints": [options.feed],
                                     "requireSignedVersion": True}}

    cargo = subprocess.check_output(["cargo", "metadata", "--locked", "--no-deps", "--format-version", "1"],
                                    cwd=native, text=True)
    bundle = Path(json.loads(cargo)["target_directory"]) / target / "release" / "bundle"
    product, version = base["productName"], base["version"]
    app = bundle / "macos" / f"{product}.app"
    archive = app.with_suffix(".app.tar.gz")
    dmg = bundle / "dmg" / f"{product}_{version}_{dmg_arch}.dmg"
    with tempfile.TemporaryDirectory(prefix="linkread-release-") as directory:
        override = Path(directory) / "tauri.release.json"
        override.write_text(json.dumps(config), encoding="utf-8")
        subprocess.run(["npm", "run", "tauri", "--", "build", "--config", str(override),
                        "--target", target, "--bundles", "app,dmg"], cwd=DESKTOP, check=True,
                       env={**os.environ, "LINKREAD_SIDECAR_SIGNING_IDENTITY": identity})

    metadata = manifest(version, arch, options.assets_base, archive, public_data)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", app], check=True)
    subprocess.run(["xcrun", "stapler", "validate", app], check=True)
    subprocess.run(["codesign", "--verify", dmg], check=True)
    notarization = subprocess.check_output(["xcrun", "notarytool", "submit", dmg, "--wait",
                                            "--output-format", "json", *credentials], text=True)
    record = json.loads(notarization)
    if record["status"] != "Accepted":
        raise ValueError(f"DMG 公证未通过，Apple 提交 ID 为 {record['id']}。")
    subprocess.run(["xcrun", "stapler", "staple", dmg], check=True)
    subprocess.run(["xcrun", "stapler", "validate", dmg], check=True)

    feed = bundle / "latest.json"
    with tempfile.NamedTemporaryFile(mode="w", dir=bundle, encoding="utf-8", delete=False) as output:
        json.dump(metadata, output, ensure_ascii=False, indent=2)
        output.write("\n")
    Path(output.name).replace(feed)
    print(f"版本 {version} 的发行文件\n{dmg}\n{archive}\n{archive}.sig\n{feed}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"发行步骤失败，退出码 {error.returncode}。") from None
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from None

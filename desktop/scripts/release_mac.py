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
RELEASES_URL = "https://github.com/LjyYano/link-read/releases"
DEFAULT_FEED = f"{RELEASES_URL}/latest/download/latest.json"
TARGETS = {"arm64": ("aarch64", "arm64"), "x86_64": ("x86_64", "amd64")}
NOTARY_API = ("APPLE_API_KEY", "APPLE_API_ISSUER", "APPLE_API_KEY_PATH")
NOTARY_ACCOUNT = ("APPLE_ID", "APPLE_TEAM_ID", "APPLE_PASSWORD")


def https_url(value):
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username is not None
            or url.password is not None or url.fragment or url.query):
        raise argparse.ArgumentTypeError("请使用不含凭据、查询参数或片段的公开 HTTPS 地址")
    return value


def notary_options():
    profile = os.environ.get("LINKREAD_NOTARY_PROFILE")
    if profile:
        return ["--keychain-profile", profile]
    for names, flags in ((NOTARY_API, ("--key-id", "--issuer", "--key")),
                         (NOTARY_ACCOUNT, ("--apple-id", "--team-id", "--password"))):
        if all(os.environ.get(name) for name in names):
            return [part for flag, name in zip(flags, names) for part in (flag, os.environ[name])]
    raise ValueError("请先配置 Apple 公证凭据，见 docs/macos-release.md。")


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


def notarize(submission, staple_target, credentials):
    print(f"提交 Apple 公证：{staple_target.name}", flush=True)
    output = subprocess.check_output(["xcrun", "notarytool", "submit", submission, "--wait",
                                      "--output-format", "json", *credentials], text=True, timeout=1800)
    record = json.loads(output)
    if record["status"] != "Accepted":
        raise ValueError(f"{staple_target.name} 公证未通过，Apple 提交 ID 为 {record['id']}。")
    subprocess.run(["xcrun", "stapler", "staple", staple_target], check=True)
    subprocess.run(["xcrun", "stapler", "validate", staple_target], check=True)


def build_dmg(app, dmg, product, identity, credentials, directory):
    image = directory / dmg.name
    python = Path(os.environ.get("LINKREAD_PYTHON", DESKTOP.parent / ".venv/bin/python")).expanduser()
    subprocess.run([python, DESKTOP / "scripts/build_dmg_image.py", app, image, product], check=True)
    subprocess.run(["codesign", "--sign", identity, "--timestamp", image], check=True)
    notarize(image, image, credentials)
    subprocess.run(["codesign", "--verify", "--strict", image], check=True)
    dmg.parent.mkdir(parents=True, exist_ok=True)
    image.replace(dmg)


def sign_update(app, archive, version, private_key):
    subprocess.run(["tar", "--no-mac-metadata", "-czf", archive, "-C", app.parent, app.name], check=True)
    sign_env = os.environ.copy()
    key_path = Path(private_key).expanduser()
    try:
        is_file = key_path.is_file()
    except OSError:
        is_file = False  # Inline key data can exceed filesystem component limits.
    if is_file:
        sign_env.pop("TAURI_SIGNING_PRIVATE_KEY")
        sign_env["TAURI_SIGNING_PRIVATE_KEY_PATH"] = str(key_path.resolve())
    subprocess.run(["npm", "run", "tauri", "--", "signer", "sign", "--app-version", version, archive],
                   cwd=DESKTOP, env=sign_env, stdin=subprocess.DEVNULL, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--feed", default=DEFAULT_FEED, type=https_url, help=f"更新清单地址，默认 {DEFAULT_FEED}")
    parser.add_argument("--assets-base", type=https_url, help="文件下载目录，默认使用本仓库的 v<版本号> Release")
    parser.add_argument("--public-key", required=True, type=Path, help="Tauri 更新公钥 .pub 文件")
    parser.add_argument("--first-dmg", action="store_true", help="首次发行仅制作 DMG 与同版本更新清单，仍内置更新公钥")
    options = parser.parse_args()

    if sys.platform != "darwin" or platform.machine() not in TARGETS:
        raise ValueError("请在目标架构对应的 macOS 环境运行发布构建。")
    identity = os.environ.get("APPLE_SIGNING_IDENTITY", "")
    if not identity.startswith("Developer ID Application:"):
        raise ValueError("APPLE_SIGNING_IDENTITY 必须指向已安装的 Developer ID Application 证书。")
    private_key = os.environ.get("TAURI_SIGNING_PRIVATE_KEY")
    if not options.first_dmg and not private_key:
        raise ValueError("请设置 TAURI_SIGNING_PRIVATE_KEY。")
    credentials = notary_options()
    arch, artifact_arch = TARGETS[platform.machine()]
    target = f"{arch}-apple-darwin"
    native = DESKTOP / "src-tauri"
    base = json.loads((native / "tauri.conf.json").read_text())
    product, version = base["productName"], base["version"]
    assets_base = options.assets_base or f"{RELEASES_URL}/download/v{quote(version, safe='')}"
    config = json.loads((native / "tauri.macos-bundle.json").read_text())
    public_key = options.public_key.expanduser().read_text(encoding="ascii").strip()
    public_data = minisign_data(public_key)
    if len(public_data) != 42 or public_data[:2] != b"Ed":
        raise ValueError("--public-key 必须指向 Tauri 生成的 .pub 公钥文件。")
    config["bundle"]["macOS"].update(signingIdentity=identity, hardenedRuntime=True)
    config["plugins"] = {"updater": {"pubkey": public_key, "endpoints": [options.feed],
                                     "requireSignedVersion": True}}

    cargo = subprocess.check_output(["cargo", "metadata", "--locked", "--no-deps", "--format-version", "1"],
                                    cwd=native, text=True)
    bundle = Path(json.loads(cargo)["target_directory"]) / target / "release" / "bundle"
    app = bundle / "macos" / f"{product}.app"
    artifact_name = f"{product}-macos-{artifact_arch}"
    archive = app.with_name(f"{artifact_name}.app.tar.gz")
    signature = archive.with_suffix(".gz.sig")
    dmg = bundle / "dmg" / f"{artifact_name}.dmg"
    bundle.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="linkread-release-", dir=bundle) as temporary:
        directory = Path(temporary)
        override = directory / "tauri.release.json"
        override.write_text(json.dumps(config), encoding="utf-8")
        build_env = {**os.environ, "LINKREAD_SIDECAR_SIGNING_IDENTITY": identity}
        for name in (*NOTARY_API, *NOTARY_ACCOUNT, "TAURI_SIGNING_PRIVATE_KEY", "TAURI_SIGNING_PRIVATE_KEY_PATH",
                     "TAURI_SIGNING_PRIVATE_KEY_PASSWORD"):
            build_env.pop(name, None)
        subprocess.run(["npm", "run", "tauri", "--", "build", "--config", str(override),
                        "--target", target, "--bundles", "app"], cwd=DESKTOP, env=build_env, check=True)
        app_zip = directory / f"{product}.zip"
        subprocess.run(["ditto", "-c", "-k", "--keepParent", app, app_zip], check=True)
        notarize(app_zip, app, credentials)
        subprocess.run(["codesign", "--verify", "--deep", "--strict", app], check=True)
        if options.first_dmg:
            metadata = {"version": version, "platforms": {}}
            artifacts = [dmg]
        else:
            staged_archive = directory / archive.name
            sign_update(app, staged_archive, version, private_key)
            metadata = manifest(version, arch, assets_base, staged_archive, public_data)
            artifacts = [dmg, archive, signature]
        build_dmg(app, dmg, product, identity, credentials, directory)
        if not options.first_dmg:
            staged_archive.replace(archive)
            staged_archive.with_suffix(".gz.sig").replace(signature)

    feed = bundle / "latest.json"
    with tempfile.NamedTemporaryFile(mode="w", dir=bundle, encoding="utf-8", delete=False) as output:
        json.dump(metadata, output, ensure_ascii=False, indent=2)
        output.write("\n")
    Path(output.name).replace(feed)
    print(f"版本 {version} 的发行文件")
    print(*artifacts, feed, sep="\n")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"发行步骤失败，退出码 {error.returncode}。") from None
    except subprocess.TimeoutExpired:
        raise SystemExit("等待 Apple 公证超时。可在钥匙串配置的公证历史中检查提交状态。") from None
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from None

"""Build the local architecture's Python sidecar (--onedir) for Tauri's macOS bundler."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


desktop = Path(__file__).resolve().parents[1]
root = desktop.parent
python = Path(os.environ.get("LINKREAD_PYTHON", root / ".venv/bin/python")).expanduser()
if sys.platform != "darwin" or not python.is_file():
    raise SystemExit("在 macOS 创建本仓库的 .venv，安装桌面后端、yt-dlp 和 PyInstaller，或配置 LINKREAD_PYTHON。")

architecture = subprocess.check_output([python, "-c", "import platform; print(platform.machine())"], text=True).strip()
targets = {"arm64": "aarch64-apple-darwin", "x86_64": "x86_64-apple-darwin"}
target = targets[architecture]
if os.environ.get("TAURI_ENV_TARGET_TRIPLE", target) != target:
    raise SystemExit("请使用目标架构对应的 Python 与 Rust 工具链构建 LinkRead。")

build = desktop / "build" / "sidecar"
dist_root = build / "dist"
sidecar_dir = dist_root / "linkread-sidecar"
binaries = desktop / "src-tauri" / "binaries"
licenses = desktop / "build" / "licenses"

if dist_root.exists():
    shutil.rmtree(dist_root)
if binaries.is_dir():
    for legacy in binaries.glob("linkread-sidecar-*"):
        if legacy.is_symlink() or legacy.is_file():
            legacy.unlink()
        elif legacy.is_dir():
            shutil.rmtree(legacy)

environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(build / "cache"))
subprocess.run([python, "-c", "import yt_dlp"], check=True, timeout=30)

identity = os.environ.get("LINKREAD_SIDECAR_SIGNING_IDENTITY")
subprocess.run([
    python, "-m", "PyInstaller", "--noconfirm", "--onedir", "--bootloader-ignore-signals",
    "--target-arch", architecture, "--name", "linkread-sidecar", "--paths", root / "src",
    "--collect-data", "linkread_desktop.renderers",
    "--distpath", dist_root, "--workpath", build / "work",
    "--specpath", build, desktop / "scripts/sidecar.py",
    *(["--codesign-identity", identity] if identity else []),
], cwd=root, env=environment, check=True, timeout=600)

if not (sidecar_dir / "linkread-sidecar").is_file():
    raise SystemExit(f"未生成 sidecar 可执行文件：{sidecar_dir / 'linkread-sidecar'}")

if identity and identity.startswith("Developer ID Application:"):
    macho_bins = sorted(
        (path for path in sidecar_dir.rglob("*") if path.is_file() and path.suffix.lower() in {".dylib", ".so"}),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for binary in [*macho_bins, sidecar_dir / "linkread-sidecar"]:
        subprocess.run([
            "codesign", "--force", "--timestamp", "--options", "runtime", "--sign", identity, str(binary),
        ], check=True, timeout=120)

# Audit runtime distributions actually frozen inside the --onedir sidecar alongside Python/npm/Rust notices.
subprocess.run([
    python, desktop / "scripts/collect_licenses.py",
    "--target", target,
    "--output", licenses,
    "--sidecar-dir", sidecar_dir,
], cwd=root, check=True, timeout=360)

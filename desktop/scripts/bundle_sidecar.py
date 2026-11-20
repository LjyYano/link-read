"""Build the local architecture's Python sidecar for Tauri's macOS bundler."""

import os
from pathlib import Path
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
binaries = desktop / "src-tauri" / "binaries"
environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(build / "cache"))
subprocess.run([
    python, "-c", "import yt_dlp",
], check=True, timeout=30)
# Run with the same interpreter used by PyInstaller. The original license texts
# are both embedded in the sidecar and copied to the App's Resources/licenses.
licenses = desktop / "build" / "licenses"
subprocess.run([
    python, desktop / "scripts/collect_licenses.py", "--target", target, "--output", licenses,
], cwd=root, check=True, timeout=360)
subprocess.run([
    python, "-m", "PyInstaller", "--noconfirm", "--onefile", "--bootloader-ignore-signals",
    "--target-arch", architecture, "--name", f"linkread-sidecar-{target}", "--paths", root / "src",
    "--collect-data", "linkread_desktop.renderers", "--add-data", f"{licenses}:licenses",
    "--distpath", binaries, "--workpath", build / "work",
    "--specpath", build, desktop / "scripts/sidecar.py",
], cwd=root, env=environment, check=True, timeout=600)

#!/bin/sh
set -eu

LINKREAD_DESKTOP_DIR="$(cd -- "$(dirname -- "$0")" && pwd -P)"
LINKREAD_REPO_DIR="$(dirname -- "$LINKREAD_DESKTOP_DIR")"
cd "$LINKREAD_REPO_DIR"

export LINKREAD_PYTHON="${LINKREAD_PYTHON:-$LINKREAD_REPO_DIR/.venv/bin/python}"
if [ "${LINKREAD_PYTHON#/}" = "$LINKREAD_PYTHON" ] || [ ! -x "$LINKREAD_PYTHON" ]; then
  echo "找不到可用的 Python，请准备仓库的 .venv 或通过 LINKREAD_PYTHON 指定解释器的绝对路径。" >&2
  exit 1
fi

export PATH="${CARGO_HOME:-$HOME/.cargo}/bin:/opt/homebrew/opt/node@24/bin:/usr/local/opt/node@24/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if command -v rustup >/dev/null 2>&1; then
  if LINKREAD_CARGO="$(rustup which --toolchain stable cargo 2>/dev/null)"; then
    LINKREAD_RUST_BIN="$(dirname -- "$LINKREAD_CARGO")"
    export PATH="$LINKREAD_RUST_BIN:$PATH"
    export RUSTUP_TOOLCHAIN=stable
  fi
fi
hash -r

if ! command -v cargo >/dev/null 2>&1 || ! command -v rustc >/dev/null 2>&1; then
  echo "未找到本地 Rust 工具链（cargo / rustc）。请先安装 Rust stable 后再运行 dev.sh。" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "未找到 npm。请先安装 Node.js 后再运行 dev.sh。" >&2
  exit 1
fi

echo "当前使用的 Cargo 与 Rustc 路径和版本"
command -v cargo
cargo --version
rustc --version

echo "启动 LinkRead..."
cd "$LINKREAD_DESKTOP_DIR"
exec npm run tauri -- dev

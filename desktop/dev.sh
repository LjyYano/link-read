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

if ! command -v rustup >/dev/null 2>&1; then
  LINKREAD_INSTALLER="$(mktemp -t linkread-rust)"
  trap 'rm -f "$LINKREAD_INSTALLER"' EXIT
  curl --proto '=https' --tlsv1.2 --connect-timeout 10 --max-time 120 -fsSL \
    https://sh.rustup.rs -o "$LINKREAD_INSTALLER"
  /bin/sh "$LINKREAD_INSTALLER" \
    -y --profile minimal --default-toolchain none --no-modify-path
  rm -f "$LINKREAD_INSTALLER"
  trap - EXIT
fi

echo "【1/2】安装或更新 Rust stable 工具链..."
rustup toolchain install stable --profile minimal --no-self-update
rustup override set stable
export RUSTUP_TOOLCHAIN=stable

LINKREAD_CARGO="$(rustup which --toolchain stable cargo)"
LINKREAD_RUST_BIN="$(dirname -- "$LINKREAD_CARGO")"
export PATH="$LINKREAD_RUST_BIN:$PATH"
hash -r

echo "当前使用的 Cargo 路径和版本"
command -v cargo
cargo --version
rustc --version

echo "【2/2】启动 LinkRead..."
cd "$LINKREAD_DESKTOP_DIR"
exec npm run tauri -- dev

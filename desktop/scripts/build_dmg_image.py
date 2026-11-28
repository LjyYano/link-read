"""Create the Finder installer layout around an already built macOS app."""

import argparse
from pathlib import Path


DESKTOP = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("volume_name")
    args = parser.parse_args()

    if not args.app.is_dir() or args.app.suffix != ".app":
        raise SystemExit(f"无效的 macOS App 目录：{args.app}")
    icon_path = DESKTOP / "src-tauri/icons/icon.icns"
    background_path = DESKTOP / "dmg/Background.tiff"
    if not icon_path.is_file():
        raise SystemExit(f"缺少 DMG 图标文件：{icon_path}")
    if not background_path.is_file():
        raise SystemExit(f"缺少 DMG 背景图文件：{background_path}")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    try:
        from dmgbuild import build_dmg
    except ModuleNotFoundError as error:
        if error.name != "dmgbuild":
            raise
        raise SystemExit("请在构建环境中安装 dmgbuild，见 docs/macos-release.md。") from None

    build_dmg(str(args.output), args.volume_name, lookForHiDPI=False, settings={
        # Changing Finder attributes on the app invalidates strict code signature verification.
        "files": [str(args.app)],
        "symlinks": {"Applications": "/Applications"},
        "icon": str(icon_path),
        "background": str(background_path),
        "window_rect": ((180, 140), (620, 270)),
        "icon_locations": {args.app.name: (165, 149), "Applications": (455, 149)},
        "icon_size": 90,
        "text_size": 14,
        "format": "UDZO",
        "filesystem": "HFS+",
    })


if __name__ == "__main__":
    main()

# 发布 LinkRead for macOS

由 `npm run release:mac` 构建的正式版在启动后检查一次更新。macOS 菜单中的「LinkRead → 检查更新…」可手动检查。发现新版本后，用户可选「稍后」或「下载并安装」。应用验证签名，安装完成后重新打开。`sh desktop/dev.sh` 和 `npm run app` 供本地开发与测试，使用这些命令构建的版本会显示未启用自动更新。

## 首次发布前

- 默认使用 [LinkRead 的 GitHub Releases](https://github.com/LjyYano/link-read/releases) 托管更新，正式包的固定检查地址是 [latest.json](https://github.com/LjyYano/link-read/releases/latest/download/latest.json)。首个正式 Release 发布后，这个地址才有清单可读。
- 在 Mac 钥匙串中安装 **Developer ID Application** 证书。根据 [Tauri 的 Apple 公证说明](https://v2.tauri.app/distribute/sign/macos/#notarization) 设置 App Store Connect API 环境变量 `APPLE_API_KEY`、`APPLE_API_ISSUER`、`APPLE_API_KEY_PATH`，或 Apple ID 环境变量 `APPLE_ID`、`APPLE_TEAM_ID`、`APPLE_PASSWORD`。也可通过 `LINKREAD_NOTARY_PROFILE` 指定 DMG 公证使用的钥匙串配置，Tauri 的 App 公证仍使用上述 Apple 环境变量。
- 生成并备份一对 [Tauri 更新签名密钥](https://v2.tauri.app/plugin/updater/#signing-updates)。后续版本继续使用同一私钥签名。将私钥与密码留在本地的安全位置。

```bash
cd desktop
npm ci
npm run tauri -- signer generate -w "$HOME/.tauri/linkread.key"
```

## 每次发布

在 `desktop/src-tauri/tauri.conf.json` 设置要发布的版本号。首次编译前按仓库 README 准备 Python 环境和 PyInstaller。确认环境变量 `TAURI_SIGNING_PRIVATE_KEY` 指向私钥，`TAURI_SIGNING_PRIVATE_KEY_PASSWORD` 已通过本地终端安全设置。

```bash
cd desktop
export APPLE_SIGNING_IDENTITY='Developer ID Application: 你的证书名称'
export TAURI_SIGNING_PRIVATE_KEY="$HOME/.tauri/linkread.key"
npm run release:mac -- --public-key "$HOME/.tauri/linkread.key.pub"
```

发布标签为 `v<版本号>`，例如配置版本为 `0.1.0` 时使用 `v0.1.0`。若改用其他文件托管，可用 `--feed` 指定长期使用的清单地址，用 `--assets-base` 指定本版本文件的下载目录。脚本为当前架构构建。Apple Silicon 与 Intel 如需同时发布，分别在对应架构环境构建，为同名更新包使用不同下载目录，然后把两份清单的对应 `platforms` 条目合并到同一个 `latest.json`。

脚本打印四份发行文件：

- `.dmg` 供用户首次下载安装。安装时将 LinkRead 拖入「应用程序」。
- `.app.tar.gz` 供已安装的应用下载更新。
- `.app.tar.gz.sig` 是对应签名。签名内容也写入更新清单。
- `latest.json` 是版本、下载地址和签名的清单。在其他文件可公开下载后上传清单。使用 GitHub Releases 时，可一次把四份文件上传到同一个草稿，再发布为最新正式版本。

在干净的 Mac 上打开 DMG 验证安装。从带相同更新地址和公钥的较旧构建启动，确认能弹出提示、安装后显示新版本，且原有书架可正常打开。更新地址和公钥会写入首次分发的 App，后续应继续维护该地址并沿用同一对密钥。

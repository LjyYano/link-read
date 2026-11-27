# 发布 LinkRead for macOS

由 `npm run release:mac` 构建的正式版在启动后检查一次更新。macOS 菜单中的「LinkRead → 检查更新…」可手动检查。发现新版本后，用户可选「稍后」或「下载并安装」。应用验证签名，安装完成后重新打开。`sh desktop/dev.sh` 和 `npm run app` 供本地开发与测试，使用这些命令构建的版本会显示未启用自动更新。

## 首次发布前

- 默认使用 [LinkRead 的 GitHub Releases](https://github.com/LjyYano/link-read/releases) 托管更新，正式包的固定检查地址是 [latest.json](https://github.com/LjyYano/link-read/releases/latest/download/latest.json)。首个正式 Release 发布后，这个地址才有清单可读。
- 在 Mac 钥匙串中安装 **Developer ID Application** 证书。通过 `LINKREAD_NOTARY_PROFILE` 指定 `notarytool` 钥匙串配置即可公证 App 和 DMG。也可使用 App Store Connect API 环境变量 `APPLE_API_KEY`、`APPLE_API_ISSUER`、`APPLE_API_KEY_PATH`，或 Apple ID 环境变量 `APPLE_ID`、`APPLE_TEAM_ID`、`APPLE_PASSWORD`。 [Apple 公证文档](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow)
- 生成并备份一对 [Tauri 更新签名密钥](https://v2.tauri.app/plugin/updater/#signing-updates)。后续版本继续使用同一私钥签名。将私钥与密码留在本地的安全位置。

```bash
cd desktop
npm ci
npm run tauri -- signer generate -w "$HOME/.tauri/linkread.key"
```

## 每次发布

在 `desktop/src-tauri/tauri.conf.json` 设置要发布的版本号，首次编译前按[桌面开发指南](../desktop/README.md#开发)准备 Python 环境，并按其中的 App 构建步骤安装 PyInstaller。DMG 的 Finder 布局由 `dmgbuild` 写入镜像，构建时将它装在同一 Python 环境。使用钥匙串公证时，设置证书名称与公证配置。

```bash
.venv/bin/python -m pip install 'dmgbuild>=1.6,<2'
cd desktop
export APPLE_SIGNING_IDENTITY='Developer ID Application: 你的证书名称'
export LINKREAD_NOTARY_PROFILE='LinkRead'
```

若首版仅供用户通过 DMG 安装，可运行以下命令。首包内置更新公钥和更新清单地址。将 DMG 和 `latest.json` 一起发布后，同版本可正常检查；后续发布较高版本时应使用完整的签名发行流程。

```bash
npm run release:mac -- --public-key "$HOME/.tauri/linkread.key.pub" --first-dmg
```

已有发行版的用户通过签名更新包升级。确认 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` 已通过本地终端安全设置，再执行完整构建。

```bash
export TAURI_SIGNING_PRIVATE_KEY="$HOME/.tauri/linkread.key"
npm run release:mac -- --public-key "$HOME/.tauri/linkread.key.pub"
```

发布标签为 `v<版本号>`，例如配置版本为 `0.1.0` 时使用 `v0.1.0`。附件名使用 `LinkRead-macos-<架构>`，其中 Apple Silicon 为 `arm64`，Intel 为 `amd64`。发布页先写英文 Changelog，再写中文更新日志。安装包放在 Assets，仓库首页的下载链接指向最新版本的 DMG。

若改用其他文件托管，可用 `--feed` 指定长期使用的清单地址，用 `--assets-base` 指定本版本文件的下载目录。脚本为当前架构构建。Apple Silicon 与 Intel 如需同时发布，分别在对应架构环境构建，把两份清单的 `platforms` 条目合并到同一个 `latest.json`，再将两个架构的附件上传至同一版本。

完整构建会在本地生成四份文件。首发 DMG 模式只产出安装包和更新清单。

- `.dmg` 供用户首次下载安装。安装时将 LinkRead 拖入「应用程序」。
- `.app.tar.gz` 供已安装的应用下载更新。
- `.app.tar.gz.sig` 是本地签名文件。签名已写入更新清单，公开附件可省略这一份。
- `latest.json` 包含版本、更新包下载地址和签名。正式版从 GitHub 的固定地址读取它，每个设为最新的正式版本都要提供此附件。

发布完整构建时，在 GitHub Releases 草稿中上传 DMG、更新包和 `latest.json`，核对下载地址后再发布为最新正式版本。

在干净的 Mac 上打开 DMG 验证安装。从带相同更新地址和公钥的较旧构建启动，确认能弹出提示、安装后显示新版本，且原有书架可正常打开。更新地址和公钥会写入首次分发的 App，后续应继续维护该地址并沿用同一对密钥。

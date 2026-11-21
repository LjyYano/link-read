# LinkRead Desktop

LinkRead 的桌面客户端：把 YouTube、B 站视频、Apple Podcasts 单集和本地字幕整理成完整阅读文章，支持书架、搜索、思维导图、金句、阅读进度及 HTML / Markdown 导出。

**这是独立维护的私有桌面应用，计划商业发行。** 免费 CLI 在 [link-read](https://github.com/LjyYano/link-read) 仓库采用 GPL；本仓库采用[专有源码许可](LICENSE)，自带后端，无需安装 CLI。

# 1. 项目结构

| 目录 | 职责 |
| --- | --- |
| `desktop/src/` | React / TypeScript 界面 |
| `desktop/src-tauri/` | Tauri / Rust 原生窗口、文件操作和进程管理 |
| `src/linkread_desktop/` | 桌面内部 Python 后端：提取、AI、ASR、书架与阅读导出 |
| `desktop/scripts/` | Python sidecar、图标和 App 打包工具 |
| `tests/`、`desktop/tests/` | 后端与前端离线回归验证 |

后端通过 `python -m linkread_desktop.desktop` 与原生进程通信，不提供公共 `linkread` 命令，不引用相邻仓库。

# 2. 本地开发

需要 macOS、Xcode 命令行工具、Rust 1.90+、Node.js 22.12+ 和 Python 3.11+。

```bash
git clone https://github.com/LjyYano/link-read-desktop.git
cd link-read-desktop

python3 -m venv .venv
.venv/bin/python -m pip install -e '.[youtube,test]'

npm ci --prefix desktop
sh desktop/dev.sh
```

开发模式默认读取本仓库的 `.venv/bin/python`。需要其他解释器时，通过 `LINKREAD_PYTHON` 指定绝对路径。详见[桌面功能与开发说明](desktop/README.md)。

# 3. 构建 App

在仓库根目录准备本机架构的 Python 环境，然后构建：

```bash
.venv/bin/python -m pip install 'pyinstaller>=6,<7'
cd desktop
npm run app
```

产物位于 `desktop/src-tauri/target/release/bundle/macos/LinkRead.app`。App 带有自己的 Python 后端、运行时、yt-dlp 和离线阅读资源。

打包时自动收集项目与实际依赖的许可证、版本和文件哈希，随 sidecar 和 App 的 `Contents/Resources/licenses/` 分发。缺少许可正文会报出具体组件并停止打包；处理方式见[许可收集说明](desktop/licenses/README.md)。

当前配置使用临时签名用于本机测试。公开分发须完成 Developer ID 签名、Apple 公证及实机安装验证；支付、激活和订阅系统尚未接入。

# 4. 本地验证

桌面应用的启动和界面验证统一在仓库根目录运行 `sh desktop/dev.sh`。离线测试和前端构建使用下列命令。

```bash
.venv/bin/python -m pytest -q
cd desktop
npm ci
npm run build
node --test tests/*.test.mjs
```

本仓库不使用 GitHub Actions。提交与合并消息使用 `[skip ci]`。各项已执行验证和平台限制见 [docs/migration.md](docs/migration.md)。

# 5. 迁移与数据兼容

迁移源为原仓库固定快照 `a2297a5a7fe98d50435ce6bed1449ffca92e2aca`。它已经包含桌面设置、任务恢复、阅读状态和导出等功能；本次保留这些行为，并把后端改为独立的 `linkread_desktop` 包。

应用标识 `com.yano.linkread`、阅读协议、资料库位置和持久化键保持兼容。默认资料库仍在 `~/Library/Application Support/com.yano.linkread/library`；环境变量 `LINKREAD_DESKTOP_LIBRARY` 可以指定已有资料库。迁移不会搬动或删除用户资料。

# 6. 许可与协作

- 项目自有代码：[LICENSE](LICENSE)。
- 历史来源与授权边界：[docs/licensing.md](docs/licensing.md)。
- 第三方声明：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- 品牌政策：[TRADEMARK.md](TRADEMARK.md)。
- 私有开发与贡献政策：[CONTRIBUTING.md](CONTRIBUTING.md)。

本仓库没有 CLA，不默认接收外部源码贡献；后续引入他人代码必须具有适用于本产品的授权。

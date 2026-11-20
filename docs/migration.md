# 1. 迁移来源与范围

迁移时间：2026-11-20（北京时间）。

| 项目 | 固定来源 / 结果 |
| --- | --- |
| 原仓库快照 | `LjyYano/link-read@a2297a5a7fe98d50435ce6bed1449ffca92e2aca` |
| 原快照树 | `9efba53c3fed82f630ded51b03b270ff35ee01b1` |
| 原 GUI 备份 | `archive/gui-before-cli-split` |
| 目标仓库 | `LjyYano/link-read-desktop`，私有 |
| 当前免费 CLI | 在独立 `link-read` 仓库维护 GPL 版本 |

迁入桌面 React / TypeScript、Tauri / Rust、图标、打包脚本、Python 后端和对应测试。未复制 CLI 后续的社区贡献；原备份及历史保留，不重写原仓库历史。

# 2. 后端独立化

- Python 包改为 `linkread_desktop`，发行名为 `linkread-desktop-backend`。
- 生成流程从旧 CLI 模块提取到 `generation.py`；四个相关函数的签名和逻辑与原快照一致。
- 移除公共 CLI 命令、`cli.py`、包级 `__main__.py` 和 Typer 依赖。
- Tauri 开发模式调用本仓库解释器的 `-m linkread_desktop.desktop`；发行模式调用随包的 `linkread-sidecar`。
- PyInstaller、浏览器测试 fixture 和资源读取全部指向本仓库的后端，不依赖相邻 checkout、已安装的 `linkread` 或公开 CLI 仓库。

保持 `com.yano.linkread`、`linkread-reader` 协议、`linkread-sidecar` 名称、JSON 文档类型、资料库与缓存键。迁移不自动搬动、覆盖或删除用户资料。

# 3. 已完成的本地验证

以下结果来自 Linux 验证环境，不能替代原生 macOS 验收。

| 验证 | 结果 |
| --- | --- |
| 独立虚拟环境 | 未安装旧 CLI 包或 Typer，后端可正常安装导入 |
| 后端全量测试 | 752 项通过 |
| 仓库外 sidecar 启动 | 使用隔离 Python 模式返回正确书架 JSON Lines |
| 后端 wheel | 构建、直接导入、渲染、缓存及资源检查通过；无旧 CLI 入口 |
| 前端依赖安装 | `npm ci` 通过 |
| TypeScript / Vite | 类型检查与生产构建通过 |
| Node 前端测试 | 134 项通过 |
| 开发服务器 | 真实 Vite 启动、6 个入口模块 HTTP 加载及依赖预构建通过 |
| 模块命名 | 大小写 / 扩展名冲突检查通过 |
| 当前许可收集 | 项目、6 个 vendor 组件、16 个 Python 依赖与构建工具、CPython、5 个 npm 生产组件，共 29 项组件 / 56 份原始文本，复制及 SHA-256 校验通过 |
| 许可收集失败路径 | 6 项测试通过，覆盖缺失正文、声明不完整、来源版本校验、路径安全和输出目录保护 |
| Linux 冻结后端 | PyInstaller 单文件构建通过；独立工作目录、移除 Python 源码路径后，书架、阅读、Markdown 导出与阅读状态命令成功，原始资料不变 |
| 冻结包内容 | 含独立后端与 15 个渲染资源，不含旧 CLI 包；56 份许可文本均入包并逐字节比对通过 |

验证未调用用户的付费模型或语音服务，没有使用 GitHub Actions。

# 4. App 打包与许可证

`desktop/scripts/collect_licenses.py` 在 App 打包时生成实际依赖及原文清单；Mac 上同时通过锁定的 Cargo 元数据收集目标平台的 Rust 依赖。缺少许可证正文时明确报出组件名称和版本，不生成空声明或根据 SPDX 名称猜造许可。

生成目录为 `desktop/build/licenses/`，包含原始文本、来源、版本和 SHA-256 清单；它会进入冻结的 sidecar 及 `LinkRead.app/Contents/Resources/licenses/`。该目录属于构建产物，不提交源代码仓库。

若上游包未携带必要文本，应从对应版本的可靠来源补齐，并记录来源，操作方式见 [desktop/licenses/README.md](../desktop/licenses/README.md)。浏览器 vendor 清单以 bundle 哈希绑定版本及来源。收集到文件并不等于完成全部法律审计；实际二进制、系统库与分发方式仍须按适用许可证核对。

# 5. macOS 原生验收

当前环境没有 Rust / macOS 工具链，Cargo 实际依赖的许可收集分支和以下步骤未在本次 Linux 环境中执行：

1. 使用与目标架构一致的 Python 与 Rust 构建 PyInstaller sidecar。
2. 编译 Tauri / Rust，运行 `npm run app`，确认 `.app` 内 sidecar 与许可资源路径正确。
3. 从 Finder 启动 App，验证书架、阅读、导出、任务取消及失败恢复。
4. 使用测试资料核对旧数据路径、阅读状态、已有凭证与 Keychain 升级行为。
5. 通过明确配置测试真实模型与 ASR，确认网络、权限和费用边界。
6. 商业分发前完成 Developer ID 签名、公证和目标机器安装测试。

在 macOS 仓库根目录：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[youtube,test]' 'pyinstaller>=6,<7'
.venv/bin/python -m pytest -q
cd desktop
npm ci
npm run build
node --test tests/*.test.mjs
npm run app
```

源码迁移和本地测试通过不代表已经签名、公证或发布商业安装包。支付、激活及订阅系统不在此次迁移范围内。

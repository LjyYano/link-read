# LinkRead Desktop 开发规则

这里只收录跨功能的硬性约束；各功能的行为细节写在 `desktop/README.md` 和 `docs/` 对应文档中，由离线测试保证。新规则只有在违反后会造成不可逆损失、违背用户明确要求，或无法从代码和现有文档推断时才加入，否则写进对应文档或测试。

## 项目定位

- 本仓库独立维护专有的桌面应用：`desktop/` 是 Tauri + React 前端，`src/linkread_desktop/` 是 Python 后端。不依赖相邻的 CLI 仓库，不安装或执行 GPL 的 `linkread` CLI，不把 CLI 命令打进后端，也不把运行时逻辑写回 `SKILL.md`。
- 数据流固定为「提取 → JSON → AI 丰富 → 渲染」。生成编排只在后端 `generation.py`；视频和本地字幕走同一条 AI 链路，没有绕过 AI 的本地文件旁路。
- 保持 `com.yano.linkread`、`linkread-reader` 协议、`linkread-sidecar`、JSON 文档类型和持久化键向后兼容，不损坏旧用户数据。

## 原文保真

- `transcript.json` 和用户导入的原始字幕文件是不可变的数据资产：不丢弃、不改写、不伪造时间戳。新内容写入独立文件或向后兼容的 Schema 版本。
- 解析和渲染是确定性代码，AI 只做语义分析。AI 对原文的引用只能是字幕索引，时间戳和逐字引文由程序回填；非法引用必须报错，不得补造内容。
- 校对只能修改标点和空白，并按非标点字符与原文逐字比对；失败时提示或回退，不能丢失内容。
- 数据或 AI 结果缺失时不显示虚构的摘要、章节、导图或金句；AI 生成的小标题只用于导航，不冒充原文。

## 用户数据

- 重新生成和重新整理在隔离工作区进行，全部必需阶段完成后才原子替换归档；失败或取消时保留旧归档和已通过校验的阶段产物。
- 永久删除归档文件夹需要用户二次确认并严格校验路径；删除失败任务只移除任务记录，不动阶段缓存。
- 已读、收藏、标题与分类覆盖等手动数据独立于生成结果保存，重新生成、重启和恢复后都要保留；已读和收藏只由用户手动标记。
- 状态文件原子写入：写入失败时不启动新任务、不覆盖原记录，也不先改界面。

## 凭据与安全

- Token 只保存在应用数据目录的私有文件（权限 0600），不进 localStorage、命令行参数或任务记录；日志和错误信息不含 Token 或完整字幕。不提交 API Key、Cookie、音频缓存和签名私钥。
- 自定义 API 只接受 HTTPS（本机 loopback 可用 HTTP）且不跟随重定向。YouTube Cookie 只在用户于设置中显式启用后使用，不自动读取浏览器会话。
- 字幕、模型输出、标题、简介等不可信文本渲染到 HTML 时必须转义，跳转链接用可信 ID 重建；生成的 HTML 单文件离线可运行，不依赖 CDN 或外部脚本。
- 第三方网络调用必须有超时和可理解的失败提示；不绕过平台的访问控制、订阅或内容保护。

## 费用与外部调用

- ASR 默认关闭：只有用户显式开启、并确认没有可用字幕后才转写。网络或权限错误、损坏字幕、受保护音源以及任何未确认的状态都不得触发收费转写；ASR 使用独立 Token，不隐式复用聊天密钥。
- 已提交的云任务持久化任务 ID 并优先恢复，不重复提交；取消本地等待不等于取消云任务。
- 调用模型、重新生成、补取简介只由用户的明确操作触发；阅读、导出和缓存重建只使用已保存的内容，不调用模型。重启后未完成的任务只展示，点击继续才执行。
- Codex 使用 read-only sandbox；Claude Code 使用 bare 模式和 API Key 并禁用工具。不要放宽这些限制。

## 界面

- 进度和状态必须真实：按实际完成的批次计数，不按计时器虚增；任务终态优先于最后一条阶段快照。
- 迟到的响应不得覆盖更新的输入、配置或用户操作。
- 不添加营销区块，书架保持现有的列表结构。

## 协作边界

- 许可：根 `LICENSE` 只覆盖有权专有的材料，没有 CLA；不得从 GPL CLI 同步社区贡献后改为专有。详见 `CONTRIBUTING.md` 和 `docs/licensing.md`。
- 不使用 GitHub Actions：未经用户重新明确授权，不创建、恢复、触发或重跑工作流。验证只在本地进行，环境不具备时如实说明；提交和合并信息保留 `[skip ci]`。
- `LjyYano/skill-pack` 和 `LjyYano/html-library` 保持只读。
- 未经验证不声称完成 macOS App、Developer ID 签名或公证验收；构建与签名产物不入库，打包须携带项目许可和依赖的原始声明。

## 开发约定

| 用途 | 命令（仓库根目录） |
| --- | --- |
| 启动与界面验证 | `sh desktop/dev.sh` |
| 后端环境（首次） | `python3 -m venv .venv && .venv/bin/python -m pip install -e '.[youtube,test]'` |
| 后端测试 | `.venv/bin/python -m pytest -q` |
| 前端构建与测试 | `npm ci --prefix desktop && cd desktop && npm run build && node --test tests/*.test.mjs` |

- 修改 Parser、Extractor、Schema 或跨语言协议时必须补充离线测试；前端、Rust、Python 共用的规范化逻辑共用 `tests/fixtures/` 下的同一组用例。推送前后端测试和前端构建都要通过。
- 并发上限是全局的：模型请求最多 4 个，ASR 最多 2 片同时转写，不要在各阶段各自放大。
- 阅读缓存和书架索引是可丢弃的派生文件，必须能从归档重建；改动元数据或渲染算法时提升 `reader_cache.CACHE_VERSION`，因为冻结的 sidecar 不含 Python 源码。
- 前端模块名不能只靠大小写或 `.ts` / `.tsx` 扩展名区分（macOS 文件系统大小写不敏感）。

## 详细规则的位置

| 主题 | 文档 |
| --- | --- |
| 桌面应用行为、设置、任务与阅读器 | `desktop/README.md` |
| AI 整理、校验、Provider 与恢复 | `docs/ai-enrichment.md`、`docs/generation-performance.md` |
| 链接预览、ASR 与 Apple Podcasts 边界 | `docs/preview-and-asr.md` |
| 渲染、数据格式与许可 | `docs/rendering.md`、`docs/schema.md`、`docs/licensing.md` |
| macOS 构建与发布 | `docs/macos-release.md`、`docs/migration.md` |

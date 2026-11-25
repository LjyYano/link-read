# 1. 范围

第三方代码、素材和依赖保留各自许可，不因 LinkRead Desktop 的专有许可而改变。本文件记录仓库中已识别的组件与来源，**不是完整的发行产物依赖审计或法律保证**。实际发布时还需核对锁定版本、构建环境、传递依赖及随包资源。

# 2. 随仓库分发的浏览器组件

完整许可证位于 [src/linkread_desktop/renderers/vendor/](src/linkread_desktop/renderers/vendor/)，随 Python 包分发；阅读页渲染器也把声明嵌入导出的 HTML。

| 组件 | 仓库中版本 | 许可与声明 |
| --- | --- | --- |
| [D3](https://github.com/d3/d3) | 7.9.2 | ISC；[d3.LICENSE.txt](src/linkread_desktop/renderers/vendor/d3.LICENSE.txt)，Mike Bostock |
| [markmap-lib / markmap-common](https://github.com/markmap/markmap) | 0.15.5 | MIT；[markmap.LICENSE.txt](src/linkread_desktop/renderers/vendor/markmap.LICENSE.txt)，Gerald |
| [markmap-view](https://github.com/markmap/markmap) | 0.18.13 | MIT；沿用上述 Markmap 声明 |
| [@gera2ld/jsx-dom](https://github.com/gera2ld/jsx-dom) | 2.2.2，嵌入 Markmap bundle | 保留 [jsx-dom.LICENSE.txt](src/linkread_desktop/renderers/vendor/jsx-dom.LICENSE.txt) 中的 MIT 文本及原 bundle 注释 |
| [js-yaml](https://github.com/nodeca/js-yaml) | 4.1.0，嵌入 markmap-lib | MIT；[js-yaml.LICENSE.txt](src/linkread_desktop/renderers/vendor/js-yaml.LICENSE.txt)，Vitaly Puzrin |

`@gera2ld/jsx-dom` 的 bundle / package 元数据标为 ISC，但其[上游 LICENSE](https://github.com/gera2ld/jsx-dom/blob/master/LICENSE) 为 MIT，现有许可证文件与上游一致。本项目保留两处原始声明，不自行改写为单一结论。

js-yaml 文本取自[固定上游提交](https://github.com/nodeca/js-yaml/blob/2321c0b9d1e25f9b8e6202744bc601a4998fe27f/LICENSE)，逐字节保留。以上表格不是对压缩 bundle 内全部传递依赖的穷尽识别。

桌面版用哔哩哔哩视频页的官方 favicon 标识 B 站来源。32 × 32 像素图标无损转换为本地 PNG，标识属于哔哩哔哩。[原始图标](https://i0.hdslb.com/bfs/static/jinkela/long/images/favicon.ico)

# 3. 阅读模板来源

首页、阅读页及思维导图交互改编自 [LjyYano/html-library](https://github.com/LjyYano/html-library/tree/bca81c0d07a2849c154b3f621cfc92e32df5468b)，固定提交 `bca81c0d07a2849c154b3f621cfc92e32df5468b`：

- [scripts/build.py](https://github.com/LjyYano/html-library/blob/bca81c0d07a2849c154b3f621cfc92e32df5468b/scripts/build.py)
- [pages/charlie-munger-etf-trap/index.html](https://github.com/LjyYano/html-library/blob/bca81c0d07a2849c154b3f621cfc92e32df5468b/pages/charlie-munger-etf-trap/index.html)
- [scripts/mindmap.py](https://github.com/LjyYano/html-library/blob/bca81c0d07a2849c154b3f621cfc92e32df5468b/scripts/mindmap.py)

该固定版本未包含单独 LICENSE；此处记录来源，不推定文章原文、外部素材或第三方代码属于 LinkRead。项目许可只授予权利人有权授予的权限。后续引入模板或素材时，应保留来源并确认必要授权。适配说明见 [docs/rendering.md](docs/rendering.md)。

# 4. 声明的运行依赖

以下是清单中的主要依赖，准确版本由具体安装或锁文件决定。它们的许可证应从实际安装版本保留，而不能仅用本表代替。

| 范围 | 主要组件 | 上游许可 |
| --- | --- | --- |
| Python | [Pydantic](https://github.com/pydantic/pydantic/blob/main/LICENSE) | MIT |
| Python | [Requests](https://github.com/psf/requests/blob/main/LICENSE) | Apache-2.0 |
| Python，可选 | [yt-dlp 的 Python 源码 / wheel](https://github.com/yt-dlp/yt-dlp#licensing) | Unlicense；见下文的二进制区别 |
| 桌面前端 | [React / React DOM](https://github.com/facebook/react/blob/main/LICENSE) | MIT |
| 桌面图标库 | [Lucide](https://github.com/lucide-icons/lucide/blob/main/LICENSE) | ISC |
| 桌面框架 | [Tauri](https://github.com/tauri-apps/tauri) | MIT OR Apache-2.0；各依赖另行确认 |

开发依赖、npm / Rust 传递依赖和系统库也可能有分发义务。当前 Python 依赖使用版本范围而非完整锁定文件；此表不保证将来的安装结果或所有桌面构建具有相同许可组合。

# 5. 桌面打包时的特别说明

App 构建会通过 `desktop/scripts/collect_licenses.py` 收集实际组件的版本、来源、原始许可文本与文件哈希，并将结果带入应用资源。浏览器组件以 `desktop/scripts/vendor_licenses.json` 中的 bundle 哈希核对；缺失许可的补齐方式见[许可收集说明](desktop/licenses/README.md)。该收集流程不替代对具体发行方式的审查。

- [PyInstaller](https://pyinstaller.org/en/stable/license.html) 提供允许商业应用打包的例外；例外不会免除被打入应用的其他依赖的许可义务。
- yt-dlp 的源码 / PyPI wheel 与其官方预编译可执行文件不能混为一谈：官方说明后者包含其他 GPLv3+ 代码。LinkRead 当前从 Python 环境构建 sidecar，应核对实际安装包和可选依赖，不根据上游项目名推定整个二进制的许可。
- 当前 FFmpeg 由用户安装，通过配置路径或 PATH 查找，没有随 App 分发。以后若内置，须按[具体 FFmpeg 构建配置](https://www.ffmpeg.org/legal.html)重新审查。
- 正式发布时，应生成实际依赖清单、保留其版权和完整许可文本，并履行适用的源码提供义务。不能仅因 GUI 与 sidecar 使用子进程通信就认定所有 GPL 义务被排除。

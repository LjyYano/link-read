# M2：静态 HTML 阅读书架

## 输出与组件许可

正常导出的阅读页面适用根 [LICENSE](../LICENSE) 中的生成输出授权，允许授权用户使用和分享页面所需的项目渲染材料；不授予整个应用、后端或独立渲染库的再分发权。视频、字幕、文章及图片的原有权利独立保留。

D3、Markmap、jsx-dom 和 js-yaml 的原有许可证保存在 `src/linkread_desktop/renderers/vendor/`，并嵌入导出的阅读页；第三方声明须随输出保留。修改这些声明也会使派生阅读缓存失效，确保新导出页面使用更新后的 notices。组件与来源清单见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

## 设计约束

- `TranscriptDocument v1.0` 是不可变数据来源，渲染器不修改字幕 JSON。
- 渲染只读取已有数据。全文取自字幕或校对稿，金句和关键词取自 `NoteDocument`。摘要和章节可由 `reading.json` 指定。
- 桌面导出使用后端 API 生成单文件 HTML 或 Markdown；`build_site()` 可生成包含首页和文章页的文件夹。
- 字幕和元数据在 HTML 内容区经 `html.escape(..., quote=True)` 转义；不会拼接进 JS 字符串。
- 视频链接由可信视频 ID 重建，拒绝不合法的协议和 ID。
- 模板内的 JS 处理搜索、目录、标签切换、主题、字号、复制和本地笔记。HTML 内嵌 CSS/JS，不依赖 CDN 或外部字体。
- 封面优先内嵌归档中的本地图片（不超过 1 MiB），否则引用三个白名单图片域名（`i.ytimg.com`、`*.hdslb.com`、`*.mzstatic.com`）上的在线封面；封面加载完成前不可见，加载失败、离线或 8 秒内没有响应时移除。除此之外页面不请求任何网络资源；读取和重建阅读缓存只使用已保存的封面文件和网址。
- 页面只展示阅读内容、实际来源和必要操作；正常校对不显示流程说明，失败回退时保留简短提示。数据版本、字幕片段数等信息留在 JSON 中。明确的本地导出标题后缀仅在展示时去除，显示标题不改变原始数据或笔记存储键。

## API

```python
from linkread_desktop.renderers.html import render_html
from linkread_desktop.renderers.site import build_site
from linkread_desktop.renderers.markdown import render_markdown

html_text = render_html(transcript)
markdown_text = render_markdown(transcript)
count = build_site(source_path, output_dir)
```

## 结构

- `renderers/formatting.py`：时长标签、安全跳转链接、按原文时间顺序分段。
- `renderers/html.py` + `renderers/reader.html` + `renderers/reader.js`：无需网络的单篇阅读器。
- `renderers/site.py` + `renderers/home.html`：生成索引首页及 `pages/<slug>/index.html`。
- `renderers/markdown.py`：带时间戳的 Markdown 转录。

## 静态站和旧仓库的关系

模板取自 `LjyYano/html-library` 的 `bca81c0`，保留两个页面的原版 CSS。

- `home.html` 取自 [`scripts/build.py` 的 `render_home`](https://github.com/LjyYano/html-library/blob/bca81c0d07a2849c154b3f621cfc92e32df5468b/scripts/build.py#L84)。文章地址使用本地 `pages/<slug>/index.html`，搜索元数据存入 `application/json` 数据块。
- `reader.html`、`reader.js` 和金句卡片样式取自 [《查理·芒格》文章页](https://github.com/LjyYano/html-library/blob/bca81c0d07a2849c154b3f621cfc92e32df5468b/pages/charlie-munger-etf-trap/index.html)。摘要沿用导语、主题行和可选来源说明，桌面与移动端目录和正文标题使用相同序号。
- 思维导图沿用 [`scripts/mindmap.py`](https://github.com/LjyYano/html-library/blob/bca81c0d07a2849c154b3f621cfc92e32df5468b/scripts/mindmap.py) 的画布、Markmap 组件和全屏交互，增加概览与全部展开控制。原版页面使用的 D3 7.9.2、markmap-lib 0.15.5 和 markmap-view 0.18.13 随包分发并内嵌到 HTML，组件许可证在 `renderers/vendor/` 及生成页面中。标题作为纯文本显示。

`build_site()` 生成独立站点，文章按 `pages/<slug>/index.html` 和 `article.json` 保存。将文章发布到 `html-library` 时，按该仓库的构建规则另行准备 `mindmap.md` 等文件。

## 视频目录

桌面应用默认将归档保存在 `~/Library/Application Support/com.yano.linkread/library`，也可通过 `LINKREAD_DESKTOP_LIBRARY` 指定根目录。`transcript.json` 是原始字幕，`note.json` 保存分析与校对结果，`article.html` 为最终阅读页。读取、重建阅读缓存和导出仅消费已有数据，不重新调用模型；重新生成走独立任务流程并保留失败前的完整归档。

## 思维导图和金句文件

渲染与书架 API 从输入 JSON 的同目录读取 `mindmap.md` 和 `quotes.json`。同名文件优先，例如 `note.mindmap.md`、`note.quotes.json`。生成编排读取视频目录中的已有数据，并将缺失的摘要、导图和金句卡片合并为一次模型调用。没有 `quotes.json` 的旧笔记会使用其中的原句摘录。静态书架 API 的目录扫描跳过文章附属数据、旧 Anki 文件和隐藏目录。

`mindmap.md` 使用一个一级根标题与至少一个分支，不再要求凑够两个分支或五个节点。生成采用按父节点索引表示的树，每个分支可有不同深度，最多使用 Markdown 的六级标题和 80 个分支节点；短语概括主要脉络。旧三层提案仍兼容，已有导图不被自动重写。初始概览折叠深层细节，支持逐节点展开、Expand all、Fit 与 Fullscreen。

`quotes.json` 的 `cards` 包含按字幕顺序排列的 1–8 张卡片。每张卡片包含 `title`、`start_segment_index`、`end_segment_index`、`front` 和 `back`。字幕索引从零开始，包含区间两端。`front` 是区间内的原句，`back` 附上区间前后各最多两条字幕。`bold` 和 `red` 可选，各填最多两段连续原文。渲染时核对原句和上下文，时间从字幕读取。卡片支持展开、富文本复制和跳转到文稿。

## 摘要和目录

`reading.json` 包含一段 `lead`、可选的 `source_note` 和按顺序排列的 `sections`。每一节填写从零开始的 `start_segment_index`、`title`、简短的 `summary`，以及可选的短标签 `label`。章节序号由模板添加。每篇最多显示六节。没有此文件时，HTML 会从旧笔记提取一段导语，并在全文范围内选取最多六个已有章节作为目录和摘要主题。原始逐句字幕和分块笔记可从 JSON 查看。

## 书架元数据

`catalog.json` 保存自动生成的内容分类 `category`、主题标签 `tags`、日期 `date`（YYYY-MM-DD）及 `date_source`。分类标签由已有文章数据生成，与其他缺失的文章数据合并调用；日期完全由程序决定，不让模型猜测。视频有有效发布日期时标记为 `published`，否则使用同源原始字幕的归档日期并标记为 `archived`。日期首次保存后保持不变。

读取时，手工 `video.article.json` 与目录 `article.json` 按字段覆盖自动值，显式空标签数组也有效。生成器不覆盖已有手工元数据或自动目录文件。书架页与单篇 HTML 使用一致的分类，日期来源显示在表格日期的悬浮提示中；原有 Notion 风格表格保留，窄屏可横向滚动查看完整表格。

## Shownotes 与手动阅读标记

`SourceInfo.description` 和 `description_format` 保存原始来源简介，默认 `None` 保持旧文档兼容。`html` 只输出白名单标签与 HTTP / HTTPS 链接；纯文本保留换行并自动链接网页地址。Shownotes 在 Highlights 后显示；在线来源缺少已保存简介时提供原始来源链接，App 还可显式补取。原 Notes 编辑面板移除，但不清理用户已有 localStorage。

App 的 `.source-metadata.json` 来源覆盖层仅更新简介，并核对准确节目 ID；桌面阅读、导出和静态书架共用。`.reading-state.json` 保存 `isRead` 与 `isStarred`，生成成功不等于阅读完成。导出页面与静态书架使用相同来源 key，在同一浏览器来源下共享 `linkread-reading-state-v1` 本地标记；App 持久化状态只作为导出时的初始值，浏览器交互不会回写文件。

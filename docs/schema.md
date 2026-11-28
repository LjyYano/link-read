# Transcript JSON v1.0

当前阶段只有**字幕原文**，不包含 AI 生成的内容。新功能应以新增文件或向后兼容的 Schema 版本实现，不要直接改写原始字幕。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | `"1.0"` | 数据格式版本 |
| `source.platform` | `local / bilibili / youtube` | 内容来源 |
| `source.title` | `string` | 视频标题或本地文件名 |
| `source.url` | `string?` | 原始视频链接；本地文件省略 |
| `source.video_id` | `string?` | 视频 ID |
| `source.author` | `string?` | UP 主 / 频道 |
| `source.published_at` | `string?` | ISO 日期或 UTC 时间 |
| `source.duration_ms` | `int?` | 内容时长，毫秒 |
| `transcript_source` | `local / subtitle` | 字幕获取方式 |
| `language` | `string?` | 原始字幕语言（来自平台） |
| `segments[].start_ms` | `int` | 开始时间，毫秒，非负 |
| `segments[].end_ms` | `int?` | 结束时间，毫秒，可缺失但不能早于开始 |
| `segments[].text` | `string` | 原始字幕文本（消除重复字幕快照和基础标记） |

约束：`segments` 非空并按 `start_ms` 升序排列；文本非空。JSON 使用 UTF-8。

## 后续数据层设计

- M2 只读取 `TranscriptDocument` 并渲染，不修改字幕。
- M3 创建独立的 `NoteDocument`（摘要、金句、章节、思维导图），引用原始 `start_ms`，不可编造时间戳。
- 后续 ASR 可增加 `transcript_source=asr` 作为兼容的下一版本变更。
- 生成的 HTML 与 Markdown 只是可再生的展示层，JSON 才是数据资产。桌面端归档以 `manifest.json`（`document_type: "linkread_manifest"`）标记发布完成，HTML 在阅读与导出时按需渲染，同时向后兼容旧归档中的 `article.html`。

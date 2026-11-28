use std::{
    net::{Ipv4Addr, Ipv6Addr},
    path::{Path, PathBuf},
};

use serde::{Deserialize, Serialize};
use tauri::Url;
use url::Host;

use crate::{environment, video_links};

pub type AppResult<T> = Result<T, String>;

pub fn details(message: &str, error: impl std::fmt::Display) -> String {
    format!("{message}（{error}）")
}

pub fn translated<'a>(language: Option<&str>, chinese: &'a str, english: &'a str) -> &'a str {
    if language == Some("en") {
        english
    } else {
        chinese
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Article {
    pub id: String,
    pub title: String,
    pub category: String,
    pub platform: String,
    pub source_url: Option<String>,
    pub author: Option<String>,
    pub published_at: Option<String>,
    pub duration_ms: Option<u64>,
    pub thumbnail_url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub local_cover: Option<String>,
    pub updated_at: u64,
    #[serde(default)]
    pub has_analysis: bool,
    #[serde(default)]
    pub is_read: bool,
    #[serde(default)]
    pub is_starred: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub preview_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reading_key: Option<String>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ReadingState {
    pub is_read: bool,
    pub is_starred: bool,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ArticleDetails {
    pub title: String,
    pub category: String,
    pub original_title: String,
    pub original_category: String,
    pub title_overridden: bool,
    pub category_overridden: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub linked_source_url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub linked_thumbnail_url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub subtitle_title: Option<String>,
}

#[derive(Deserialize, Serialize)]
pub struct PreparedReader {
    pub revision: String,
    #[serde(rename = "readingKey", default)]
    pub reading_key: String,
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
pub enum ShownotesRefresh {
    Empty,
    Updated(PreparedReader),
}

#[derive(Deserialize, Serialize)]
pub struct PreparedPreview {
    #[serde(flatten)]
    pub prepared: PreparedReader,
    pub article: Article,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RequestActivity {
    pub sequence: u64,
    pub id: String,
    pub stage: Option<String>,
    pub phase: String,
    pub active: u32,
    pub queued: u32,
    pub validating: u32,
    pub completed: u32,
    pub cache_hits: u32,
}

#[derive(Deserialize, Serialize)]
pub struct Progress {
    pub stage: String,
    pub message: String,
    pub completed: Option<u32>,
    pub total: Option<u32>,
    pub metadata: Option<SourceMetadata>,
    #[serde(rename = "previewId", default)]
    pub preview_id: Option<String>,
    #[serde(default)]
    pub request: Option<RequestActivity>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct GenerationFailure {
    pub message: String,
    pub code: String,
    pub stage: Option<String>,
    pub retryable: bool,
    pub action: String,
}

impl GenerationFailure {
    pub fn core(message: String) -> Self {
        Self {
            message,
            code: "core".into(),
            stage: None,
            retryable: true,
            action: "retry".into(),
        }
    }
    pub fn context(message: String, code: &str, action: &str) -> Self {
        Self {
            message,
            code: code.into(),
            stage: None,
            retryable: action == "retry",
            action: action.into(),
        }
    }
    pub fn cancelled() -> Self {
        Self::context("已取消生成".into(), "cancelled", "retry")
    }
}

pub type GenerationResult<T> = Result<T, GenerationFailure>;

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceMetadata {
    pub title: String,
    pub platform: String,
    pub source_url: Option<String>,
    pub author: Option<String>,
    pub published_at: Option<String>,
    pub duration_ms: Option<u64>,
    pub thumbnail_url: Option<String>,
}

#[derive(Deserialize, Serialize)]
pub struct Library {
    pub articles: Vec<Article>,
    pub unavailable: usize,
}

#[derive(Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SidecarEvent {
    Progress {
        #[serde(flatten)]
        progress: Progress,
    },
    Completed {
        article: Article,
    },
    Error {
        message: String,
        #[serde(default)]
        code: Option<String>,
        #[serde(default)]
        stage: Option<String>,
        #[serde(default)]
        retryable: Option<bool>,
        #[serde(default)]
        action: Option<String>,
    },
}

#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Provider {
    Codex,
    Claude,
    Api,
}

impl Provider {
    pub fn command(self) -> &'static str {
        match self {
            Self::Codex => "codex",
            Self::Claude => "claude",
            Self::Api => "api",
        }
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ToolStatus {
    pub executable: Option<PathBuf>,
    pub key_available: bool,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ApiConnectionCheck {
    pub ok: bool,
    pub message: String,
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AsrOptions {
    pub enabled: bool,
    pub reuse_token: bool,
    pub base_url: String,
    pub model: String,
    pub max_minutes: u32,
    #[serde(default = "default_chunk_minutes")]
    pub chunk_minutes: u32,
    #[serde(default)]
    pub ffmpeg_path: String,
}

fn default_chunk_minutes() -> u32 {
    5
}

impl AsrOptions {
    pub fn validate(&self) -> AppResult<Url> {
        if !environment::valid_custom_path(&self.ffmpeg_path) {
            return Err("FFmpeg 路径无效，请在设置中重新选择程序".into());
        }
        let url = Url::parse(self.base_url.trim()).map_err(|_| "ASR 接口地址无效".to_owned())?;
        let host = url.host_str().unwrap_or("");
        if url.scheme() != "https"
            || !url.username().is_empty()
            || url.password().is_some()
            || url.port().is_some()
            || url.query().is_some()
            || url.fragment().is_some()
            || url.path().trim_end_matches('/') != "/api/v1"
            || !(host == "dashscope.aliyuncs.com"
                || host == "dashscope-intl.aliyuncs.com"
                || host.ends_with(".cn-beijing.maas.aliyuncs.com"))
            || ![0, 5, 10, 15].contains(&self.chunk_minutes)
            || self.model != "paraformer-v2"
            || !(1..=720).contains(&self.max_minutes)
        {
            return Err("请配置百炼原生 /api/v1 接口及 paraformer-v2 模型".into());
        }
        Ok(url)
    }

    pub fn token(&self, locations: &crate::Locations) -> AppResult<Option<String>> {
        if self.reuse_token {
            crate::credentials::load(&locations.data)
        } else {
            crate::credentials::load_asr(&locations.data)
        }
    }

    pub fn test_token(
        &self,
        locations: &crate::Locations,
        draft: Option<String>,
    ) -> AppResult<String> {
        // The settings form explicitly resolves its chosen credential source.
        // A supplied draft, including an empty one, must never fall back to a
        // different saved key. Other callers may omit it to use saved settings.
        let token = match draft {
            Some(value) => value,
            None => self.token(locations)?.ok_or("请填写语音 API 密钥")?,
        };
        if token.trim().is_empty() || token.len() > 8192 || token.chars().any(char::is_control) {
            return Err("请填写有效的语音 API 密钥".into());
        }
        Ok(token)
    }
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct YoutubeCookieOptions {
    pub mode: String,
    #[serde(default)]
    pub browser: String,
    #[serde(default)]
    pub file_path: String,
}

pub const YOUTUBE_COOKIE_BROWSERS: &[&str] = &[
    "brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale",
];

pub fn is_youtube_source(source: &str) -> bool {
    video_url(source)
        .ok()
        .and_then(|url| url.host_str().map(str::to_owned))
        .is_some_and(|host| {
            let host = host.to_ascii_lowercase();
            host == "youtu.be"
                || host == "www.youtu.be"
                || host == "youtube.com"
                || host.ends_with(".youtube.com")
        })
}

pub fn youtube_cookie_args(
    source: &str,
    options: Option<&YoutubeCookieOptions>,
) -> AppResult<Vec<String>> {
    let Some(options) = options else {
        return Ok(Vec::new());
    };
    if options.mode == "none" || !is_youtube_source(source) {
        return Ok(Vec::new());
    }
    match options.mode.as_str() {
        "browser" => {
            let browser = options.browser.trim().to_ascii_lowercase();
            if !YOUTUBE_COOKIE_BROWSERS.contains(&browser.as_str()) {
                return Err("不支持所选浏览器，请选择 Chrome、Safari、Edge、Firefox 或 Brave 等".into());
            }
            Ok(vec!["--cookies-from-browser".into(), browser])
        }
        "file" => {
            let path = options.file_path.trim();
            let target = Path::new(path);
            if path.is_empty()
                || path.len() > 4096
                || path.chars().any(char::is_control)
                || !target.is_absolute()
                || target.is_symlink()
                || !target.is_file()
            {
                return Err("YouTube Cookie 文件不存在或不可读取，请在设置中重新选择".into());
            }
            Ok(vec!["--cookies".into(), path.to_owned()])
        }
        _ => Err("YouTube Cookie 认证模式无效".into()),
    }
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceCheck {
    pub source: String,
    pub status: String,
    pub code: String,
    pub message: String,
    pub checked_at: u64,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DownloadedSubtitles {
    pub preview_id: String,
    pub source: String,
    pub source_key: String,
    pub reading_key: String,
    pub language: Option<String>,
    pub segment_count: u32,
    pub metadata: SourceMetadata,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DuplicateInfo {
    pub key: Option<String>,
    pub article: Option<Article>,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ExportFormat {
    Html,
    Markdown,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MarkdownContent {
    Readable,
    Full,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MarkdownExportOptions {
    pub content: MarkdownContent,
    pub include_timestamped: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SubtitleExportFormat {
    Srt,
    Vtt,
}

pub fn validated_api_base_url(base: &str) -> AppResult<(Url, bool)> {
    let value = base.trim();
    if value.chars().any(char::is_control) {
        return Err("API Base URL 需要 HTTPS（本机 localhost 可用 HTTP），不要包含账号或查询参数".into());
    }
    let url = Url::parse(value).map_err(|_| "请输入有效的 API Base URL".to_owned())?;
    let host = url.host();
    let loopback = matches!(
        host,
        Some(Host::Domain("localhost") | Host::Ipv4(Ipv4Addr::LOCALHOST) | Host::Ipv6(Ipv6Addr::LOCALHOST))
    );
    if (url.scheme() != "https" && !(url.scheme() == "http" && loopback))
        || host.is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.path().trim_end_matches('/').ends_with("/chat/completions")
    {
        return Err("API Base URL 需要 HTTPS（本机 localhost 可用 HTTP），不要包含账号或查询参数".into());
    }
    Ok((url, loopback))
}

pub fn video_url(source: &str) -> AppResult<Url> {
    video_links::canonical(source)
}

pub fn subtitle_file(source: &Path) -> AppResult<()> {
    let supported = source
        .extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| {
            matches!(
                extension.to_ascii_lowercase().as_str(),
                "srt" | "vtt" | "json"
            )
        });
    if !source.is_absolute() || !source.is_file() || !supported {
        return Err("请选择可读取的 SRT、VTT 或 LinkRead JSON 字幕文件".into());
    }
    Ok(())
}

pub fn preview_id(id: &str) -> AppResult<()> {
    if id.len() != 64
        || !id
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    {
        return Err("原文预览编号无效".into());
    }
    Ok(())
}

pub fn external_url(value: &str) -> AppResult<Url> {
    let url = Url::parse(value).map_err(|_| "链接格式无效".to_owned())?;
    if value.len() > 8192
        || value.chars().any(char::is_control)
        || !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return Err("仅支持打开 HTTP 或 HTTPS 网页链接".into());
    }
    Ok(url)
}

pub fn export_file_stem(title: &str) -> String {
    let mut result = String::new();
    for ch in title.trim().chars() {
        let ch = if ch.is_control() || matches!(ch, '/' | '\\' | ':') {
            '_'
        } else {
            ch
        };
        if result.len() + ch.len_utf8() > 180 {
            break;
        }
        result.push(ch);
    }
    let result = result.trim().trim_matches('.').trim();
    if result.is_empty() {
        "Article".into()
    } else {
        result.into()
    }
}

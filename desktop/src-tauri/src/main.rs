#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod reader;
mod video_links;
mod keychain;
mod credentials;
mod durable_state;
mod environment;
mod subtitle_clipboard;
mod app_updates;
#[cfg(target_os = "macos")]
mod desktop_menu;

use std::{
    collections::VecDeque,
    env,
    ffi::OsString,
    fs,
    future::{poll_fn, Future},
    io::{self, Read, Write},
    net::{Ipv4Addr, Ipv6Addr},
    os::{fd::AsRawFd, unix::{fs::{OpenOptionsExt, PermissionsExt}, process::CommandExt}},
    path::{Component, Path, PathBuf},
    pin::pin,
    process::{ExitStatus, Stdio},
    sync::{Mutex, atomic::{AtomicBool, Ordering}},
    task::Poll,
    time::{Duration, Instant},
};

use percent_encoding::percent_decode_str;
use serde::{Deserialize, Serialize};
use tauri::{http, ipc::Channel, AppHandle, Manager, State, Url};
use tauri_plugin_dialog::DialogExt;
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    process::{Child, Command},
    sync::oneshot,
};
use url::Host;

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Article {
    id: String,
    title: String,
    category: String,
    platform: String,
    source_url: Option<String>,
    author: Option<String>,
    published_at: Option<String>,
    duration_ms: Option<u64>,
    thumbnail_url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    local_cover: Option<String>,
    updated_at: u64,
    #[serde(default)]
    has_analysis: bool,
    #[serde(default)]
    is_read: bool,
    #[serde(default)]
    is_starred: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    preview_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    reading_key: Option<String>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ReadingState {
    is_read: bool,
    is_starred: bool,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ArticleDetails {
    title: String,
    category: String,
    original_title: String,
    original_category: String,
    title_overridden: bool,
    category_overridden: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    linked_source_url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    linked_thumbnail_url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    subtitle_title: Option<String>,
}

#[derive(Deserialize, Serialize)]
struct PreparedReader {
    revision: String,
    #[serde(rename = "readingKey", default)]
    reading_key: String,
}

#[derive(Deserialize, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
enum ShownotesRefresh {
    Empty,
    Updated(PreparedReader),
}

#[derive(Deserialize, Serialize)]
struct PreparedPreview {
    #[serde(flatten)]
    prepared: PreparedReader,
    article: Article,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct RequestActivity {
    sequence: u64,
    id: String,
    stage: Option<String>,
    phase: String,
    active: u32,
    queued: u32,
    validating: u32,
    completed: u32,
    cache_hits: u32,
}

#[derive(Deserialize, Serialize)]
struct Progress {
    stage: String,
    message: String,
    completed: Option<u32>,
    total: Option<u32>,
    metadata: Option<SourceMetadata>,
    #[serde(rename = "previewId", default)]
    preview_id: Option<String>,
    #[serde(default)]
    request: Option<RequestActivity>,
}

#[derive(Debug, Deserialize, Serialize)]
struct GenerationFailure {
    message: String,
    code: String,
    stage: Option<String>,
    retryable: bool,
    action: String,
}

impl GenerationFailure {
    fn core(message: String) -> Self { Self { message, code: "core".into(), stage: None, retryable: true, action: "retry".into() } }
    fn context(message: String, code: &str, action: &str) -> Self {
        Self { message, code: code.into(), stage: None, retryable: action == "retry", action: action.into() }
    }
    fn cancelled() -> Self { Self::context("已取消生成".into(), "cancelled", "retry") }
}
type GenerationResult<T> = Result<T, GenerationFailure>;

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct SourceMetadata {
    title: String,
    platform: String,
    source_url: Option<String>,
    author: Option<String>,
    published_at: Option<String>,
    duration_ms: Option<u64>,
    thumbnail_url: Option<String>,
}

#[derive(Deserialize, Serialize)]
struct Library {
    articles: Vec<Article>,
    unavailable: usize,
}

#[derive(Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum SidecarEvent {
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
enum Provider {
    Codex,
    Claude,
    Api,
}

impl Provider {
    fn command(self) -> &'static str {
        match self {
            Self::Codex => "codex",
            Self::Claude => "claude",
            Self::Api => "api",
        }
    }
}

#[derive(Default)]
struct Generation(Mutex<GenerationState>);

#[derive(Default)]
struct GenerationState {
    active: Option<ActiveProcess>,
    updating: bool,
    exiting: bool,
    cancelled_requests: VecDeque<String>,
    finished_requests: VecDeque<String>,
}

struct ActiveProcess {
    request_id: String,
    source_key: Option<String>,
    pid: Option<i32>,
    cancel_deadline: Option<Instant>,
    cancel_notice: Option<oneshot::Sender<Instant>>,
}

const GENERATION_CANCEL_GRACE: Duration = Duration::from_secs(5);

struct GenerationChild<'a> {
    generation: &'a Generation,
    request_id: &'a str,
    child: Child,
}

impl Drop for GenerationChild<'_> {
    fn drop(&mut self) {
        // An abandoned generation future may drop its child before the app's
        // exit handler. Clear ownership while the child still pins its PID.
        self.generation.kill_owned_group(self.request_id, false);
    }
}

struct UpdateInstall<'a> {
    generation: &'a Generation,
}

impl Drop for UpdateInstall<'_> {
    fn drop(&mut self) {
        self.generation.0.lock().unwrap().updating = false;
    }
}

impl Generation {
    fn begin_update(&self) -> AppResult<UpdateInstall<'_>> {
        let mut state = self.0.lock().unwrap();
        if state.active.is_some() || state.updating || state.exiting {
            return Err("update_busy".into());
        }
        state.updating = true;
        Ok(UpdateInstall { generation: self })
    }

    fn updating(&self) -> bool {
        self.0.lock().unwrap().updating
    }

    #[cfg(test)]
    fn begin(&self, request_id: &str) -> AppResult<()> {
        self.begin_with_source(request_id, None)
    }

    fn begin_with_source(&self, request_id: &str, source_key: Option<String>) -> AppResult<()> {
        validate_request_id(request_id)?;
        let mut state = self.0.lock().unwrap();
        if let Some(index) = state
            .cancelled_requests
            .iter()
            .position(|id| id == request_id)
        {
            state.cancelled_requests.remove(index);
            remember_request(&mut state.finished_requests, request_id);
            return Err("已取消生成".into());
        }
        if state.finished_requests.iter().any(|id| id == request_id) {
            return Err("此生成任务已结束，请重新生成".into());
        }
        if state.active.is_some() {
            return Err("已有文章正在生成".into());
        }
        if state.exiting {
            return Err("已取消生成".into());
        }
        if state.updating {
            return Err("LinkRead 正在安装更新，请重启后再试".into());
        }
        state.active = Some(ActiveProcess {
            request_id: request_id.to_owned(),
            source_key,
            pid: None,
            cancel_deadline: None,
            cancel_notice: None,
        });
        Ok(())
    }

    fn spawn<'a>(&'a self, request_id: &'a str, command: &mut Command) -> AppResult<(GenerationChild<'a>, oneshot::Receiver<Instant>)> {
        let mut state = self.0.lock().unwrap();
        let active = state
            .active
            .as_mut()
            .filter(|active| active.request_id == request_id)
            .ok_or_else(|| "找不到正在启动的生成任务".to_owned())?;
        if active.cancel_deadline.is_some() {
            return Err("已取消生成".into());
        }
        command.as_std_mut().process_group(0);
        let child = command
            .kill_on_drop(true)
            .spawn()
            .map_err(|error| details("无法启动 LinkRead 核心", error))?;
        let (notice, cancelled) = oneshot::channel();
        active.pid = child.id().map(|pid| pid as i32);
        active.cancel_notice = Some(notice);
        Ok((GenerationChild { generation: self, request_id, child }, cancelled))
    }

    fn try_reap(&self, request_id: &str, child: &mut Child) -> io::Result<Option<ExitStatus>> {
        let mut state = self.0.lock().unwrap();
        let active = state.active.as_mut().filter(|active| active.request_id == request_id)
            .ok_or_else(|| io::Error::other("generation is no longer active"))?;
        // Keep the group leader unreaped through escalation. Its PID pins the
        // group ID even if a descendant closes stdout or the leader exits first.
        if active.cancel_deadline.is_some() {
            return Ok(None);
        }
        let status = child.try_wait();
        if !matches!(status, Ok(None)) {
            active.pid = None;
        }
        status
    }

    fn finish(&self, request_id: &str) -> bool {
        let mut state = self.0.lock().unwrap();
        let cancelled = if state
            .active
            .as_ref()
            .is_some_and(|active| active.request_id == request_id)
        {
            state.active.take().is_some_and(|active| active.cancel_deadline.is_some())
        } else {
            false
        };
        remember_request(&mut state.finished_requests, request_id);
        cancelled
    }

    fn cancel(&self, request_id: &str) -> AppResult<()> {
        self.cancel_with_grace(request_id, GENERATION_CANCEL_GRACE)
    }

    fn cancel_with_grace(&self, request_id: &str, grace: Duration) -> AppResult<()> {
        validate_request_id(request_id)?;
        let mut state = self.0.lock().unwrap();
        if let Some(active) = state
            .active
            .as_mut()
            .filter(|active| active.request_id == request_id)
        {
            return stop_process(active, grace);
        }
        if !state.finished_requests.iter().any(|id| id == request_id) {
            // IPC scheduling may deliver cancellation before generate starts.
            remember_request(&mut state.cancelled_requests, request_id);
        }
        Ok(())
    }

    fn kill_owned_group(&self, request_id: &str, cancelled_only: bool) {
        let mut state = self.0.lock().unwrap();
        if let Some(active) = state.active.as_mut().filter(|active| active.request_id == request_id
            && (!cancelled_only || active.cancel_deadline.is_some()))
        {
            if let Some(pid) = active.pid.take() {
                let _ = signal_process_group(pid, libc::SIGKILL);
            }
        }
    }

    fn cancel_active_on_exit(&self, grace: Duration) {
        let pending = {
            let mut state = self.0.lock().unwrap();
            state.exiting = true;
            state.active.as_mut().and_then(|active| {
                if stop_process(active, grace).is_err() {
                    mark_cancelled(active, grace);
                }
                active.pid.zip(active.cancel_deadline)
                    .map(|(_, deadline)| (active.request_id.clone(), deadline))
            })
        };
        if let Some((request_id, deadline)) = pending {
            std::thread::sleep(deadline.saturating_duration_since(Instant::now()));
            self.kill_owned_group(&request_id, true);
        }
    }
}

fn validate_request_id(request_id: &str) -> AppResult<()> {
    if request_id.is_empty() || request_id.len() > 128 {
        return Err("无效的生成任务标识".into());
    }
    Ok(())
}

fn remember_request(requests: &mut VecDeque<String>, request_id: &str) {
    if !requests.iter().any(|id| id == request_id) {
        requests.push_back(request_id.to_owned());
        if requests.len() > 64 {
            requests.pop_front();
        }
    }
}

fn signal_process_group(pid: i32, signal: i32) -> io::Result<()> {
    // The sidecar and the CLI processes it starts share this process group.
    if unsafe { libc::kill(-pid, signal) } != 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() != Some(libc::ESRCH) { return Err(error); }
    }
    Ok(())
}

fn mark_cancelled(active: &mut ActiveProcess, grace: Duration) {
    let deadline = Instant::now() + grace;
    active.cancel_deadline = Some(deadline);
    if let Some(notice) = active.cancel_notice.take() { let _ = notice.send(deadline); }
}

fn stop_process(active: &mut ActiveProcess, grace: Duration) -> AppResult<()> {
    if active.cancel_deadline.is_none() {
        if let Some(pid) = active.pid {
            signal_process_group(pid, libc::SIGTERM).map_err(|error| details("无法取消生成，请重试", error))?;
        }
        mark_cancelled(active, grace);
    }
    Ok(())
}

struct Locations {
    data: PathBuf,
    library: PathBuf,
    english: AtomicBool,
}

impl Locations {
    fn language(&self) -> &'static str {
        if self.english.load(Ordering::Relaxed) { "en" } else { "zh-CN" }
    }
}

#[tauri::command]
fn set_ui_language(app: AppHandle, locations: State<'_, Locations>, language: String) -> AppResult<()> {
    if !matches!(language.as_str(), "en" | "zh-CN") { return Err("Unsupported interface language".into()); }
    locations.english.store(language == "en", Ordering::Relaxed);
    #[cfg(target_os = "macos")]
    desktop_menu::localize(&app, language == "en").map_err(|error| details("无法更新菜单语言", error))?;
    Ok(())
}

fn translated<'a>(language: Option<&str>, chinese: &'a str, english: &'a str) -> &'a str {
    if language == Some("en") { english } else { chinese }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ToolStatus {
    executable: Option<PathBuf>,
    key_available: bool,
}

type AppResult<T> = Result<T, String>;

fn details(message: &str, error: impl std::fmt::Display) -> String {
    format!("{message}（{error}）")
}

fn command_paths() -> Vec<PathBuf> {
    let mut paths = env::var_os("PATH").map_or_else(Vec::new, |path| {
        env::split_paths(&path)
            .filter(|path| path.is_absolute())
            .collect()
    });
    paths.extend(
        [
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
        .map(PathBuf::from),
    );
    if let Some(home) = env::var_os("HOME").map(PathBuf::from) {
        paths.extend(
            [
                ".local/bin",
                ".npm-global/bin",
                ".volta/bin",
                ".bun/bin",
                ".deno/bin",
            ]
            .map(|directory| home.join(directory)),
        );
    }
    paths
}

fn executable_file(path: &Path) -> bool {
    path.is_absolute()
        && fs::metadata(path)
            .is_ok_and(|metadata| metadata.is_file() && metadata.permissions().mode() & 0o111 != 0)
}

fn tool_path(provider: Provider, custom: &str) -> Option<PathBuf> {
    if matches!(provider, Provider::Api) { return None; }
    if !custom.trim().is_empty() {
        let path = PathBuf::from(custom.trim());
        return executable_file(&path).then_some(path);
    }
    command_paths()
        .into_iter()
        .map(|directory| directory.join(provider.command()))
        .find(|path| executable_file(path))
}

#[tauri::command]
fn check_tool(provider: Provider, executable: String, locations: State<'_, Locations>) -> ToolStatus {
    ToolStatus {
        executable: tool_path(provider, &executable),
        key_available: match provider {
            Provider::Codex => true,
            Provider::Claude => env::var_os("ANTHROPIC_API_KEY").is_some_and(|key| !key.is_empty()),
            Provider::Api => credentials::load(&locations.data).ok().flatten().is_some(),
        },
    }
}

#[tauri::command]
fn load_api_token(locations: State<'_, Locations>) -> AppResult<Option<String>> {
    credentials::load(&locations.data)
}

#[tauri::command]
fn save_api_token(locations: State<'_, Locations>, token: String) -> AppResult<()> {
    credentials::store(&locations.data, &token)
}

#[tauri::command]
fn delete_api_token(locations: State<'_, Locations>) -> AppResult<()> {
    credentials::delete(&locations.data)
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct ApiConnectionCheck {
    ok: bool,
    message: String,
}

#[tauri::command]
async fn test_api_connection(
    locations: State<'_, Locations>,
    base_url: String,
    model: String,
    token: Option<String>,
) -> AppResult<ApiConnectionCheck> {
    validated_api_base_url(&base_url)?;
    let model = model.trim();
    if model.is_empty() || model.len() > 256 || model.chars().any(char::is_control) {
        return Err("请填写有效的模型名称".into());
    }
    let saved = credentials::load(&locations.data)?;
    let token = token.filter(|v| !v.is_empty()).or(saved)
        .ok_or("请先填写 API Token")?;
    if token.len() > 8192 || token.chars().any(char::is_control) {
        return Err("API Token 包含无效字符或过长".into());
    }
    let mut cmd = sidecar(&locations)?;
    cmd.arg("test-api")
        .args(["--base-url", base_url.trim(), "--model", model])
        .env("LINKREAD_API_TOKEN", token)
        .kill_on_drop(true);
    let output = tokio::time::timeout(Duration::from_secs(45), cmd.output())
        .await
        .map_err(|_| "连接测试超时，请检查网络或模型响应时间".to_owned())?
        .map_err(|_| "无法启动模型连通性测试".to_owned())?;
    let event: serde_json::Value = serde_json::from_slice(&output.stdout)
        .map_err(|_| "测试进程未返回有效状态".to_owned())?;
    if !output.status.success() || event["type"] != "connection" || event["ok"] != true {
        return Err(event["message"].as_str().unwrap_or("模型连通性测试失败").to_owned());
    }
    Ok(ApiConnectionCheck { ok: true, message: "连接成功，模型和 Token 可用".into() })
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
struct AsrOptions {
    enabled: bool,
    reuse_token: bool,
    base_url: String,
    model: String,
    max_minutes: u32,
    #[serde(default = "default_chunk_minutes")]
    chunk_minutes: u32,
    #[serde(default)]
    ffmpeg_path: String,
}

fn default_chunk_minutes() -> u32 { 5 }

impl AsrOptions {
    fn validate(&self) -> AppResult<()> {
        if !environment::valid_custom_path(&self.ffmpeg_path) {
            return Err("FFmpeg 路径无效，请在设置中重新选择程序".into());
        }
        let url = Url::parse(self.base_url.trim()).map_err(|_| "ASR 接口地址无效".to_owned())?;
        let host = url.host_str().unwrap_or("");
        if url.scheme() != "https" || !url.username().is_empty() || url.password().is_some()
            || url.port().is_some() || url.query().is_some() || url.fragment().is_some()
            || url.path().trim_end_matches('/') != "/api/v1"
            || !(host == "dashscope.aliyuncs.com" || host == "dashscope-intl.aliyuncs.com"
                 || host.ends_with(".cn-beijing.maas.aliyuncs.com"))
            || ![0, 5, 10, 15].contains(&self.chunk_minutes)
            || self.model != "paraformer-v2" || !(1..=720).contains(&self.max_minutes)
        { return Err("请配置百炼原生 /api/v1 接口及 paraformer-v2 模型".into()); }
        Ok(())
    }
    fn token(&self, locations: &Locations) -> AppResult<Option<String>> {
        if self.reuse_token { credentials::load(&locations.data) }
        else { credentials::load_asr(&locations.data) }
    }
    fn test_token(&self, locations: &Locations, draft: Option<String>) -> AppResult<String> {
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

#[tauri::command]
async fn preview_video(locations: State<'_, Locations>, source: String) -> AppResult<SourceMetadata> {
    video_url(&source)?;
    query_with_timeout(&locations, &["preview", "--", &source], Duration::from_secs(15),
        "来源预览暂时超时，可以重试或直接开始生成").await
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct YoutubeCookieOptions {
    mode: String,
    #[serde(default)]
    browser: String,
    #[serde(default)]
    file_path: String,
}

const YOUTUBE_COOKIE_BROWSERS: &[&str] = &[
    "brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale",
];

fn is_youtube_source(source: &str) -> bool {
    video_url(source).ok().and_then(|url| url.host_str().map(str::to_owned)).is_some_and(|host| {
        let host = host.to_ascii_lowercase();
        host == "youtu.be" || host == "www.youtu.be" || host == "youtube.com" || host.ends_with(".youtube.com")
    })
}

fn youtube_cookie_args(source: &str, options: Option<&YoutubeCookieOptions>) -> AppResult<Vec<String>> {
    let Some(options) = options else { return Ok(Vec::new()) };
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
            if path.is_empty() || path.len() > 4096 || path.chars().any(char::is_control)
                || !target.is_absolute() || target.is_symlink() || !target.is_file()
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
struct SourceCheck {
    source: String,
    status: String,
    code: String,
    message: String,
    checked_at: u64,
}

#[tauri::command]
async fn check_source(
    locations: State<'_, Locations>,
    source: String,
    youtube_cookies: Option<YoutubeCookieOptions>,
) -> AppResult<SourceCheck> {
    video_url(&source)?;
    let cookies = youtube_cookie_args(&source, youtube_cookies.as_ref())?;
    let mut owned: Vec<String> = vec!["check-source".into()];
    owned.extend(cookies);
    owned.push("--".into());
    owned.push(source);
    let args: Vec<&str> = owned.iter().map(String::as_str).collect();
    query_with_timeout(&locations, &args, Duration::from_secs(35),
        "来源检查暂时超时，尚未确认内容是否可用；请重试检查或查看来源").await
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct DownloadedSubtitles {
    preview_id: String,
    source: String,
    source_key: String,
    reading_key: String,
    language: Option<String>,
    segment_count: u32,
    metadata: SourceMetadata,
}

#[tauri::command]
async fn download_subtitles(
    locations: State<'_, Locations>,
    source: String,
    youtube_cookies: Option<YoutubeCookieOptions>,
) -> GenerationResult<DownloadedSubtitles> {
    video_url(&source).map_err(|e| GenerationFailure::context(e, "unsupported_source", "source"))?;
    let cookies = youtube_cookie_args(&source, youtube_cookies.as_ref())
        .map_err(|e| GenerationFailure::context(e, "youtube_cookies_unavailable", "settings"))?;
    let mut owned: Vec<String> = vec!["download-subtitles".into()];
    owned.extend(cookies);
    owned.push("--".into());
    owned.push(source);
    let args: Vec<&str> = owned.iter().map(String::as_str).collect();
    let mut command = sidecar(&locations).map_err(GenerationFailure::core)?;
    command.args(&args);
    query_command_detailed(command, Duration::from_secs(60), "下载字幕超时，请检查网络或稍后重试").await
}

#[tauri::command]
async fn pick_cookies_file(
    app: AppHandle,
    window: tauri::WebviewWindow,
    ui_language: Option<String>,
) -> AppResult<Option<PathBuf>> {
    let (send, receive) = oneshot::channel();
    app.dialog()
        .file()
        .set_parent(&window)
        .set_title(translated(ui_language.as_deref(), "选择 YouTube Cookie 文件", "Choose YouTube cookies file"))
        .add_filter(translated(ui_language.as_deref(), "Cookie 文本文件", "Cookies text file"), &["txt"])
        .pick_file(move |file| {
            let _ = send.send(file);
        });
    let Some(file) = receive
        .await
        .map_err(|error| details("无法打开文件选择窗口", error))?
    else {
        return Ok(None);
    };
    let path = file
        .into_path()
        .map_err(|error| details("无法访问所选 Cookie 文件", error))?;
    if !path.is_absolute() || path.is_symlink() || !path.is_file() {
        return Err("请选择有效的本地 cookies.txt 文件".into());
    }
    Ok(Some(path))
}

#[tauri::command]
async fn test_youtube_cookies(
    locations: State<'_, Locations>,
    options: YoutubeCookieOptions,
) -> AppResult<ApiConnectionCheck> {
    if options.mode == "none" {
        return Err("请先选择浏览器或 cookies.txt 文件".into());
    }
    let cookies = youtube_cookie_args("https://www.youtube.com/watch?v=1-Fo8McCD9w", Some(&options))?;
    let mut owned: Vec<String> = vec!["test-youtube-cookies".into()];
    owned.extend(cookies);
    let args: Vec<&str> = owned.iter().map(String::as_str).collect();
    query_with_timeout(&locations, &args, Duration::from_secs(45),
        "读取 YouTube Cookie 超时；如系统弹出钥匙串授权窗口，请确认授权后重试").await
}

#[tauri::command]
fn load_asr_token(locations: State<'_, Locations>) -> AppResult<Option<String>> {
    credentials::load_asr(&locations.data)
}

#[tauri::command]
fn save_asr_token(locations: State<'_, Locations>, token: String) -> AppResult<()> {
    credentials::store_asr(&locations.data, &token)
}

#[tauri::command]
fn delete_asr_token(locations: State<'_, Locations>) -> AppResult<()> {
    credentials::delete_asr(&locations.data)
}

#[tauri::command]
async fn test_asr_connection(
    locations: State<'_, Locations>, options: AsrOptions, token: Option<String>,
) -> AppResult<ApiConnectionCheck> {
    options.validate()?;
    let token = options.test_token(&locations, token)?;
    let mut cmd = sidecar(&locations)?;
    cmd.args(["test-asr", "--base-url", options.base_url.trim(), "--model", &options.model])
        .env("LINKREAD_ASR_TOKEN", token).kill_on_drop(true);
    let output = tokio::time::timeout(Duration::from_secs(120), cmd.output()).await
        .map_err(|_| "语音测试等待超时；云端任务可能仍在处理，请勿连续重复测试".to_owned())?
        .map_err(|_| "无法启动语音测试".to_owned())?;
    let value: serde_json::Value = serde_json::from_slice(&output.stdout)
        .map_err(|_| "语音测试未返回有效结果".to_owned())?;
    if !output.status.success() || value["type"] != "connection" || value["ok"] != true {
        return Err(value["message"].as_str().unwrap_or("语音测试失败").to_owned());
    }
    Ok(ApiConnectionCheck { ok: true, message: "已连接".into() })
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct DuplicateInfo {
    key: Option<String>,
    article: Option<Article>,
}

#[tauri::command]
async fn check_duplicate(
    locations: State<'_, Locations>,
    source: String,
    local_file: bool,
    resume_preview: Option<String>,
) -> AppResult<DuplicateInfo> {
    if let Some(id) = resume_preview.as_deref() {
        preview_id(id)?;
        let mut args = vec!["duplicate", "--resume-preview", id];
        if local_file { args.push("--local-file"); } else { video_url(&source)?; }
        args.extend(["--", &source]);
        return query(&locations, &args).await;
    }
    if local_file {
        subtitle_file(Path::new(&source))?;
        query(&locations, &["duplicate", "--local-file", "--", &source]).await
    } else {
        video_url(&source)?;
        query(&locations, &["duplicate", "--", &source]).await
    }
}

fn validated_api_base_url(base: &str) -> AppResult<()> {
    let value = base.trim();
    let url = Url::parse(value).map_err(|_| "请输入有效的 API Base URL".to_owned())?;
    let host = url.host();
    let loopback = matches!(host, Some(Host::Domain("localhost") | Host::Ipv4(Ipv4Addr::LOCALHOST)
        | Host::Ipv6(Ipv6Addr::LOCALHOST)));
    if (url.scheme() != "https" && !(url.scheme() == "http" && loopback))
        || host.is_none() || !url.username().is_empty()
        || url.password().is_some() || url.query().is_some() || url.fragment().is_some()
        || url.path().trim_end_matches('/').ends_with("/chat/completions")
    {
        return Err("API Base URL 需要 HTTPS（本机 localhost 可用 HTTP），不要包含账号或查询参数".into());
    }
    Ok(())
}

fn sidecar(locations: &Locations) -> AppResult<Command> {
    let mut command = if cfg!(debug_assertions) {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let python = env::var_os("LINKREAD_PYTHON")
            .map_or_else(|| root.join(".venv/bin/python"), PathBuf::from);
        if !executable_file(&python) {
            return Err(
                "找不到 LinkRead 的 Python 环境，请创建仓库的 .venv 或配置 LINKREAD_PYTHON".into(),
            );
        }
        let mut command = std::process::Command::new(python);
        command
            .args(["-m", "linkread_desktop.desktop"])
            .env("PYTHONPATH", root.join("src"));
        command
    } else {
        let path = env::current_exe()
            .map_err(|error| details("找不到 LinkRead 应用路径", error))?
            .with_file_name("linkread-sidecar");
        if !executable_file(&path) {
            return Err("应用缺少内置的 LinkRead 核心，请使用完整的 LinkRead.app".into());
        }
        let mut command = std::process::Command::new(path);
        command.env_remove("PYTHONPATH").env_remove("PYTHONHOME");
        command
    };
    if let Ok(path) = env::join_paths(command_paths()) {
        command.env("PATH", path);
    }
    command
        .args([
            OsString::from("--library"),
            locations.library.as_os_str().to_owned(),
        ])
        .current_dir(&locations.data)
        .env("PYTHONUTF8", "1")
        .env("LINKREAD_UI_LANGUAGE", locations.language())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .process_group(0);
    Ok(Command::from(command))
}

fn video_url(source: &str) -> AppResult<Url> {
    video_links::canonical(source)
}

fn subtitle_file(source: &Path) -> AppResult<()> {
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

#[tauri::command]
async fn pick_subtitle(app: AppHandle, window: tauri::WebviewWindow, ui_language: Option<String>) -> AppResult<Option<PathBuf>> {
    let (send, receive) = oneshot::channel();
    app.dialog()
        .file()
        .set_parent(&window)
        .set_title(translated(ui_language.as_deref(), "导入字幕文件", "Import subtitles"))
        .add_filter(translated(ui_language.as_deref(), "字幕文件", "Subtitle files"), &["srt", "vtt", "json"])
        .pick_file(move |file| {
            let _ = send.send(file);
        });
    let Some(file) = receive
        .await
        .map_err(|error| details("无法打开文件选择窗口", error))?
    else {
        return Ok(None);
    };
    let path = file
        .into_path()
        .map_err(|error| details("无法访问所选字幕文件", error))?;
    subtitle_file(&path)?;
    Ok(Some(path))
}

#[tauri::command]
async fn read_subtitle_clipboard(app: AppHandle, ui_language: Option<String>) -> AppResult<subtitle_clipboard::ClipboardSubtitles> {
    let (send, receive) = oneshot::channel();
    let failed = translated(ui_language.as_deref(), "无法读取剪贴板", "Could not read the clipboard");
    app.run_on_main_thread(move || {
        let _ = send.send(subtitle_clipboard::read(ui_language.as_deref()));
    }).map_err(|_| failed.to_owned())?;
    receive.await.map_err(|_| failed.to_owned())?
}

#[tauri::command]
fn resolve_subtitle_path(path: String) -> AppResult<PathBuf> {
    subtitle_clipboard::resolve_path(&path)
}

#[tauri::command]
async fn save_pasted_subtitle(
    locations: State<'_, Locations>, name: String, text: String,
) -> AppResult<PathBuf> {
    let language = Some(locations.language());
    let (temporary, destination) = subtitle_clipboard::stage(&locations.data, &name, &text, language)?;
    let path = temporary.path().to_str().ok_or_else(|| translated(language,
        "无法读取字幕文件路径", "Could not read the subtitle file path").to_owned())?;
    let _: serde_json::Value = query_with_timeout(&locations, &["validate-subtitle", "--", path],
        Duration::from_secs(20), translated(language, "校验字幕超时，请重试", "Subtitle validation timed out. Try again.")).await?;
    temporary.persist(&destination).map_err(|_| translated(language,
        "无法保存粘贴的字幕", "Could not save the pasted subtitles").to_owned())?;
    Ok(destination)
}

#[tauri::command]
async fn check_ffmpeg(path: String) -> environment::FfmpegStatus {
    environment::check(&path, command_paths()).await
}

#[tauri::command]
async fn locate_ffmpeg(app: AppHandle, window: tauri::WebviewWindow, ui_language: Option<String>) -> AppResult<Option<environment::FfmpegStatus>> {
    let (send, receive) = oneshot::channel();
    app.dialog().file().set_parent(&window).set_title(translated(ui_language.as_deref(), "选择 FFmpeg 可执行程序", "Choose the FFmpeg executable"))
        .pick_file(move |file| { let _ = send.send(file); });
    let Some(file) = receive.await.map_err(|_| "无法打开文件选择窗口".to_owned())? else { return Ok(None) };
    let path = file.into_path().map_err(|_| "无法访问所选程序".to_owned())?;
    let path = path.to_str().ok_or("程序路径包含无法识别的字符")?;
    let result = environment::check(path, vec![]).await;
    if result.state != "ready" { return Err(result.message); }
    Ok(Some(result))
}

#[tauri::command]
async fn generate(
    state: State<'_, Generation>,
    locations: State<'_, Locations>,
    request_id: String,
    source: String,
    provider: Provider,
    executable: String,
    progress: Channel<Progress>,
    credentials_read: Channel<()>,
    local_file: Option<bool>,
    base_url: Option<String>,
    model: Option<String>,
    replace: Option<bool>,
    asr: Option<AsrOptions>,
    resume_preview: Option<String>,
    page_language: Option<String>,
    ui_language: Option<String>,
    youtube_cookies: Option<YoutubeCookieOptions>,
) -> GenerationResult<Article> {
    let active_key = if local_file.unwrap_or(false) { None } else { video_links::source_key(&source) };
    state.begin_with_source(&request_id, active_key).map_err(|error| if error == "已取消生成" { GenerationFailure::cancelled() } else { GenerationFailure::core(error) })?;
    let command = generation_command(&locations, &source, provider, &executable,
        base_url.as_deref(), model.as_deref(), replace.unwrap_or(false),
        local_file.unwrap_or(false), asr.as_ref(), resume_preview.as_deref(), page_language.as_deref(),
        youtube_cookies.as_ref());
    let result = match command {
        Ok(mut command) => {
            // The command owns the credential snapshot. Settings can now commit
            // a new configuration while this generation runs.
            let _ = credentials_read.send(());
            if let Some(language @ ("en" | "zh-CN")) = ui_language.as_deref() { command.env("LINKREAD_UI_LANGUAGE", language); }
            run_generation(&state, &request_id, &mut command, progress).await
        }
        Err(error) => Err(error),
    };
    if state.finish(&request_id) {
        return Err(GenerationFailure::cancelled());
    }
    result
}

fn generation_command(
    locations: &Locations,
    source: &str,
    provider: Provider,
    executable: &str,
    base_url: Option<&str>,
    model: Option<&str>,
    replace: bool,
    local_file: bool,
    asr: Option<&AsrOptions>,
    resume_preview: Option<&str>,
    page_language: Option<&str>,
    youtube_cookies: Option<&YoutubeCookieOptions>,
) -> GenerationResult<Command> {
    if let Some(id) = resume_preview { preview_id(id).map_err(|e| GenerationFailure::context(e, "source", "source"))?; }
    if local_file {
        if resume_preview.is_none() { subtitle_file(Path::new(source)).map_err(|e| GenerationFailure::context(e, "source", "source"))?; }
        else if !Path::new(source).is_absolute() { return Err(GenerationFailure::context("原字幕路径无效".into(), "source", "source")) }
    } else { video_url(source).map_err(|e| GenerationFailure::context(e, "source", "source"))?; }
    let command = sidecar(locations).map_err(|e| GenerationFailure::context(e, "core_setup", "setup"))?;
    configure_generation_with_resume(command, locations, source, provider, executable,
                         base_url, model, replace, local_file, asr, resume_preview, page_language, youtube_cookies)
        .map_err(|e| GenerationFailure::context(e, "configuration", "settings"))
}

#[cfg(test)]
fn configure_generation(
    command: Command,
    locations: &Locations,
    source: &str,
    provider: Provider,
    executable: &str,
    base_url: Option<&str>,
    model: Option<&str>,
    replace: bool,
    local_file: bool,
    asr: Option<&AsrOptions>,
) -> AppResult<Command> {
    configure_generation_with_resume(command, locations, source, provider, executable, base_url, model, replace, local_file, asr, None, None, None)
}

fn configure_generation_with_resume(
    mut command: Command, locations: &Locations, source: &str, provider: Provider, executable: &str,
    base_url: Option<&str>, model: Option<&str>, replace: bool, local_file: bool, asr: Option<&AsrOptions>, resume_preview: Option<&str>,
    page_language: Option<&str>, youtube_cookies: Option<&YoutubeCookieOptions>,
) -> AppResult<Command> {
    // Validate the source independently from configuring the selected provider.
    // File imports must carry the same API/Codex/Claude options as videos.
    let source = if local_file {
        if resume_preview.is_none() { subtitle_file(Path::new(source))?; }
        source.to_owned()
    } else {
        video_url(source)?.to_string()
    };
    let tool = tool_path(provider, executable);
    if !matches!(provider, Provider::Api) && !executable.trim().is_empty() && tool.is_none() {
        return Err("指定的 AI 可执行文件无法运行，请在设置中检查路径".into());
    }
    let mut paths = command_paths();
    command.args(["generate", "--provider", provider.command()]);
    if let Some(language) = page_language {
        if !matches!(language, "original" | "en" | "zh-CN") { return Err("Unsupported generated page language".into()); }
        command.args(["--page-language", language]);
    }
    if let Some(id) = resume_preview { preview_id(id)?; command.args(["--resume-preview", id]); }
    if replace { command.arg("--replace"); }
    if local_file { command.arg("--local-file"); }
    if !local_file && resume_preview.is_none() {
        for arg in youtube_cookie_args(&source, youtube_cookies)? {
            command.arg(arg);
        }
    }
    if let Some(config) = asr.filter(|config| config.enabled && !local_file) {
        config.validate()?;
        command.args(["--asr", "--asr-base-url", config.base_url.trim(), "--asr-model", &config.model]);
        command.arg("--asr-max-minutes").arg(config.max_minutes.to_string());
        command.arg("--asr-chunk-minutes").arg(config.chunk_minutes.to_string());
        if !config.ffmpeg_path.is_empty() { command.args(["--ffmpeg-path", &config.ffmpeg_path]); }
        // Missing ASR credentials must not block an available subtitle path.
        command.env("LINKREAD_ASR_TOKEN", config.token(locations)?.unwrap_or_default());
    }
    if matches!(provider, Provider::Api) {
        let base = base_url.unwrap_or("").trim();
        validated_api_base_url(base)?;
        let model = model.unwrap_or("").trim();
        if model.is_empty() || model.len() > 256 || model.chars().any(char::is_control) {
            return Err("请在设置中填写模型名称".into());
        }
        let token = credentials::load(&locations.data)?.ok_or("请先在设置中填写 API Token")?;
        command.args(["--base-url", base, "--model", model]);
        command.env("LINKREAD_API_TOKEN", token);
    }
    if let Some(path) = tool {
        paths.insert(
            0,
            path.parent()
                .expect("absolute executable path")
                .to_path_buf(),
        );
        command.arg("--executable").arg(path);
    }
    let path =
        env::join_paths(paths).map_err(|error| details("无法配置 AI 工具的搜索路径", error))?;
    command.arg("--").arg(source.as_str()).env("PATH", path);
    Ok(command)
}

async fn run_generation(
    state: &Generation,
    request_id: &str,
    command: &mut Command,
    progress: Channel<Progress>,
) -> GenerationResult<Article> {
    let (mut process, cancelled) = state.spawn(request_id, command).map_err(GenerationFailure::core)?;
    let mut cancelled = Some(cancelled);
    let completed = {
        let mut work = pin!(async {
            let result = generation_result(&mut process.child, progress).await;
            let status = loop {
                match state.try_reap(request_id, &mut process.child) {
                    Ok(None) => tokio::time::sleep(Duration::from_millis(20)).await,
                    Ok(Some(status)) => break Ok(status),
                    Err(error) => break Err(error),
                }
            };
            (result, status)
        });
        let first = poll_fn(|cx| {
            if let Some(notice) = cancelled.as_mut() {
                match pin!(notice).poll(cx) {
                    Poll::Ready(Ok(deadline)) => return Poll::Ready(Err(deadline)),
                    Poll::Ready(Err(_)) => cancelled = None,
                    Poll::Pending => {},
                }
            }
            work.as_mut().poll(cx).map(Ok)
        }).await;
        match first {
            Ok(completed) => Some(completed),
            Err(deadline) => tokio::time::timeout_at(deadline.into(), work.as_mut()).await.ok(),
        }
    };
    let Some((result, status)) = completed else {
        state.kill_owned_group(request_id, true);
        // Drop the stdout reader even if a descendant left the process group
        // with the pipe open. Bound the direct child's reap separately.
        let _ = tokio::time::timeout(Duration::from_secs(1), process.child.wait()).await;
        return Err(GenerationFailure::cancelled());
    };
    let article = result?;
    if !status.map_err(|error| GenerationFailure::core(details("无法读取 LinkRead 的退出状态", error)))?
        .success() {
        return Err(GenerationFailure::core("LinkRead 核心异常退出，请重试".into()));
    }
    Ok(article)
}

async fn generation_result(child: &mut Child, channel: Channel<Progress>) -> GenerationResult<Article> {
    let stdout = child.stdout.take().expect("piped stdout");
    let mut lines = BufReader::new(stdout).lines();
    let mut result = Err(GenerationFailure::core("LinkRead 核心提前退出，请重试".to_owned()));
    loop {
        match lines.next_line().await {
            Ok(Some(line)) => match serde_json::from_str::<SidecarEvent>(&line) {
                Ok(SidecarEvent::Progress { progress }) => {
                    let _ = channel.send(progress);
                }
                Ok(SidecarEvent::Error { message, code, stage, retryable, action }) => result = Err(GenerationFailure {
                    message, code: code.unwrap_or_else(|| "unknown".into()), stage,
                    retryable: retryable.unwrap_or(true), action: action.unwrap_or_else(|| "retry".into()),
                }),
                Ok(SidecarEvent::Completed { article }) => result = Ok(article),
                Err(error) => result = Err(GenerationFailure::core(details("无法读取生成事件", error))),
            },
            Ok(None) => return result,
            Err(error) => return Err(GenerationFailure::core(details("无法读取 LinkRead 的输出", error))),
        }
    }
}

#[tauri::command]
fn cancel_generation(state: State<'_, Generation>, request_id: String) -> AppResult<()> {
    state.cancel(&request_id)
}

fn lock_archive(dir: &Path) -> AppResult<fs::File> {
    let lock = fs::OpenOptions::new().read(true).write(true).create(true).mode(0o600)
        .custom_flags(libc::O_NOFOLLOW).open(dir.join(".article-state.lock"))
        .map_err(|error| details("无法锁定资料库", error))?;
    if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX) } != 0 {
        return Err(details("无法锁定资料库", io::Error::last_os_error()));
    }
    Ok(lock)
}

fn valid_source_key(key: &str) -> bool {
    !key.is_empty() && key.len() <= 256 && !key.starts_with('.')
        && key.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_'))
}

fn folder_source_key(name: &str) -> Option<String> {
    let inner = name.strip_suffix(']')?.rsplit_once(" [")?.1;
    valid_source_key(inner).then(|| inner.to_owned())
}

fn read_bounded_json(path: &Path, limit: u64) -> Option<serde_json::Value> {
    let file = fs::OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW).open(path).ok()?;
    let meta = file.metadata().ok()?;
    if !meta.is_file() || meta.len() > limit { return None; }
    let mut bytes = Vec::new();
    file.take(limit + 1).read_to_end(&mut bytes).ok()?;
    if bytes.len() as u64 > limit { return None; }
    serde_json::from_slice(&bytes).ok()
}

fn transcript_source_key(path: &Path) -> Option<String> {
    let value = read_bounded_json(path, 32 * 1024 * 1024)?;
    let url = value.get("source")?.get("url")?.as_str()?;
    video_links::source_key(url)
}

fn cached_reading_key(folder: &Path) -> Option<String> {
    let value = read_bounded_json(&folder.join(".reader-cache.json"), 64 * 1024)?;
    value.get("readingKey")?.as_str().filter(|s| !s.is_empty()).map(str::to_owned)
}

fn remove_dir_if_real(path: &Path) -> AppResult<bool> {
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.file_type().is_dir() => {
            fs::remove_dir_all(path).map_err(|error| details("删除本地文件失败", error))?;
            Ok(true)
        }
        Ok(_) => Err("本地目录路径不可用".into()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(details("无法检查本地目录", error)),
    }
}

fn cleanup_source_caches_locked(
    library: &Path,
    source_key: Option<&str>,
    preview_id_value: Option<&str>,
) -> AppResult<()> {
    let mut resolved_key = source_key.filter(|key| valid_source_key(key)).map(str::to_owned);
    let previews = library.join(".transcript-previews");
    if resolved_key.is_none() {
        if let Some(pid) = preview_id_value {
            if preview_id(pid).is_ok() && !previews.is_symlink() {
                if let Some(manifest) = read_bounded_json(&previews.join(pid).join("request.json"), 1024 * 1024) {
                    if let Some(key) = manifest.get("sourceKey").and_then(|v| v.as_str()).filter(|k| valid_source_key(k)) {
                        resolved_key = Some(key.to_owned());
                    }
                }
            }
        }
    }

    if let Some(key) = resolved_key.as_deref() {
        let stages = library.join(".generation-staging");
        if fs::symlink_metadata(&stages).is_ok_and(|meta| meta.file_type().is_dir()) {
            let _stage_lock = lock_archive(&stages)?;
            if let Ok(entries) = fs::read_dir(&stages) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if fs::symlink_metadata(&path).is_ok_and(|meta| meta.file_type().is_dir()) {
                        let matched = read_bounded_json(&path.join("identity.json"), 64 * 1024)
                            .and_then(|v| v.get("sourceKey").and_then(|k| k.as_str()).map(|k| k == key))
                            .unwrap_or(false);
                        if matched {
                            fs::remove_dir_all(&path).map_err(|error| details("删除暂存缓存失败", error))?;
                        }
                    }
                }
            }
        }
    }

    if fs::symlink_metadata(&previews).is_ok_and(|meta| meta.file_type().is_dir()) {
        if let Some(pid) = preview_id_value {
            preview_id(pid)?;
            remove_dir_if_real(&previews.join(pid))?;
        }
        if let Some(key) = resolved_key.as_deref() {
            if let Ok(entries) = fs::read_dir(&previews) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if fs::symlink_metadata(&path).is_ok_and(|meta| meta.file_type().is_dir()) {
                        let matched = read_bounded_json(&path.join("request.json"), 1024 * 1024)
                            .and_then(|v| v.get("sourceKey").and_then(|k| k.as_str()).map(|k| k == key))
                            .unwrap_or(false);
                        if matched {
                            fs::remove_dir_all(&path).map_err(|error| details("删除原文预览缓存失败", error))?;
                        }
                    }
                }
            }
        }
    }

    if let Some(key) = resolved_key.as_deref() {
        let asr_root = library.join(".asr");
        if fs::symlink_metadata(&asr_root).is_ok_and(|meta| meta.file_type().is_dir()) {
            if let Ok(entries) = fs::read_dir(&asr_root) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if fs::symlink_metadata(&path).is_ok_and(|meta| meta.file_type().is_dir()) {
                        let matched = transcript_source_key(&path.join("transcript.json"))
                            .or_else(|| transcript_source_key(&path.join("audio.json")))
                            .is_some_and(|found| found == key);
                        if matched {
                            fs::remove_dir_all(&path).map_err(|error| details("删除语音转写缓存失败", error))?;
                        }
                    }
                }
            }
        }
    }
    Ok(())
}

fn remove_pasted_subtitle_if_managed(data: &Path, source: &str) {
    let imports = data.join("subtitle-imports");
    if !fs::symlink_metadata(&imports).is_ok_and(|meta| meta.file_type().is_dir()) {
        return;
    }
    let candidate = Path::new(source);
    let Some(parent) = candidate.parent() else { return };
    if parent.parent() != Some(imports.as_path()) { return; }
    let Some(hash_dir) = parent.file_name().and_then(|n| n.to_str()) else { return };
    if preview_id(hash_dir).is_err() { return; }
    let _ = remove_dir_if_real(parent);
}

#[tauri::command]
fn delete_article(
    state: State<'_, Generation>,
    locations: State<'_, Locations>,
    id: String,
    reading_key: Option<String>,
) -> AppResult<()> {
    // Do not delete a workspace while a sidecar might still be using it.
    if state.0.lock().unwrap().active.is_some() {
        return Err("正在生成文章，请在当前任务结束后再删除".into());
    }
    let fallback_key = article_file(&locations.library, &id)
        .ok()
        .and_then(|path| path.parent().and_then(cached_reading_key));
    remove_article_files(&locations.library, &id)?;
    if let Some(key) = reading_key.filter(|k| !k.is_empty()).or(fallback_key) {
        let _ = durable_state::delete_position(&locations.data, &key);
    }
    Ok(())
}

fn remove_article_files(library: &Path, id: &str) -> AppResult<()> {
    // Python state updates, reader caching and archive replacement use this same
    // lock. A deletion must not let a sidecar recreate a just-deleted directory.
    let _lock = lock_archive(library)?;
    let path = article_file(library, id)?;
    let folder = path.parent().ok_or("文章目录无效")?;
    let source_key = folder_source_key(id).or_else(|| transcript_source_key(&folder.join("transcript.json")));
    fs::remove_dir_all(folder).map_err(|error| details("删除文章失败", error))?;
    let backups = library.join(".linkread-backups");
    if fs::symlink_metadata(&backups).is_ok_and(|meta| meta.file_type().is_dir()) {
        let _ = remove_dir_if_real(&backups.join(id));
    }
    cleanup_source_caches_locked(library, source_key.as_deref(), None)?;
    Ok(())
}

fn remove_task_files(
    library: &Path,
    data: Option<&Path>,
    source: Option<&str>,
    local_file: bool,
    source_key: Option<&str>,
    preview_id_value: Option<&str>,
) -> AppResult<()> {
    if let Some(pid) = preview_id_value {
        preview_id(pid)?;
    }
    let mut effective_key = source_key.filter(|k| valid_source_key(k)).map(str::to_owned);
    if effective_key.is_none() && !local_file {
        effective_key = source.and_then(video_links::source_key);
    }
    if fs::symlink_metadata(library).is_ok_and(|meta| meta.file_type().is_dir()) {
        let _lock = lock_archive(library)?;
        if effective_key.is_none() {
            if let Some(pid) = preview_id_value {
                let manifest_path = library.join(".transcript-previews").join(pid).join("request.json");
                if let Some(manifest) = read_bounded_json(&manifest_path, 1024 * 1024) {
                    if let Some(key) = manifest.get("sourceKey").and_then(|v| v.as_str()).filter(|k| valid_source_key(k)) {
                        effective_key = Some(key.to_owned());
                    }
                }
            }
        }
        // Only remove an unfinished workspace directory (never one with a published article.html).
        if let Some(key) = effective_key.as_deref() {
            let suffix = format!(" [{key}]");
            if let Ok(entries) = fs::read_dir(library) {
                for entry in entries.flatten() {
                    let name = entry.file_name();
                    let Some(name) = name.to_str() else { continue };
                    if name.starts_with('.') || !name.ends_with(&suffix) { continue; }
                    let folder = entry.path();
                    if fs::symlink_metadata(&folder).is_ok_and(|meta| meta.file_type().is_dir()) {
                        let published = folder.join("article.html");
                        if fs::symlink_metadata(&published).is_err() {
                            fs::remove_dir_all(&folder).map_err(|error| details("删除未完成任务目录失败", error))?;
                        }
                    }
                }
            }
        }
        cleanup_source_caches_locked(library, effective_key.as_deref(), preview_id_value)?;
    }
    if local_file {
        if let (Some(data_dir), Some(path_str)) = (data, source) {
            remove_pasted_subtitle_if_managed(data_dir, path_str);
        }
    }
    Ok(())
}

#[tauri::command]
fn delete_task_data(
    state: State<'_, Generation>,
    locations: State<'_, Locations>,
    source: Option<String>,
    local_file: Option<bool>,
    source_key: Option<String>,
    preview_id_value: Option<String>,
) -> AppResult<()> {
    let is_local = local_file.unwrap_or(false);
    let candidate_key = source_key.as_deref().filter(|k| valid_source_key(k)).map(str::to_owned)
        .or_else(|| if !is_local { source.as_deref().and_then(video_links::source_key) } else { None });
    {
        let guard = state.0.lock().unwrap();
        if let Some(active) = guard.active.as_ref() {
            if active.source_key.is_none() || candidate_key.is_none() || active.source_key == candidate_key {
                return Err("正在处理该来源的任务，请等待当前任务结束后再删除".into());
            }
        }
    }
    remove_task_files(
        &locations.library,
        Some(&locations.data),
        source.as_deref(),
        is_local,
        source_key.as_deref(),
        preview_id_value.as_deref(),
    )
}

async fn query<T: serde::de::DeserializeOwned>(
    locations: &Locations,
    args: &[&str],
) -> AppResult<T> {
    query_with_timeout(locations, args, Duration::from_secs(60), "读取资料库超时，请重试").await
}

async fn query_with_timeout<T: serde::de::DeserializeOwned>(
    locations: &Locations,
    args: &[&str],
    deadline: Duration,
    timeout_message: &str,
) -> AppResult<T> {
    let mut command = sidecar(locations)?;
    command.args(args);
    query_command(command, deadline, timeout_message).await
}

/// A packaged one-file sidecar has a bootloader and a Python child. Dropping
/// only the direct Child can leave that Python process running after a timeout.
struct QueryProcessGroup(Option<i32>);

impl QueryProcessGroup {
    fn disarm(&mut self) {
        self.0 = None;
    }
}

impl Drop for QueryProcessGroup {
    fn drop(&mut self) {
        if let Some(pgid) = self.0.filter(|pid| *pid > 0) {
            // Cleanup must also run if the query future is cancelled, or if
            // reading/decoding the response fails. Do not mask the query error.
            unsafe { libc::kill(-pgid, libc::SIGKILL); }
        }
    }
}

async fn query_command<T: serde::de::DeserializeOwned>(
    mut command: Command,
    deadline: Duration,
    timeout_message: &str,
) -> AppResult<T> {
    // Keep this invariant local to the guard: a query must never join the app's
    // process group. kill_on_drop also lets Tokio reap the direct child.
    command.as_std_mut().process_group(0);
    let child = command.kill_on_drop(true).spawn()
        .map_err(|error| details("无法启动 LinkRead 核心", error))?;
    let mut process_group = QueryProcessGroup(child.id().map(|pid| pid as i32));
    let output = tokio::time::timeout(
        deadline,
        child.wait_with_output(),
    )
    .await
    .map_err(|_| timeout_message.to_owned())?
    .map_err(|error| details("无法启动 LinkRead 核心", error))?;

    if !output.status.success() {
        if let Ok(SidecarEvent::Error { message, .. }) = serde_json::from_slice(&output.stdout) {
            return Err(message);
        }
        return Err("LinkRead 核心提前退出，请重试".into());
    }
    let response = serde_json::from_slice(&output.stdout)
        .map_err(|error| details("无法读取资料库", error))?;
    process_group.disarm();
    Ok(response)
}

async fn query_command_detailed<T: serde::de::DeserializeOwned>(
    mut command: Command,
    deadline: Duration,
    timeout_message: &str,
) -> GenerationResult<T> {
    command.as_std_mut().process_group(0);
    let child = command.kill_on_drop(true).spawn()
        .map_err(|error| GenerationFailure::core(details("无法启动 LinkRead 核心", error)))?;
    let mut process_group = QueryProcessGroup(child.id().map(|pid| pid as i32));
    let output = tokio::time::timeout(deadline, child.wait_with_output())
        .await
        .map_err(|_| GenerationFailure {
            message: timeout_message.to_owned(),
            code: "source_timeout".into(),
            stage: Some("extract".into()),
            retryable: true,
            action: "retry".into(),
        })?
        .map_err(|error| GenerationFailure::core(details("无法启动 LinkRead 核心", error)))?;

    if !output.status.success() {
        if let Ok(SidecarEvent::Error { message, code, stage, retryable, action }) = serde_json::from_slice(&output.stdout) {
            return Err(GenerationFailure {
                message,
                code: code.unwrap_or_else(|| "extraction_failed".into()),
                stage,
                retryable: retryable.unwrap_or(false),
                action: action.unwrap_or_else(|| "source".into()),
            });
        }
        return Err(GenerationFailure::core("LinkRead 核心提前退出，请重试".into()));
    }
    let response = serde_json::from_slice(&output.stdout)
        .map_err(|error| GenerationFailure::core(details("无法读取字幕结果", error)))?;
    process_group.disarm();
    Ok(response)
}

#[tauri::command]
async fn list_articles(locations: State<'_, Locations>) -> AppResult<Library> {
    query(&locations, &["library"]).await
}

#[tauri::command]
async fn get_article_details(locations: State<'_, Locations>, id: String) -> AppResult<ArticleDetails> {
    article_file(&locations.library, &id)?;
    query(&locations, &["article-details", "--", &id]).await
}

#[tauri::command]
async fn update_article_details(
    locations: State<'_, Locations>, id: String, title: Option<String>, category: Option<String>,
    source_url: Option<String>,
) -> AppResult<Article> {
    article_file(&locations.library, &id)?;
    let mut values = serde_json::json!({"title": title, "category": category});
    if let Some(source) = source_url {
        let source = source.trim();
        if source.len() > 4096 { return Err("请输入有效的视频或播客单集链接".into()); }
        values["sourceUrl"] = if source.is_empty() {
            serde_json::Value::Null
        } else {
            serde_json::json!(video_url(source)?.as_str())
        };
    }
    let values = values.to_string();
    query(&locations, &["update-article-details", "--values", &values, "--", &id]).await
}

#[tauri::command]
async fn update_article_state(
    locations: State<'_, Locations>, id: String,
    is_read: Option<bool>, is_starred: Option<bool>,
) -> AppResult<ReadingState> {
    article_file(&locations.library, &id)?;
    if is_read.is_none() && is_starred.is_none() {
        return Err("请选择要更新的阅读状态".into());
    }
    let mut args = vec!["reading-state"];
    if let Some(value) = is_read {
        args.extend(["--read", if value { "true" } else { "false" }]);
    }
    if let Some(value) = is_starred {
        args.extend(["--starred", if value { "true" } else { "false" }]);
    }
    args.extend(["--", &id]);
    query(&locations, &args).await
}

#[tauri::command]
async fn prepare_article(locations: State<'_, Locations>, id: String) -> AppResult<PreparedReader> {
    article_file(&locations.library, &id)?;
    query(&locations, &["reader", "--", &id]).await
}

#[tauri::command]
async fn prepare_preview(locations: State<'_, Locations>, id: String) -> AppResult<PreparedPreview> {
    preview_id(&id)?;
    query(&locations, &["preview-reader", "--", &id]).await
}

#[tauri::command]
fn load_task_journal(locations: State<'_, Locations>) -> AppResult<serde_json::Value> {
    durable_state::load_journal(&locations.data)
}

#[tauri::command]
fn save_task_journal(locations: State<'_, Locations>, journal: serde_json::Value) -> AppResult<()> {
    durable_state::save_journal(&locations.data, &journal)
}

#[tauri::command]
fn load_reading_position(locations: State<'_, Locations>, key: String) -> AppResult<serde_json::Value> {
    durable_state::load_position(&locations.data, &key)
}

#[tauri::command]
fn save_reading_position(locations: State<'_, Locations>, key: String, position: serde_json::Value) -> AppResult<()> {
    durable_state::save_position(&locations.data, &key, &position)
}

fn preview_id(id: &str) -> AppResult<()> {
    if id.len() != 64 || !id.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) {
        return Err("原文预览编号无效".into());
    }
    Ok(())
}

fn transcript_preview_file(library: &Path, id: &str) -> AppResult<PathBuf> {
    preview_id(id)?;
    let root = library.join(".transcript-previews");
    let folder = root.join(id);
    let file = folder.join("preview.html");
    if root.is_symlink() || folder.is_symlink() || file.is_symlink() || !file.is_file() {
        return Err("找不到已保存的原文预览".into());
    }
    Ok(file)
}

#[tauri::command]
async fn refresh_shownotes(locations: State<'_, Locations>, id: String) -> AppResult<ShownotesRefresh> {
    article_file(&locations.library, &id)?;
    let timeout = if locations.language() == "en" {
        "Loading Shownotes timed out. Try again."
    } else {
        "加载 Shownotes 超时，请稍后重试"
    };
    query_with_timeout(&locations, &["refresh-shownotes", "--", &id], Duration::from_secs(60),
        timeout).await
}

fn article_file(library: &Path, id: &str) -> AppResult<PathBuf> {
    let mut components = Path::new(id).components();
    if id.starts_with('.')
        || !matches!(components.next(), Some(Component::Normal(name)) if name == id)
        || components.next().is_some()
    {
        return Err("无效的文章编号".into());
    }
    let folder = library.join(id);
    let file = folder.join("article.html");
    if library.is_symlink() || !library.is_dir() || folder.is_symlink() || file.is_symlink() || !file.is_file() {
        return Err("找不到这篇已生成的文章".into());
    }
    Ok(file)
}

fn rendered_article_file(library: &Path, id: &str) -> AppResult<PathBuf> {
    let article = article_file(library, id)?;
    let rendered = article.with_file_name(".reader.html");
    if rendered.is_symlink() {
        return Err("阅读缓存路径不可用".into());
    }
    Ok(if rendered.is_file() { rendered } else { article })
}

fn article_cover_file(library: &Path, id: &str) -> Option<(Vec<u8>, &'static str)> {
    const MAX_COVER_BYTES: u64 = 5 * 1024 * 1024;
    const CANDIDATES: &[(&str, &str)] = &[
        ("cover.jpg", "image/jpeg"),
        ("cover.png", "image/png"),
        ("cover.webp", "image/webp"),
        ("cover.gif", "image/gif"),
    ];
    let article = article_file(library, id).ok()?;
    let folder = article.parent()?;
    for &(name, mime) in CANDIDATES {
        let path = folder.join(name);
        let Ok(file) = fs::OpenOptions::new().read(true).custom_flags(libc::O_NOFOLLOW).open(&path) else {
            continue;
        };
        let Ok(meta) = file.metadata() else { continue };
        if !meta.is_file() || meta.len() == 0 || meta.len() > MAX_COVER_BYTES { continue; }
        let mut bytes = Vec::with_capacity(meta.len() as usize);
        if file.take(MAX_COVER_BYTES + 1).read_to_end(&mut bytes).is_ok()
            && !bytes.is_empty() && (bytes.len() as u64) <= MAX_COVER_BYTES
        {
            return Some((bytes, mime));
        }
    }
    None
}

fn cover_response(library: &Path, id: &str) -> http::Response<Vec<u8>> {
    match article_cover_file(library, id) {
        Some((bytes, mime)) => http::Response::builder()
            .status(http::StatusCode::OK)
            .header(http::header::CONTENT_TYPE, mime)
            .header(http::header::X_CONTENT_TYPE_OPTIONS, "nosniff")
            .header(http::header::CACHE_CONTROL, "private, max-age=31536000, immutable")
            .body(bytes)
            .expect("valid cover response"),
        None => http::Response::builder()
            .status(http::StatusCode::NOT_FOUND)
            .header(http::header::CACHE_CONTROL, "no-store")
            .body(Vec::new())
            .expect("valid missing cover response"),
    }
}

fn preview_response(locations: &Locations, uri: &http::Uri) -> http::Response<Vec<u8>> {
    const POLICY: &str = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'";
    let decoded = percent_decode_str(uri.path().trim_start_matches('/'))
        .decode_utf8()
        .ok()
        .filter(|_| uri.host() == Some("localhost"));
    if let Some(id) = decoded.as_deref().and_then(|path| path.strip_prefix("cover/")) {
        return cover_response(&locations.library, id);
    }
    let html = decoded
        .and_then(|id| match id.strip_prefix("preview/") {
            Some(preview) => transcript_preview_file(&locations.library, preview).ok(),
            None => rendered_article_file(&locations.library, &id).ok(),
        })
        .and_then(|path| {
            fs::read_to_string(&path)
                .ok()
                .map(|html| reader::with_takeaways(html, &path))
        });
    let (status, html) = match html {
        Some(html) => (http::StatusCode::OK, preview_document(html)),
        None => (http::StatusCode::NOT_FOUND, format!(
            "<!doctype html><html lang={}><meta charset=utf-8><p>{}</p>", locations.language(),
            translated(Some(locations.language()), "无法读取这篇文章，请刷新书架。", "Could not read this article. Reload the library and try again."))),
    };
    http::Response::builder()
        .status(status)
        .header(http::header::CONTENT_TYPE, "text/html; charset=utf-8")
        .header(http::header::CONTENT_SECURITY_POLICY, POLICY)
        .header(http::header::CACHE_CONTROL, "no-store")
        .body(html.into_bytes())
        .expect("valid preview response")
}

fn preview_document(mut html: String) -> String {
    // A bundled script can contain an earlier </body> in a JavaScript string.
    if let Some(end) = html.rfind("</body>") {
        html.insert_str(end, include_str!("preview.html"));
    }
    html
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum ExportFormat {
    Html,
    Markdown,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum MarkdownContent {
    Readable,
    Full,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct MarkdownExportOptions {
    content: MarkdownContent,
    include_timestamped: bool,
}

fn export_file_stem(title: &str) -> String {
    let mut result = String::new();
    for ch in title.trim().chars() {
        let ch = if ch.is_control() || matches!(ch, '/' | '\\' | ':') { '_' } else { ch };
        if result.len() + ch.len_utf8() > 180 { break; }
        result.push(ch);
    }
    let result = result.trim().trim_matches('.').trim();
    if result.is_empty() { "Article".into() } else { result.into() }
}

#[tauri::command]
async fn export_article(
    app: AppHandle,
    window: tauri::WebviewWindow,
    locations: State<'_, Locations>,
    id: String,
    format: ExportFormat,
    options: Option<MarkdownExportOptions>,
    ui_language: Option<String>,
) -> AppResult<bool> {
    #[derive(Deserialize)]
    struct Markdown {
        text: String,
    }

    article_file(&locations.library, &id)?;
    let (kind, extension) = match format {
        ExportFormat::Html => ("HTML", "html"),
        ExportFormat::Markdown => ("Markdown", "md"),
    };
    let metadata: ArticleDetails = query(&locations, &["article-details", "--", &id]).await?;
    let title = export_file_stem(&metadata.title);
    let (send, receive) = oneshot::channel();
    app.dialog()
        .file()
        .set_parent(&window)
        .set_title(format!("{} {kind}", translated(ui_language.as_deref(), "导出", "Export")))
        .add_filter(kind, &[extension])
        .set_file_name(format!("{title}.{extension}"))
        .save_file(move |file| {
            let _ = send.send(file);
        });
    let Some(target) = receive
        .await
        .map_err(|error| details("无法打开保存窗口", error))?
    else {
        return Ok(false);
    };
    let target = target
        .into_path()
        .map_err(|error| details("无法访问所选保存位置", error))?;
    let (source, markdown) = match format {
        ExportFormat::Html => {
            let _: PreparedReader = query(&locations, &["reader", "--", &id]).await?;
            (Some(rendered_article_file(&locations.library, &id)?), None)
        }
        ExportFormat::Markdown => {
            // Omitting options preserves existing callers. The new dialog sends
            // its explicit readable/full choice and subtitle appendix setting.
            let content = match options.as_ref().map(|value| &value.content) {
                Some(MarkdownContent::Readable) => "readable", _ => "full",
            };
            let timestamps = if options.as_ref().map_or(true, |value| value.include_timestamped) { "true" } else { "false" };
            let markdown: Markdown = query(&locations, &["markdown", "--content", content,
                "--include-timestamped", timestamps, "--", &id]).await?;
            (None, Some(markdown.text))
        }
    };
    tauri::async_runtime::spawn_blocking(move || -> io::Result<()> {
        let parent = target.parent().ok_or(io::ErrorKind::InvalidInput)?;
        let mut temp = tempfile::NamedTempFile::new_in(parent)?;
        if let Some(text) = markdown {
            temp.write_all(text.as_bytes())?;
        } else if let Some(source) = source {
            io::copy(&mut fs::File::open(source)?, temp.as_file_mut())?;
        } else {
            return Err(io::ErrorKind::InvalidInput.into());
        }
        temp.persist(target)?;
        Ok(())
    })
    .await
    .map_err(|error| details("导出失败", error))?
    .map_err(|error| details("导出失败", error))?;
    Ok(true)
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum SubtitleExportFormat {
    Srt,
    Vtt,
}

#[tauri::command]
async fn export_subtitles(
    app: AppHandle,
    window: tauri::WebviewWindow,
    locations: State<'_, Locations>,
    id: String,
    format: SubtitleExportFormat,
    preview: Option<bool>,
    ui_language: Option<String>,
) -> AppResult<bool> {
    #[derive(Deserialize)]
    struct ExportedSubtitles {
        text: String,
        title: String,
    }

    let is_preview = preview.unwrap_or(false);
    if is_preview {
        preview_id(&id)?;
    } else {
        article_file(&locations.library, &id)?;
    }
    let (kind, extension) = match format {
        SubtitleExportFormat::Srt => ("SRT", "srt"),
        SubtitleExportFormat::Vtt => ("WebVTT", "vtt"),
    };
    let mut args = vec!["export-subtitles", "--format", extension];
    if is_preview {
        args.push("--preview");
    }
    args.extend(["--", &id]);
    let exported: ExportedSubtitles = query(&locations, &args).await?;
    let title = export_file_stem(&exported.title);
    let (send, receive) = oneshot::channel();
    app.dialog()
        .file()
        .set_parent(&window)
        .set_title(format!("{} {kind}", translated(ui_language.as_deref(), "导出字幕", "Export subtitles")))
        .add_filter(kind, &[extension])
        .set_file_name(format!("{title}.{extension}"))
        .save_file(move |file| {
            let _ = send.send(file);
        });
    let Some(target) = receive
        .await
        .map_err(|error| details("无法打开保存窗口", error))?
    else {
        return Ok(false);
    };
    let target = target
        .into_path()
        .map_err(|error| details("无法访问所选保存位置", error))?;
    tauri::async_runtime::spawn_blocking(move || -> io::Result<()> {
        let parent = target.parent().ok_or(io::ErrorKind::InvalidInput)?;
        let mut temp = tempfile::NamedTempFile::new_in(parent)?;
        temp.write_all(exported.text.as_bytes())?;
        temp.persist(target)?;
        Ok(())
    })
    .await
    .map_err(|error| details("导出字幕失败", error))?
    .map_err(|error| details("导出字幕失败", error))?;
    Ok(true)
}

#[tauri::command]
async fn open_video(source: String) -> AppResult<()> {
    let url = video_url(&source)?;
    let status = Command::new("/usr/bin/open")
        .arg(url.as_str())
        .status()
        .await
        .map_err(|error| details("无法打开浏览器", error))?;
    status
        .success()
        .then_some(())
        .ok_or_else(|| "无法打开浏览器".to_owned())
}

fn external_url(value: &str) -> AppResult<Url> {
    let url = Url::parse(value).map_err(|_| "链接格式无效".to_owned())?;
    if value.len() > 8192 || value.chars().any(char::is_control) || !matches!(url.scheme(), "http" | "https")
        || url.host_str().is_none() || !url.username().is_empty() || url.password().is_some()
    {
        return Err("仅支持打开 HTTP 或 HTTPS 网页链接".into());
    }
    Ok(url)
}

#[tauri::command]
async fn open_external(url: String) -> AppResult<()> {
    let url = external_url(&url)?;
    let status = Command::new("/usr/bin/open").arg(url.as_str()).status().await
        .map_err(|error| details("无法打开链接", error))?;
    status.success().then_some(()).ok_or_else(|| "无法打开链接".to_owned())
}

#[tauri::command]
async fn open_article(locations: State<'_, Locations>, id: String) -> AppResult<()> {
    article_file(&locations.library, &id)?;
    let _: PreparedReader = query(&locations, &["reader", "--", &id]).await?;
    let path = rendered_article_file(&locations.library, &id)?;
    let status = Command::new("/usr/bin/open")
        .arg(path)
        .status()
        .await
        .map_err(|error| details("无法打开文章", error))?;
    status
        .success()
        .then_some(())
        .ok_or_else(|| "无法打开文章".to_owned())
}

fn saved_transcript_file(library: &Path, id: &str) -> AppResult<PathBuf> {
    let path = article_file(library, id)?.with_file_name("transcript.json");
    if path.is_symlink() || !path.is_file() {
        return Err("找不到这篇文章的原始字幕，请重新导入文件".into());
    }
    Ok(path)
}

#[tauri::command]
fn article_transcript_path(locations: State<'_, Locations>, id: String) -> AppResult<PathBuf> {
    saved_transcript_file(&locations.library, &id)
}

#[tauri::command]
async fn reveal_article(locations: State<'_, Locations>, id: String) -> AppResult<()> {
    let path = article_file(&locations.library, &id)?;
    let status = Command::new("/usr/bin/open")
        .arg("-R")
        .arg(path)
        .status()
        .await
        .map_err(|error| details("无法在访达显示文章", error))?;
    status
        .success()
        .then_some(())
        .ok_or_else(|| "无法在访达显示文章".to_owned())
}

fn main() {
    let builder = tauri::Builder::default();
    #[cfg(target_os = "macos")]
    let builder = builder.menu(desktop_menu::build).on_menu_event(desktop_menu::dispatch);
    builder
        .plugin(tauri_plugin_dialog::init())
        .register_uri_scheme_protocol("linkread-reader", |context, request| {
            preview_response(&context.app_handle().state::<Locations>(), request.uri())
        })
        .manage(Generation::default())
        .manage(app_updates::AppUpdates::default())
        .setup(|app| {
            if app.config().plugins.0.contains_key("updater") {
                app.handle().plugin(tauri_plugin_updater::Builder::new().build())?;
            }
            let data = app.path().app_data_dir()?;
            fs::create_dir_all(&data)?;
            let library = env::var_os("LINKREAD_DESKTOP_LIBRARY")
                .map(PathBuf::from)
                .filter(|path| path.is_absolute())
                .unwrap_or_else(|| data.join("library"));
            app.manage(Locations { data, library, english: AtomicBool::new(false) });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            set_ui_language,
            app_updates::check_app_update,
            app_updates::install_app_update,
            get_article_details,
            update_article_details,
            update_article_state,
            prepare_article,
            prepare_preview,
            load_task_journal,
            save_task_journal,
            load_reading_position,
            save_reading_position,
            refresh_shownotes,
            open_external,
            check_tool,
            preview_video,
            check_source,
            download_subtitles,
            export_subtitles,
            pick_cookies_file,
            test_youtube_cookies,
            load_asr_token,
            save_asr_token,
            delete_asr_token,
            test_asr_connection,
            check_ffmpeg,
            locate_ffmpeg,
            load_api_token,
            save_api_token,
            test_api_connection,
            delete_api_token,
            check_duplicate,
            delete_article,
            delete_task_data,
            generate,
            cancel_generation,
            list_articles,
            export_article,
            open_video,
            open_article,
            reveal_article,
            article_transcript_path,
            pick_subtitle,
            read_subtitle_clipboard,
            resolve_subtitle_path,
            save_pasted_subtitle,
        ])
        .build(tauri::generate_context!())
        .expect("failed to initialize LinkRead")
        .run(|app, event| match event {
            tauri::RunEvent::ExitRequested { code, api, .. }
                if code != Some(tauri::RESTART_EXIT_CODE) && app.state::<Generation>().updating() =>
            {
                api.prevent_exit();
            }
            tauri::RunEvent::Exit => app.state::<Generation>().cancel_active_on_exit(GENERATION_CANCEL_GRACE),
            _ => {}
        });
}

#[cfg(test)]
mod tests {
    use super::{article_file, fs, preview_document, validated_api_base_url, remove_article_files, saved_transcript_file, configure_generation, Locations, Provider, Generation, SidecarEvent};
    use std::{path::Path, process::Stdio, time::{Duration, Instant}};
    use std::sync::atomic::AtomicBool;
    use tokio::process::Command;

    #[test]
    fn shownotes_refresh_distinguishes_empty_and_updated_responses() {
        let empty: super::ShownotesRefresh = serde_json::from_value(serde_json::json!({
            "type": "shownotes", "status": "empty",
        })).unwrap();
        assert_eq!(serde_json::to_value(empty).unwrap(), serde_json::json!({"status": "empty"}));

        let updated = serde_json::json!({"status": "updated", "revision": "new", "readingKey": "original"});
        let response: super::ShownotesRefresh = serde_json::from_value(updated.clone()).unwrap();
        assert_eq!(serde_json::to_value(response).unwrap(), updated);
    }

    #[test]
    fn installation_and_generation_cannot_overlap() {
        let generation = Generation::default();
        generation.begin("running").unwrap();
        assert!(generation.begin_update().is_err());
        generation.finish("running");
        let install = generation.begin_update().unwrap();
        assert!(generation.updating());
        assert!(generation.begin("queued").is_err());
        drop(install);
        assert!(generation.begin("queued").is_ok());
    }

    #[test]
    fn asr_test_uses_the_explicit_draft_and_never_falls_back_from_an_empty_draft() {
        let dir = tempfile::tempdir().unwrap();
        let locations = Locations { data: dir.path().join("data"), library: dir.path().join("library"), english: AtomicBool::new(false) };
        super::credentials::store(&locations.data, "saved-chat-key").unwrap();
        super::credentials::store_asr(&locations.data, "saved-asr-key").unwrap();
        let mut options = super::AsrOptions {
            enabled: true, reuse_token: true,
            base_url: "https://dashscope.aliyuncs.com/api/v1".into(), model: "paraformer-v2".into(),
            max_minutes: 120, chunk_minutes: 5, ffmpeg_path: String::new(),
        };
        assert_eq!(options.test_token(&locations, Some("draft-chat-key".into())).unwrap(), "draft-chat-key");
        assert_eq!(options.test_token(&locations, None).unwrap(), "saved-chat-key");
        options.reuse_token = false;
        assert_eq!(options.test_token(&locations, Some("draft-asr-key".into())).unwrap(), "draft-asr-key");
        assert_eq!(options.test_token(&locations, None).unwrap(), "saved-asr-key");
        for reuse in [false, true] {
            options.reuse_token = reuse;
            for invalid in [String::new(), "   ".into(), "invalid\nkey".into(), "x".repeat(8193)] {
                assert!(options.test_token(&locations, Some(invalid)).is_err());
            }
        }
    }

    #[test]
    fn an_explicit_asr_test_key_does_not_read_the_saved_credential() {
        let dir = tempfile::tempdir().unwrap();
        let locations = Locations { data: dir.path().join("data"), library: dir.path().join("library"), english: AtomicBool::new(false) };
        fs::create_dir_all(locations.data.join("api-token")).unwrap();
        let options = super::AsrOptions {
            enabled: true, reuse_token: true,
            base_url: "https://dashscope.aliyuncs.com/api/v1".into(), model: "paraformer-v2".into(),
            max_minutes: 120, chunk_minutes: 5, ffmpeg_path: String::new(),
        };
        assert!(options.test_token(&locations, None).is_err());
        assert_eq!(options.test_token(&locations, Some("new-key".into())).unwrap(), "new-key");
    }

    #[test]
    fn export_filename_uses_display_title_without_path_components() {
        assert_eq!(super::export_file_stem("  标题 / Part: 2\\3\n. "), "标题 _ Part_ 2_3_");
        assert_eq!(super::export_file_stem(" . . "), "Article");
        let title = super::export_file_stem(&"字幕🙂".repeat(100));
        assert!(title.len() <= 180 && !title.is_empty());
        assert!("字幕🙂".repeat(100).starts_with(&title));
    }

    #[test]
    fn markdown_export_options_match_the_frontend_contract() {
        let options: super::MarkdownExportOptions = serde_json::from_value(serde_json::json!({
            "content": "readable", "includeTimestamped": false,
        })).unwrap();
        assert!(matches!(options.content, super::MarkdownContent::Readable));
        assert!(!options.include_timestamped);
        for value in [
            serde_json::json!({"content":"other","includeTimestamped":false}),
            serde_json::json!({"content":"full","includeTimestamped":"false"}),
            serde_json::json!({"content":"full"}),
            serde_json::json!({"content":"full","includeTimestamped":true,"unknown":true}),
        ] {
            assert!(serde_json::from_value::<super::MarkdownExportOptions>(value).is_err());
        }
    }

    fn query_test_command(script: &str, pid_file: &Path) -> Command {
        let mut command = Command::new("/bin/sh");
        command.args(["-c", script, "linkread-test-query"]).arg(pid_file)
            .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null());
        command
    }

    // Model the one-file bootloader, Python child, and an inherited descriptor.
    // Killing only the first shell would leave both descendants running.
    const QUERY_PROCESS_TREE: &str = r#"
        /bin/sh -c 'sleep 60 & printf "%s %s %s\n" "$2" "$$" "$!" > "$1"; wait' query-child "$1" "$$" &
        wait
    "#;

    async fn query_process_ids(pid_file: &Path, expected: usize) -> Vec<i32> {
        let started = Instant::now();
        loop {
            if let Ok(text) = fs::read_to_string(pid_file) {
                if let Ok(pids) = text.split_whitespace().map(str::parse::<i32>).collect::<Result<Vec<_>, _>>() {
                    if text.ends_with('\n') && pids.len() == expected && pids.iter().all(|pid| *pid > 0) {
                        return pids;
                    }
                }
            }
            assert!(started.elapsed() < Duration::from_secs(2), "query process tree did not start");
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }

    fn query_process_is_running(pid: i32) -> bool {
        let output = std::process::Command::new("/bin/ps")
            .args(["-o", "stat=", "-p", &pid.to_string()]).output().unwrap();
        assert!(output.status.success() || output.status.code() == Some(1));
        let state = String::from_utf8(output.stdout).unwrap();
        // Orphaned descendants can briefly be zombies until the OS reaps them.
        !state.trim().is_empty() && !state.trim().starts_with('Z')
    }

    async fn assert_query_processes_stopped(pids: &[i32]) {
        let started = Instant::now();
        loop {
            let running: Vec<_> = pids.iter().copied().filter(|pid| query_process_is_running(*pid)).collect();
            if running.is_empty() { return; }
            assert!(started.elapsed() < Duration::from_secs(2), "query left processes running: {running:?}");
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    }

    #[test]
    fn query_timeout_stops_the_entire_sidecar_process_group() {
        let directory = tempfile::tempdir().unwrap();
        let pid_file = directory.path().join("pids");
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let result = super::query_command::<serde_json::Value>(
                query_test_command(QUERY_PROCESS_TREE, &pid_file),
                Duration::from_secs(2), "preview timeout",
            ).await;
            let pids = query_process_ids(&pid_file, 3).await;
            let mut cleanup = super::QueryProcessGroup(Some(pids[0]));
            assert_eq!(result.unwrap_err(), "preview timeout");
            assert_query_processes_stopped(&pids).await;
            cleanup.disarm();
        });
    }

    #[test]
    fn cancelling_query_future_stops_the_entire_sidecar_process_group() {
        let directory = tempfile::tempdir().unwrap();
        let pid_file = directory.path().join("pids");
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let task = tokio::spawn(super::query_command::<serde_json::Value>(
                query_test_command(QUERY_PROCESS_TREE, &pid_file),
                Duration::from_secs(60), "preview timeout",
            ));
            let pids = query_process_ids(&pid_file, 3).await;
            let mut cleanup = super::QueryProcessGroup(Some(pids[0]));
            task.abort();
            assert!(task.await.unwrap_err().is_cancelled());
            assert_query_processes_stopped(&pids).await;
            cleanup.disarm();
        });
    }

    #[test]
    fn invalid_query_response_cleans_up_remaining_children() {
        let directory = tempfile::tempdir().unwrap();
        let pid_file = directory.path().join("pids");
        let script = r#"
            sleep 60 >/dev/null 2>&1 &
            printf "%s %s\n" "$$" "$!" > "$1"
            printf 'not-json'
        "#;
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let result = super::query_command::<serde_json::Value>(
                query_test_command(script, &pid_file), Duration::from_secs(2), "preview timeout",
            ).await;
            let pids = query_process_ids(&pid_file, 2).await;
            let mut cleanup = super::QueryProcessGroup(Some(pids[0]));
            assert!(result.unwrap_err().starts_with("无法读取资料库"));
            assert_query_processes_stopped(&pids).await;
            cleanup.disarm();
        });
    }

    #[test]
    fn successful_query_preserves_its_json_response() {
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let result = super::query_command::<serde_json::Value>(
                query_test_command(r#"printf '{"title":"ready"}'"#, Path::new("unused")),
                Duration::from_secs(2), "preview timeout",
            ).await.unwrap();
            assert_eq!(result["title"], "ready");
        });
    }

    #[test]
    fn archived_regeneration_rejects_symlinks_and_outside_paths() {
        let root = tempfile::tempdir().unwrap();
        let library = root.path().join("library");
        let folder = library.join("article");
        fs::create_dir_all(&folder).unwrap();
        fs::write(folder.join("article.html"), "old page").unwrap();
        fs::write(folder.join("transcript.json"), "{}").unwrap();
        assert_eq!(saved_transcript_file(&library, "article").unwrap(), folder.join("transcript.json"));
        assert!(saved_transcript_file(&library, "../article").is_err());
        fs::remove_file(folder.join("transcript.json")).unwrap();
        let unrelated = root.path().join("private.json");
        fs::write(&unrelated, "private").unwrap();
        std::os::unix::fs::symlink(unrelated, folder.join("transcript.json")).unwrap();
        assert!(saved_transcript_file(&library, "article").is_err());
    }

    #[test]
    fn local_generation_passes_api_configuration_without_token_in_arguments() {
        let root = tempfile::tempdir().unwrap();
        let locations = Locations { data: root.path().to_path_buf(), library: root.path().join("library"), english: AtomicBool::new(false) };
        let source = root.path().join("captions.srt");
        fs::write(&source, "1\n00:00:00,000 --> 00:00:01,000\nhello\n").unwrap();
        super::credentials::store(&locations.data, "private-test-token").unwrap();
        let command = configure_generation(Command::new("python3"), &locations, source.to_str().unwrap(), Provider::Api, "",
            Some("https://api.example.com/v1"), Some("test-model"), false, true, None).unwrap();
        let args: Vec<_> = command.as_std().get_args().map(|v| v.to_string_lossy().into_owned()).collect();
        assert!(args.windows(2).any(|w| w == ["--provider", "api"]));
        assert!(args.windows(2).any(|w| w == ["--model", "test-model"]));
        assert!(args.contains(&"--local-file".to_owned()));
        assert!(!args.contains(&"--asr".to_owned()));
        assert!(!args.join(" ").contains("private-test-token"));
        assert!(command.as_std().get_envs().any(|(k,v)| k == "LINKREAD_API_TOKEN"
            && v.is_some_and(|value| value == "private-test-token")));
    }

    #[test]
    fn generation_passes_the_selected_ffmpeg_path_as_one_argument() {
        let root = tempfile::tempdir().unwrap();
        let locations = Locations { data: root.path().to_path_buf(), library: root.path().join("library"), english: AtomicBool::new(false) };
        super::credentials::store(&locations.data, "private-test-token").unwrap();
        let options: super::AsrOptions = serde_json::from_value(serde_json::json!({
            "enabled":true,"reuseToken":false,"baseUrl":"https://dashscope.aliyuncs.com/api/v1",
            "model":"paraformer-v2","maxMinutes":120,"chunkMinutes":5,
            "ffmpegPath":"/Users/yano/Audio Tools/ffmpeg"
        })).unwrap();
        let command = configure_generation(Command::new("python3"), &locations,
            "https://www.youtube.com/watch?v=1-Fo8McCD9w", Provider::Api, "", Some("https://api.example.com/v1"),
            Some("test-model"), false, false, Some(&options)).unwrap();
        let args: Vec<_> = command.as_std().get_args().map(|v| v.to_string_lossy().into_owned()).collect();
        assert!(args.windows(2).any(|w| w == ["--ffmpeg-path", "/Users/yano/Audio Tools/ffmpeg"]));
        assert!(!args.join(" ").contains("private-test-token"));
        let old: super::AsrOptions = serde_json::from_value(serde_json::json!({
            "enabled":false,"reuseToken":false,"baseUrl":"https://dashscope.aliyuncs.com/api/v1",
            "model":"paraformer-v2","maxMinutes":120
        })).unwrap();
        assert_eq!(old.ffmpeg_path, "");
        assert_eq!(old.chunk_minutes, 5);
    }

    #[test]
    fn article_open_rejects_paths_outside_the_library() {
        let directory = tempfile::tempdir().unwrap();
        let library = directory.path().join("library");
        let valid = library.join("已归档的文章");
        fs::create_dir_all(&valid).unwrap();
        let archived = valid.join("article.html");
        fs::write(&archived, "archived article").unwrap();
        assert_eq!(article_file(&library, "已归档的文章").unwrap(), archived);

        let outside = directory.path().join("outside");
        fs::create_dir(&outside).unwrap();
        fs::write(outside.join("article.html"), "unrelated article").unwrap();
        std::os::unix::fs::symlink(&outside, library.join("Shortcut")).unwrap();
        assert!(article_file(&library, "../outside").is_err());
        assert!(article_file(&library, "Shortcut").is_err());
    }

    #[test]
    fn cancellation_before_generate_is_consumed_by_its_request_only() {
        let generation = Generation::default();
        generation.cancel("early").unwrap();
        assert_eq!(generation.begin("early").unwrap_err(), "已取消生成");
        generation.begin("next").unwrap();
        assert!(!generation.finish("next"));
    }

    #[test]
    fn cancellation_during_startup_prevents_spawning() {
        let generation = Generation::default();
        generation.begin("starting").unwrap();
        generation.cancel("starting").unwrap();
        let mut command = Command::new("/this-command-must-not-run");
        assert_eq!(generation.spawn("starting", &mut command).err().unwrap(), "已取消生成");
        assert!(generation.finish("starting"));
        generation.begin("retry").unwrap();
        assert!(!generation.finish("retry"));
    }

    #[test]
    fn generation_cancellation_kills_a_term_resistant_descendant_after_the_leader_exits() {
        let directory = tempfile::tempdir().unwrap();
        let pid_file = directory.path().join("pids");
        let script = r#"
            /bin/sh -c 'trap "" TERM; printf "%s %s\n" "$2" "$$" > "$1"; exec sleep 60' child "$1" "$$" >/dev/null &
            wait
        "#;
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let generation = std::sync::Arc::new(Generation::default());
            generation.begin("stubborn").unwrap();
            let worker = generation.clone();
            let mut command = query_test_command(script, &pid_file);
            let running = tokio::spawn(async move {
                let channel = super::Channel::new(|_| Ok(()));
                let result = super::run_generation(&worker, "stubborn", &mut command, channel).await;
                assert!(worker.finish("stubborn"));
                result
            });
            let pids = query_process_ids(&pid_file, 2).await;
            let grace = Duration::from_millis(150);
            let started = Instant::now();
            generation.cancel_with_grace("stubborn", grace).unwrap();
            assert!(generation.begin("next").is_err());
            let failure = tokio::time::timeout(grace + Duration::from_millis(750), running)
                .await.expect("generation did not finish within the cancellation limit")
                .unwrap().err().expect("a cancelled generation must not succeed");
            assert_eq!(failure.code, "cancelled");
            assert!(started.elapsed() >= grace);
            assert_query_processes_stopped(&pids).await;
            generation.begin("next").unwrap();
            assert!(!generation.finish("next"));
        });
    }

    #[test]
    fn app_exit_kills_a_term_resistant_sidecar_without_the_async_supervisor() {
        use std::os::unix::process::ExitStatusExt;

        let directory = tempfile::tempdir().unwrap();
        let pid_file = directory.path().join("pid");
        let script = r#"trap '' TERM; printf '%s\n' "$$" > "$1"; exec sleep 60"#;
        tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
            let generation = Generation::default();
            generation.begin("exit").unwrap();
            let mut command = query_test_command(script, &pid_file);
            let (mut process, _) = generation.spawn("exit", &mut command).unwrap();
            query_process_ids(&pid_file, 1).await;
            let grace = Duration::from_millis(100);
            let started = Instant::now();
            generation.cancel_active_on_exit(grace);
            let exit_status = tokio::time::timeout(Duration::from_millis(750), process.child.wait()).await.unwrap().unwrap();
            assert_eq!(exit_status.signal(), Some(libc::SIGKILL));
            assert!(started.elapsed() >= grace && started.elapsed() < grace + Duration::from_millis(750));
            assert!(generation.finish("exit"));
            assert!(generation.begin("later").is_err());
        });
    }

    #[test]
    fn late_cancellation_cannot_cancel_a_subsequent_generation() {
        let generation = Generation::default();
        generation.begin("finished").unwrap();
        assert!(!generation.finish("finished"));
        generation.cancel("finished").unwrap();
        generation.begin("next").unwrap();
        generation.cancel("finished").unwrap();
        assert!(!generation.finish("next"));
        assert!(generation.0.lock().unwrap().cancelled_requests.is_empty());
    }

    #[test]
    fn rejected_concurrent_request_preserves_active_generation() {
        let generation = Generation::default();
        generation.begin("active").unwrap();
        assert_eq!(generation.begin("other").unwrap_err(), "已有文章正在生成");
        generation.cancel("other").unwrap();
        assert!(!generation.finish("active"));
    }

    #[test]
    fn progress_preserves_optional_source_metadata() {
        let event: SidecarEvent = serde_json::from_value(serde_json::json!({
            "type": "progress", "stage": "saved_transcript", "message": "已保存原始字幕",
            "metadata": {"title": "真实标题", "platform": "local", "durationMs": 2000}
        }))
        .unwrap();
        let SidecarEvent::Progress { progress } = event else {
            panic!("expected progress")
        };
        let value = serde_json::to_value(progress).unwrap();
        assert_eq!(value["metadata"]["title"], "真实标题");
        assert_eq!(value["metadata"]["durationMs"], 2000);
        assert!(value["metadata"]["thumbnailUrl"].is_null());
    }

    #[test]
    fn actual_python_progress_preserves_request_counts_previews_and_failures() {
        let events: Vec<SidecarEvent> = serde_json::from_str(include_str!("../../tests/fixtures/generation-progress.json")).unwrap();
        let mut sequence = 0;
        let mut preview_seen = false;
        let mut validated = false;
        let mut failure_seen = false;
        for event in events {
            match event {
                SidecarEvent::Progress { progress } => {
                    if let Some(id) = progress.preview_id.as_deref() {
                        super::preview_id(id).unwrap();
                        assert!(progress.metadata.is_some());
                        preview_seen = true;
                    }
                    if let Some(request) = progress.request {
                        assert!(request.sequence > sequence);
                        sequence = request.sequence;
                        assert!(request.id.starts_with("request-"));
                        assert!(request.active <= 4);
                        assert!(matches!(request.stage.as_deref(), Some("analysis" | "polish" | "publication")));
                        validated |= request.phase == "complete" && request.completed > 0;
                    }
                }
                SidecarEvent::Error { code, stage, retryable, action, .. } => {
                    assert_eq!(code.as_deref(), Some("model_timeout"));
                    assert_eq!(stage.as_deref(), Some("polish"));
                    assert_eq!(retryable, Some(true));
                    assert_eq!(action.as_deref(), Some("retry"));
                    failure_seen = true;
                }
                SidecarEvent::Completed { .. } => panic!("fixture must retain the failed task"),
            }
        }
        assert!(preview_seen && validated && failure_seen && sequence > 0);
    }

    #[test]
    fn deleting_article_removes_only_its_archived_folder() {
        let directory = tempfile::tempdir().unwrap();
        let library = directory.path().join("library");
        let article_dir = library.join("good");
        fs::create_dir_all(&article_dir).unwrap();
        fs::write(article_dir.join("article.html"), "ok").unwrap();
        fs::write(article_dir.join("transcript.json"), "{}").unwrap();
        let outsider = directory.path().join("unrelated");
        fs::create_dir_all(&outsider).unwrap();
        fs::write(outsider.join("article.html"), "keep").unwrap();
        assert!(remove_article_files(&library, "../unrelated").is_err());
        remove_article_files(&library, "good").unwrap();
        assert!(!article_dir.exists());
        assert!(outsider.join("article.html").exists());
    }

    #[test]
    fn api_urls_reject_http_remote_and_embedded_tokens() {
        assert!(validated_api_base_url("https://api.deepseek.com/v1").is_ok());
        assert!(validated_api_base_url("http://127.0.0.1:8000/v1").is_ok());
        assert!(validated_api_base_url("http://[::1]:8000/v1").is_ok());
        assert!(validated_api_base_url("http://[::2]/v1").is_err());
        assert!(validated_api_base_url("http://example.com/v1").is_err());
        assert!(validated_api_base_url("https://user:pass@api.example.com/v1").is_err());
        assert!(validated_api_base_url("https://api.example.com/v1?key=secret").is_err());
    }

    #[test]
    fn preview_preserves_script_strings_containing_html() {
        let original = "<body><script>const template = '</body>';</script></body>";
        let preview = preview_document(original.to_owned());
        assert!(preview.starts_with("<body><script>const template = '</body>';</script>"));
        assert!(preview.ends_with("</script>\n</body>"));
    }

    #[test]
    fn reader_cache_is_preferred_only_for_a_valid_archive() {
        let directory = tempfile::tempdir().unwrap();
        let folder = directory.path().join("article");
        fs::create_dir_all(&folder).unwrap();
        let original = folder.join("article.html");
        let cached = folder.join(".reader.html");
        fs::write(&original, "original").unwrap();
        assert_eq!(super::rendered_article_file(directory.path(), "article").unwrap(), original);
        fs::write(&cached, "upgraded").unwrap();
        assert_eq!(super::rendered_article_file(directory.path(), "article").unwrap(), cached);
        fs::remove_file(&cached).unwrap();
        std::os::unix::fs::symlink(&original, &cached).unwrap();
        assert!(super::rendered_article_file(directory.path(), "article").is_err());
        assert!(super::rendered_article_file(directory.path(), "../article").is_err());
    }

    #[test]
    fn shownotes_links_open_only_explicit_web_urls() {
        assert!(super::external_url("https://example.com/episode?chapter=2#references").is_ok());
        assert!(super::external_url("http://example.com/source").is_ok());
        for value in ["javascript:alert(1)", "file:///tmp/test", "data:text/html,test",
                      "https://user:secret@example.com/", "https://exam\nple.com/"] {
            assert!(super::external_url(value).is_err(), "accepted {value}");
        }
    }

    #[test]
    fn older_article_records_default_to_unread_and_unstarred() {
        let value = serde_json::json!({
            "id":"test", "title":"Title", "category":"", "platform":"local", "updatedAt":1,
        });
        let article: super::Article = serde_json::from_value(value).unwrap();
        assert!(!article.is_read && !article.is_starred);
        let encoded = serde_json::to_value(article).unwrap();
        assert_eq!(encoded["isRead"], false);
        assert_eq!(encoded["isStarred"], false);
    }

    #[test]
    fn archive_deletion_waits_for_sidecar_mutations() {
        use std::os::fd::AsRawFd;
        let directory = tempfile::tempdir().unwrap();
        let library = directory.path().to_owned();
        let folder = library.join("article");
        fs::create_dir(&folder).unwrap();
        fs::write(folder.join("article.html"), "keep until unlocked").unwrap();
        let lock = fs::OpenOptions::new().read(true).write(true).create(true)
            .open(library.join(".article-state.lock")).unwrap();
        assert_eq!(unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX) }, 0);
        let (started, waiting) = std::sync::mpsc::channel();
        let (finished, result) = std::sync::mpsc::channel();
        let worker = std::thread::spawn(move || {
            started.send(()).unwrap();
            finished.send(remove_article_files(&library, "article")).unwrap();
        });
        waiting.recv_timeout(Duration::from_secs(2)).unwrap();
        assert!(result.recv_timeout(Duration::from_millis(50)).is_err());
        assert!(folder.join("article.html").is_file());
        drop(lock);
        result.recv_timeout(Duration::from_secs(2)).unwrap().unwrap();
        worker.join().unwrap();
        assert!(!folder.exists());
    }

    #[test]
    fn youtube_cookie_args_apply_only_to_explicit_youtube_sources() {
        let dir = tempfile::tempdir().unwrap();
        let cookies_txt = dir.path().join("cookies.txt");
        fs::write(&cookies_txt, "# Netscape\n").unwrap();

        let browser = super::YoutubeCookieOptions {
            mode: "browser".into(), browser: "Chrome".into(), file_path: String::new(),
        };
        assert_eq!(
            super::youtube_cookie_args("https://www.youtube.com/watch?v=1-Fo8McCD9w", Some(&browser)).unwrap(),
            vec!["--cookies-from-browser", "chrome"],
        );
        // Non-YouTube links ignore YouTube cookie settings cleanly.
        assert!(super::youtube_cookie_args("https://www.bilibili.com/video/BV1rxqmBhE91", Some(&browser)).unwrap().is_empty());

        let file = super::YoutubeCookieOptions {
            mode: "file".into(), browser: String::new(), file_path: cookies_txt.to_string_lossy().into_owned(),
        };
        assert_eq!(
            super::youtube_cookie_args("https://youtu.be/1-Fo8McCD9w", Some(&file)).unwrap(),
            vec!["--cookies".to_owned(), cookies_txt.to_string_lossy().into_owned()],
        );

        let symlink = dir.path().join("link.txt");
        std::os::unix::fs::symlink(&cookies_txt, &symlink).unwrap();
        let bad_link = super::YoutubeCookieOptions {
            mode: "file".into(), browser: String::new(), file_path: symlink.to_string_lossy().into_owned(),
        };
        assert!(super::youtube_cookie_args("https://www.youtube.com/watch?v=1-Fo8McCD9w", Some(&bad_link)).is_err());

        let bad_browser = super::YoutubeCookieOptions {
            mode: "browser".into(), browser: "unknown".into(), file_path: String::new(),
        };
        assert!(super::youtube_cookie_args("https://www.youtube.com/watch?v=1-Fo8McCD9w", Some(&bad_browser)).is_err());
    }

    #[test]
    fn local_cover_protocol_serves_regular_cover_and_rejects_symlinks_and_traversal() {
        let dir = tempfile::tempdir().unwrap();
        let library = dir.path().join("library");
        let article_dir = library.join("示例 [youtube-1-Fo8McCD9w]");
        fs::create_dir_all(&article_dir).unwrap();
        fs::write(article_dir.join("article.html"), "<html></html>").unwrap();
        let png = b"\x89PNG\r\n\x1a\n12345678";
        fs::write(article_dir.join("cover.png"), png).unwrap();

        let locations = Locations {
            data: dir.path().join("data"),
            library: library.clone(),
            english: std::sync::atomic::AtomicBool::new(false),
        };
        let uri: tauri::http::Uri = "linkread-reader://localhost/cover/%E7%A4%BA%E4%BE%8B%20%5Byoutube-1-Fo8McCD9w%5D?v=1".parse().unwrap();
        let response = super::preview_response(&locations, &uri);
        assert_eq!(response.status(), tauri::http::StatusCode::OK);
        assert_eq!(response.headers().get(tauri::http::header::CONTENT_TYPE).unwrap(), "image/png");
        assert_eq!(response.headers().get(tauri::http::header::X_CONTENT_TYPE_OPTIONS).unwrap(), "nosniff");
        assert_eq!(response.body().as_slice(), png);

        let traversal: tauri::http::Uri = "linkread-reader://localhost/cover/..%2Fsecret".parse().unwrap();
        assert_eq!(super::preview_response(&locations, &traversal).status(), tauri::http::StatusCode::NOT_FOUND);

        // Symlinked cover must never be served.
        fs::remove_file(article_dir.join("cover.png")).unwrap();
        let external = dir.path().join("secret.jpg");
        fs::write(&external, b"\xff\xd8\xff\xe0secret").unwrap();
        std::os::unix::fs::symlink(&external, article_dir.join("cover.jpg")).unwrap();
        assert_eq!(super::preview_response(&locations, &uri).status(), tauri::http::StatusCode::NOT_FOUND);
    }

    #[test]
    fn deleting_article_and_failed_task_clean_all_related_disk_caches_safely() {
        let dir = tempfile::tempdir().unwrap();
        let library = dir.path().join("library");
        let data = dir.path().join("data");
        let key = "youtube-1-Fo8McCD9w";
        let other_key = "youtube-2-OtherVid9w";
        let preview_id = "a".repeat(64);
        let other_preview = "b".repeat(64);

        // Set up an unfinished workspace + staging + preview + ASR cache + managed pasted subtitle.
        let unfinished = library.join(format!("Video [{key}]"));
        fs::create_dir_all(unfinished.join(".ai-checkpoints")).unwrap();
        fs::write(unfinished.join("transcript.json"), "{}").unwrap();
        fs::write(unfinished.join(".ai-checkpoints/batch.json"), "{}").unwrap();

        let stage = library.join(".generation-staging/stage-one");
        fs::create_dir_all(&stage).unwrap();
        fs::write(stage.join("identity.json"), serde_json::json!({"version": 1, "sourceKey": key}).to_string()).unwrap();

        let other_stage = library.join(".generation-staging/stage-other");
        fs::create_dir_all(&other_stage).unwrap();
        fs::write(other_stage.join("identity.json"), serde_json::json!({"version": 1, "sourceKey": other_key}).to_string()).unwrap();

        let preview = library.join(".transcript-previews").join(&preview_id);
        fs::create_dir_all(&preview).unwrap();
        fs::write(preview.join("request.json"), serde_json::json!({"version": 1, "sourceKey": key, "requests": []}).to_string()).unwrap();

        let keep_preview = library.join(".transcript-previews").join(&other_preview);
        fs::create_dir_all(&keep_preview).unwrap();
        fs::write(keep_preview.join("request.json"), serde_json::json!({"version": 1, "sourceKey": other_key, "requests": []}).to_string()).unwrap();

        let asr_dir = library.join(".asr/fp-one");
        fs::create_dir_all(&asr_dir).unwrap();
        fs::write(asr_dir.join("transcript.json"), serde_json::json!({"source": {"url": "https://www.youtube.com/watch?v=1-Fo8McCD9w"}}).to_string()).unwrap();

        let managed_import = data.join("subtitle-imports").join(&preview_id);
        fs::create_dir_all(&managed_import).unwrap();
        let pasted_file = managed_import.join("pasted.srt");
        fs::write(&pasted_file, "1\n00:00:00,000 --> 00:00:01,000\nhi\n").unwrap();

        super::remove_task_files(&library, Some(&data), Some(pasted_file.to_str().unwrap()), true, Some(key), Some(&preview_id)).unwrap();
        assert!(!unfinished.exists());
        assert!(!stage.exists());
        assert!(!preview.exists());
        assert!(!asr_dir.exists());
        assert!(!managed_import.exists());
        assert!(other_stage.exists());
        assert!(keep_preview.exists());

        // Failed regeneration task must NEVER delete a completed article with article.html.
        let published_id = format!("Published [{other_key}]");
        let published = library.join(&published_id);
        fs::create_dir_all(&published).unwrap();
        fs::write(published.join("article.html"), "<html>ok</html>").unwrap();
        fs::write(published.join("cover.jpg"), b"\xff\xd8\xff\xe0").unwrap();
        super::remove_task_files(&library, Some(&data), None, false, Some(other_key), None).unwrap();
        assert!(published.join("article.html").exists());
        assert!(!other_stage.exists());
        assert!(!keep_preview.exists());

        // Deleting the published article removes the folder, backup, and reading position.
        let backup = library.join(".linkread-backups").join(&published_id);
        fs::create_dir_all(&backup).unwrap();
        super::durable_state::save_position(&data, "youtube:2-OtherVid9w", &serde_json::json!({
            "version": 1, "updatedAt": 1000, "activeTab": "summary", "tabs": {}, "mindmap": null,
        })).unwrap();
        remove_article_files(&library, &published_id).unwrap();
        super::durable_state::delete_position(&data, "youtube:2-OtherVid9w").unwrap();
        assert!(!published.exists());
        assert!(!backup.exists());
        assert_eq!(super::durable_state::load_position(&data, "youtube:2-OtherVid9w").unwrap(), serde_json::Value::Null);
    }
}

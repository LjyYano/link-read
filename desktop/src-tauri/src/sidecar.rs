use std::{
    collections::{HashSet, VecDeque},
    env,
    ffi::OsString,
    fs,
    future::{poll_fn, Future},
    io,
    os::unix::{fs::PermissionsExt, process::CommandExt},
    path::{Path, PathBuf},
    pin::pin,
    process::{ExitStatus, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex, MutexGuard,
    },
    task::Poll,
    time::{Duration, Instant},
};

use serde::de::DeserializeOwned;
use tauri::ipc::Channel;
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, BufReader},
    process::{Child, ChildStdin, Command},
    sync::oneshot,
};

use crate::{
    credentials,
    types::{
        details, preview_id, subtitle_file, validated_api_base_url, video_url,
        youtube_cookie_args, AppResult, Article, AsrOptions, GenerationFailure, GenerationResult,
        Progress, Provider, SidecarEvent, YoutubeCookieOptions,
    },
};

pub const GENERATION_CANCEL_GRACE: Duration = Duration::from_secs(5);
pub const MAX_SIDECAR_STDOUT: u64 = 64 * 1024 * 1024;

pub fn lock_unpoisoned<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|error| error.into_inner())
}

pub struct Locations {
    pub data: PathBuf,
    pub library: PathBuf,
    pub english: AtomicBool,
}

impl Locations {
    pub fn language(&self) -> &'static str {
        if self.english.load(Ordering::Relaxed) {
            "en"
        } else {
            "zh-CN"
        }
    }
}

#[derive(Default)]
pub struct ActiveQueries(Mutex<HashSet<i32>>);

impl ActiveQueries {
    fn register(&self, pgid: i32) {
        if pgid > 0 {
            lock_unpoisoned(&self.0).insert(pgid);
        }
    }

    fn unregister(&self, pgid: i32) {
        if pgid > 0 {
            lock_unpoisoned(&self.0).remove(&pgid);
        }
    }

    pub fn cancel_all_on_exit(&self, grace: Duration) {
        let pgids: Vec<i32> = lock_unpoisoned(&self.0).drain().collect();
        if pgids.is_empty() {
            return;
        }
        for &pgid in &pgids {
            let _ = signal_process_group(pgid, libc::SIGTERM);
        }
        let deadline = Instant::now() + grace;
        while Instant::now() < deadline {
            if pgids.iter().all(|&pgid| !is_process_group_alive(pgid)) {
                return;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
        for &pgid in &pgids {
            let _ = signal_process_group(pgid, libc::SIGKILL);
        }
    }
}

#[derive(Default)]
pub struct Generation(pub Mutex<GenerationState>);

#[derive(Default)]
pub struct GenerationState {
    pub active: Option<ActiveProcess>,
    pub updating: bool,
    pub exiting: bool,
    pub cancelled_requests: VecDeque<String>,
    pub finished_requests: VecDeque<String>,
}

pub struct ActiveProcess {
    pub request_id: String,
    pub source_key: Option<String>,
    pub pid: Option<i32>,
    pub leader_status: Option<ExitStatus>,
    pub cancel_deadline: Option<Instant>,
    pub cancel_notice: Option<oneshot::Sender<Instant>>,
}

pub struct GenerationChild<'a> {
    pub generation: &'a Generation,
    pub request_id: &'a str,
    pub child: Child,
    pub stdin: Option<ChildStdin>,
}

impl Drop for GenerationChild<'_> {
    fn drop(&mut self) {
        // Close parent watchdog stdin first, then clear ownership while child pins its PID.
        self.stdin.take();
        self.generation.kill_owned_group(self.request_id, false);
    }
}

pub struct UpdateInstall<'a> {
    pub generation: &'a Generation,
}

impl Drop for UpdateInstall<'_> {
    fn drop(&mut self) {
        lock_unpoisoned(&self.generation.0).updating = false;
    }
}

impl Generation {
    pub fn begin_update(&self) -> AppResult<UpdateInstall<'_>> {
        let mut state = lock_unpoisoned(&self.0);
        if state.active.is_some() || state.updating || state.exiting {
            return Err("update_busy".into());
        }
        state.updating = true;
        Ok(UpdateInstall { generation: self })
    }

    pub fn updating(&self) -> bool {
        lock_unpoisoned(&self.0).updating
    }

    #[cfg(test)]
    pub fn begin(&self, request_id: &str) -> AppResult<()> {
        self.begin_with_source(request_id, None)
    }

    pub fn begin_with_source(
        &self,
        request_id: &str,
        source_key: Option<String>,
    ) -> AppResult<()> {
        validate_request_id(request_id)?;
        let mut state = lock_unpoisoned(&self.0);
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
            leader_status: None,
            cancel_deadline: None,
            cancel_notice: None,
        });
        Ok(())
    }

    pub fn spawn<'a>(
        &'a self,
        request_id: &'a str,
        command: &mut Command,
    ) -> AppResult<(GenerationChild<'a>, oneshot::Receiver<Instant>)> {
        let mut state = lock_unpoisoned(&self.0);
        let active = state
            .active
            .as_mut()
            .filter(|active| active.request_id == request_id)
            .ok_or_else(|| "找不到正在启动的生成任务".to_owned())?;
        if active.cancel_deadline.is_some() {
            return Err("已取消生成".into());
        }
        command.as_std_mut().process_group(0);
        let mut child = command
            .kill_on_drop(true)
            .spawn()
            .map_err(|error| details("无法启动 LinkRead 核心", error))?;
        let stdin = child.stdin.take();
        let (notice, cancelled) = oneshot::channel();
        active.pid = child.id().map(|pid| pid as i32);
        active.cancel_notice = Some(notice);
        Ok((
            GenerationChild {
                generation: self,
                request_id,
                child,
                stdin,
            },
            cancelled,
        ))
    }

    pub fn try_reap(&self, request_id: &str, child: &mut Child) -> io::Result<Option<ExitStatus>> {
        let mut state = lock_unpoisoned(&self.0);
        let active = state
            .active
            .as_mut()
            .filter(|active| active.request_id == request_id)
            .ok_or_else(|| io::Error::other("generation is no longer active"))?;
        if active.cancel_deadline.is_some() {
            // If we already reaped an exited leader while a stubborn descendant remained,
            // wait only until the process group empties or the cancellation deadline fires.
            let status = match active.leader_status {
                Some(status) => Some(status),
                None => child.try_wait()?,
            };
            let Some(status) = status else {
                return Ok(None);
            };
            if active
                .pid
                .is_some_and(is_process_group_alive)
            {
                active.leader_status = Some(status);
                return Ok(None);
            }
            active.pid = None;
            return Ok(Some(status));
        }
        let status = child.try_wait();
        if !matches!(status, Ok(None)) {
            active.pid = None;
        }
        status
    }

    pub fn is_cancelling(&self, request_id: &str) -> bool {
        lock_unpoisoned(&self.0)
            .active
            .as_ref()
            .is_some_and(|active| active.request_id == request_id && active.cancel_deadline.is_some())
    }

    pub fn finish(&self, request_id: &str) -> bool {
        let mut state = lock_unpoisoned(&self.0);
        let cancelled = if state
            .active
            .as_ref()
            .is_some_and(|active| active.request_id == request_id)
        {
            state
                .active
                .take()
                .is_some_and(|active| active.cancel_deadline.is_some())
        } else {
            false
        };
        remember_request(&mut state.finished_requests, request_id);
        cancelled
    }

    pub fn cancel(&self, request_id: &str) -> AppResult<()> {
        self.cancel_with_grace(request_id, GENERATION_CANCEL_GRACE)
    }

    pub fn cancel_with_grace(&self, request_id: &str, grace: Duration) -> AppResult<()> {
        validate_request_id(request_id)?;
        let mut state = lock_unpoisoned(&self.0);
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

    pub fn kill_owned_group(&self, request_id: &str, cancelled_only: bool) {
        let mut state = lock_unpoisoned(&self.0);
        if let Some(active) = state.active.as_mut().filter(|active| {
            active.request_id == request_id && (!cancelled_only || active.cancel_deadline.is_some())
        }) {
            if let Some(pid) = active.pid.take() {
                let _ = signal_process_group(pid, libc::SIGKILL);
            }
        }
    }

    pub fn cancel_active_on_exit(&self, grace: Duration) {
        let pending = {
            let mut state = lock_unpoisoned(&self.0);
            state.exiting = true;
            state.active.as_mut().and_then(|active| {
                if stop_process(active, grace).is_err() {
                    mark_cancelled(active, grace);
                }
                active
                    .pid
                    .zip(active.cancel_deadline)
                    .map(|(pid, deadline)| (active.request_id.clone(), pid, deadline))
            })
        };
        if let Some((request_id, pid, deadline)) = pending {
            while Instant::now() < deadline {
                let done = {
                    let state = lock_unpoisoned(&self.0);
                    let still_pinned = state
                        .active
                        .as_ref()
                        .is_some_and(|active| active.request_id == request_id && active.pid.is_some());
                    !still_pinned || !is_process_group_alive(pid)
                };
                if done {
                    return;
                }
                std::thread::sleep(Duration::from_millis(20));
            }
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

fn is_process_group_alive(pid: i32) -> bool {
    if pid <= 0 {
        return false;
    }
    if unsafe { libc::kill(-pid, 0) } == 0 {
        return true;
    }
    io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH)
}

fn signal_process_group(pid: i32, signal: i32) -> io::Result<()> {
    // The sidecar and the CLI processes it starts share this process group.
    if unsafe { libc::kill(-pid, signal) } != 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() != Some(libc::ESRCH) {
            return Err(error);
        }
    }
    Ok(())
}

fn mark_cancelled(active: &mut ActiveProcess, grace: Duration) {
    let deadline = Instant::now() + grace;
    active.cancel_deadline = Some(deadline);
    if let Some(notice) = active.cancel_notice.take() {
        let _ = notice.send(deadline);
    }
}

fn stop_process(active: &mut ActiveProcess, grace: Duration) -> AppResult<()> {
    if active.cancel_deadline.is_none() {
        if let Some(pid) = active.pid {
            signal_process_group(pid, libc::SIGTERM)
                .map_err(|error| details("无法取消生成，请重试", error))?;
        }
        mark_cancelled(active, grace);
    }
    Ok(())
}

pub fn command_paths() -> Vec<PathBuf> {
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

pub fn executable_file(path: &Path) -> bool {
    path.is_absolute()
        && fs::metadata(path)
            .is_ok_and(|metadata| metadata.is_file() && metadata.permissions().mode() & 0o111 != 0)
}

pub fn tool_path(provider: Provider, custom: &str) -> Option<PathBuf> {
    if matches!(provider, Provider::Api) {
        return None;
    }
    if !custom.trim().is_empty() {
        let path = PathBuf::from(custom.trim());
        return executable_file(&path).then_some(path);
    }
    command_paths()
        .into_iter()
        .map(|directory| directory.join(provider.command()))
        .find(|path| executable_file(path))
}

fn release_sidecar_path(exe: &Path) -> Option<PathBuf> {
    let onedir = exe
        .parent()
        .and_then(|parent| parent.parent())
        .map(|contents| contents.join("Resources/sidecar/linkread-sidecar"));
    if let Some(candidate) = onedir.filter(|path| executable_file(path)) {
        return Some(candidate);
    }
    let flat = exe.with_file_name("linkread-sidecar");
    executable_file(&flat).then_some(flat)
}

pub fn sidecar(locations: &Locations) -> AppResult<Command> {
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
        let exe = env::current_exe().map_err(|error| details("找不到 LinkRead 应用路径", error))?;
        let Some(path) = release_sidecar_path(&exe) else {
            return Err("应用缺少内置的 LinkRead 核心，请使用完整的 LinkRead.app".into());
        };
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
        .env_remove("LINKREAD_API_TOKEN")
        .env_remove("LINKREAD_ASR_TOKEN")
        .env("PYTHONUTF8", "1")
        .env("LINKREAD_UI_LANGUAGE", locations.language())
        .env("LINKREAD_WATCH_PARENT", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .process_group(0);
    Ok(Command::from(command))
}

/// A packaged sidecar can spawn child workers. Dropping only the direct Child
/// can leave descendants running after a timeout or future cancellation.
pub struct QueryProcessGroup(pub(crate) Option<i32>);

impl QueryProcessGroup {
    pub fn disarm(&mut self) {
        self.0 = None;
    }
}

impl Drop for QueryProcessGroup {
    fn drop(&mut self) {
        if let Some(pgid) = self.0.filter(|pid| *pid > 0) {
            unsafe {
                libc::kill(-pgid, libc::SIGKILL);
            }
        }
    }
}

pub struct TrackedQueryGroup<'a> {
    group: QueryProcessGroup,
    pgid: Option<i32>,
    registry: Option<&'a ActiveQueries>,
    stdin: Option<ChildStdin>,
}

impl<'a> TrackedQueryGroup<'a> {
    fn new(pgid: Option<i32>, registry: Option<&'a ActiveQueries>, stdin: Option<ChildStdin>) -> Self {
        if let (Some(pid), Some(active)) = (pgid, registry) {
            active.register(pid);
        }
        Self {
            group: QueryProcessGroup(pgid),
            pgid,
            registry,
            stdin,
        }
    }

    fn disarm(&mut self) {
        self.stdin.take();
        self.group.disarm();
    }
}

impl Drop for TrackedQueryGroup<'_> {
    fn drop(&mut self) {
        self.stdin.take();
        if let (Some(pid), Some(active)) = (self.pgid, self.registry) {
            active.unregister(pid);
        }
    }
}

pub enum QueryOutputError {
    Timeout,
    Oversized,
    Start(io::Error),
}

pub async fn run_command_output<'a>(
    mut command: Command,
    deadline: Duration,
    registry: Option<&'a ActiveQueries>,
) -> Result<(ExitStatus, Vec<u8>, TrackedQueryGroup<'a>), QueryOutputError> {
    command.as_std_mut().process_group(0);
    let mut child = command
        .kill_on_drop(true)
        .spawn()
        .map_err(QueryOutputError::Start)?;
    let stdin = child.stdin.take();
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| QueryOutputError::Start(io::Error::other("missing stdout pipe")))?;
    let pgid = child.id().map(|pid| pid as i32);
    let mut guard = TrackedQueryGroup::new(pgid, registry, stdin);
    let result = tokio::time::timeout(deadline, async {
        let mut bytes = Vec::new();
        stdout
            .take(MAX_SIDECAR_STDOUT + 1)
            .read_to_end(&mut bytes)
            .await
            .map_err(QueryOutputError::Start)?;
        if bytes.len() as u64 > MAX_SIDECAR_STDOUT {
            return Err(QueryOutputError::Oversized);
        }
        let status = child.wait().await.map_err(QueryOutputError::Start)?;
        Ok((status, bytes))
    })
    .await
    .map_err(|_| QueryOutputError::Timeout)??;
    // Close parent-death stdin once child has exited, while keeping the process-group
    // kill guard armed until the caller finishes validating status and JSON output.
    guard.stdin.take();
    Ok((result.0, result.1, guard))
}

pub async fn run_sidecar_check(
    command: Command,
    deadline: Duration,
    registry: Option<&ActiveQueries>,
    timeout_message: &str,
    start_error: &str,
    invalid_output_error: &str,
    fallback_error: &str,
    success_message: &str,
) -> AppResult<crate::types::ApiConnectionCheck> {
    let (status, stdout, mut guard) = run_command_output(command, deadline, registry)
        .await
        .map_err(|error| match error {
            QueryOutputError::Timeout => timeout_message.to_owned(),
            QueryOutputError::Oversized => invalid_output_error.to_owned(),
            QueryOutputError::Start(_) => start_error.to_owned(),
        })?;
    let event: serde_json::Value =
        serde_json::from_slice(&stdout).map_err(|_| invalid_output_error.to_owned())?;
    if !status.success() || event["type"] != "connection" || event["ok"] != true {
        return Err(event["message"]
            .as_str()
            .unwrap_or(fallback_error)
            .to_owned());
    }
    guard.disarm();
    Ok(crate::types::ApiConnectionCheck {
        ok: true,
        message: success_message.into(),
    })
}

pub async fn query_tracked<T: DeserializeOwned>(
    locations: &Locations,
    args: &[&str],
    deadline: Duration,
    timeout_message: &str,
    registry: Option<&ActiveQueries>,
) -> AppResult<T> {
    let mut command = sidecar(locations)?;
    command.args(args);
    query_command_tracked(command, deadline, timeout_message, registry).await
}

#[cfg(test)]
pub async fn query_command<T: DeserializeOwned>(
    command: Command,
    deadline: Duration,
    timeout_message: &str,
) -> AppResult<T> {
    query_command_tracked(command, deadline, timeout_message, None).await
}

pub async fn query_command_tracked<T: DeserializeOwned>(
    command: Command,
    deadline: Duration,
    timeout_message: &str,
    registry: Option<&ActiveQueries>,
) -> AppResult<T> {
    let (status, stdout, mut guard) = run_command_output(command, deadline, registry)
        .await
        .map_err(|error| match error {
            QueryOutputError::Timeout => timeout_message.to_owned(),
            QueryOutputError::Oversized => "无法读取资料库（输出超过上限）".to_owned(),
            QueryOutputError::Start(inner) => details("无法启动 LinkRead 核心", inner),
        })?;
    if !status.success() {
        if let Ok(SidecarEvent::Error { message, .. }) = serde_json::from_slice(&stdout) {
            return Err(message);
        }
        return Err("LinkRead 核心提前退出，请重试".into());
    }
    let response = serde_json::from_slice(&stdout).map_err(|error| details("无法读取资料库", error))?;
    guard.disarm();
    Ok(response)
}

pub async fn run_youtube_cookie_test(
    command: Command,
    deadline: Duration,
    timeout_message: &str,
    registry: Option<&ActiveQueries>,
) -> AppResult<crate::types::YoutubeCookieTestResult> {
    let (status, stdout, mut guard) = run_command_output(command, deadline, registry)
        .await
        .map_err(|error| match error {
            QueryOutputError::Timeout => timeout_message.to_owned(),
            QueryOutputError::Oversized => "无法读取资料库（输出超过上限）".to_owned(),
            QueryOutputError::Start(inner) => details("无法启动 LinkRead 核心", inner),
        })?;
    if !status.success() {
        if let Ok(SidecarEvent::Error { message, code, .. }) = serde_json::from_slice(&stdout) {
            guard.disarm();
            return Ok(crate::types::YoutubeCookieTestResult {
                ok: false,
                message,
                code,
                is_dev: cfg!(debug_assertions),
            });
        }
        return Err("LinkRead 核心提前退出，请重试".into());
    }
    let check: crate::types::ApiConnectionCheck =
        serde_json::from_slice(&stdout).map_err(|error| details("无法读取资料库", error))?;
    guard.disarm();
    Ok(crate::types::YoutubeCookieTestResult {
        ok: check.ok,
        message: check.message,
        code: None,
        is_dev: cfg!(debug_assertions),
    })
}

pub async fn query_command_detailed<T: DeserializeOwned>(
    command: Command,
    deadline: Duration,
    timeout_message: &str,
    registry: Option<&ActiveQueries>,
) -> GenerationResult<T> {
    let (status, stdout, mut guard) = run_command_output(command, deadline, registry)
        .await
        .map_err(|error| match error {
            QueryOutputError::Timeout => GenerationFailure {
                message: timeout_message.to_owned(),
                code: "source_timeout".into(),
                stage: Some("extract".into()),
                retryable: true,
                action: "retry".into(),
            },
            QueryOutputError::Oversized => GenerationFailure::core("无法读取字幕结果（输出超过上限）".into()),
            QueryOutputError::Start(inner) => {
                GenerationFailure::core(details("无法启动 LinkRead 核心", inner))
            }
        })?;
    if !status.success() {
        if let Ok(SidecarEvent::Error {
            message,
            code,
            stage,
            retryable,
            action,
        }) = serde_json::from_slice(&stdout)
        {
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
    let response = serde_json::from_slice(&stdout)
        .map_err(|error| GenerationFailure::core(details("无法读取字幕结果", error)))?;
    guard.disarm();
    Ok(response)
}

#[cfg(test)]
pub fn configure_generation(
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
    configure_generation_with_resume(
        command,
        locations,
        source,
        provider,
        executable,
        base_url,
        model,
        None,
        replace,
        local_file,
        asr,
        None,
        None,
        None,
    )
}

pub fn configure_generation_with_resume(
    mut command: Command,
    locations: &Locations,
    source: &str,
    provider: Provider,
    executable: &str,
    base_url: Option<&str>,
    model: Option<&str>,
    reasoning_effort: Option<&str>,
    replace: bool,
    local_file: bool,
    asr: Option<&AsrOptions>,
    resume_preview: Option<&str>,
    page_language: Option<&str>,
    youtube_cookies: Option<&YoutubeCookieOptions>,
) -> AppResult<Command> {
    let source = if local_file {
        if resume_preview.is_none() {
            subtitle_file(Path::new(source))?;
        }
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
        if !matches!(language, "original" | "en" | "zh-CN") {
            return Err("Unsupported generated page language".into());
        }
        command.args(["--page-language", language]);
    }
    if let Some(id) = resume_preview {
        preview_id(id)?;
        command.args(["--resume-preview", id]);
    }
    if replace {
        command.arg("--replace");
    }
    if local_file {
        command.arg("--local-file");
    }
    if !local_file && resume_preview.is_none() {
        for arg in youtube_cookie_args(&source, youtube_cookies)? {
            command.arg(arg);
        }
    }
    if let Some(config) = asr.filter(|config| config.enabled && !local_file) {
        let asr_url = config.validate()?;
        command.args([
            "--asr",
            "--asr-base-url",
            asr_url.as_str(),
            "--asr-model",
            &config.model,
        ]);
        command
            .arg("--asr-max-minutes")
            .arg(config.max_minutes.to_string());
        command
            .arg("--asr-chunk-minutes")
            .arg(config.chunk_minutes.to_string());
        if !config.ffmpeg_path.is_empty() {
            command.args(["--ffmpeg-path", &config.ffmpeg_path]);
        }
        command.env("LINKREAD_ASR_TOKEN", config.token(locations)?.unwrap_or_default());
    }
    if matches!(provider, Provider::Api) {
        let base = base_url.unwrap_or("").trim();
        let (parsed_base, loopback) = validated_api_base_url(base)?;
        let model = model.unwrap_or("").trim();
        if model.is_empty() || model.len() > 256 || model.chars().any(char::is_control) {
            return Err("请在设置中填写模型名称".into());
        }
        let token = credentials::load(&locations.data)?;
        let resolved_token = match token {
            Some(value) => value,
            None if loopback => String::new(),
            None => return Err("请先在设置中填写 API Token".into()),
        };
        command.args(["--base-url", parsed_base.as_str(), "--model", model]);
        if !resolved_token.is_empty() {
            command.env("LINKREAD_API_TOKEN", resolved_token);
        }
    } else {
        if let Some(model) = model.map(str::trim).filter(|m| !m.is_empty()) {
            if !crate::cli_models::valid_cli_model(model) {
                return Err("请在设置中填写有效的模型名称".into());
            }
            command.args(["--model", model]);
        }
        if let Some(effort) = reasoning_effort.map(str::trim).filter(|e| !e.is_empty()) {
            if !crate::cli_models::valid_reasoning_effort(provider, effort) {
                return Err("不支持的思考深度设置，请在设置中重新选择".into());
            }
            command.args(["--reasoning-effort", effort]);
        }
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

pub fn generation_command(
    locations: &Locations,
    source: &str,
    provider: Provider,
    executable: &str,
    base_url: Option<&str>,
    model: Option<&str>,
    reasoning_effort: Option<&str>,
    replace: bool,
    local_file: bool,
    asr: Option<&AsrOptions>,
    resume_preview: Option<&str>,
    page_language: Option<&str>,
    youtube_cookies: Option<&YoutubeCookieOptions>,
) -> GenerationResult<Command> {
    if let Some(id) = resume_preview {
        preview_id(id).map_err(|e| GenerationFailure::context(e, "source", "source"))?;
    }
    if local_file {
        if resume_preview.is_none() {
            subtitle_file(Path::new(source))
                .map_err(|e| GenerationFailure::context(e, "source", "source"))?;
        } else if !Path::new(source).is_absolute() {
            return Err(GenerationFailure::context(
                "原字幕路径无效".into(),
                "source",
                "source",
            ));
        }
    } else {
        video_url(source).map_err(|e| GenerationFailure::context(e, "source", "source"))?;
    }
    let command = sidecar(locations).map_err(|e| GenerationFailure::context(e, "core_setup", "setup"))?;
    configure_generation_with_resume(
        command,
        locations,
        source,
        provider,
        executable,
        base_url,
        model,
        reasoning_effort,
        replace,
        local_file,
        asr,
        resume_preview,
        page_language,
        youtube_cookies,
    )
    .map_err(|e| GenerationFailure::context(e, "configuration", "settings"))
}

pub async fn run_generation(
    state: &Generation,
    request_id: &str,
    command: &mut Command,
    progress: Channel<Progress>,
) -> GenerationResult<Article> {
    let (mut process, cancelled) = state
        .spawn(request_id, command)
        .map_err(GenerationFailure::core)?;
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
                    Poll::Pending => {}
                }
            }
            work.as_mut().poll(cx).map(Ok)
        })
        .await;
        match first {
            Ok(completed) => Some(completed),
            Err(deadline) => tokio::time::timeout_at(deadline.into(), work.as_mut())
                .await
                .ok(),
        }
    };
    let Some((result, status)) = completed else {
        process.stdin.take();
        state.kill_owned_group(request_id, true);
        let _ = tokio::time::timeout(Duration::from_secs(1), process.child.wait()).await;
        return Err(GenerationFailure::cancelled());
    };
    process.stdin.take();
    if state.is_cancelling(request_id) {
        return Err(GenerationFailure::cancelled());
    }
    let article = result?;
    if !status
        .map_err(|error| GenerationFailure::core(details("无法读取 LinkRead 的退出状态", error)))?
        .success()
    {
        return Err(GenerationFailure::core("LinkRead 核心异常退出，请重试".into()));
    }
    Ok(article)
}

async fn generation_result(
    child: &mut Child,
    channel: Channel<Progress>,
) -> GenerationResult<Article> {
    let stdout = child.stdout.take().expect("piped stdout");
    let mut lines = BufReader::new(stdout).lines();
    let mut result = Err(GenerationFailure::core(
        "LinkRead 核心提前退出，请重试".to_owned(),
    ));
    loop {
        match lines.next_line().await {
            Ok(Some(line)) => match serde_json::from_str::<SidecarEvent>(&line) {
                Ok(SidecarEvent::Progress { progress }) => {
                    let _ = channel.send(progress);
                }
                Ok(SidecarEvent::Error {
                    message,
                    code,
                    stage,
                    retryable,
                    action,
                }) => {
                    result = Err(GenerationFailure {
                        message,
                        code: code.unwrap_or_else(|| "unknown".into()),
                        stage,
                        retryable: retryable.unwrap_or(true),
                        action: action.unwrap_or_else(|| "retry".into()),
                    })
                }
                Ok(SidecarEvent::Completed { article }) => result = Ok(article),
                Err(error) => {
                    result = Err(GenerationFailure::core(details("无法读取生成事件", error)))
                }
            },
            Ok(None) => return result,
            Err(error) => {
                return Err(GenerationFailure::core(details("无法读取 LinkRead 的输出", error)))
            }
        }
    }
}

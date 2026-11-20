//! Local dependency inspection; no sidecar, network access or ASR credentials.
use std::{os::unix::process::CommandExt, path::{Path, PathBuf}, process::Stdio, time::Duration};
use serde::Serialize;
use tokio::{io::AsyncReadExt, process::Command};

const VERSION_LIMIT: u64 = 32 * 1024;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FfmpegStatus {
    pub state: &'static str,
    pub path: Option<PathBuf>,
    pub version: Option<String>,
    pub message: String,
}

pub fn valid_custom_path(path: &str) -> bool {
    path.is_empty() || (path.len() <= 4096 && Path::new(path).is_absolute() && !path.chars().any(char::is_control))
}

fn unavailable(state: &'static str, path: Option<PathBuf>, message: &str) -> FfmpegStatus {
    FfmpegStatus { state, path, version: None, message: message.into() }
}

pub async fn check(custom: &str, paths: Vec<PathBuf>) -> FfmpegStatus {
    if !valid_custom_path(custom) {
        return unavailable("invalid", None, "请选择 FFmpeg 程序的完整路径");
    }
    let candidate = if custom.is_empty() {
        paths.into_iter().map(|path| path.join("ffmpeg")).find(|path| super::executable_file(path))
    } else { Some(PathBuf::from(custom)) };
    let Some(path) = candidate else {
        return unavailable("missing", None, "未找到 FFmpeg，可选择已有程序或查看获取指引");
    };
    if !super::executable_file(&path) {
        return unavailable("invalid", Some(path), "所选文件不存在或没有执行权限，请重新选择 FFmpeg 程序");
    }
    // Resolve common Homebrew and user-created symlinks, retaining the actual
    // executable identity rather than expanding or interpreting a shell string.
    let path = match path.canonicalize() {
        Ok(path) => path,
        Err(_) => return unavailable("invalid", Some(path), "无法访问所选 FFmpeg 程序"),
    };
    match version(&path, Duration::from_secs(5)).await {
        Ok(version) => FfmpegStatus { state: "ready", path: Some(path), version: Some(version), message: "本地检查通过".into() },
        Err(message) => unavailable("invalid", Some(path), &message),
    }
}

async fn version(path: &Path, deadline: Duration) -> Result<String, String> {
    let mut command = Command::new(path);
    command.arg("-version").stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null()).kill_on_drop(true);
    command.as_std_mut().process_group(0);
    let mut child = command.spawn().map_err(|_| "无法运行 FFmpeg，请检查程序架构、执行权限或 macOS 安全性提示".to_owned())?;
    let mut group = super::QueryProcessGroup(child.id().map(|id| id as i32));
    let stdout = child.stdout.take().ok_or("无法读取 FFmpeg 版本")?;
    let output = tokio::time::timeout(deadline, async {
        let mut output = Vec::new();
        stdout.take(VERSION_LIMIT + 1).read_to_end(&mut output).await.map_err(|_| "无法读取 FFmpeg 版本".to_owned())?;
        if output.len() as u64 > VERSION_LIMIT { return Err("程序返回了异常的版本信息，请重新选择 FFmpeg".to_owned()); }
        let status = child.wait().await.map_err(|_| "无法检查 FFmpeg 运行状态".to_owned())?;
        if !status.success() { return Err("FFmpeg 未能正常运行，请检查程序架构、执行权限或 macOS 安全性提示".to_owned()); }
        let text = std::str::from_utf8(&output).map_err(|_| "所选程序未返回有效的 FFmpeg 版本".to_owned())?;
        let first = text.lines().next().unwrap_or_default().trim();
        let version = first.strip_prefix("ffmpeg version ").and_then(|value| value.split_whitespace().next())
            .filter(|value| !value.is_empty() && value.len() <= 128 && value.chars().all(|c| c.is_ascii_graphic()));
        version.map(str::to_owned).ok_or_else(|| "所选程序不是可用的 FFmpeg，请重新选择".to_owned())
    }).await.map_err(|_| "FFmpeg 本地检查超时，请重新选择可运行的程序".to_owned())??;
    group.disarm();
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs, os::unix::fs::PermissionsExt};

    fn executable(folder: &Path, text: &str) -> PathBuf {
        let path = folder.join("ffmpeg");
        fs::write(&path, format!("#!/bin/sh\n{text}\n")).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        path
    }

    #[test]
    fn validates_local_version_and_resolves_selected_path() {
      tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
        let folder = tempfile::tempdir().unwrap();
        let path = executable(folder.path(), "printf 'ffmpeg version 8.0 Copyright test\\n'");
        let result = check(path.to_str().unwrap(), vec![]).await;
        assert_eq!(result.state, "ready");
        assert_eq!(result.version.as_deref(), Some("8.0"));
        assert_eq!(result.path, Some(path.canonicalize().unwrap()));
      });
    }

    #[test]
    fn custom_selection_never_falls_back_to_another_program() {
      tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
        let folder = tempfile::tempdir().unwrap();
        executable(folder.path(), "printf 'ffmpeg version 8.0\\n'");
        assert_eq!(check("/missing/ffmpeg", vec![folder.path().to_path_buf()]).await.state, "invalid");
        assert_eq!(check("", vec![folder.path().to_path_buf()]).await.state, "ready");
        assert_eq!(check("", vec![]).await.state, "missing");
        assert_eq!(check("relative/ffmpeg", vec![]).await.state, "invalid");
      });
    }

    #[test]
    fn rejects_non_executable_wrong_version_failed_and_unbounded_programs() {
      tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(async {
        let folder = tempfile::tempdir().unwrap();
        let path = executable(folder.path(), "printf 'ffprobe version 8.0\\n'");
        assert_eq!(check(path.to_str().unwrap(), vec![]).await.state, "invalid");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
        assert_eq!(check(path.to_str().unwrap(), vec![]).await.state, "invalid");
        executable(folder.path(), "printf 'ffmpeg version 8.0\\n'; exit 1");
        assert_eq!(check(path.to_str().unwrap(), vec![]).await.state, "invalid");
        executable(folder.path(), "while :; do printf 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'; done");
        assert!(version(&path, Duration::from_secs(2)).await.unwrap_err().contains("异常"));
        executable(folder.path(), "sleep 10");
        assert!(version(&path, Duration::from_millis(20)).await.unwrap_err().contains("超时"));
      });
    }
}

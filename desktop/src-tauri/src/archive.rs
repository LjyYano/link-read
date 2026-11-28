use std::{
    fs,
    io::{self, Read},
    os::{fd::AsRawFd, unix::fs::OpenOptionsExt},
    path::Path,
};

use tauri::State;

use crate::{
    durable_state,
    protocol::article_file,
    sidecar::{lock_unpoisoned, Generation, Locations},
    types::{details, preview_id, AppResult},
    video_links,
};

pub fn lock_archive(dir: &Path) -> AppResult<fs::File> {
    let lock = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(dir.join(".article-state.lock"))
        .map_err(|error| details("无法锁定资料库", error))?;
    loop {
        if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX) } == 0 {
            return Ok(lock);
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::Interrupted {
            return Err(details("无法锁定资料库", error));
        }
    }
}

pub fn try_lock_file(path: &Path) -> AppResult<Option<fs::File>> {
    let lock = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .map_err(|error| details("无法锁定暂存目录", error))?;
    loop {
        if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0 {
            return Ok(Some(lock));
        }
        let error = io::Error::last_os_error();
        if error.kind() == io::ErrorKind::Interrupted {
            continue;
        }
        if error.kind() == io::ErrorKind::WouldBlock {
            return Ok(None);
        }
        return Err(details("无法锁定暂存目录", error));
    }
}

pub fn valid_source_key(key: &str) -> bool {
    !key.is_empty()
        && key.len() <= 256
        && !key.starts_with('.')
        && key
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_'))
}

fn folder_source_key(name: &str) -> Option<String> {
    let inner = name.strip_suffix(']')?.rsplit_once(" [")?.1;
    valid_source_key(inner).then(|| inner.to_owned())
}

fn read_bounded_json(path: &Path, limit: u64) -> Option<serde_json::Value> {
    let file = fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .ok()?;
    let meta = file.metadata().ok()?;
    if !meta.is_file() || meta.len() > limit {
        return None;
    }
    let mut bytes = Vec::new();
    file.take(limit + 1).read_to_end(&mut bytes).ok()?;
    if bytes.len() as u64 > limit {
        return None;
    }
    serde_json::from_slice(&bytes).ok()
}

fn transcript_source_key(path: &Path) -> Option<String> {
    let value = read_bounded_json(path, 32 * 1024 * 1024)?;
    let url = value.get("source")?.get("url")?.as_str()?;
    video_links::source_key(url)
}

fn cached_reading_key(folder: &Path) -> Option<String> {
    let value = read_bounded_json(&folder.join(".reader-cache.json"), 64 * 1024)?;
    value
        .get("readingKey")?
        .as_str()
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
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
    let mut resolved_key = source_key
        .filter(|key| valid_source_key(key))
        .map(str::to_owned);
    let previews = library.join(".transcript-previews");
    if resolved_key.is_none() {
        if let Some(pid) = preview_id_value {
            if preview_id(pid).is_ok() && !previews.is_symlink() {
                if let Some(manifest) =
                    read_bounded_json(&previews.join(pid).join("request.json"), 1024 * 1024)
                {
                    if let Some(key) = manifest
                        .get("sourceKey")
                        .and_then(|v| v.as_str())
                        .filter(|k| valid_source_key(k))
                    {
                        resolved_key = Some(key.to_owned());
                    }
                }
            }
        }
    }

    if let Some(key) = resolved_key.as_deref() {
        let stages = library.join(".generation-staging");
        if fs::symlink_metadata(&stages).is_ok_and(|meta| meta.file_type().is_dir()) {
            // Never acquire a blocking staging lock while holding library/.article-state.lock:
            // Python holds per-stage locks while calling archive_mutation(library).
            let legacy_lock_path = stages.join(".article-state.lock");
            let _legacy_guard = if fs::symlink_metadata(&legacy_lock_path).is_ok() {
                match try_lock_file(&legacy_lock_path)? {
                    Some(guard) => Some(guard),
                    None => return Err("暂存目录正在处理中，请等待当前任务结束后再试".into()),
                }
            } else {
                None
            };
            if let Ok(entries) = fs::read_dir(&stages) {
                for entry in entries.flatten() {
                    let name = entry.file_name();
                    let Some(stage_name) = name.to_str() else { continue };
                    if stage_name.starts_with('.') {
                        continue;
                    }
                    let path = entry.path();
                    if fs::symlink_metadata(&path).is_ok_and(|meta| meta.file_type().is_dir()) {
                        let matched = read_bounded_json(&path.join("identity.json"), 64 * 1024)
                            .and_then(|v| {
                                v.get("sourceKey")
                                    .and_then(|k| k.as_str())
                                    .map(|k| k == key)
                            })
                            .unwrap_or(false);
                        if matched {
                            let stage_lock = stages.join(format!(".{stage_name}.lock"));
                            let Some(_stage_guard) = try_lock_file(&stage_lock)? else {
                                return Err(
                                    "该来源的暂存目录正在处理中，请等待当前任务结束后再试".into(),
                                );
                            };
                            let still_matched =
                                read_bounded_json(&path.join("identity.json"), 64 * 1024)
                                    .and_then(|v| {
                                        v.get("sourceKey")
                                            .and_then(|k| k.as_str())
                                            .map(|k| k == key)
                                    })
                                    .unwrap_or(false);
                            if still_matched {
                                fs::remove_dir_all(&path)
                                    .map_err(|error| details("删除暂存缓存失败", error))?;
                            }
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
                            .and_then(|v| {
                                v.get("sourceKey")
                                    .and_then(|k| k.as_str())
                                    .map(|k| k == key)
                            })
                            .unwrap_or(false);
                        if matched {
                            fs::remove_dir_all(&path)
                                .map_err(|error| details("删除原文预览缓存失败", error))?;
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
                            fs::remove_dir_all(&path)
                                .map_err(|error| details("删除语音转写缓存失败", error))?;
                        }
                    }
                }
            }
        }
    }
    Ok(())
}

#[tauri::command]
pub async fn delete_article(
    state: State<'_, Generation>,
    locations: State<'_, Locations>,
    id: String,
    reading_key: Option<String>,
) -> AppResult<()> {
    // Do not delete a workspace while a sidecar might still be using it.
    if lock_unpoisoned(&state.0).active.is_some() {
        return Err("正在生成文章，请在当前任务结束后再删除".into());
    }
    let library = locations.library.clone();
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || {
        let fallback_key = article_file(&library, &id)
            .ok()
            .and_then(|path| path.parent().and_then(cached_reading_key));
        let result = remove_article_files(&library, &id);
        if let Some(key) = reading_key.filter(|k| !k.is_empty()).or(fallback_key) {
            if article_file(&library, &id).is_err() {
                let _ = durable_state::delete_position(&data, &key);
            }
        }
        result
    })
    .await
    .map_err(|error| details("删除文章失败", error))?
}

pub fn remove_article_files(library: &Path, id: &str) -> AppResult<()> {
    let _lock = lock_archive(library)?;
    let path = article_file(library, id)?;
    let folder = path.parent().ok_or("文章目录无效")?;
    let source_key =
        folder_source_key(id).or_else(|| transcript_source_key(&folder.join("transcript.json")));
    fs::remove_dir_all(folder).map_err(|error| details("删除文章失败", error))?;
    let backups = library.join(".linkread-backups");
    if fs::symlink_metadata(&backups).is_ok_and(|meta| meta.file_type().is_dir()) {
        let _ = remove_dir_if_real(&backups.join(id));
    }
    cleanup_source_caches_locked(library, source_key.as_deref(), None)?;
    Ok(())
}

/// Deleting a failed/cancelled task record preserves reusable stage caches
/// (`.generation-staging`, `.ai-checkpoints`, `.asr`, and `.transcript-previews`)
/// per AGENTS.md so retries do not repeat paid ASR or verified AI batches.
pub fn remove_task_files(
    _library: &Path,
    _data: Option<&Path>,
    _source: Option<&str>,
    _local_file: bool,
    _source_key: Option<&str>,
    preview_id_value: Option<&str>,
) -> AppResult<()> {
    if let Some(pid) = preview_id_value {
        preview_id(pid)?;
    }
    Ok(())
}

#[tauri::command]
pub async fn delete_task_data(
    state: State<'_, Generation>,
    locations: State<'_, Locations>,
    source: Option<String>,
    local_file: Option<bool>,
    source_key: Option<String>,
    preview_id_value: Option<String>,
) -> AppResult<()> {
    let is_local = local_file.unwrap_or(false);
    let candidate_key = source_key
        .as_deref()
        .filter(|k| valid_source_key(k))
        .map(str::to_owned)
        .or_else(|| {
            if !is_local {
                source.as_deref().and_then(video_links::source_key)
            } else {
                None
            }
        });
    {
        let guard = lock_unpoisoned(&state.0);
        if let Some(active) = guard.active.as_ref() {
            if active.source_key.is_none()
                || candidate_key.is_none()
                || active.source_key == candidate_key
            {
                return Err("正在处理该来源的任务，请等待当前任务结束后再删除".into());
            }
        }
    }
    let library = locations.library.clone();
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || {
        remove_task_files(
            &library,
            Some(&data),
            source.as_deref(),
            is_local,
            source_key.as_deref(),
            preview_id_value.as_deref(),
        )
    })
    .await
    .map_err(|error| details("删除任务记录失败", error))?
}

//! Private, bounded state. No credentials, arbitrary paths or remote content.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    fs::{self, DirBuilder},
    io::{self, Read, Write},
    os::{
        fd::AsRawFd,
        unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt},
    },
    path::Path,
};

type Result<T> = std::result::Result<T, String>;
const JOURNAL_LIMIT: usize = 1024 * 1024;
const POSITION_LIMIT: usize = 256 * 1024;
const PREFERENCES_LIMIT: usize = 64 * 1024;
const TABS: &[&str] = &["summary", "mindmap", "transcript", "keywords", "highlights", "shownotes"];
const LANES: &[&str] = &[
    "extract", "audio", "upload", "asr", "analysis", "polish", "publication", "translation", "render",
];

enum ReadError {
    Corrupt(String),
    Other(String),
}

impl ReadError {
    fn into_message(self) -> String {
        match self {
            Self::Corrupt(msg) | Self::Other(msg) => msg,
        }
    }
}

fn fields(value: &Value, allowed: &[&str], required: &[&str]) -> bool {
    value.as_object().is_some_and(|o| {
        o.keys().all(|k| allowed.contains(&k.as_str()))
            && required.iter().all(|k| o.contains_key(*k))
    })
}
fn text(value: &Value, max: usize) -> bool {
    value.as_str().is_some_and(|s| s.len() <= max && !s.contains('\0'))
}
fn optional_text(value: &Value, max: usize) -> bool {
    value.is_null() || text(value, max)
}
fn integer(value: &Value, max: u64) -> bool {
    value.as_u64().is_some_and(|v| v <= max)
}
fn number(value: &Value, min: f64, max: f64) -> bool {
    value.as_f64().is_some_and(|v| v.is_finite() && v >= min && v <= max)
}
fn choice(value: &Value, choices: &[&str]) -> bool {
    value.as_str().is_some_and(|v| choices.contains(&v))
}
fn hash_id(value: &Value) -> bool {
    value.as_str().is_some_and(|s| {
        s.len() == 64 && s.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    })
}
fn optional_int(value: &Value) -> bool {
    value.is_null() || integer(value, 1_000_000_000_000_000)
}
fn endpoint(value: &Value) -> bool {
    value
        .as_str()
        .is_some_and(|s| s.len() <= 8192 && (s.is_empty() || super::validated_api_base_url(s).is_ok()))
}

fn metadata(value: &Value) -> bool {
    fields(
        value,
        &["title", "platform", "sourceUrl", "author", "publishedAt", "durationMs", "thumbnailUrl"],
        &[],
    ) && optional_text(&value["title"], 8000)
        && (value["platform"].is_null()
            || choice(&value["platform"], &["local", "bilibili", "youtube", "apple_podcasts"]))
        && optional_text(&value["sourceUrl"], 32768)
        && optional_text(&value["author"], 8000)
        && optional_text(&value["publishedAt"], 512)
        && optional_text(&value["thumbnailUrl"], 8192)
        && optional_int(&value["durationMs"])
}
fn failure(value: &Value) -> bool {
    fields(
        value,
        &["message", "code", "stage", "retryable", "action"],
        &["message", "code", "retryable", "action"],
    ) && text(&value["message"], 8000)
        && text(&value["code"], 128)
        && optional_text(&value["stage"], 128)
        && value["retryable"].is_boolean()
        && choice(&value["action"], &["retry", "settings", "setup", "source", "details"])
}
fn activity(value: &Value) -> bool {
    if !fields(
        value,
        &["startedAt", "lastEventAt", "lanes", "history"],
        &["startedAt", "lastEventAt", "lanes", "history"],
    ) || !integer(&value["startedAt"], 1_000_000_000_000_000)
        || !integer(&value["lastEventAt"], 1_000_000_000_000_000)
    {
        return false;
    }
    let Some(lanes) = value["lanes"].as_object() else { return false };
    if !lanes.iter().all(|(key, lane)| {
        LANES.contains(&key.as_str())
            && fields(
                lane,
                &["label", "message", "state", "startedAt", "updatedAt", "completed", "total"],
                &["label", "message", "state", "startedAt", "updatedAt"],
            )
            && text(&lane["label"], 400)
            && text(&lane["message"], 8000)
            && choice(&lane["state"], &["running", "complete"])
            && integer(&lane["startedAt"], 1_000_000_000_000_000)
            && integer(&lane["updatedAt"], 1_000_000_000_000_000)
            && optional_int(&lane["completed"])
            && optional_int(&lane["total"])
    }) {
        return false;
    }
    value["history"].as_array().is_some_and(|lines| {
        lines.len() <= 12
            && lines.iter().all(|line| {
                fields(line, &["at", "stage", "message"], &["at", "stage", "message"])
                    && integer(&line["at"], 1_000_000_000_000_000)
                    && text(&line["stage"], 128)
                    && text(&line["message"], 8000)
            })
    })
}

pub fn validate_journal(value: &Value) -> Result<()> {
    let invalid = || "任务记录格式无效，已保留原文件".to_owned();
    if !fields(value, &["version", "tasks"], &["version", "tasks"]) || value["version"] != 1 {
        return Err(invalid());
    }
    let tasks = value["tasks"].as_array().ok_or_else(invalid)?;
    if tasks.len() > 50 {
        return Err("最多保留 50 个未完成任务，请先移除不需要的记录".into());
    }
    let mut ids = HashSet::new();
    for task in tasks {
        if !fields(
            task,
            &[
                "id", "createdAt", "updatedAt", "status", "input", "settings", "metadata",
                "progress", "previewId", "failure",
            ],
            &["id", "createdAt", "updatedAt", "status", "input", "settings", "metadata", "progress"],
        ) {
            return Err(invalid());
        }
        let id = task["id"].as_str().ok_or_else(invalid)?;
        if id.is_empty()
            || id.len() > 128
            || !id
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
            || !ids.insert(id)
            || !integer(&task["createdAt"], 1_000_000_000_000_000)
            || !integer(&task["updatedAt"], 1_000_000_000_000_000)
            || !choice(
                &task["status"],
                &["queued", "running", "cancelling", "error", "cancelled", "interrupted"],
            )
            || !metadata(&task["metadata"])
        {
            return Err(invalid());
        }
        let input = &task["input"];
        if !fields(
            input,
            &["source", "localFile", "sourceKey", "replaceRequested"],
            &["source", "localFile", "sourceKey", "replaceRequested"],
        ) || !text(&input["source"], 32768)
            || input["source"].as_str().is_none_or(|s| s.is_empty())
            || !input["localFile"].is_boolean()
            || !optional_text(&input["sourceKey"], 8192)
            || !input["replaceRequested"].is_boolean()
        {
            return Err(invalid());
        }
        let settings = &task["settings"];
        if !fields(
            settings,
            &["provider", "executable", "baseUrl", "model", "asr", "pageLanguage"],
            &["provider", "executable", "baseUrl", "model", "asr"],
        ) || (settings.get("pageLanguage").is_some()
            && !choice(&settings["pageLanguage"], &["original", "zh-CN", "en"]))
            || !choice(&settings["provider"], &["codex", "claude", "api"])
            || settings["executable"]
                .as_str()
                .is_none_or(|s| !super::environment::valid_custom_path(s))
            || !endpoint(&settings["baseUrl"])
            || !text(&settings["model"], 1024)
        {
            return Err(invalid());
        }
        let asr = &settings["asr"];
        if !fields(
            asr,
            &["enabled", "reuseToken", "baseUrl", "model", "maxMinutes", "chunkMinutes", "ffmpegPath"],
            &["enabled", "reuseToken", "baseUrl", "model", "maxMinutes", "chunkMinutes"],
        ) || !asr["enabled"].is_boolean()
            || !asr["reuseToken"].is_boolean()
            || !endpoint(&asr["baseUrl"])
            || !text(&asr["model"], 1024)
            || asr
                .get("ffmpegPath")
                .is_some_and(|v| v.as_str().is_none_or(|s| !super::environment::valid_custom_path(s)))
            || !integer(&asr["maxMinutes"], 720)
            || asr["maxMinutes"] == 0
            || ![json!(0), json!(5), json!(10), json!(15)].contains(&asr["chunkMinutes"])
        {
            return Err(invalid());
        }
        if let Some(progress) = task.get("progress") {
            if !fields(progress, &["step", "percent", "message", "activity"], &["step", "percent", "message"])
                || !integer(&progress["step"], 3)
                || !number(&progress["percent"], 0.0, 99.0)
                || !text(&progress["message"], 8000)
                || progress.get("activity").is_some_and(|v| !activity(v))
            {
                return Err(invalid());
            }
        }
        if task.get("previewId").is_some_and(|v| !hash_id(v))
            || task.get("failure").is_some_and(|v| !failure(v))
        {
            return Err(invalid());
        }
    }
    if serde_json::to_vec(value).map_err(|_| invalid())?.len() > JOURNAL_LIMIT {
        return Err("任务记录过大，请先移除不需要的任务".into());
    }
    Ok(())
}

fn signature(value: &Value) -> bool {
    let Some(s) = value.as_str().filter(|s| s.len() <= 64) else {
        return false;
    };
    let parts: Vec<_> = s.split('-').collect();
    parts.len() == 3
        && parts[0] == "p1"
        && parts[1].len() == 16
        && parts[1]
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        && !parts[2].is_empty()
        && parts[2].bytes().all(|c| c.is_ascii_digit())
}
fn anchor(value: &Value) -> bool {
    match value["kind"].as_str() {
        Some("top") => fields(value, &["kind"], &["kind"]),
        Some("block") => {
            fields(value, &["kind", "token", "occurrence"], &["kind", "token", "occurrence"])
                && signature(&value["token"])
                && integer(&value["occurrence"], 1_000_000)
        }
        Some("cue") => {
            fields(
                value,
                &["kind", "cue", "endCue", "token", "occurrence"],
                &["kind", "cue", "endCue", "token", "occurrence"],
            ) && signature(&value["token"])
                && integer(&value["occurrence"], 1_000_000)
                && integer(&value["cue"], 10_000_000)
                && integer(&value["endCue"], 10_000_000)
                && value["endCue"].as_u64() >= value["cue"].as_u64()
        }
        _ => false,
    }
}
fn node_path(value: &Value) -> bool {
    let Some(path) = value.as_str().filter(|s| s.len() <= 64) else {
        return false;
    };
    let parts: Vec<_> = path.split('.').collect();
    !parts.is_empty()
        && parts.len() <= 7
        && parts[0] == "0"
        && parts
            .iter()
            .skip(1)
            .all(|s| !s.is_empty() && s.bytes().all(|c| c.is_ascii_digit()))
}
pub fn validate_position(value: &Value) -> Result<()> {
    let invalid = || "阅读位置格式无效".to_owned();
    if !fields(
        value,
        &["version", "updatedAt", "activeTab", "tabs", "mindmap"],
        &["version", "updatedAt", "activeTab", "tabs", "mindmap"],
    ) || value["version"] != 1
        || !integer(&value["updatedAt"], 1_000_000_000_000_000)
        || !choice(&value["activeTab"], TABS)
    {
        return Err(invalid());
    }
    let tabs = value["tabs"].as_object().ok_or_else(invalid)?;
    if tabs.len() > 6
        || !tabs.iter().all(|(key, tab)| {
            TABS.contains(&key.as_str())
                && fields(tab, &["signature", "anchor", "offset", "ratio"], &["signature", "anchor", "offset", "ratio"])
                && signature(&tab["signature"])
                && anchor(&tab["anchor"])
                && number(&tab["offset"], -100_000.0, 100_000.0)
                && number(&tab["ratio"], 0.0, 1.0)
        })
    {
        return Err(invalid());
    }
    let map = &value["mindmap"];
    if !map.is_null() {
        if !fields(map, &["signature", "folded", "mode", "viewport"], &["signature", "folded", "mode", "viewport"])
            || !signature(&map["signature"])
            || !choice(&map["mode"], &["overview", "expanded", "custom"])
            || !map["folded"]
                .as_array()
                .is_some_and(|a| a.len() <= 2048 && a.iter().all(node_path))
        {
            return Err(invalid());
        }
        let v = &map["viewport"];
        if !v.is_null()
            && (!fields(v, &["engine", "cx", "cy", "scale"], &["engine", "cx", "cy", "scale"])
                || !choice(&v["engine"], &["markmap", "fallback"])
                || !number(&v["cx"], -10_000_000.0, 10_000_000.0)
                || !number(&v["cy"], -10_000_000.0, 10_000_000.0)
                || !number(&v["scale"], 0.02, 20.0))
        {
            return Err(invalid());
        }
    }
    if serde_json::to_vec(value).map_err(|_| invalid())?.len() > POSITION_LIMIT {
        return Err(invalid());
    }
    Ok(())
}

pub fn validate_preferences(value: &Value) -> Result<()> {
    let invalid = || "应用偏好设置格式无效".to_owned();
    let Some(obj) = value.as_object() else {
        return Err(invalid());
    };
    if obj.keys().any(|k| {
        k.to_ascii_lowercase().contains("token") || k.to_ascii_lowercase().contains("secret")
    }) {
        return Err(invalid());
    }
    if !fields(
        value,
        &["version", "preferences", "language", "theme"],
        &["version"],
    ) || value["version"] != 1
    {
        return Err(invalid());
    }
    if let Some(prefs) = obj.get("preferences") {
        if !prefs.is_null() {
            if !fields(
                prefs,
                &["provider", "paths", "api", "asr", "youtubeCookies"],
                &["provider", "paths", "api", "asr"],
            ) || !choice(&prefs["provider"], &["codex", "claude", "api"])
            {
                return Err(invalid());
            }
            let paths = &prefs["paths"];
            if !fields(paths, &["codex", "claude"], &["codex", "claude"])
                || !text(&paths["codex"], 4096)
                || !text(&paths["claude"], 4096)
            {
                return Err(invalid());
            }
            let api = &prefs["api"];
            if !fields(api, &["baseUrl", "model"], &["baseUrl", "model"])
                || !text(&api["baseUrl"], 8192)
                || !text(&api["model"], 1024)
            {
                return Err(invalid());
            }
            let asr = &prefs["asr"];
            if !fields(
                asr,
                &["enabled", "reuseToken", "baseUrl", "model", "maxMinutes", "chunkMinutes", "ffmpegPath"],
                &["enabled", "reuseToken", "baseUrl", "model", "maxMinutes", "chunkMinutes", "ffmpegPath"],
            ) || !asr["enabled"].is_boolean()
                || !asr["reuseToken"].is_boolean()
                || !text(&asr["baseUrl"], 8192)
                || !text(&asr["model"], 1024)
                || !asr["maxMinutes"].is_number()
                || !asr["chunkMinutes"].is_number()
                || !text(&asr["ffmpegPath"], 4096)
            {
                return Err(invalid());
            }
            if let Some(yc) = prefs.get("youtubeCookies") {
                if !fields(yc, &["mode", "browser", "filePath"], &["mode", "browser", "filePath"])
                    || !choice(&yc["mode"], &["none", "browser", "file"])
                    || !text(&yc["browser"], 64)
                    || !text(&yc["filePath"], 4096)
                {
                    return Err(invalid());
                }
            }
        }
    }
    if let Some(lang) = obj.get("language") {
        if !lang.is_null()
            && (!fields(lang, &["interfaceLanguage", "pageLanguage"], &["interfaceLanguage", "pageLanguage"])
                || !choice(&lang["interfaceLanguage"], &["system", "zh-CN", "en"])
                || !choice(&lang["pageLanguage"], &["original", "zh-CN", "en"]))
        {
            return Err(invalid());
        }
    }
    if let Some(theme) = obj.get("theme") {
        if !theme.is_null() && !choice(theme, &["system", "light", "dark"]) {
            return Err(invalid());
        }
    }
    if serde_json::to_vec(value).map_err(|_| invalid())?.len() > PREFERENCES_LIMIT {
        return Err(invalid());
    }
    Ok(())
}

fn ensure_folder(folder: &Path) -> Result<()> {
    if folder.is_symlink() {
        return Err("状态目录不可使用符号链接".into());
    }
    match fs::symlink_metadata(folder) {
        Ok(meta) if meta.file_type().is_dir() => Ok(()),
        Ok(_) => Err("状态目录路径无效".into()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            DirBuilder::new()
                .recursive(true)
                .mode(0o700)
                .create(folder)
                .map_err(|_| "无法创建本地状态目录".to_owned())?;
            let _ = fs::set_permissions(folder, fs::Permissions::from_mode(0o700));
            Ok(())
        }
        Err(_) => Err("无法访问本地状态目录".into()),
    }
}

fn lock(folder: &Path) -> Result<fs::File> {
    ensure_folder(folder)?;
    let file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(folder.join(".state.lock"))
        .map_err(|_| "无法锁定本地状态".to_owned())?;
    loop {
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) } == 0 {
            return Ok(file);
        }
        let error = io::Error::last_os_error();
        if error.kind() != io::ErrorKind::Interrupted {
            return Err("无法锁定本地状态".into());
        }
    }
}

fn read_inner(path: &Path, limit: usize) -> std::result::Result<Option<Value>, ReadError> {
    let file = match fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
    {
        Ok(file) => file,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(_) => return Err(ReadError::Other("无法读取本地状态，已保留原文件".into())),
    };
    let meta = file
        .metadata()
        .map_err(|_| ReadError::Other("无法检查状态文件".to_owned()))?;
    if !meta.is_file() || meta.len() > limit as u64 {
        return Err(ReadError::Other("状态文件过大或类型无效".into()));
    }
    let mut bytes = Vec::new();
    file.take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| ReadError::Other("无法读取本地状态".to_owned()))?;
    if bytes.len() > limit {
        return Err(ReadError::Other("状态文件过大".into()));
    }
    serde_json::from_slice(&bytes)
        .map(Some)
        .map_err(|_| ReadError::Corrupt("本地状态文件损坏，已保留原文件".to_owned()))
}

fn read(path: &Path, limit: usize) -> Result<Option<Value>> {
    read_inner(path, limit).map_err(ReadError::into_message)
}

fn write(path: &Path, value: &Value) -> Result<()> {
    if path.is_symlink() {
        return Err("状态文件不可使用符号链接".into());
    }
    let folder = path.parent().ok_or("状态目录无效")?;
    let mut file = tempfile::NamedTempFile::new_in(folder).map_err(|_| "无法保存本地状态".to_owned())?;
    file.as_file()
        .set_permissions(fs::Permissions::from_mode(0o600))
        .map_err(|_| "无法保护状态文件".to_owned())?;
    serde_json::to_writer(&mut file, value).map_err(|_| "无法写入本地状态".to_owned())?;
    file.flush()
        .and_then(|_| file.as_file().sync_all())
        .map_err(|_| "无法同步状态文件".to_owned())?;
    file.persist(path).map_err(|_| "无法原子保存本地状态".to_owned())?;
    fs::File::open(folder)
        .and_then(|file| file.sync_all())
        .map_err(|_| "无法同步状态目录".to_owned())
}

pub fn load_journal(data: &Path) -> Result<Value> {
    let folder = data.join("state");
    let _lock = lock(&folder)?;
    let value = read(&folder.join("tasks.json"), JOURNAL_LIMIT)?
        .unwrap_or_else(|| json!({"version":1,"tasks":[]}));
    validate_journal(&value)?;
    Ok(value)
}

pub fn save_journal(data: &Path, value: &Value) -> Result<()> {
    validate_journal(value)?;
    let folder = data.join("state");
    let _lock = lock(&folder)?;
    write(&folder.join("tasks.json"), value)
}

pub fn load_preferences(data: &Path) -> Result<Value> {
    let folder = data.join("state");
    let _lock = lock(&folder)?;
    let Some(value) = read(&folder.join("preferences.json"), PREFERENCES_LIMIT)? else {
        return Ok(Value::Null);
    };
    validate_preferences(&value)?;
    Ok(value)
}

pub fn save_preferences(data: &Path, value: &Value) -> Result<()> {
    validate_preferences(value)?;
    let folder = data.join("state");
    let _lock = lock(&folder)?;
    write(&folder.join("preferences.json"), value)
}

fn reading_key(key: &str) -> Result<String> {
    if key.is_empty() || key.len() > 2048 || key.chars().any(char::is_control) {
        return Err("阅读来源编号无效".into());
    }
    Ok(format!("{:x}", Sha256::digest(key.as_bytes())))
}

pub fn load_position(data: &Path, key: &str) -> Result<Value> {
    let id = reading_key(key)?;
    let folder = data.join("reading-positions");
    let _lock = lock(&folder)?;
    let Some(value) = read(&folder.join(format!("{id}.json")), POSITION_LIMIT + 8192)? else {
        return Ok(Value::Null);
    };
    if value["key"] != key {
        return Err("阅读位置来源不匹配".into());
    }
    validate_position(&value["position"])?;
    Ok(value["position"].clone())
}

pub fn save_position(data: &Path, key: &str, position: &Value) -> Result<()> {
    validate_position(position)?;
    let id = reading_key(key)?;
    let folder = data.join("reading-positions");
    let _lock = lock(&folder)?;
    let path = folder.join(format!("{id}.json"));
    match read_inner(&path, POSITION_LIMIT + 8192) {
        Ok(Some(old)) => {
            if let Some(old_key) = old["key"].as_str() {
                if old_key != key {
                    return Err("阅读位置来源不匹配".into());
                }
                if old["position"]["updatedAt"].as_u64() > position["updatedAt"].as_u64() {
                    return Ok(());
                }
            }
        }
        Ok(None) => {}
        Err(ReadError::Corrupt(_)) => {
            // Self-heal a corrupted non-symlink regular file on explicit save.
        }
        Err(err) => return Err(err.into_message()),
    }
    write(&path, &json!({"key":key,"position":position}))
}

pub fn delete_position(data: &Path, key: &str) -> Result<()> {
    let id = reading_key(key)?;
    let folder = data.join("reading-positions");
    if folder.is_symlink() || !folder.exists() {
        return Ok(());
    }
    let _lock = lock(&folder)?;
    let path = folder.join(format!("{id}.json"));
    match fs::symlink_metadata(&path) {
        Ok(meta) if meta.file_type().is_file() => {
            fs::remove_file(&path).map_err(|_| "无法删除阅读位置记录".to_owned())?;
        }
        Ok(_) => return Err("阅读位置文件类型无效".into()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(_) => return Err("无法检查阅读位置记录".into()),
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn frontend_journal_round_trips_and_embedded_credentials_are_rejected() {
        let value: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/task-journal.json")).unwrap();
        let dir = tempfile::tempdir().unwrap();
        save_journal(dir.path(), &value).unwrap();
        assert_eq!(load_journal(dir.path()).unwrap(), value);
        for url in [
            "https://user:secret@example.com/v1",
            "https://example.com/v1?api_key=secret",
        ] {
            let mut bad = value.clone();
            bad["tasks"][0]["settings"]["baseUrl"] = json!(url);
            assert!(save_journal(dir.path(), &bad).is_err());
            let mut bad_asr = value.clone();
            bad_asr["tasks"][0]["settings"]["asr"]["baseUrl"] = json!(url);
            assert!(save_journal(dir.path(), &bad_asr).is_err());
        }
        assert_eq!(load_journal(dir.path()).unwrap(), value);
    }
    #[test]
    fn journal_round_trips_an_ipv6_loopback_api() {
        let mut value: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/task-journal.json")).unwrap();
        value["tasks"][0]["settings"]["baseUrl"] = json!("http://[::1]:8000/v1");
        let dir = tempfile::tempdir().unwrap();
        save_journal(dir.path(), &value).unwrap();
        assert_eq!(load_journal(dir.path()).unwrap(), value);
    }
    #[test]
    fn journal_accepts_old_and_selected_ffmpeg_paths_but_rejects_unsafe_paths() {
        let mut value: Value =
            serde_json::from_str(include_str!("../../tests/fixtures/task-journal.json")).unwrap();
        assert!(validate_journal(&value).is_ok());
        value["tasks"][0]["settings"]["asr"]["ffmpegPath"] = json!("/Users/yano/Audio Tools/ffmpeg");
        let dir = tempfile::tempdir().unwrap();
        save_journal(dir.path(), &value).unwrap();
        assert_eq!(load_journal(dir.path()).unwrap(), value);
        for path in ["ffmpeg", "/tmp/ffmpeg\n-version"] {
            let mut bad = value.clone();
            bad["tasks"][0]["settings"]["asr"]["ffmpegPath"] = json!(path);
            assert!(save_journal(dir.path(), &bad).is_err());
        }
        assert_eq!(load_journal(dir.path()).unwrap(), value);
    }
    fn position() -> Value {
        json!({"version":1,"updatedAt":10,"activeTab":"transcript","tabs":{"transcript":{"signature":"p1-0123456789abcdef-1","anchor":{"kind":"cue","cue":7,"endCue":8,"token":"p1-0123456789abcdef-2","occurrence":0},"offset":25.0,"ratio":0.3}},"mindmap":null})
    }
    #[test]
    fn positions_are_private_isolated_and_ignore_late_writes() {
        let dir = tempfile::tempdir().unwrap();
        let key = "https://www.youtube.com/watch?v=AbCdEfGhI12";
        save_position(dir.path(), key, &position()).unwrap();
        let mut older = position();
        older["updatedAt"] = json!(9);
        older["activeTab"] = json!("summary");
        save_position(dir.path(), key, &older).unwrap();
        assert_eq!(load_position(dir.path(), key).unwrap()["activeTab"], "transcript");
        assert_eq!(load_position(dir.path(), "different-source").unwrap(), Value::Null);
        let path = dir
            .path()
            .join("reading-positions")
            .join(format!("{}.json", reading_key(key).unwrap()));
        assert_eq!(fs::metadata(path).unwrap().permissions().mode() & 0o777, 0o600);
    }
    #[test]
    fn malformed_positions_and_symlinks_never_replace_valid_data_and_corrupt_heals() {
        let dir = tempfile::tempdir().unwrap();
        save_position(dir.path(), "a", &position()).unwrap();
        let mut bad = position();
        bad["tabs"]["transcript"]["anchor"]["cue"] = json!(-1);
        assert!(save_position(dir.path(), "a", &bad).is_err());
        assert_eq!(load_position(dir.path(), "a").unwrap(), position());
        let target = dir.path().join("target");
        fs::write(&target, "unchanged").unwrap();
        let path = dir
            .path()
            .join("reading-positions")
            .join(format!("{}.json", reading_key("b").unwrap()));
        std::os::unix::fs::symlink(&target, &path).unwrap();
        assert!(save_position(dir.path(), "b", &position()).is_err());
        assert_eq!(fs::read_to_string(target).unwrap(), "unchanged");

        // A corrupt non-symlink file self-heals on a valid save.
        let path_a = dir
            .path()
            .join("reading-positions")
            .join(format!("{}.json", reading_key("a").unwrap()));
        fs::write(&path_a, "{corrupt").unwrap();
        save_position(dir.path(), "a", &position()).unwrap();
        assert_eq!(load_position(dir.path(), "a").unwrap(), position());
    }
    #[test]
    fn journal_unknown_sensitive_fields_and_corruption_are_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let valid = json!({"version":1,"tasks":[]});
        save_journal(dir.path(), &valid).unwrap();
        assert!(save_journal(dir.path(), &json!({"version":1,"tasks":[],"token":"secret"})).is_err());
        assert_eq!(load_journal(dir.path()).unwrap(), valid);
        fs::write(dir.path().join("state/tasks.json"), "{").unwrap();
        assert!(load_journal(dir.path()).is_err());
        assert_eq!(fs::read_to_string(dir.path().join("state/tasks.json")).unwrap(), "{");
    }
    #[test]
    fn preferences_round_trip_and_reject_secret_keys() {
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(load_preferences(dir.path()).unwrap(), Value::Null);
        let prefs = json!({
            "version": 1,
            "preferences": {
                "provider": "codex",
                "paths": {"codex": "", "claude": ""},
                "api": {"baseUrl": "https://api.example.com/v1", "model": "m"},
                "asr": {"enabled": false, "reuseToken": false, "baseUrl": "https://dashscope.aliyuncs.com/api/v1", "model": "paraformer-v2", "maxMinutes": 120, "chunkMinutes": 5, "ffmpegPath": ""}
            },
            "language": {"interfaceLanguage": "zh-CN", "pageLanguage": "original"},
            "theme": "dark"
        });
        save_preferences(dir.path(), &prefs).unwrap();
        assert_eq!(load_preferences(dir.path()).unwrap(), prefs);
        assert!(save_preferences(dir.path(), &json!({"version": 1, "apiToken": "secret"})).is_err());
    }
}

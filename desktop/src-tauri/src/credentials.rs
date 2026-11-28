//! LinkRead API Token storage: a user-private file in the application data directory.
//! New tokens are never written to Keychain. A previous Keychain entry is migrated once.
use std::{
    fs::{self, DirBuilder},
    io::{self, Read, Write},
    os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt},
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
};

const MAX_TOKEN_BYTES: usize = 8192;
static LEGACY_KEYCHAIN_CHECKED: AtomicBool = AtomicBool::new(false);

fn path(data: &Path) -> PathBuf {
    data.join("api-token")
}

fn keychain_marker(data: &Path) -> PathBuf {
    data.join(".keychain-migrated")
}

fn ensure_private_dir(dir: &Path, message: &str) -> Result<(), String> {
    if dir.is_symlink() {
        return Err(message.to_owned());
    }
    match fs::symlink_metadata(dir) {
        Ok(meta) if meta.file_type().is_dir() => Ok(()),
        Ok(_) => Err(message.to_owned()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            DirBuilder::new()
                .recursive(true)
                .mode(0o700)
                .create(dir)
                .map_err(|_| message.to_owned())?;
            let _ = fs::set_permissions(dir, fs::Permissions::from_mode(0o700));
            Ok(())
        }
        Err(_) => Err(message.to_owned()),
    }
}

fn verify_file(target: &Path, label: &str) -> Result<bool, String> {
    match fs::symlink_metadata(target) {
        Ok(metadata) if metadata.file_type().is_file() && !metadata.file_type().is_symlink() => {
            // Never accept a secret file accessible to other local users.
            if metadata.permissions().mode() & 0o077 != 0 {
                return Err(format!("{label} 文件权限不安全，请检查应用数据目录"));
            }
            if metadata.len() > MAX_TOKEN_BYTES as u64 {
                return Err(format!("{label} 文件过大"));
            }
            Ok(true)
        }
        Ok(_) => Err(format!("{label} 存储路径不是普通文件")),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(_) => Err(format!("无法读取本地 {label} 文件")),
    }
}

fn read_secret_file(target: &Path, label: &str) -> Result<Option<String>, String> {
    if !verify_file(target, label)? {
        return Ok(None);
    }
    let file = fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(target)
        .map_err(|_| format!("无法读取本地 {label}"))?;
    let meta = file.metadata().map_err(|_| format!("无法读取本地 {label}"))?;
    if !meta.is_file() || meta.len() > MAX_TOKEN_BYTES as u64 {
        return Err(format!("{label} 存储路径无效或过大"));
    }
    let mut bytes = Vec::new();
    file.take(MAX_TOKEN_BYTES as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| format!("无法读取本地 {label}"))?;
    if bytes.len() > MAX_TOKEN_BYTES {
        return Err(format!("{label} 不能超过 {MAX_TOKEN_BYTES} 字节"));
    }
    let text = String::from_utf8(bytes).map_err(|_| format!("{label} 文件不是有效 UTF-8"))?;
    Ok((!text.is_empty()).then_some(text))
}

fn mark_keychain_migrated(data: &Path) {
    LEGACY_KEYCHAIN_CHECKED.store(true, Ordering::Relaxed);
    if !data.is_dir() || data.is_symlink() {
        return;
    }
    let marker = keychain_marker(data);
    if marker.is_symlink() || marker.exists() {
        return;
    }
    if let Ok(mut file) = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(marker)
    {
        let _ = file.write_all(b"1\n");
    }
}

pub fn load(data: &Path) -> Result<Option<String>, String> {
    let target = path(data);
    if let Some(secret) = read_secret_file(&target, "API Token")? {
        return Ok(Some(secret));
    }
    if keychain_marker(data).is_file() || LEGACY_KEYCHAIN_CHECKED.load(Ordering::Relaxed) {
        return Ok(None);
    }
    // Existing users of the first API implementation have a saved Keychain
    // entry. Migrate it to the app-private file, sync to disk, then delete that entry.
    // Keychain is never used to save new credentials.
    if let Ok(Some(secret)) = crate::keychain::load() {
        store(data, &secret)?;
        let _ = crate::keychain::delete();
        mark_keychain_migrated(data);
        return Ok(Some(secret));
    }
    mark_keychain_migrated(data);
    Ok(None)
}

pub fn store(data: &Path, secret: &str) -> Result<(), String> {
    if secret.is_empty() || secret.len() > MAX_TOKEN_BYTES || secret.chars().any(char::is_control) {
        return Err("Token 不能为空、超过 8192 字节或包含控制字符".into());
    }
    ensure_private_dir(data, "无法创建应用数据目录")?;
    let target = path(data);
    // Do not overwrite symlinks or other untrusted file types.
    verify_file(&target, "API Token")?;
    let mut temporary =
        tempfile::NamedTempFile::new_in(data).map_err(|_| "无法创建临时 Token 文件".to_owned())?;
    temporary
        .as_file()
        .set_permissions(fs::Permissions::from_mode(0o600))
        .map_err(|_| "无法设置 Token 文件权限".to_owned())?;
    temporary
        .write_all(secret.as_bytes())
        .map_err(|_| "无法保存 API Token".to_owned())?;
    temporary
        .flush()
        .and_then(|_| temporary.as_file().sync_all())
        .map_err(|_| "无法保存 API Token".to_owned())?;
    temporary
        .persist(target)
        .map_err(|_| "无法发布 API Token 文件".to_owned())?;
    Ok(())
}

pub fn delete(data: &Path) -> Result<(), String> {
    let target = path(data);
    if verify_file(&target, "API Token")? {
        fs::remove_file(&target).map_err(|_| "删除 API Token 失败".to_owned())?;
    }
    // Clean up any unmigrated legacy credential as well.
    let _ = crate::keychain::delete();
    mark_keychain_migrated(data);
    Ok(())
}

// A separate namespace: ASR never implicitly inherits the chat API key.
pub fn load_asr(data: &Path) -> Result<Option<String>, String> {
    let directory = data.join("asr");
    if directory.is_symlink() {
        return Err("语音配置目录不可用".into());
    }
    read_secret_file(&path(&directory), "ASR Token")
}

pub fn store_asr(data: &Path, token: &str) -> Result<(), String> {
    let directory = data.join("asr");
    ensure_private_dir(data, "无法创建应用数据目录")?;
    ensure_private_dir(&directory, "语音配置目录不可用")?;
    store(&directory, token)
}

pub fn delete_asr(data: &Path) -> Result<(), String> {
    let directory = data.join("asr");
    if directory.is_symlink() {
        return Err("语音配置目录不可用".into());
    }
    let target = path(&directory);
    if verify_file(&target, "ASR Token")? {
        fs::remove_file(target).map_err(|_| "无法清除 ASR Token".to_owned())?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn asr_credential_never_implicitly_reads_or_clears_chat_token() {
        let dir = tempfile::tempdir().unwrap();
        store(dir.path(), "chat-secret").unwrap();
        assert_eq!(load_asr(dir.path()).unwrap(), None);
        store_asr(dir.path(), "speech-secret").unwrap();
        assert_eq!(load_asr(dir.path()).unwrap(), Some("speech-secret".into()));
        assert_eq!(load(dir.path()).unwrap(), Some("chat-secret".into()));
        delete_asr(dir.path()).unwrap();
        assert_eq!(load_asr(dir.path()).unwrap(), None);
        assert_eq!(load(dir.path()).unwrap(), Some("chat-secret".into()));
    }

    #[test]
    fn private_file_round_trip() {
        let dir = tempfile::tempdir().unwrap();
        let data = dir.path().join("data");
        store(&data, "s3cret-test").unwrap();
        assert_eq!(load(&data).unwrap(), Some("s3cret-test".to_owned()));
        let metadata = fs::metadata(path(&data)).unwrap();
        assert_eq!(metadata.permissions().mode() & 0o777, 0o600);
        assert_eq!(fs::metadata(&data).unwrap().permissions().mode() & 0o777, 0o700);
        store(&data, "replacement-token").unwrap();
        assert_eq!(load(&data).unwrap(), Some("replacement-token".to_owned()));
        delete(&data).unwrap();
        assert!(!path(&data).exists());
    }

    #[test]
    fn reject_symlink_to_unrelated_file_and_oversized_tokens() {
        let dir = tempfile::tempdir().unwrap();
        let outside = dir.path().join("other.txt");
        fs::write(&outside, "private").unwrap();
        std::os::unix::fs::symlink(&outside, path(dir.path())).unwrap();
        assert!(store(dir.path(), "secret").is_err());
        assert!(load(dir.path()).is_err());
        assert_eq!(fs::read_to_string(&outside).unwrap(), "private");

        // Oversized api-token or asr token is rejected safely.
        let asr_dir = dir.path().join("asr");
        fs::create_dir_all(&asr_dir).unwrap();
        let big = path(&asr_dir);
        fs::write(&big, "x".repeat(MAX_TOKEN_BYTES + 1)).unwrap();
        fs::set_permissions(&big, fs::Permissions::from_mode(0o600)).unwrap();
        assert!(load_asr(dir.path()).is_err());
    }
}

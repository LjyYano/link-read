//! LinkRead API Token storage: a user-private file in the application data directory.
//! New tokens are never written to Keychain. A previous Keychain entry is migrated once.
use std::{
    fs::{self},
    io::{Read, Write},
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
};

fn path(data: &Path) -> PathBuf {
    data.join("api-token")
}

fn verify_file(target: &Path) -> Result<bool, String> {
    match fs::symlink_metadata(target) {
        Ok(metadata) if metadata.file_type().is_file() && !metadata.file_type().is_symlink() => {
            // Never accept a secret file accessible to other local users.
            if metadata.permissions().mode() & 0o077 != 0 {
                return Err("API Token 文件权限不安全，请检查应用数据目录".into());
            }
            Ok(true)
        }
        Ok(_) => Err("API Token 存储路径不是普通文件".into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(_) => Err("无法读取本地 API Token 文件".into()),
    }
}

pub fn load(data: &Path) -> Result<Option<String>, String> {
    let target = path(data);
    if verify_file(&target)? {
        let mut file = fs::File::open(&target).map_err(|_| "无法读取本地 API Token".to_owned())?;
        let mut text = String::new();
        file.read_to_string(&mut text).map_err(|_| "API Token 文件不是有效 UTF-8".to_owned())?;
        return Ok((!text.is_empty()).then_some(text));
    }
    // Existing users of the first API implementation have a saved Keychain
    // entry. Migrate it to the app-private file, then delete that entry.
    // Keychain is never used to save new credentials.
    if let Ok(Some(secret)) = crate::keychain::load() {
        store(data, &secret)?;
        let _ = crate::keychain::delete();
        return Ok(Some(secret));
    }
    Ok(None)
}

pub fn store(data: &Path, secret: &str) -> Result<(), String> {
    if secret.is_empty() || secret.len() > 8192 || secret.chars().any(char::is_control) {
        return Err("Token 不能为空、超过 8192 字节或包含控制字符".into());
    }
    fs::create_dir_all(data).map_err(|_| "无法创建应用数据目录".to_owned())?;
    let target = path(data);
    // Do not overwrite symlinks or other untrusted file types.
    verify_file(&target)?;
    let mut temporary = tempfile::NamedTempFile::new_in(data)
        .map_err(|_| "无法创建临时 Token 文件".to_owned())?;
    temporary
        .as_file()
        .set_permissions(fs::Permissions::from_mode(0o600))
        .map_err(|_| "无法设置 Token 文件权限".to_owned())?;
    temporary
        .write_all(secret.as_bytes())
        .map_err(|_| "无法保存 API Token".to_owned())?;
    temporary.flush().map_err(|_| "无法保存 API Token".to_owned())?;
    temporary
        .persist(target)
        .map_err(|_| "无法发布 API Token 文件".to_owned())?;
    Ok(())
}

pub fn delete(data: &Path) -> Result<(), String> {
    let target = path(data);
    if verify_file(&target)? {
        fs::remove_file(&target).map_err(|_| "删除 API Token 失败".to_owned())?;
    }
    // Clean up any unmigrated legacy credential as well.
    let _ = crate::keychain::delete();
    Ok(())
}

// A separate namespace: ASR never implicitly inherits the chat API key.
pub fn load_asr(data: &Path) -> Result<Option<String>, String> {
    let directory = data.join("asr");
    if directory.is_symlink() { return Err("语音配置目录不可用".into()); }
    let target = path(&directory);
    if !verify_file(&target)? { return Ok(None); }
    let value = fs::read_to_string(target).map_err(|_| "无法读取 ASR Token".to_owned())?;
    Ok((!value.is_empty()).then_some(value))
}

pub fn store_asr(data: &Path, token: &str) -> Result<(), String> {
    let directory = data.join("asr");
    if directory.is_symlink() { return Err("语音配置目录不可用".into()); }
    store(&directory, token)
}

pub fn delete_asr(data: &Path) -> Result<(), String> {
    let directory = data.join("asr");
    if directory.is_symlink() { return Err("语音配置目录不可用".into()); }
    let target = path(&directory);
    if verify_file(&target)? {
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
        store(dir.path(), "s3cret-test").unwrap();
        assert_eq!(load(dir.path()).unwrap(), Some("s3cret-test".to_owned()));
        let metadata = fs::metadata(path(dir.path())).unwrap();
        assert_eq!(metadata.permissions().mode() & 0o777, 0o600);
        store(dir.path(), "replacement-token").unwrap();
        assert_eq!(load(dir.path()).unwrap(), Some("replacement-token".to_owned()));
        delete(dir.path()).unwrap();
        assert!(!path(dir.path()).exists());
    }

    #[test]
    fn reject_symlink_to_unrelated_file() {
        let dir = tempfile::tempdir().unwrap();
        let outside = dir.path().join("other.txt");
        fs::write(&outside, "private").unwrap();
        std::os::unix::fs::symlink(&outside, path(dir.path())).unwrap();
        assert!(store(dir.path(), "secret").is_err());
        assert!(load(dir.path()).is_err());
        assert_eq!(fs::read_to_string(outside).unwrap(), "private");
    }
}

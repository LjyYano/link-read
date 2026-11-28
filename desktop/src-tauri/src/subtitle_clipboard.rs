use std::{fs, io::{self, Write}, os::unix::fs::DirBuilderExt, path::{Path, PathBuf}};

use serde::Serialize;
use sha2::{Digest, Sha256};
use tempfile::NamedTempFile;
use tauri::Url;

use crate::{AppResult, subtitle_file, translated};

pub const MAX_SUBTITLE_BYTES: usize = 16 * 1024 * 1024;

#[derive(Default, Serialize)]
pub struct ClipboardSubtitles {
    paths: Vec<PathBuf>,
    text: String,
}

fn file_path(value: &str) -> Option<PathBuf> {
    let url = Url::parse(value).ok()?;
    if url.scheme() != "file" || url.query().is_some() || url.fragment().is_some() {
        return None;
    }
    let path = url.to_file_path().ok()?;
    #[cfg(target_os = "macos")]
    if url.path().starts_with("/.file/id=") {
        use objc2_foundation::{NSString, NSURL};

        // Finder may put an inode-based file reference on the pasteboard.
        // Foundation resolves it to a path with the actual name and extension.
        let reference = NSURL::URLWithString(&NSString::from_str(value))?;
        let resolved = reference.filePathURL()?.path()?;
        let path = PathBuf::from(resolved.to_string());
        return (path.is_absolute() && !path.starts_with("/.file")).then_some(path);
    }
    Some(path)
}

pub fn resolve_path(value: &str) -> AppResult<PathBuf> {
    let path = if value
        .get(..5)
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case("file:"))
    {
        file_path(value).unwrap_or_default()
    } else {
        PathBuf::from(value)
    };
    subtitle_file(&path)?;
    Ok(path)
}

#[cfg(target_os = "macos")]
pub fn read(language: Option<&str>) -> AppResult<ClipboardSubtitles> {
    use objc2_app_kit::NSPasteboard;
    read_pasteboard(&NSPasteboard::generalPasteboard(), language)
}

#[cfg(target_os = "macos")]
fn read_pasteboard(board: &objc2_app_kit::NSPasteboard, language: Option<&str>) -> AppResult<ClipboardSubtitles> {
    use objc2_app_kit::{NSPasteboardTypeFileURL, NSPasteboardTypeString};

    // Both are exported, immutable AppKit type identifiers. Read the board
    // on the main thread, only in response to an explicit paste.
    let (file_type, text_type) = unsafe { (NSPasteboardTypeFileURL, NSPasteboardTypeString) };
    let mut paths = Vec::new();
    if let Some(items) = board.pasteboardItems() {
        for item in items.iter() {
            if let Some(url) = item.stringForType(file_type) {
                let path = file_path(&url.to_string()).ok_or_else(|| translated(language,
                    "无法读取剪贴板中的字幕文件", "Could not read the subtitle file from the clipboard").to_owned())?;
                paths.push(path);
                if paths.len() == 2 { break; }
            }
        }
    }
    if !paths.is_empty() { return Ok(ClipboardSubtitles { paths, text: String::new() }); }
    let text = match board.stringForType(text_type) {
        Some(value) if value.length() <= MAX_SUBTITLE_BYTES => {
            let text = value.to_string();
            if text.len() > MAX_SUBTITLE_BYTES {
                return Err(size_error(language));
            }
            text
        }
        Some(_) => return Err(size_error(language)),
        None => String::new(),
    };
    Ok(ClipboardSubtitles { paths, text })
}

#[cfg(not(target_os = "macos"))]
pub fn read(_language: Option<&str>) -> AppResult<ClipboardSubtitles> {
    Ok(ClipboardSubtitles::default())
}

pub fn size_error(language: Option<&str>) -> String {
    translated(language, "粘贴的字幕超过 16 MB", "Pasted subtitles exceed 16 MB").to_owned()
}

fn private_dir(path: &Path) -> io::Result<()> {
    match fs::DirBuilder::new().mode(0o700).create(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists
            && fs::symlink_metadata(path)?.is_dir() => Ok(()),
        Err(error) => Err(error),
    }
}

pub fn stage(data: &Path, name: &str, text: &str, language: Option<&str>) -> AppResult<(NamedTempFile, PathBuf)> {
    if text.len() > MAX_SUBTITLE_BYTES { return Err(size_error(language)); }
    let path = Path::new(name);
    let extension = path.extension().and_then(|value| value.to_str()).unwrap_or_default();
    if path.file_name() != Some(path.as_os_str()) || name.len() > 240
        || name.chars().any(|c| c.is_control() || c == '\\')
        || !matches!(extension.to_ascii_lowercase().as_str(), "srt" | "vtt" | "json")
    {
        return Err(translated(language, "请选择 SRT、VTT 或 JSON 字幕文件",
            "Choose an SRT, VTT, or JSON subtitle file").into());
    }
    let folder = data.join("subtitle-imports");
    let archive = folder.join(format!("{:x}", Sha256::digest(text.as_bytes())));
    let io_error = || translated(language, "无法保存粘贴的字幕", "Could not save the pasted subtitles").to_owned();
    private_dir(&folder).and_then(|()| private_dir(&archive)).map_err(|_| io_error())?;
    let mut temporary = tempfile::Builder::new().suffix(&format!(".{extension}"))
        .tempfile_in(&archive).map_err(|_| io_error())?;
    temporary.write_all(text.as_bytes()).map_err(|_| io_error())?;
    Ok((temporary, archive.join(name)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn staged_files_keep_the_name_and_exact_bytes_for_validation() {
        let data = tempfile::tempdir().unwrap();
        let content = "\u{feff}WEBVTT\r\n\r\n00:00.000 --> 00:01.000\r\n字幕\r\n";
        let (draft, destination) = stage(data.path(), "访谈 VTT.VTT", content, None).unwrap();
        assert_eq!(destination.file_name().unwrap(), "访谈 VTT.VTT");
        assert_eq!(draft.path().extension().unwrap(), "VTT");
        assert_eq!(fs::read(draft.path()).unwrap(), content.as_bytes());
        assert!(!destination.exists());
        let temporary_path = draft.path().to_owned();
        drop(draft);
        assert!(!temporary_path.exists());
        assert!(stage(data.path(), "../external.srt", content, None).is_err());
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn pasteboard_preserves_a_file_url_before_its_text_representation() {
        use objc2_app_kit::{NSPasteboard, NSPasteboardTypeFileURL, NSPasteboardTypeString};
        use objc2_foundation::NSString;

        // A private pasteboard leaves the user's system clipboard untouched.
        let board = NSPasteboard::pasteboardWithUniqueName();
        let (file_type, text_type) = unsafe { (NSPasteboardTypeFileURL, NSPasteboardTypeString) };
        board.clearContents();
        assert!(board.setString_forType(&NSString::from_str("file:///tmp/Interview%20A.srt"), file_type));
        assert!(board.setString_forType(&NSString::from_str("Interview A.srt"), text_type));
        let paste = read_pasteboard(&board, None).unwrap();
        assert_eq!(paste.paths, [PathBuf::from("/tmp/Interview A.srt")]);
        assert!(paste.text.is_empty());
        board.clearContents();
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn finder_reference_resolves_to_an_existing_subtitle_with_its_original_name() {
        use objc2_app_kit::{NSPasteboard, NSPasteboardTypeFileURL, NSPasteboardTypeString};
        use objc2_foundation::{NSString, NSURL};

        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("英文字幕 P2.srt");
        fs::write(&path, "1\n00:00:00,000 --> 00:00:01,000\nSubtitle\n").unwrap();
        let path = path.canonicalize().unwrap();
        let file_url = NSURL::fileURLWithPath(&NSString::from_str(path.to_str().unwrap()));
        let reference = file_url.fileReferenceURL().unwrap().absoluteString().unwrap();
        let reference_url = reference.to_string();
        assert!(Url::parse(&reference_url).unwrap().path().starts_with("/.file/id="));

        let board = NSPasteboard::pasteboardWithUniqueName();
        let (file_type, text_type) = unsafe { (NSPasteboardTypeFileURL, NSPasteboardTypeString) };
        assert!(board.setString_forType(&reference, file_type));
        assert!(board.setString_forType(&NSString::from_str("英文字幕 P2.srt"), text_type));
        let paste = read_pasteboard(&board, None).unwrap();
        assert_eq!(paste.paths, [path.clone()]);
        assert_eq!(resolve_path(&reference_url).unwrap(), path);
        board.clearContents();
    }
}

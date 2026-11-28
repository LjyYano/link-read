use std::{
    fs,
    io::Read,
    os::unix::fs::OpenOptionsExt,
    path::{Component, Path, PathBuf},
};

use percent_encoding::percent_decode_str;
use tauri::http;

use crate::{
    reader,
    types::{preview_id, translated, AppResult},
    Locations,
};

const MAX_READER_HTML_BYTES: u64 = 64 * 1024 * 1024;
const MAX_COVER_BYTES: u64 = 5 * 1024 * 1024;

/// Reader pages stay offline: scripts and styles are inline, and the only image
/// sources besides inline data are the cover CDNs the library window already trusts.
pub const READER_CONTENT_SECURITY_POLICY: &str = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: https://i.ytimg.com https://*.hdslb.com https://*.mzstatic.com; base-uri 'none'; form-action 'none'";

pub fn article_file(library: &Path, id: &str) -> AppResult<PathBuf> {
    let mut components = Path::new(id).components();
    if id.starts_with('.')
        || !matches!(components.next(), Some(Component::Normal(name)) if name == id)
        || components.next().is_some()
    {
        return Err("无效的文章编号".into());
    }
    let folder = library.join(id);
    if library.is_symlink() || !library.is_dir() || folder.is_symlink() || !folder.is_dir() {
        return Err("找不到这篇已生成的文章".into());
    }
    let manifest = folder.join("manifest.json");
    let legacy = folder.join("article.html");
    if manifest.is_symlink() || legacy.is_symlink() {
        return Err("找不到这篇已生成的文章".into());
    }
    if manifest.is_file() {
        return Ok(manifest);
    }
    if legacy.is_file() {
        return Ok(legacy);
    }
    Err("找不到这篇已生成的文章".into())
}

pub fn rendered_article_file(library: &Path, id: &str) -> AppResult<PathBuf> {
    let marker = article_file(library, id)?;
    let rendered = marker.with_file_name(".reader.html");
    if rendered.is_symlink() {
        return Err("阅读缓存路径不可用".into());
    }
    if rendered.is_file() {
        return Ok(rendered);
    }
    let legacy = marker.with_file_name("article.html");
    if legacy.is_symlink() {
        return Err("找不到这篇已生成的文章".into());
    }
    if legacy.is_file() {
        return Ok(legacy);
    }
    Err("找不到这篇已生成的文章".into())
}

pub fn transcript_preview_file(library: &Path, id: &str) -> AppResult<PathBuf> {
    preview_id(id)?;
    let root = library.join(".transcript-previews");
    let folder = root.join(id);
    let file = folder.join("preview.html");
    if root.is_symlink() || folder.is_symlink() || file.is_symlink() || !file.is_file() {
        return Err("找不到已保存的原文预览".into());
    }
    Ok(file)
}

pub fn saved_transcript_file(library: &Path, id: &str) -> AppResult<PathBuf> {
    let path = article_file(library, id)?.with_file_name("transcript.json");
    if path.is_symlink() || !path.is_file() {
        return Err("找不到这篇文章的原始字幕，请重新导入文件".into());
    }
    Ok(path)
}

fn read_bounded_utf8(path: &Path, limit: u64) -> Option<String> {
    let file = fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .ok()?;
    let meta = file.metadata().ok()?;
    if !meta.is_file() || meta.len() > limit {
        return None;
    }
    let mut bytes = Vec::with_capacity(meta.len().min(64 * 1024) as usize);
    file.take(limit + 1).read_to_end(&mut bytes).ok()?;
    if bytes.len() as u64 > limit {
        return None;
    }
    String::from_utf8(bytes).ok()
}

fn article_cover_file(library: &Path, id: &str) -> Option<(Vec<u8>, &'static str)> {
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
        let Ok(file) = fs::OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW)
            .open(&path)
        else {
            continue;
        };
        let Ok(meta) = file.metadata() else { continue };
        if !meta.is_file() || meta.len() == 0 || meta.len() > MAX_COVER_BYTES {
            continue;
        }
        let mut bytes = Vec::with_capacity(meta.len() as usize);
        if file.take(MAX_COVER_BYTES + 1).read_to_end(&mut bytes).is_ok()
            && !bytes.is_empty()
            && (bytes.len() as u64) <= MAX_COVER_BYTES
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
            .header(
                http::header::CACHE_CONTROL,
                "private, max-age=31536000, immutable",
            )
            .body(bytes)
            .expect("valid cover response"),
        None => http::Response::builder()
            .status(http::StatusCode::NOT_FOUND)
            .header(http::header::X_CONTENT_TYPE_OPTIONS, "nosniff")
            .header(http::header::CACHE_CONTROL, "no-store")
            .body(Vec::new())
            .expect("valid missing cover response"),
    }
}

pub fn preview_response(locations: &Locations, uri: &http::Uri) -> http::Response<Vec<u8>> {
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
            read_bounded_utf8(&path, MAX_READER_HTML_BYTES)
                .map(|html| reader::with_takeaways(html, &path))
        });
    let (status, html) = match html {
        Some(html) => (http::StatusCode::OK, preview_document(html)),
        None => (
            http::StatusCode::NOT_FOUND,
            format!(
                "<!doctype html><html lang={}><meta charset=utf-8><p>{}</p>",
                locations.language(),
                translated(
                    Some(locations.language()),
                    "无法读取这篇文章，请刷新书架。",
                    "Could not read this article. Reload the library and try again."
                )
            ),
        ),
    };
    http::Response::builder()
        .status(status)
        .header(http::header::CONTENT_TYPE, "text/html; charset=utf-8")
        .header(http::header::X_CONTENT_TYPE_OPTIONS, "nosniff")
        .header(
            http::header::CONTENT_SECURITY_POLICY,
            READER_CONTENT_SECURITY_POLICY,
        )
        .header(http::header::CACHE_CONTROL, "no-store")
        .body(html.into_bytes())
        .expect("valid preview response")
}

pub fn preview_document(mut html: String) -> String {
    // A bundled script can contain an earlier </body> in a JavaScript string.
    if let Some(end) = html.rfind("</body>") {
        html.insert_str(end, include_str!("preview.html"));
    }
    html
}

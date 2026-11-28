#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod app_updates;
mod archive;
mod cli_models;
mod credentials;
#[cfg(target_os = "macos")]
mod desktop_menu;
mod durable_state;
mod environment;
mod keychain;
mod protocol;
mod reader;
mod sidecar;
mod subtitle_clipboard;
mod system_fonts;
mod types;
mod video_links;

use std::{
    env,
    fs::{self, DirBuilder},
    io::{self, Write},
    os::unix::fs::{DirBuilderExt, PermissionsExt},
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
    time::Duration,
};

use serde::Deserialize;
use tauri::{ipc::Channel, AppHandle, Manager, State};
use tauri_plugin_dialog::DialogExt;
use tokio::{process::Command, sync::oneshot};

pub(crate) use archive::*;
pub(crate) use protocol::*;
pub(crate) use sidecar::*;
pub(crate) use types::*;

#[tauri::command]
fn set_ui_language(
    app: AppHandle,
    locations: State<'_, Locations>,
    language: String,
) -> AppResult<()> {
    if !matches!(language.as_str(), "en" | "zh-CN") {
        return Err("Unsupported interface language".into());
    }
    locations
        .english
        .store(language == "en", Ordering::Relaxed);
    #[cfg(target_os = "macos")]
    desktop_menu::localize(&app, language == "en")
        .map_err(|error| details("无法更新菜单语言", error))?;
    Ok(())
}

#[tauri::command]
async fn check_tool(
    provider: Provider,
    executable: String,
    locations: State<'_, Locations>,
) -> AppResult<ToolStatus> {
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || ToolStatus {
        executable: tool_path(provider, &executable),
        key_available: match provider {
            Provider::Codex => true,
            Provider::Claude => cli_models::claude_auth_available(),
            Provider::Api => credentials::load(&data).ok().flatten().is_some(),
        },
    })
    .await
    .map_err(|error| details("无法检查 AI 工具状态", error))
}

#[tauri::command]
async fn list_cli_models(provider: Provider) -> AppResult<cli_models::CliModelCatalog> {
    tokio::task::spawn_blocking(move || cli_models::list_models(provider))
        .await
        .map_err(|error| details("无法读取可用模型列表", error))
}

#[tauri::command]
async fn pick_executable(
    app: AppHandle,
    window: tauri::WebviewWindow,
    title: String,
    ui_language: Option<String>,
) -> AppResult<Option<PathBuf>> {
    let (send, receive) = oneshot::channel();
    let dialog_title = if title.trim().is_empty() {
        translated(ui_language.as_deref(), "选择可执行程序", "Choose executable").to_owned()
    } else {
        title
    };
    app.dialog()
        .file()
        .set_parent(&window)
        .set_title(&dialog_title)
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
        .map_err(|error| details("无法访问所选程序", error))?;
    if !executable_file(&path) {
        return Err(translated(
            ui_language.as_deref(),
            "所选文件不存在或没有执行权限，请重新选择程序",
            "The selected file is missing or not executable.",
        )
        .to_owned());
    }
    Ok(Some(path))
}

#[tauri::command]
async fn load_api_token(locations: State<'_, Locations>) -> AppResult<Option<String>> {
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || credentials::load(&data))
        .await
        .map_err(|error| details("无法读取 API Token", error))?
}

#[tauri::command]
async fn save_api_token(locations: State<'_, Locations>, token: String) -> AppResult<()> {
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || credentials::store(&data, &token))
        .await
        .map_err(|error| details("无法保存 API Token", error))?
}

#[tauri::command]
async fn delete_api_token(locations: State<'_, Locations>) -> AppResult<()> {
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || credentials::delete(&data))
        .await
        .map_err(|error| details("无法删除 API Token", error))?
}

#[tauri::command]
async fn test_api_connection(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
    base_url: String,
    model: String,
    token: Option<String>,
) -> AppResult<ApiConnectionCheck> {
    let (parsed_base, loopback) = validated_api_base_url(&base_url)?;
    let model = model.trim().to_owned();
    if model.is_empty() || model.len() > 256 || model.chars().any(char::is_control) {
        return Err("请填写有效的模型名称".into());
    }
    let data = locations.data.clone();
    let saved = tokio::task::spawn_blocking(move || credentials::load(&data))
        .await
        .map_err(|error| details("无法读取 API Token", error))??;
    let resolved = token.filter(|v| !v.is_empty()).or(saved);
    let token = match resolved {
        Some(value) => {
            if value.len() > 8192 || value.chars().any(char::is_control) {
                return Err("API Token 包含无效字符或过长".into());
            }
            Some(value)
        }
        None if loopback => None,
        None => return Err("请先填写 API Token".into()),
    };
    let mut cmd = sidecar(&locations)?;
    cmd.arg("test-api")
        .args(["--base-url", parsed_base.as_str(), "--model", &model]);
    if let Some(value) = token {
        cmd.env("LINKREAD_API_TOKEN", value);
    }
    run_sidecar_check(
        cmd,
        Duration::from_secs(45),
        Some(&queries),
        "连接测试超时，请检查网络或模型响应时间",
        "无法启动模型连通性测试",
        "测试进程未返回有效状态",
        "模型连通性测试失败",
        "连接成功，模型和 Token 可用",
    )
    .await
}

#[tauri::command]
async fn preview_video(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
    source: String,
) -> AppResult<SourceMetadata> {
    video_url(&source)?;
    query_tracked(
        &locations,
        &["preview", "--", &source],
        Duration::from_secs(15),
        "来源预览暂时超时，可以重试或直接开始生成",
        Some(&queries),
    )
    .await
}

#[tauri::command]
async fn check_source(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
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
    query_tracked(
        &locations,
        &args,
        Duration::from_secs(35),
        "来源检查暂时超时，尚未确认内容是否可用；请重试检查或查看来源",
        Some(&queries),
    )
    .await
}

#[tauri::command]
async fn download_subtitles(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
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
    query_command_detailed(
        command,
        Duration::from_secs(60),
        "下载字幕超时，请检查网络或稍后重试",
        Some(&queries),
    )
    .await
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
        .set_title(translated(
            ui_language.as_deref(),
            "选择 YouTube Cookie 文件",
            "Choose YouTube cookies file",
        ))
        .add_filter(
            translated(
                ui_language.as_deref(),
                "Cookie 文本文件",
                "Cookies text file",
            ),
            &["txt"],
        )
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
    queries: State<'_, ActiveQueries>,
    options: YoutubeCookieOptions,
) -> AppResult<YoutubeCookieTestResult> {
    if options.mode == "none" {
        return Err("请先选择浏览器或 cookies.txt 文件".into());
    }
    let cookies =
        youtube_cookie_args("https://www.youtube.com/watch?v=1-Fo8McCD9w", Some(&options))?;
    let mut command = sidecar(&locations)?;
    command.arg("test-youtube-cookies");
    command.args(&cookies);
    run_youtube_cookie_test(
        command,
        Duration::from_secs(45),
        "读取 YouTube Cookie 超时；如系统弹出钥匙串授权窗口，请确认授权后重试",
        Some(&queries),
    )
    .await
}

#[tauri::command]
async fn open_privacy_settings(pane: PrivacySettingsPane) -> AppResult<()> {
    #[cfg(target_os = "macos")]
    {
        let status = Command::new("/usr/bin/open")
            .arg(pane.macos_url())
            .status()
            .await
            .map_err(|error| details("无法打开系统设置", error))?;
        status
            .success()
            .then_some(())
            .ok_or_else(|| "无法打开系统设置".to_owned())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = pane;
        Err("仅支持在 macOS 打开系统设置".into())
    }
}

#[tauri::command]
async fn load_asr_token(locations: State<'_, Locations>) -> AppResult<Option<String>> {
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || credentials::load_asr(&data))
        .await
        .map_err(|error| details("无法读取语音 API 密钥", error))?
}

#[tauri::command]
async fn save_asr_token(locations: State<'_, Locations>, token: String) -> AppResult<()> {
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || credentials::store_asr(&data, &token))
        .await
        .map_err(|error| details("无法保存语音 API 密钥", error))?
}

#[tauri::command]
async fn delete_asr_token(locations: State<'_, Locations>) -> AppResult<()> {
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || credentials::delete_asr(&data))
        .await
        .map_err(|error| details("无法清除语音 API 密钥", error))?
}

#[tauri::command]
async fn test_asr_connection(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
    options: AsrOptions,
    token: Option<String>,
) -> AppResult<ApiConnectionCheck> {
    let parsed_url = options.validate()?;
    let locations_data = locations.data.clone();
    let options_clone = options.clone();
    let token = tokio::task::spawn_blocking(move || {
        let temp_locations = Locations {
            data: locations_data,
            library: PathBuf::new(),
            english: AtomicBool::new(false),
        };
        options_clone.test_token(&temp_locations, token)
    })
    .await
    .map_err(|error| details("无法读取语音 API 密钥", error))??;

    let mut cmd = sidecar(&locations)?;
    cmd.args([
        "test-asr",
        "--base-url",
        parsed_url.as_str(),
        "--model",
        &options.model,
    ])
    .env("LINKREAD_ASR_TOKEN", token);
    run_sidecar_check(
        cmd,
        Duration::from_secs(120),
        Some(&queries),
        "语音测试等待超时；云端任务可能仍在处理，请勿连续重复测试",
        "无法启动语音测试",
        "语音测试未返回有效结果",
        "语音测试失败",
        "已连接",
    )
    .await
}

#[tauri::command]
async fn check_duplicate(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
    source: String,
    local_file: bool,
    resume_preview: Option<String>,
) -> AppResult<DuplicateInfo> {
    if let Some(id) = resume_preview.as_deref() {
        preview_id(id)?;
        let mut args = vec!["duplicate", "--resume-preview", id];
        if local_file {
            args.push("--local-file");
        } else {
            video_url(&source)?;
        }
        args.extend(["--", &source]);
        return query_tracked(
            &locations,
            &args,
            Duration::from_secs(60),
            "读取资料库超时，请重试",
            Some(&queries),
        )
        .await;
    }
    if local_file {
        subtitle_file(Path::new(&source))?;
        query_tracked(
            &locations,
            &["duplicate", "--local-file", "--", &source],
            Duration::from_secs(60),
            "读取资料库超时，请重试",
            Some(&queries),
        )
        .await
    } else {
        video_url(&source)?;
        query_tracked(
            &locations,
            &["duplicate", "--", &source],
            Duration::from_secs(60),
            "读取资料库超时，请重试",
            Some(&queries),
        )
        .await
    }
}

#[tauri::command]
async fn pick_subtitle(
    app: AppHandle,
    window: tauri::WebviewWindow,
    ui_language: Option<String>,
) -> AppResult<Option<PathBuf>> {
    let (send, receive) = oneshot::channel();
    app.dialog()
        .file()
        .set_parent(&window)
        .set_title(translated(
            ui_language.as_deref(),
            "导入字幕文件",
            "Import subtitles",
        ))
        .add_filter(
            translated(ui_language.as_deref(), "字幕文件", "Subtitle files"),
            &["srt", "vtt", "json"],
        )
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
async fn read_subtitle_clipboard(
    app: AppHandle,
    ui_language: Option<String>,
) -> AppResult<subtitle_clipboard::ClipboardSubtitles> {
    let (send, receive) = oneshot::channel();
    let failed = translated(
        ui_language.as_deref(),
        "无法读取剪贴板",
        "Could not read the clipboard",
    );
    app.run_on_main_thread(move || {
        let _ = send.send(subtitle_clipboard::read(ui_language.as_deref()));
    })
    .map_err(|_| failed.to_owned())?;
    receive.await.map_err(|_| failed.to_owned())?
}

#[tauri::command]
async fn resolve_subtitle_path(path: String) -> AppResult<PathBuf> {
    tokio::task::spawn_blocking(move || subtitle_clipboard::resolve_path(&path))
        .await
        .map_err(|error| details("无法读取字幕路径", error))?
}

#[tauri::command]
async fn save_pasted_subtitle(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
    name: String,
    text: String,
) -> AppResult<PathBuf> {
    let language = Some(locations.language());
    let data = locations.data.clone();
    let (temporary, destination) = tokio::task::spawn_blocking(move || {
        subtitle_clipboard::stage(&data, &name, &text, language)
    })
    .await
    .map_err(|_| {
        translated(
            language,
            "无法保存粘贴的字幕",
            "Could not save the pasted subtitles",
        )
        .to_owned()
    })??;
    let path = temporary
        .path()
        .to_str()
        .ok_or_else(|| {
            translated(
                language,
                "无法读取字幕文件路径",
                "Could not read the subtitle file path",
            )
            .to_owned()
        })?
        .to_owned();
    let _: serde_json::Value = query_tracked(
        &locations,
        &["validate-subtitle", "--", &path],
        Duration::from_secs(20),
        translated(
            language,
            "校验字幕超时，请重试",
            "Subtitle validation timed out. Try again.",
        ),
        Some(&queries),
    )
    .await?;
    tokio::task::spawn_blocking(move || {
        temporary.persist(&destination).map(|_| destination)
    })
    .await
    .map_err(|_| {
        translated(
            language,
            "无法保存粘贴的字幕",
            "Could not save the pasted subtitles",
        )
        .to_owned()
    })?
    .map_err(|_| {
        translated(
            language,
            "无法保存粘贴的字幕",
            "Could not save the pasted subtitles",
        )
        .to_owned()
    })
}

#[tauri::command]
async fn check_ffmpeg(path: String) -> environment::FfmpegStatus {
    environment::check(&path, command_paths()).await
}

#[tauri::command]
async fn locate_ffmpeg(
    app: AppHandle,
    window: tauri::WebviewWindow,
    ui_language: Option<String>,
) -> AppResult<Option<environment::FfmpegStatus>> {
    let (send, receive) = oneshot::channel();
    app.dialog()
        .file()
        .set_parent(&window)
        .set_title(translated(
            ui_language.as_deref(),
            "选择 FFmpeg 可执行程序",
            "Choose the FFmpeg executable",
        ))
        .pick_file(move |file| {
            let _ = send.send(file);
        });
    let Some(file) = receive
        .await
        .map_err(|_| "无法打开文件选择窗口".to_owned())?
    else {
        return Ok(None);
    };
    let path = file.into_path().map_err(|_| "无法访问所选程序".to_owned())?;
    let path = path.to_str().ok_or("程序路径包含无法识别的字符")?;
    let result = environment::check(path, vec![]).await;
    if result.state != "ready" {
        return Err(result.message);
    }
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
    reasoning_effort: Option<String>,
    replace: Option<bool>,
    asr: Option<AsrOptions>,
    resume_preview: Option<String>,
    page_language: Option<String>,
    ui_language: Option<String>,
    youtube_cookies: Option<YoutubeCookieOptions>,
) -> GenerationResult<Article> {
    let active_key = if local_file.unwrap_or(false) {
        None
    } else {
        video_links::source_key(&source)
    };
    state
        .begin_with_source(&request_id, active_key)
        .map_err(|error| {
            if error == "已取消生成" {
                GenerationFailure::cancelled()
            } else {
                GenerationFailure::core(error)
            }
        })?;
    let command = generation_command(
        &locations,
        &source,
        provider,
        &executable,
        base_url.as_deref(),
        model.as_deref(),
        reasoning_effort.as_deref(),
        replace.unwrap_or(false),
        local_file.unwrap_or(false),
        asr.as_ref(),
        resume_preview.as_deref(),
        page_language.as_deref(),
        youtube_cookies.as_ref(),
    );
    let result = match command {
        Ok(mut command) => {
            let _ = credentials_read.send(());
            if let Some(language @ ("en" | "zh-CN")) = ui_language.as_deref() {
                command.env("LINKREAD_UI_LANGUAGE", language);
            }
            run_generation(&state, &request_id, &mut command, progress).await
        }
        Err(error) => Err(error),
    };
    if state.finish(&request_id) {
        return Err(GenerationFailure::cancelled());
    }
    result
}

#[tauri::command]
fn cancel_generation(state: State<'_, Generation>, request_id: String) -> AppResult<()> {
    state.cancel(&request_id)
}

#[tauri::command]
async fn list_articles(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
) -> AppResult<Library> {
    query_tracked(
        &locations,
        &["library"],
        Duration::from_secs(60),
        "读取资料库超时，请重试",
        Some(&queries),
    )
    .await
}

#[tauri::command]
async fn get_article_details(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
    id: String,
) -> AppResult<ArticleDetails> {
    article_file(&locations.library, &id)?;
    query_tracked(
        &locations,
        &["article-details", "--", &id],
        Duration::from_secs(60),
        "读取资料库超时，请重试",
        Some(&queries),
    )
    .await
}

#[tauri::command]
async fn update_article_details(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
    id: String,
    title: Option<String>,
    category: Option<String>,
    source_url: Option<String>,
) -> AppResult<Article> {
    article_file(&locations.library, &id)?;
    let mut values = serde_json::json!({"title": title, "category": category});
    if let Some(source) = source_url {
        let source = source.trim();
        if source.len() > 4096 {
            return Err("请输入有效的视频或播客单集链接".into());
        }
        values["sourceUrl"] = if source.is_empty() {
            serde_json::Value::Null
        } else {
            serde_json::json!(video_url(source)?.as_str())
        };
    }
    let values = values.to_string();
    query_tracked(
        &locations,
        &["update-article-details", "--values", &values, "--", &id],
        Duration::from_secs(60),
        "读取资料库超时，请重试",
        Some(&queries),
    )
    .await
}

#[tauri::command]
async fn update_article_state(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
    id: String,
    is_read: Option<bool>,
    is_starred: Option<bool>,
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
    query_tracked(
        &locations,
        &args,
        Duration::from_secs(60),
        "读取资料库超时，请重试",
        Some(&queries),
    )
    .await
}

async fn reading_preferences_args(locations: &Locations) -> Vec<String> {
    let data = locations.data.clone();
    let json = tokio::task::spawn_blocking(move || durable_state::load_reading_preferences_json(&data))
        .await
        .ok()
        .flatten();
    match json {
        Some(value) => vec!["--reading-preferences".into(), value],
        None => Vec::new(),
    }
}

#[tauri::command]
async fn list_system_fonts() -> AppResult<Vec<String>> {
    tokio::task::spawn_blocking(system_fonts::installed_font_families)
        .await
        .map_err(|error| details("无法读取系统字体列表", error))
}

#[tauri::command]
async fn prepare_article(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
    id: String,
) -> AppResult<PreparedReader> {
    article_file(&locations.library, &id)?;
    let mut owned = vec!["reader".to_owned()];
    owned.extend(reading_preferences_args(&locations).await);
    owned.push("--".into());
    owned.push(id);
    let args: Vec<&str> = owned.iter().map(String::as_str).collect();
    query_tracked(
        &locations,
        &args,
        Duration::from_secs(60),
        "读取资料库超时，请重试",
        Some(&queries),
    )
    .await
}

#[tauri::command]
async fn prepare_preview(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
    id: String,
) -> AppResult<PreparedPreview> {
    preview_id(&id)?;
    let mut owned = vec!["preview-reader".to_owned()];
    owned.extend(reading_preferences_args(&locations).await);
    owned.push("--".into());
    owned.push(id);
    let args: Vec<&str> = owned.iter().map(String::as_str).collect();
    query_tracked(
        &locations,
        &args,
        Duration::from_secs(60),
        "读取资料库超时，请重试",
        Some(&queries),
    )
    .await
}

#[tauri::command]
async fn load_task_journal(locations: State<'_, Locations>) -> AppResult<serde_json::Value> {
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || durable_state::load_journal(&data))
        .await
        .map_err(|error| details("无法读取任务记录", error))?
}

#[tauri::command]
async fn save_task_journal(
    locations: State<'_, Locations>,
    journal: serde_json::Value,
) -> AppResult<()> {
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || durable_state::save_journal(&data, &journal))
        .await
        .map_err(|error| details("无法保存任务记录", error))?
}

#[tauri::command]
async fn load_preferences(locations: State<'_, Locations>) -> AppResult<serde_json::Value> {
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || durable_state::load_preferences(&data))
        .await
        .map_err(|error| details("无法读取应用设置", error))?
}

#[tauri::command]
async fn save_preferences(
    locations: State<'_, Locations>,
    preferences: serde_json::Value,
) -> AppResult<()> {
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || durable_state::save_preferences(&data, &preferences))
        .await
        .map_err(|error| details("无法保存应用设置", error))?
}

#[tauri::command]
async fn load_reading_position(
    locations: State<'_, Locations>,
    key: String,
) -> AppResult<serde_json::Value> {
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || durable_state::load_position(&data, &key))
        .await
        .map_err(|error| details("无法读取阅读位置", error))?
}

#[tauri::command]
async fn save_reading_position(
    locations: State<'_, Locations>,
    key: String,
    position: serde_json::Value,
) -> AppResult<()> {
    let data = locations.data.clone();
    tokio::task::spawn_blocking(move || durable_state::save_position(&data, &key, &position))
        .await
        .map_err(|error| details("无法保存阅读位置", error))?
}

#[tauri::command]
async fn refresh_shownotes(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
    id: String,
    youtube_cookies: Option<YoutubeCookieOptions>,
) -> AppResult<ShownotesRefresh> {
    let library = locations.library.clone();
    let article_id = id.clone();
    let source_url = tokio::task::spawn_blocking(move || {
        article_file(&library, &article_id)?;
        let raw_transcript = library.join(&article_id).join("transcript.json");
        fs::read_to_string(raw_transcript)
            .ok()
            .and_then(|text| serde_json::from_str::<serde_json::Value>(&text).ok())
            .and_then(|v| v.pointer("/source/url")?.as_str().map(str::to_owned))
            .map_or(Ok(None), |u| Ok::<Option<String>, String>(Some(u)))
    })
    .await
    .map_err(|error| details("无法读取文章来源", error))??;
    let cookies = source_url
        .as_deref()
        .map(|src| youtube_cookie_args(src, youtube_cookies.as_ref()))
        .transpose()?
        .unwrap_or_default();
    let timeout = if locations.language() == "en" {
        "Loading Shownotes timed out. Try again."
    } else {
        "加载 Shownotes 超时，请稍后重试"
    };
    let mut owned: Vec<String> = vec!["refresh-shownotes".into()];
    owned.extend(cookies);
    owned.extend(reading_preferences_args(&locations).await);
    owned.push("--".into());
    owned.push(id);
    let args: Vec<&str> = owned.iter().map(String::as_str).collect();
    query_tracked(&locations, &args, Duration::from_secs(60), timeout, Some(&queries)).await
}

fn persist_exported_file(
    target: PathBuf,
    markdown: Option<String>,
    source: Option<PathBuf>,
) -> io::Result<()> {
    let parent = target.parent().ok_or(io::ErrorKind::InvalidInput)?;
    let mut temp = tempfile::NamedTempFile::new_in(parent)?;
    if let Some(text) = markdown {
        temp.write_all(text.as_bytes())?;
    } else if let Some(source) = source {
        io::copy(&mut fs::File::open(source)?, temp.as_file_mut())?;
    } else {
        return Err(io::ErrorKind::InvalidInput.into());
    }
    temp.flush()?;
    temp.as_file().set_permissions(fs::Permissions::from_mode(0o644))?;
    temp.as_file().sync_all()?;
    temp.persist(target)?;
    Ok(())
}

#[tauri::command]
async fn export_article(
    app: AppHandle,
    window: tauri::WebviewWindow,
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
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
    let metadata: ArticleDetails = query_tracked(
        &locations,
        &["article-details", "--", &id],
        Duration::from_secs(60),
        "读取资料库超时，请重试",
        Some(&queries),
    )
    .await?;
    let title = export_file_stem(&metadata.title);
    let (send, receive) = oneshot::channel();
    app.dialog()
        .file()
        .set_parent(&window)
        .set_title(format!(
            "{} {kind}",
            translated(ui_language.as_deref(), "导出", "Export")
        ))
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
            let mut owned = vec!["reader".to_owned()];
            owned.extend(reading_preferences_args(&locations).await);
            owned.push("--".into());
            owned.push(id.clone());
            let args: Vec<&str> = owned.iter().map(String::as_str).collect();
            let _: PreparedReader = query_tracked(
                &locations,
                &args,
                Duration::from_secs(60),
                "读取资料库超时，请重试",
                Some(&queries),
            )
            .await?;
            (Some(rendered_article_file(&locations.library, &id)?), None)
        }
        ExportFormat::Markdown => {
            let content = match options.as_ref().map(|value| &value.content) {
                Some(MarkdownContent::Readable) => "readable",
                _ => "full",
            };
            let timestamps = if options.as_ref().map_or(true, |value| value.include_timestamped) {
                "true"
            } else {
                "false"
            };
            let markdown: Markdown = query_tracked(
                &locations,
                &[
                    "markdown",
                    "--content",
                    content,
                    "--include-timestamped",
                    timestamps,
                    "--",
                    &id,
                ],
                Duration::from_secs(60),
                "读取资料库超时，请重试",
                Some(&queries),
            )
            .await?;
            (None, Some(markdown.text))
        }
    };
    tokio::task::spawn_blocking(move || persist_exported_file(target, markdown, source))
        .await
        .map_err(|error| details("导出失败", error))?
        .map_err(|error| details("导出失败", error))?;
    Ok(true)
}

#[tauri::command]
async fn export_subtitles(
    app: AppHandle,
    window: tauri::WebviewWindow,
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
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
    let exported: ExportedSubtitles = query_tracked(
        &locations,
        &args,
        Duration::from_secs(60),
        "读取资料库超时，请重试",
        Some(&queries),
    )
    .await?;
    let title = export_file_stem(&exported.title);
    let (send, receive) = oneshot::channel();
    app.dialog()
        .file()
        .set_parent(&window)
        .set_title(format!(
            "{} {kind}",
            translated(ui_language.as_deref(), "导出字幕", "Export subtitles")
        ))
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
    tokio::task::spawn_blocking(move || persist_exported_file(target, Some(exported.text), None))
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

#[tauri::command]
async fn open_external(url: String) -> AppResult<()> {
    let url = external_url(&url)?;
    let status = Command::new("/usr/bin/open")
        .arg(url.as_str())
        .status()
        .await
        .map_err(|error| details("无法打开链接", error))?;
    status
        .success()
        .then_some(())
        .ok_or_else(|| "无法打开链接".to_owned())
}

#[tauri::command]
async fn open_article(
    locations: State<'_, Locations>,
    queries: State<'_, ActiveQueries>,
    id: String,
) -> AppResult<()> {
    article_file(&locations.library, &id)?;
    let mut owned = vec!["reader".to_owned()];
    owned.extend(reading_preferences_args(&locations).await);
    owned.push("--".into());
    owned.push(id.clone());
    let args: Vec<&str> = owned.iter().map(String::as_str).collect();
    let _: PreparedReader = query_tracked(
        &locations,
        &args,
        Duration::from_secs(60),
        "读取资料库超时，请重试",
        Some(&queries),
    )
    .await?;
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

#[tauri::command]
async fn article_transcript_path(locations: State<'_, Locations>, id: String) -> AppResult<PathBuf> {
    let library = locations.library.clone();
    tokio::task::spawn_blocking(move || saved_transcript_file(&library, &id))
        .await
        .map_err(|error| details("无法读取字幕路径", error))?
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

fn ensure_private_data_dir(data: &Path) -> io::Result<()> {
    DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(data)?;
    Ok(())
}

fn main() {
    let builder = tauri::Builder::default();
    #[cfg(target_os = "macos")]
    let builder = builder
        .menu(desktop_menu::build)
        .on_menu_event(desktop_menu::dispatch);
    builder
        .plugin(tauri_plugin_dialog::init())
        .register_asynchronous_uri_scheme_protocol("linkread-reader", |context, request, responder| {
            let app = context.app_handle().clone();
            let uri = request.uri().clone();
            tauri::async_runtime::spawn_blocking(move || {
                let locations = app.state::<Locations>();
                let response = preview_response(&locations, &uri);
                responder.respond(response);
            });
        })
        .manage(Generation::default())
        .manage(ActiveQueries::default())
        .manage(app_updates::AppUpdates::default())
        .setup(|app| {
            if app.config().plugins.0.contains_key("updater") {
                app.handle()
                    .plugin(tauri_plugin_updater::Builder::new().build())?;
            }
            let data = app.path().app_data_dir()?;
            ensure_private_data_dir(&data)?;
            let library = env::var_os("LINKREAD_DESKTOP_LIBRARY")
                .map(PathBuf::from)
                .filter(|path| path.is_absolute())
                .unwrap_or_else(|| data.join("library"));
            app.manage(Locations {
                data,
                library,
                english: AtomicBool::new(false),
            });
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
            list_system_fonts,
            load_task_journal,
            save_task_journal,
            load_preferences,
            save_preferences,
            load_reading_position,
            save_reading_position,
            refresh_shownotes,
            open_external,
            check_tool,
            list_cli_models,
            pick_executable,
            preview_video,
            check_source,
            download_subtitles,
            export_subtitles,
            pick_cookies_file,
            test_youtube_cookies,
            open_privacy_settings,
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
            tauri::RunEvent::Exit => {
                app.state::<ActiveQueries>()
                    .cancel_all_on_exit(Duration::from_millis(500));
                app.state::<Generation>()
                    .cancel_active_on_exit(GENERATION_CANCEL_GRACE);
            }
            _ => {}
        });
}

#[cfg(test)]
mod tests {
    use super::{
        article_file, configure_generation, fs, persist_exported_file, preview_document,
        remove_article_files, saved_transcript_file, validated_api_base_url, Generation, Locations,
        Provider, SidecarEvent,
    };
    use std::{
        os::unix::fs::PermissionsExt,
        path::Path,
        process::Stdio,
        sync::atomic::AtomicBool,
        time::{Duration, Instant},
    };
    use tokio::process::Command;

    #[test]
    fn reader_policy_allows_only_inlined_code_and_known_cover_images() {
        fn directive<'a>(policy: &'a str, name: &str) -> &'a str {
            policy
                .split(';')
                .map(str::trim)
                .find_map(|part| part.strip_prefix(name)?.strip_prefix(' '))
                .unwrap()
        }
        let cases: serde_json::Value =
            serde_json::from_str(include_str!("../../../tests/fixtures/cover_hosts.json")).unwrap();
        let policy = super::READER_CONTENT_SECURITY_POLICY;
        assert_eq!(directive(policy, "default-src"), "'none'");
        assert_eq!(directive(policy, "script-src"), "'unsafe-inline'");
        assert_eq!(directive(policy, "style-src"), "'unsafe-inline'");
        assert_eq!(directive(policy, "base-uri"), "'none'");
        assert_eq!(directive(policy, "form-action"), "'none'");
        let mut expected = vec!["data:"];
        expected.extend(
            cases["csp"]
                .as_array()
                .unwrap()
                .iter()
                .map(|value| value.as_str().unwrap()),
        );
        let actual: Vec<&str> = directive(policy, "img-src").split_whitespace().collect();
        assert_eq!(actual, expected);
    }

    #[test]
    fn shownotes_refresh_distinguishes_empty_and_updated_responses() {
        let empty: super::ShownotesRefresh = serde_json::from_value(serde_json::json!({
            "type": "shownotes", "status": "empty",
        }))
        .unwrap();
        assert_eq!(
            serde_json::to_value(empty).unwrap(),
            serde_json::json!({"status": "empty"})
        );

        let updated =
            serde_json::json!({"status": "updated", "revision": "new", "readingKey": "original"});
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
        let locations = Locations {
            data: dir.path().join("data"),
            library: dir.path().join("library"),
            english: AtomicBool::new(false),
        };
        super::credentials::store(&locations.data, "saved-chat-key").unwrap();
        super::credentials::store_asr(&locations.data, "saved-asr-key").unwrap();
        let mut options = super::AsrOptions {
            enabled: true,
            reuse_token: true,
            base_url: "https://dashscope.aliyuncs.com/api/v1".into(),
            model: "paraformer-v2".into(),
            max_minutes: 120,
            chunk_minutes: 5,
            ffmpeg_path: String::new(),
        };
        assert_eq!(
            options
                .test_token(&locations, Some("draft-chat-key".into()))
                .unwrap(),
            "draft-chat-key"
        );
        assert_eq!(options.test_token(&locations, None).unwrap(), "saved-chat-key");
        options.reuse_token = false;
        assert_eq!(
            options
                .test_token(&locations, Some("draft-asr-key".into()))
                .unwrap(),
            "draft-asr-key"
        );
        assert_eq!(options.test_token(&locations, None).unwrap(), "saved-asr-key");
        for reuse in [false, true] {
            options.reuse_token = reuse;
            for invalid in [
                String::new(),
                "   ".into(),
                "invalid\nkey".into(),
                "x".repeat(8193),
            ] {
                assert!(options.test_token(&locations, Some(invalid)).is_err());
            }
        }
    }

    #[test]
    fn an_explicit_asr_test_key_does_not_read_the_saved_credential() {
        let dir = tempfile::tempdir().unwrap();
        let locations = Locations {
            data: dir.path().join("data"),
            library: dir.path().join("library"),
            english: AtomicBool::new(false),
        };
        fs::create_dir_all(locations.data.join("api-token")).unwrap();
        let options = super::AsrOptions {
            enabled: true,
            reuse_token: true,
            base_url: "https://dashscope.aliyuncs.com/api/v1".into(),
            model: "paraformer-v2".into(),
            max_minutes: 120,
            chunk_minutes: 5,
            ffmpeg_path: String::new(),
        };
        assert!(options.test_token(&locations, None).is_err());
        assert_eq!(
            options.test_token(&locations, Some("new-key".into())).unwrap(),
            "new-key"
        );
    }

    #[test]
    fn export_filename_uses_display_title_without_path_components() {
        assert_eq!(
            super::export_file_stem("  标题 / Part: 2\\3\n. "),
            "标题 _ Part_ 2_3_"
        );
        assert_eq!(super::export_file_stem(" . . "), "Article");
        let title = super::export_file_stem(&"字幕🙂".repeat(100));
        assert!(title.len() <= 180 && !title.is_empty());
        assert!("字幕🙂".repeat(100).starts_with(&title));
    }

    #[test]
    fn exported_files_receive_readable_permissions() {
        let dir = tempfile::tempdir().unwrap();
        let target = dir.path().join("notes.md");
        persist_exported_file(target.clone(), Some("# Notes\n".into()), None).unwrap();
        assert_eq!(fs::metadata(&target).unwrap().permissions().mode() & 0o777, 0o644);
    }

    #[test]
    fn markdown_export_options_match_the_frontend_contract() {
        let options: super::MarkdownExportOptions = serde_json::from_value(serde_json::json!({
            "content": "readable", "includeTimestamped": false,
        }))
        .unwrap();
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
        command
            .args(["-c", script, "linkread-test-query"])
            .arg(pid_file)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        command
    }

    const QUERY_PROCESS_TREE: &str = r#"
        /bin/sh -c 'sleep 60 & printf "%s %s %s\n" "$2" "$$" "$!" > "$1"; wait' query-child "$1" "$$" &
        wait
    "#;

    async fn query_process_ids(pid_file: &Path, expected: usize) -> Vec<i32> {
        let started = Instant::now();
        loop {
            if let Ok(text) = fs::read_to_string(pid_file) {
                if let Ok(pids) = text
                    .split_whitespace()
                    .map(str::parse::<i32>)
                    .collect::<Result<Vec<_>, _>>()
                {
                    if text.ends_with('\n')
                        && pids.len() == expected
                        && pids.iter().all(|pid| *pid > 0)
                    {
                        return pids;
                    }
                }
            }
            assert!(
                started.elapsed() < Duration::from_secs(2),
                "query process tree did not start"
            );
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }

    fn query_process_is_running(pid: i32) -> bool {
        let output = std::process::Command::new("/bin/ps")
            .args(["-o", "stat=", "-p", &pid.to_string()])
            .output()
            .unwrap();
        assert!(output.status.success() || output.status.code() == Some(1));
        let state = String::from_utf8(output.stdout).unwrap();
        !state.trim().is_empty() && !state.trim().starts_with('Z')
    }

    async fn assert_query_processes_stopped(pids: &[i32]) {
        let started = Instant::now();
        loop {
            let running: Vec<_> = pids
                .iter()
                .copied()
                .filter(|pid| query_process_is_running(*pid))
                .collect();
            if running.is_empty() {
                return;
            }
            assert!(
                started.elapsed() < Duration::from_secs(2),
                "query left processes running: {running:?}"
            );
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    }

    #[test]
    fn query_timeout_stops_the_entire_sidecar_process_group() {
        let directory = tempfile::tempdir().unwrap();
        let pid_file = directory.path().join("pids");
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(async {
                let result = super::query_command::<serde_json::Value>(
                    query_test_command(QUERY_PROCESS_TREE, &pid_file),
                    Duration::from_secs(2),
                    "preview timeout",
                )
                .await;
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
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(async {
                let task = tokio::spawn(super::query_command::<serde_json::Value>(
                    query_test_command(QUERY_PROCESS_TREE, &pid_file),
                    Duration::from_secs(60),
                    "preview timeout",
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
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(async {
                let result = super::query_command::<serde_json::Value>(
                    query_test_command(script, &pid_file),
                    Duration::from_secs(2),
                    "preview timeout",
                )
                .await;
                let pids = query_process_ids(&pid_file, 2).await;
                let mut cleanup = super::QueryProcessGroup(Some(pids[0]));
                assert!(result.unwrap_err().starts_with("无法读取资料库"));
                assert_query_processes_stopped(&pids).await;
                cleanup.disarm();
            });
    }

    #[test]
    fn successful_query_preserves_its_json_response() {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(async {
                let result = super::query_command::<serde_json::Value>(
                    query_test_command(r#"printf '{"title":"ready"}'"#, Path::new("unused")),
                    Duration::from_secs(2),
                    "preview timeout",
                )
                .await
                .unwrap();
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
        assert_eq!(
            saved_transcript_file(&library, "article").unwrap(),
            folder.join("transcript.json")
        );
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
        let locations = Locations {
            data: root.path().to_path_buf(),
            library: root.path().join("library"),
            english: AtomicBool::new(false),
        };
        let source = root.path().join("captions.srt");
        fs::write(&source, "1\n00:00:00,000 --> 00:00:01,000\nhello\n").unwrap();
        super::credentials::store(&locations.data, "private-test-token").unwrap();
        let command = configure_generation(
            Command::new("python3"),
            &locations,
            source.to_str().unwrap(),
            Provider::Api,
            "",
            Some("https://api.example.com/v1"),
            Some("test-model"),
            false,
            true,
            None,
        )
        .unwrap();
        let args: Vec<_> = command
            .as_std()
            .get_args()
            .map(|v| v.to_string_lossy().into_owned())
            .collect();
        assert!(args.windows(2).any(|w| w == ["--provider", "api"]));
        assert!(args.windows(2).any(|w| w == ["--model", "test-model"]));
        assert!(args.contains(&"--local-file".to_owned()));
        assert!(!args.contains(&"--asr".to_owned()));
        assert!(!args.join(" ").contains("private-test-token"));
        assert!(command
            .as_std()
            .get_envs()
            .any(|(k, v)| k == "LINKREAD_API_TOKEN"
                && v.is_some_and(|value| value == "private-test-token")));
    }

    #[test]
    fn loopback_api_allows_omitted_token_while_remote_api_requires_token() {
        let root = tempfile::tempdir().unwrap();
        let locations = Locations {
            data: root.path().to_path_buf(),
            library: root.path().join("library"),
            english: AtomicBool::new(false),
        };
        let source = root.path().join("captions.srt");
        fs::write(&source, "1\n00:00:00,000 --> 00:00:01,000\nhello\n").unwrap();
        let local_cmd = configure_generation(
            Command::new("python3"),
            &locations,
            source.to_str().unwrap(),
            Provider::Api,
            "",
            Some("http://127.0.0.1:11434/v1"),
            Some("qwen"),
            false,
            true,
            None,
        )
        .unwrap();
        assert!(!local_cmd
            .as_std()
            .get_envs()
            .any(|(k, _)| k == "LINKREAD_API_TOKEN"));
        assert!(configure_generation(
            Command::new("python3"),
            &locations,
            source.to_str().unwrap(),
            Provider::Api,
            "",
            Some("https://api.example.com/v1"),
            Some("qwen"),
            false,
            true,
            None,
        )
        .is_err());
    }

    #[test]
    fn generation_passes_the_selected_ffmpeg_path_as_one_argument() {
        let root = tempfile::tempdir().unwrap();
        let locations = Locations {
            data: root.path().to_path_buf(),
            library: root.path().join("library"),
            english: AtomicBool::new(false),
        };
        super::credentials::store(&locations.data, "private-test-token").unwrap();
        let options: super::AsrOptions = serde_json::from_value(serde_json::json!({
            "enabled":true,"reuseToken":false,"baseUrl":"https://dashscope.aliyuncs.com/api/v1",
            "model":"paraformer-v2","maxMinutes":120,"chunkMinutes":5,
            "ffmpegPath":"/Users/yano/Audio Tools/ffmpeg"
        }))
        .unwrap();
        let command = configure_generation(
            Command::new("python3"),
            &locations,
            "https://www.youtube.com/watch?v=1-Fo8McCD9w",
            Provider::Api,
            "",
            Some("https://api.example.com/v1"),
            Some("test-model"),
            false,
            false,
            Some(&options),
        )
        .unwrap();
        let args: Vec<_> = command
            .as_std()
            .get_args()
            .map(|v| v.to_string_lossy().into_owned())
            .collect();
        assert!(args
            .windows(2)
            .any(|w| w == ["--ffmpeg-path", "/Users/yano/Audio Tools/ffmpeg"]));
        assert!(!args.join(" ").contains("private-test-token"));
        let old: super::AsrOptions = serde_json::from_value(serde_json::json!({
            "enabled":false,"reuseToken":false,"baseUrl":"https://dashscope.aliyuncs.com/api/v1",
            "model":"paraformer-v2","maxMinutes":120
        }))
        .unwrap();
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
        assert_eq!(
            generation.spawn("starting", &mut command).err().unwrap(),
            "已取消生成"
        );
        assert!(generation.finish("starting"));
        generation.begin("retry").unwrap();
        assert!(!generation.finish("retry"));
    }

    #[test]
    fn generation_cancellation_returns_immediately_when_process_exits_cleanly() {
        let directory = tempfile::tempdir().unwrap();
        let pid_file = directory.path().join("pids");
        let script = r#"
            trap 'exit 0' TERM
            printf "%s\n" "$$" > "$1"
            while :; do sleep 1; done
        "#;
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(async {
                let generation = std::sync::Arc::new(Generation::default());
                generation.begin("fast").unwrap();
                let worker = generation.clone();
                let mut command = query_test_command(script, &pid_file);
                let running = tokio::spawn(async move {
                    let channel = super::Channel::new(|_| Ok(()));
                    let result = super::run_generation(&worker, "fast", &mut command, channel).await;
                    assert!(worker.finish("fast"));
                    result
                });
                let pids = query_process_ids(&pid_file, 1).await;
                let started = Instant::now();
                generation
                    .cancel_with_grace("fast", Duration::from_secs(5))
                    .unwrap();
                let failure = tokio::time::timeout(Duration::from_millis(800), running)
                    .await
                    .expect("clean exit cancellation should not wait the full grace period")
                    .unwrap()
                    .err()
                    .expect("cancelled generation must not succeed");
                assert_eq!(failure.code, "cancelled");
                assert!(started.elapsed() < Duration::from_millis(800));
                assert_query_processes_stopped(&pids).await;
            });
    }

    #[test]
    fn generation_cancellation_kills_a_term_resistant_descendant_after_the_leader_exits() {
        let directory = tempfile::tempdir().unwrap();
        let pid_file = directory.path().join("pids");
        let script = r#"
            /bin/sh -c 'trap "" TERM; printf "%s %s\n" "$2" "$$" > "$1"; exec sleep 60' child "$1" "$$" >/dev/null &
            wait
        "#;
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(async {
                let generation = std::sync::Arc::new(Generation::default());
                generation.begin("stubborn").unwrap();
                let worker = generation.clone();
                let mut command = query_test_command(script, &pid_file);
                let running = tokio::spawn(async move {
                    let channel = super::Channel::new(|_| Ok(()));
                    let result =
                        super::run_generation(&worker, "stubborn", &mut command, channel).await;
                    assert!(worker.finish("stubborn"));
                    result
                });
                let pids = query_process_ids(&pid_file, 2).await;
                let grace = Duration::from_millis(150);
                let started = Instant::now();
                generation.cancel_with_grace("stubborn", grace).unwrap();
                assert!(generation.begin("next").is_err());
                let failure = tokio::time::timeout(grace + Duration::from_millis(750), running)
                    .await
                    .expect("generation did not finish within the cancellation limit")
                    .unwrap()
                    .err()
                    .expect("a cancelled generation must not succeed");
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
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(async {
                let generation = Generation::default();
                generation.begin("exit").unwrap();
                let mut command = query_test_command(script, &pid_file);
                let (mut process, _) = generation.spawn("exit", &mut command).unwrap();
                query_process_ids(&pid_file, 1).await;
                let grace = Duration::from_millis(100);
                let started = Instant::now();
                generation.cancel_active_on_exit(grace);
                let exit_status = tokio::time::timeout(Duration::from_millis(750), process.child.wait())
                    .await
                    .unwrap()
                    .unwrap();
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
        let events: Vec<SidecarEvent> =
            serde_json::from_str(include_str!("../../tests/fixtures/generation-progress.json"))
                .unwrap();
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
                        assert!(matches!(
                            request.stage.as_deref(),
                            Some("analysis" | "polish" | "publication")
                        ));
                        validated |= request.phase == "complete" && request.completed > 0;
                    }
                }
                SidecarEvent::Error {
                    code,
                    stage,
                    retryable,
                    action,
                    ..
                } => {
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
        assert!(validated_api_base_url("https://api.example.com/\tv1").is_err());
    }

    #[test]
    fn preview_preserves_script_strings_containing_html() {
        let original = "<body><script>const template = '</body>';</script></body>";
        let preview = preview_document(original.to_owned());
        assert!(preview.starts_with("<body><script>const template = '</body>';</script>"));
        assert!(preview.ends_with("</script>\n</body>"));
    }

    #[test]
    fn pure_json_manifest_archive_is_recognized_and_never_served_as_html() {
        let directory = tempfile::tempdir().unwrap();
        let folder = directory.path().join("pure-json-article");
        fs::create_dir_all(&folder).unwrap();
        let manifest = folder.join("manifest.json");
        let cached = folder.join(".reader.html");
        fs::write(&manifest, r#"{"version":1,"document_type":"linkread_manifest"}"#).unwrap();

        assert_eq!(article_file(directory.path(), "pure-json-article").unwrap(), manifest);
        // Without a materialized .reader.html cache, manifest.json must never be served as HTML.
        assert!(super::rendered_article_file(directory.path(), "pure-json-article").is_err());

        fs::write(&cached, "<!doctype html><p>rendered</p>").unwrap();
        assert_eq!(
            super::rendered_article_file(directory.path(), "pure-json-article").unwrap(),
            cached
        );
    }

    #[test]
    fn reader_cache_is_preferred_only_for_a_valid_archive() {
        let directory = tempfile::tempdir().unwrap();
        let folder = directory.path().join("article");
        fs::create_dir_all(&folder).unwrap();
        let original = folder.join("article.html");
        let cached = folder.join(".reader.html");
        fs::write(&original, "original").unwrap();
        assert_eq!(
            super::rendered_article_file(directory.path(), "article").unwrap(),
            original
        );
        fs::write(&cached, "upgraded").unwrap();
        assert_eq!(
            super::rendered_article_file(directory.path(), "article").unwrap(),
            cached
        );
        fs::remove_file(&cached).unwrap();
        std::os::unix::fs::symlink(&original, &cached).unwrap();
        assert!(super::rendered_article_file(directory.path(), "article").is_err());
        assert!(super::rendered_article_file(directory.path(), "../article").is_err());
    }

    #[test]
    fn shownotes_links_open_only_explicit_web_urls() {
        assert!(super::external_url("https://example.com/episode?chapter=2#references").is_ok());
        assert!(super::external_url("http://example.com/source").is_ok());
        for value in [
            "javascript:alert(1)",
            "file:///tmp/test",
            "data:text/html,test",
            "https://user:secret@example.com/",
            "https://exam\nple.com/",
        ] {
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
        let lock = fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .open(library.join(".article-state.lock"))
            .unwrap();
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
            mode: "browser".into(),
            browser: "Chrome".into(),
            file_path: String::new(),
        };
        assert_eq!(
            super::youtube_cookie_args("https://www.youtube.com/watch?v=1-Fo8McCD9w", Some(&browser))
                .unwrap(),
            vec!["--cookies-from-browser", "chrome"],
        );
        assert!(
            super::youtube_cookie_args("https://www.bilibili.com/video/BV1rxqmBhE91", Some(&browser))
                .unwrap()
                .is_empty()
        );

        let file = super::YoutubeCookieOptions {
            mode: "file".into(),
            browser: String::new(),
            file_path: cookies_txt.to_string_lossy().into_owned(),
        };
        assert_eq!(
            super::youtube_cookie_args("https://youtu.be/1-Fo8McCD9w", Some(&file)).unwrap(),
            vec![
                "--cookies".to_owned(),
                cookies_txt.to_string_lossy().into_owned()
            ],
        );

        let symlink = dir.path().join("link.txt");
        std::os::unix::fs::symlink(&cookies_txt, &symlink).unwrap();
        let bad_link = super::YoutubeCookieOptions {
            mode: "file".into(),
            browser: String::new(),
            file_path: symlink.to_string_lossy().into_owned(),
        };
        assert!(
            super::youtube_cookie_args("https://www.youtube.com/watch?v=1-Fo8McCD9w", Some(&bad_link))
                .is_err()
        );

        let bad_browser = super::YoutubeCookieOptions {
            mode: "browser".into(),
            browser: "unknown".into(),
            file_path: String::new(),
        };
        assert!(
            super::youtube_cookie_args("https://www.youtube.com/watch?v=1-Fo8McCD9w", Some(&bad_browser))
                .is_err()
        );
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
        let uri: tauri::http::Uri =
            "linkread-reader://localhost/cover/%E7%A4%BA%E4%BE%8B%20%5Byoutube-1-Fo8McCD9w%5D?v=1"
                .parse()
                .unwrap();
        let response = super::preview_response(&locations, &uri);
        assert_eq!(response.status(), tauri::http::StatusCode::OK);
        assert_eq!(
            response
                .headers()
                .get(tauri::http::header::CONTENT_TYPE)
                .unwrap(),
            "image/png"
        );
        assert_eq!(
            response
                .headers()
                .get(tauri::http::header::X_CONTENT_TYPE_OPTIONS)
                .unwrap(),
            "nosniff"
        );
        assert_eq!(response.body().as_slice(), png);

        let article_uri: tauri::http::Uri =
            "linkread-reader://localhost/%E7%A4%BA%E4%BE%8B%20%5Byoutube-1-Fo8McCD9w%5D"
                .parse()
                .unwrap();
        let html_response = super::preview_response(&locations, &article_uri);
        assert_eq!(
            html_response
                .headers()
                .get(tauri::http::header::X_CONTENT_TYPE_OPTIONS)
                .unwrap(),
            "nosniff"
        );

        let traversal: tauri::http::Uri =
            "linkread-reader://localhost/cover/..%2Fsecret".parse().unwrap();
        assert_eq!(
            super::preview_response(&locations, &traversal).status(),
            tauri::http::StatusCode::NOT_FOUND
        );

        fs::remove_file(article_dir.join("cover.png")).unwrap();
        let external = dir.path().join("secret.jpg");
        fs::write(&external, b"\xff\xd8\xff\xe0secret").unwrap();
        std::os::unix::fs::symlink(&external, article_dir.join("cover.jpg")).unwrap();
        assert_eq!(
            super::preview_response(&locations, &uri).status(),
            tauri::http::StatusCode::NOT_FOUND
        );
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

        let unfinished = library.join(format!("Video [{key}]"));
        fs::create_dir_all(unfinished.join(".ai-checkpoints")).unwrap();
        fs::write(unfinished.join("transcript.json"), "{}").unwrap();
        fs::write(unfinished.join(".ai-checkpoints/batch.json"), "{}").unwrap();

        let stage = library.join(".generation-staging/stage-one");
        fs::create_dir_all(&stage).unwrap();
        fs::write(
            stage.join("identity.json"),
            serde_json::json!({"version": 1, "sourceKey": key}).to_string(),
        )
        .unwrap();

        let other_stage = library.join(".generation-staging/stage-other");
        fs::create_dir_all(&other_stage).unwrap();
        fs::write(
            other_stage.join("identity.json"),
            serde_json::json!({"version": 1, "sourceKey": other_key}).to_string(),
        )
        .unwrap();

        let preview = library.join(".transcript-previews").join(&preview_id);
        fs::create_dir_all(&preview).unwrap();
        fs::write(
            preview.join("request.json"),
            serde_json::json!({"version": 1, "sourceKey": key, "requests": []}).to_string(),
        )
        .unwrap();

        let keep_preview = library.join(".transcript-previews").join(&other_preview);
        fs::create_dir_all(&keep_preview).unwrap();
        fs::write(
            keep_preview.join("request.json"),
            serde_json::json!({"version": 1, "sourceKey": other_key, "requests": []}).to_string(),
        )
        .unwrap();

        let asr_dir = library.join(".asr/fp-one");
        fs::create_dir_all(&asr_dir).unwrap();
        fs::write(
            asr_dir.join("transcript.json"),
            serde_json::json!({"source": {"url": "https://www.youtube.com/watch?v=1-Fo8McCD9w"}})
                .to_string(),
        )
        .unwrap();

        let managed_import = data.join("subtitle-imports").join(&preview_id);
        fs::create_dir_all(&managed_import).unwrap();
        let pasted_file = managed_import.join("pasted.srt");
        fs::write(&pasted_file, "1\n00:00:00,000 --> 00:00:01,000\nhi\n").unwrap();

        super::remove_task_files(
            &library,
            Some(&data),
            Some(pasted_file.to_str().unwrap()),
            true,
            Some(key),
            Some(&preview_id),
        )
        .unwrap();
        assert!(unfinished.exists());
        assert!(stage.exists());
        assert!(preview.exists());
        assert!(asr_dir.exists());
        assert!(managed_import.exists());
        assert!(other_stage.exists());
        assert!(keep_preview.exists());

        let published_id = format!("Published [{other_key}]");
        let published = library.join(&published_id);
        fs::create_dir_all(&published).unwrap();
        fs::write(published.join("article.html"), "<html>ok</html>").unwrap();
        fs::write(published.join("cover.jpg"), b"\xff\xd8\xff\xe0").unwrap();
        super::remove_task_files(&library, Some(&data), None, false, Some(other_key), None)
            .unwrap();
        assert!(published.join("article.html").exists());
        assert!(other_stage.exists());
        assert!(keep_preview.exists());

        let backup = library.join(".linkread-backups").join(&published_id);
        fs::create_dir_all(&backup).unwrap();
        super::durable_state::save_position(
            &data,
            "youtube:2-OtherVid9w",
            &serde_json::json!({
                "version": 1, "updatedAt": 1000, "activeTab": "summary", "tabs": {}, "mindmap": null,
            }),
        )
        .unwrap();
        remove_article_files(&library, &published_id).unwrap();
        super::durable_state::delete_position(&data, "youtube:2-OtherVid9w").unwrap();
        assert!(!published.exists());
        assert!(!backup.exists());
        assert!(!other_stage.exists());
        assert!(!keep_preview.exists());
        assert!(stage.exists());
        assert!(asr_dir.exists());
        assert_eq!(
            super::durable_state::load_position(&data, "youtube:2-OtherVid9w").unwrap(),
            serde_json::Value::Null
        );
    }

    #[test]
    fn deleting_article_never_deadlocks_against_active_per_stage_lock() {
        let dir = tempfile::tempdir().unwrap();
        let library = dir.path().join("library");
        let stages = library.join(".generation-staging");
        let active_key = "youtube-1-Fo8McCD9w";
        let other_key = "youtube-2-OtherVid9w";

        let active_stage = stages.join("stage-active");
        fs::create_dir_all(&active_stage).unwrap();
        fs::write(
            active_stage.join("identity.json"),
            serde_json::json!({"version": 1, "sourceKey": active_key}).to_string(),
        )
        .unwrap();

        let other_stage = stages.join("stage-other");
        fs::create_dir_all(&other_stage).unwrap();
        fs::write(
            other_stage.join("identity.json"),
            serde_json::json!({"version": 1, "sourceKey": other_key}).to_string(),
        )
        .unwrap();

        let article_a_id = format!("Active [{active_key}]");
        let article_b_id = format!("Other [{other_key}]");
        for id in [&article_a_id, &article_b_id] {
            let folder = library.join(id);
            fs::create_dir_all(&folder).unwrap();
            fs::write(folder.join("article.html"), "<html>ok</html>").unwrap();
        }

        let held_stage_lock =
            super::try_lock_file(&stages.join(".stage-active.lock")).unwrap().unwrap();

        remove_article_files(&library, &article_b_id).unwrap();
        assert!(!library.join(&article_b_id).exists());
        assert!(!other_stage.exists());
        assert!(active_stage.exists());

        let err = remove_article_files(&library, &article_a_id).unwrap_err();
        assert!(err.contains("暂存目录正在处理中"));
        assert!(active_stage.exists());
        drop(held_stage_lock);
    }

    #[test]
    fn cli_generation_forwards_validated_model_and_reasoning_effort() {
        let root = tempfile::tempdir().unwrap();
        let locations = Locations {
            data: root.path().to_path_buf(),
            library: root.path().join("library"),
            english: AtomicBool::new(false),
        };
        let codex_cmd = super::configure_generation_with_resume(
            Command::new("python3"),
            &locations,
            "https://www.youtube.com/watch?v=1-Fo8McCD9w",
            Provider::Codex,
            "",
            None,
            Some("gpt-6.2-sol"),
            Some("xhigh"),
            false,
            false,
            None,
            None,
            None,
            None,
        )
        .unwrap();
        let codex_args: Vec<_> = codex_cmd
            .as_std()
            .get_args()
            .map(|v| v.to_string_lossy().into_owned())
            .collect();
        assert!(codex_args.windows(2).any(|w| w == ["--provider", "codex"]));
        assert!(codex_args.windows(2).any(|w| w == ["--model", "gpt-6.2-sol"]));
        assert!(codex_args
            .windows(2)
            .any(|w| w == ["--reasoning-effort", "xhigh"]));

        let claude_cmd = super::configure_generation_with_resume(
            Command::new("python3"),
            &locations,
            "https://www.youtube.com/watch?v=1-Fo8McCD9w",
            Provider::Claude,
            "",
            None,
            Some("opus"),
            Some("high"),
            false,
            false,
            None,
            None,
            None,
            None,
        )
        .unwrap();
        let claude_args: Vec<_> = claude_cmd
            .as_std()
            .get_args()
            .map(|v| v.to_string_lossy().into_owned())
            .collect();
        assert!(claude_args.windows(2).any(|w| w == ["--provider", "claude"]));
        assert!(claude_args.windows(2).any(|w| w == ["--model", "opus"]));
        assert!(claude_args
            .windows(2)
            .any(|w| w == ["--reasoning-effort", "high"]));

        assert!(super::configure_generation_with_resume(
            Command::new("python3"),
            &locations,
            "https://www.youtube.com/watch?v=1-Fo8McCD9w",
            Provider::Claude,
            "",
            None,
            Some("opus"),
            Some("ultra"),
            false,
            false,
            None,
            None,
            None,
            None,
        )
        .is_err());

        assert!(super::configure_generation_with_resume(
            Command::new("python3"),
            &locations,
            "https://www.youtube.com/watch?v=1-Fo8McCD9w",
            Provider::Codex,
            "",
            None,
            Some("--injected"),
            None,
            false,
            false,
            None,
            None,
            None,
            None,
        )
        .is_err());
    }

    #[test]
    fn privacy_settings_panes_use_allowlisted_macos_urls() {
        assert_eq!(
            super::PrivacySettingsPane::FullDiskAccess.macos_url(),
            "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
        );
        assert_eq!(
            super::PrivacySettingsPane::Privacy.macos_url(),
            "x-apple.systempreferences:com.apple.preference.security?Privacy"
        );
        let parsed: super::PrivacySettingsPane =
            serde_json::from_str("\"full_disk_access\"").unwrap();
        assert_eq!(parsed, super::PrivacySettingsPane::FullDiskAccess);
        assert!(
            serde_json::from_str::<super::PrivacySettingsPane>("\"file:///etc/passwd\"").is_err()
        );
    }

    #[test]
    fn youtube_cookie_test_preserves_structured_error_code_and_success() {
        tauri::async_runtime::block_on(async {
            let mut denied = Command::new("/bin/sh");
            denied
                .args([
                    "-c",
                    r#"printf '%s\n' '{"type":"error","code":"youtube_cookies_keychain_denied","message":"keychain blocked"}'; exit 1"#,
                ])
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::null());
            let result =
                super::run_youtube_cookie_test(denied, Duration::from_secs(5), "timeout", None)
                    .await
                    .unwrap();
            assert!(!result.ok);
            assert_eq!(
                result.code.as_deref(),
                Some("youtube_cookies_keychain_denied")
            );
            assert_eq!(result.message, "keychain blocked");
            assert_eq!(result.is_dev, cfg!(debug_assertions));

            let mut ok_cmd = Command::new("/bin/sh");
            ok_cmd
                .args([
                    "-c",
                    r#"printf '%s\n' '{"type":"youtube_cookies_test","ok":true,"message":"cookies ready"}'"#,
                ])
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::null());
            let ok_result =
                super::run_youtube_cookie_test(ok_cmd, Duration::from_secs(5), "timeout", None)
                    .await
                    .unwrap();
            assert!(ok_result.ok);
            assert_eq!(ok_result.code, None);
            assert_eq!(ok_result.message, "cookies ready");
        });
    }
}

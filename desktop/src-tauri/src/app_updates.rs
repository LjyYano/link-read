use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, State};
use tauri_plugin_updater::{Update, UpdaterExt};
use tokio::sync::Mutex;

use crate::{AppResult, Generation};

#[derive(Default)]
pub struct AppUpdates(Mutex<Option<Update>>);

#[derive(Serialize)]
#[serde(
    tag = "status",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum UpdateCheck {
    Unconfigured,
    Current {
        current_version: String,
    },
    Available {
        current_version: String,
        version: String,
    },
}

#[tauri::command]
pub async fn check_app_update(
    app: AppHandle,
    updates: State<'_, AppUpdates>,
) -> AppResult<UpdateCheck> {
    let mut pending = updates.0.try_lock().map_err(|_| "update_busy")?;
    *pending = None;
    if !app.config().plugins.0.contains_key("updater") {
        return Ok(UpdateCheck::Unconfigured);
    }
    let updater = app
        .updater_builder()
        .timeout(Duration::from_secs(15))
        .configure_client(|client| {
            client
                .https_only(true)
                .connect_timeout(Duration::from_secs(10))
        })
        .build()
        .map_err(|_| "update_check_failed")?;
    let checked = tokio::time::timeout(Duration::from_secs(15), updater.check())
        .await
        .map_err(|_| "update_check_failed")?
        .map_err(|_| "update_check_failed")?;
    let current_version = app.package_info().version.to_string();
    let result = match checked.as_ref() {
        Some(update) => UpdateCheck::Available {
            current_version,
            version: update.version.clone(),
        },
        None => UpdateCheck::Current { current_version },
    };
    *pending = checked;
    Ok(result)
}

#[tauri::command]
pub async fn install_app_update(
    app: AppHandle,
    updates: State<'_, AppUpdates>,
    generation: State<'_, Generation>,
    version: String,
) -> AppResult<()> {
    let mut pending = updates.0.try_lock().map_err(|_| "update_busy")?;
    let mut update = pending
        .as_ref()
        .filter(|update| update.version == version)
        .ok_or("update_check_failed")?
        .clone();
    update.timeout = Some(Duration::from_secs(600));
    let _installing = generation.begin_update()?;
    let bytes = update
        .download(|_, _| {}, || {})
        .await
        .map_err(|_| "update_download_failed")?;
    tauri::async_runtime::spawn_blocking(move || update.install(bytes))
        .await
        .map_err(|_| "update_install_failed")?
        .map_err(|_| "update_install_failed")?;
    *pending = None;
    app.request_restart();
    Ok(())
}

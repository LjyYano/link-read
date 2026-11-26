use tauri::{
    menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem, Submenu},
    AppHandle, Emitter, EventTarget,
};

const SETTINGS: &str = "open-settings";
const UPDATES: &str = "check-updates";

fn application_menu(menu: &Menu<tauri::Wry>) -> tauri::Result<Option<Submenu<tauri::Wry>>> {
    Ok(menu
        .items()?
        .first()
        .and_then(|item| item.as_submenu())
        .cloned())
}

pub fn build(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let menu = Menu::default(app)?;
    if let Some(submenu) = application_menu(&menu)? {
        let updates = MenuItem::with_id(app, UPDATES, "Check for Updates…", true, None::<&str>)?;
        let settings = MenuItem::with_id(app, SETTINGS, "Settings…", true, Some("Cmd+,"))?;
        let separator = PredefinedMenuItem::separator(app)?;
        submenu.insert_items(&[&updates, &separator, &settings], 1)?;
    }
    Ok(menu)
}

pub fn localize(app: &AppHandle, english: bool) -> tauri::Result<()> {
    if let Some(menu) = app.menu() {
        if let Some(submenu) = application_menu(&menu)? {
            for (id, label) in [
                (SETTINGS, if english { "Settings…" } else { "设置…" }),
                (
                    UPDATES,
                    if english {
                        "Check for Updates…"
                    } else {
                        "检查更新…"
                    },
                ),
            ] {
                if let Some(item) = submenu.get(id).and_then(|item| item.as_menuitem().cloned()) {
                    item.set_text(label)?;
                }
            }
        }
    }
    Ok(())
}

pub fn dispatch(app: &AppHandle, event: MenuEvent) {
    let id = event.id().as_ref();
    if matches!(id, SETTINGS | UPDATES) {
        let _ = app.emit_to(EventTarget::webview("main"), "linkread-menu", id);
    }
}

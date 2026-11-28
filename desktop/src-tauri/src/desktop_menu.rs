use tauri::{
    menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem, Submenu},
    AppHandle, Emitter, EventTarget,
};

const SETTINGS: &str = "open-settings";
const UPDATES: &str = "check-updates";
const NEW_ARTICLE: &str = "new-article";
const IMPORT_SUBTITLE: &str = "import-subtitle";
const PRINT_ARTICLE: &str = "print-article";
const NAV_HOME: &str = "nav-home";
const NAV_LIBRARY: &str = "nav-library";
const QUICK_OPEN: &str = "quick-open";
const TOGGLE_TASKS: &str = "toggle-tasks";
const TOGGLE_SIDEBAR: &str = "toggle-sidebar";

const ALL_MENU_IDS: &[&str] = &[
    SETTINGS,
    UPDATES,
    NEW_ARTICLE,
    IMPORT_SUBTITLE,
    PRINT_ARTICLE,
    NAV_HOME,
    NAV_LIBRARY,
    QUICK_OPEN,
    TOGGLE_TASKS,
    TOGGLE_SIDEBAR,
];

fn localized_label(id: &str, english: bool) -> Option<&'static str> {
    Some(match (id, english) {
        (SETTINGS, true) => "Settings…",
        (SETTINGS, false) => "设置…",
        (UPDATES, true) => "Check for Updates…",
        (UPDATES, false) => "检查更新…",
        (NEW_ARTICLE, true) => "New Article…",
        (NEW_ARTICLE, false) => "新建文章…",
        (IMPORT_SUBTITLE, true) => "Import Subtitles…",
        (IMPORT_SUBTITLE, false) => "导入字幕…",
        (PRINT_ARTICLE, true) => "Print / Save PDF…",
        (PRINT_ARTICLE, false) => "打印 / 存储为 PDF…",
        (NAV_HOME, true) => "Home",
        (NAV_HOME, false) => "首页",
        (NAV_LIBRARY, true) => "Bookshelf",
        (NAV_LIBRARY, false) => "书架",
        (QUICK_OPEN, true) => "Quick Open…",
        (QUICK_OPEN, false) => "快速打开…",
        (TOGGLE_TASKS, true) => "Task Center",
        (TOGGLE_TASKS, false) => "任务中心",
        (TOGGLE_SIDEBAR, true) => "Toggle Contents Sidebar",
        (TOGGLE_SIDEBAR, false) => "切换目录侧栏",
        _ => return None,
    })
}

fn application_menu(menu: &Menu<tauri::Wry>) -> tauri::Result<Option<Submenu<tauri::Wry>>> {
    Ok(menu
        .items()?
        .first()
        .and_then(|item| item.as_submenu())
        .cloned())
}

fn find_submenu(menu: &Menu<tauri::Wry>, titles: &[&str]) -> tauri::Result<Option<Submenu<tauri::Wry>>> {
    for item in menu.items()? {
        if let Some(submenu) = item.as_submenu() {
            if let Ok(title) = submenu.text() {
                if titles.iter().any(|candidate| title.eq_ignore_ascii_case(candidate)) {
                    return Ok(Some(submenu.clone()));
                }
            }
        }
    }
    Ok(None)
}

pub fn build(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let menu = Menu::default(app)?;
    if let Some(submenu) = application_menu(&menu)? {
        let updates = MenuItem::with_id(app, UPDATES, "Check for Updates…", true, None::<&str>)?;
        let settings = MenuItem::with_id(app, SETTINGS, "Settings…", true, Some("Cmd+,"))?;
        let separator = PredefinedMenuItem::separator(app)?;
        submenu.insert_items(&[&updates, &separator, &settings], 1)?;
    }

    let new_article = MenuItem::with_id(app, NEW_ARTICLE, "New Article…", true, Some("Cmd+N"))?;
    let import_subtitle = MenuItem::with_id(app, IMPORT_SUBTITLE, "Import Subtitles…", true, Some("Cmd+O"))?;
    let print_article = MenuItem::with_id(app, PRINT_ARTICLE, "Print / Save PDF…", true, Some("Cmd+P"))?;
    let file_sep = PredefinedMenuItem::separator(app)?;
    if let Some(file_menu) = find_submenu(&menu, &["File", "文件"])? {
        file_menu.insert_items(&[&new_article, &import_subtitle, &print_article, &file_sep], 0)?;
    } else {
        let file_menu = Submenu::with_items(app, "File", true, &[&new_article, &import_subtitle, &print_article])?;
        menu.insert(&file_menu, 1)?;
    }

    let nav_home = MenuItem::with_id(app, NAV_HOME, "Home", true, Some("Cmd+1"))?;
    let nav_library = MenuItem::with_id(app, NAV_LIBRARY, "Bookshelf", true, Some("Cmd+2"))?;
    let nav_sep = PredefinedMenuItem::separator(app)?;
    let quick_open = MenuItem::with_id(app, QUICK_OPEN, "Quick Open…", true, Some("Cmd+K"))?;
    let toggle_tasks = MenuItem::with_id(app, TOGGLE_TASKS, "Task Center", true, Some("Cmd+Shift+T"))?;
    let toggle_sidebar = MenuItem::with_id(app, TOGGLE_SIDEBAR, "Toggle Contents Sidebar", true, Some("Cmd+Backslash"))?;
    let view_sep = PredefinedMenuItem::separator(app)?;
    if let Some(view_menu) = find_submenu(&menu, &["View", "显示"])? {
        view_menu.insert_items(
            &[
                &nav_home,
                &nav_library,
                &nav_sep,
                &quick_open,
                &toggle_tasks,
                &toggle_sidebar,
                &view_sep,
            ],
            0,
        )?;
    } else {
        let view_menu = Submenu::with_items(
            app,
            "View",
            true,
            &[&nav_home, &nav_library, &nav_sep, &quick_open, &toggle_tasks, &toggle_sidebar],
        )?;
        menu.append(&view_menu)?;
    }

    Ok(menu)
}

pub fn localize(app: &AppHandle, english: bool) -> tauri::Result<()> {
    if let Some(menu) = app.menu() {
        for item in menu.items()? {
            if let Some(submenu) = item.as_submenu() {
                for &id in ALL_MENU_IDS {
                    if let Some(label) = localized_label(id, english) {
                        if let Some(menu_item) = submenu.get(id).and_then(|entry| entry.as_menuitem().cloned()) {
                            menu_item.set_text(label)?;
                        }
                    }
                }
            }
        }
    }
    Ok(())
}

pub fn dispatch(app: &AppHandle, event: MenuEvent) {
    let id = event.id().as_ref();
    if ALL_MENU_IDS.contains(&id) {
        let _ = app.emit_to(EventTarget::webview("main"), "linkread-menu", id);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_menu_item_has_chinese_and_english_labels() {
        for &id in ALL_MENU_IDS {
            let en = localized_label(id, true).unwrap();
            let zh = localized_label(id, false).unwrap();
            assert!(!en.is_empty(), "missing English label for {id}");
            assert!(!zh.is_empty(), "missing Chinese label for {id}");
            assert_ne!(en, zh, "label should differ between locales for {id}");
        }
    }
}

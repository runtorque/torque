use serde::Deserialize;
use tauri::menu::{Menu, MenuBuilder, MenuItem, SubmenuBuilder};
use tauri::{Manager, Runtime};

pub const MENU_NEW_GROUP: &str = "new_group";
pub const MENU_NEW_ARCHITECT: &str = "new_architect";
pub const MENU_CLOSE_WINDOW: &str = "close_window";
pub const MENU_RELOAD: &str = "reload";
pub const MENU_FORCE_RELOAD: &str = "force_reload";
pub const MENU_DEVTOOLS: &str = "devtools";
pub const MENU_DETACH_ACTIVE_PANEL: &str = "detach_active_panel";
pub const MENU_REATTACH_PANEL: &str = "reattach_panel";
pub const MENU_REATTACH_ALL: &str = "reattach_all";
pub const MENU_SHOW_LOGS: &str = "show_logs";
pub const MENU_REVEAL_LOGS: &str = "reveal_logs";
pub const MENU_DOCUMENTATION: &str = "documentation";
pub const MENU_KEYBOARD_SHORTCUTS: &str = "keyboard_shortcuts";
pub const MENU_SHOW_WELCOME: &str = "show_welcome";

const CHEATSHEET_JSON: &str = include_str!(concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../static/cheatsheet/cheatsheet.json"
));

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct ShortcutSpec {
    pub id: String,
    pub title: String,
    #[serde(default)]
    pub accelerator: String,
    #[serde(default)]
    pub description: String,
}

#[derive(Debug, Deserialize)]
struct CheatsheetDoc {
    #[serde(default)]
    categories: Vec<ShortcutCategory>,
}

#[derive(Debug, Deserialize)]
struct ShortcutCategory {
    #[serde(default)]
    items: Vec<ShortcutSpec>,
}

pub fn shortcut_specs() -> Vec<ShortcutSpec> {
    serde_json::from_str::<CheatsheetDoc>(CHEATSHEET_JSON)
        .map(|doc| {
            doc.categories
                .into_iter()
                .flat_map(|category| category.items)
                .collect()
        })
        .unwrap_or_default()
}

pub fn accelerator_for(id: &str) -> Option<String> {
    shortcut_specs()
        .into_iter()
        .find(|item| item.id == id)
        .and_then(|item| {
            let text = item.accelerator.trim().to_string();
            if text.is_empty() {
                None
            } else {
                Some(text)
            }
        })
}

fn menu_item<R, M>(manager: &M, id: &str, text: &str) -> tauri::Result<MenuItem<R>>
where
    R: Runtime,
    M: Manager<R>,
{
    MenuItem::with_id(manager, id, text, true, accelerator_for(id))
}

fn menu_item_with_accel<R, M>(
    manager: &M,
    id: &str,
    text: &str,
    accelerator: Option<&str>,
) -> tauri::Result<MenuItem<R>>
where
    R: Runtime,
    M: Manager<R>,
{
    let accel = accelerator
        .map(str::to_string)
        .or_else(|| accelerator_for(id));
    MenuItem::with_id(manager, id, text, true, accel)
}

pub fn build_main_menu<R, M>(manager: &M) -> tauri::Result<Menu<R>>
where
    R: Runtime,
    M: Manager<R>,
{
    let new_group = menu_item(manager, MENU_NEW_GROUP, "New Group")?;
    let new_architect = menu_item_with_accel(
        manager,
        MENU_NEW_ARCHITECT,
        "New Architect",
        Some("CmdOrCtrl+Alt+P"),
    )?;
    let close_window = menu_item(manager, MENU_CLOSE_WINDOW, "Close Window")?;
    let file = SubmenuBuilder::new(manager, "File")
        .item(&new_group)
        .item(&new_architect)
        .separator()
        .item(&close_window)
        .build()?;

    let edit = SubmenuBuilder::new(manager, "Edit")
        .cut()
        .copy()
        .paste()
        .build()?;

    let reload = menu_item(manager, MENU_RELOAD, "Reload")?;
    let force_reload = menu_item(manager, MENU_FORCE_RELOAD, "Force Reload")?;
    let show_logs = menu_item(manager, MENU_SHOW_LOGS, "Show Logs")?;
    let devtools = menu_item(manager, MENU_DEVTOOLS, "Developer Tools")?;
    let view = SubmenuBuilder::new(manager, "View")
        .item(&reload)
        .item(&force_reload)
        .separator()
        .item(&show_logs)
        .separator()
        .item(&devtools)
        .build()?;

    let detach_active = menu_item(manager, MENU_DETACH_ACTIVE_PANEL, "Detach Active Panel")?;
    let reattach_panel = menu_item_with_accel(
        manager,
        MENU_REATTACH_PANEL,
        "Reattach Panel",
        Some("CmdOrCtrl+Alt+W"),
    )?;
    let reattach_all = menu_item(manager, MENU_REATTACH_ALL, "Reattach All Panels")?;
    let window = SubmenuBuilder::new(manager, "Window")
        .item(&detach_active)
        .item(&reattach_panel)
        .item(&reattach_all)
        .build()?;

    let keyboard = menu_item(manager, MENU_KEYBOARD_SHORTCUTS, "Keyboard Shortcuts")?;
    let welcome = menu_item(manager, MENU_SHOW_WELCOME, "Show Welcome")?;
    let reveal_logs = menu_item_with_accel(manager, MENU_REVEAL_LOGS, "Reveal Logs Folder", None)?;
    let docs = menu_item_with_accel(manager, MENU_DOCUMENTATION, "Torque Documentation", None)?;
    let help = SubmenuBuilder::new(manager, "Help")
        .item(&keyboard)
        .item(&welcome)
        .separator()
        .item(&reveal_logs)
        .item(&docs)
        .build()?;

    MenuBuilder::new(manager)
        .item(&file)
        .item(&edit)
        .item(&view)
        .item(&window)
        .item(&help)
        .build()
}

pub fn build_detached_panel_menu<R, M>(manager: &M) -> tauri::Result<Menu<R>>
where
    R: Runtime,
    M: Manager<R>,
{
    // Detached panels intentionally omit main-window creation commands.
    let close_window = menu_item(manager, MENU_CLOSE_WINDOW, "Close Window")?;
    let file = SubmenuBuilder::new(manager, "File")
        .item(&close_window)
        .build()?;

    let edit = SubmenuBuilder::new(manager, "Edit")
        .cut()
        .copy()
        .paste()
        .build()?;

    let reload = menu_item(manager, MENU_RELOAD, "Reload")?;
    let force_reload = menu_item(manager, MENU_FORCE_RELOAD, "Force Reload")?;
    let show_logs = menu_item(manager, MENU_SHOW_LOGS, "Show Logs")?;
    let view = SubmenuBuilder::new(manager, "View")
        .item(&reload)
        .item(&force_reload)
        .separator()
        .item(&show_logs)
        .build()?;

    let reattach_panel = menu_item_with_accel(
        manager,
        MENU_REATTACH_PANEL,
        "Reattach Panel",
        Some("CmdOrCtrl+Alt+W"),
    )?;
    let reattach_all = menu_item(manager, MENU_REATTACH_ALL, "Reattach All Panels")?;
    let window = SubmenuBuilder::new(manager, "Window")
        .item(&reattach_panel)
        .item(&reattach_all)
        .build()?;

    let keyboard = menu_item(manager, MENU_KEYBOARD_SHORTCUTS, "Keyboard Shortcuts")?;
    let welcome = menu_item(manager, MENU_SHOW_WELCOME, "Show Welcome")?;
    let reveal_logs = menu_item_with_accel(manager, MENU_REVEAL_LOGS, "Reveal Logs Folder", None)?;
    let docs = menu_item_with_accel(manager, MENU_DOCUMENTATION, "Torque Documentation", None)?;
    let help = SubmenuBuilder::new(manager, "Help")
        .item(&keyboard)
        .item(&welcome)
        .separator()
        .item(&reveal_logs)
        .item(&docs)
        .build()?;

    MenuBuilder::new(manager)
        .item(&file)
        .item(&edit)
        .item(&view)
        .item(&window)
        .item(&help)
        .build()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accelerator_metadata_comes_from_cheatsheet_json() {
        assert_eq!(
            accelerator_for(MENU_NEW_GROUP).as_deref(),
            Some("CmdOrCtrl+N")
        );
        assert_eq!(
            accelerator_for(MENU_DETACH_ACTIVE_PANEL).as_deref(),
            Some("CmdOrCtrl+Alt+D")
        );
        assert_eq!(
            accelerator_for(MENU_KEYBOARD_SHORTCUTS).as_deref(),
            Some("CmdOrCtrl+Shift+/")
        );
    }

    #[test]
    fn shortcut_specs_include_unique_ids() {
        let specs = shortcut_specs();
        assert!(!specs.is_empty());
        let mut ids = std::collections::HashSet::new();
        for spec in specs {
            assert!(
                ids.insert(spec.id.clone()),
                "duplicate shortcut id {}",
                spec.id
            );
        }
    }
}

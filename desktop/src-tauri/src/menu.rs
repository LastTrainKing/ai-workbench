//! The native application menu.
//!
//! A desktop application has a menu bar. Its absence is one of the smaller tells
//! that a window is a web page in a box — and more practically, it is where
//! "restart the backend" and "open the log folder" belong, because those are
//! exactly the actions a user needs when something has gone wrong and the UI
//! cannot help them.
//!
//! The two items that matter most are the two that work when nothing else does:
//! restarting the backend, and opening the folder with the logs. Everything else
//! here is convenience.
//!
//! Menu events are handled by id in `main.rs`, so this module stays declarative.

use tauri::menu::{Menu, MenuBuilder, MenuItemBuilder, PredefinedMenuItem, SubmenuBuilder};
use tauri::{AppHandle, Runtime};

/// Menu item identifiers. Kept as constants so the builder and the event handler
/// cannot drift apart — a mismatch there fails silently, which is the worst kind.
pub const ID_RESTART: &str = "aw.restart-backend";
pub const ID_LOGS: &str = "aw.open-logs";
pub const ID_FOCUS_SEARCH: &str = "aw.focus-search";
pub const ID_FULLSCREEN: &str = "aw.toggle-fullscreen";
pub const ID_ABOUT: &str = "aw.about";
pub const ID_API_DOCS: &str = "aw.api-docs";

pub fn build<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<Menu<R>> {
    let restart = MenuItemBuilder::with_id(ID_RESTART, "重启后端服务")
        .accelerator("CmdOrCtrl+Shift+R")
        .build(app)?;
    let logs = MenuItemBuilder::with_id(ID_LOGS, "打开日志目录").build(app)?;

    // 文件
    let file = SubmenuBuilder::new(app, "文件")
        .item(&restart)
        .item(&logs)
        .separator()
        .item(&PredefinedMenuItem::quit(app, Some("退出"))?)
        .build()?;

    // 视图
    let focus = MenuItemBuilder::with_id(ID_FOCUS_SEARCH, "聚焦搜索")
        .accelerator("CmdOrCtrl+K")
        .build(app)?;
    let fullscreen = MenuItemBuilder::with_id(ID_FULLSCREEN, "全屏")
        .accelerator("F11")
        .build(app)?;

    let view = SubmenuBuilder::new(app, "视图")
        .item(&focus)
        .item(&fullscreen)
        .build()?;

    // 帮助
    let about = MenuItemBuilder::with_id(ID_ABOUT, "关于 AI Workbench").build(app)?;
    let docs = MenuItemBuilder::with_id(ID_API_DOCS, "打开 API 文档").build(app)?;

    let help = SubmenuBuilder::new(app, "帮助")
        .item(&about)
        .item(&docs)
        .build()?;

    MenuBuilder::new(app).items(&[&file, &view, &help]).build()
}

// AI Workbench desktop shell.
//
// This is a Windows host for the existing application, not a second application.
// It owns exactly three things:
//
//   1. the native window (WebView2), so the UI no longer runs inside Edge or
//      Chrome and there is no 127.0.0.1:5173 for the user to see;
//   2. starting the backend — infrastructure first, then the API, each gated on
//      a real readiness probe, reusing the same scripts the CLI uses;
//   3. a startup screen that shows progress and, on failure, the actual reason
//      and the log path instead of a blank window.
//
// All business logic stays in FastAPI. Nothing about RAG, agents, tools or the
// database is reimplemented here.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod commands;
mod lifecycle;
mod menu;

use std::sync::Arc;

use tauri::{Emitter, Manager, RunEvent};
use std::os::windows::process::CommandExt;

/// Windows: never show a console window for a spawned helper process.
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

fn main() {
    let state = Arc::new(backend::BackendState::new());

    tauri::Builder::default()
        .manage(state.clone())
        .invoke_handler(tauri::generate_handler![
            commands::startup_status,
            commands::restart_backend,
            commands::open_log_folder,
            commands::window_ready,
        ])
        .on_menu_event(|app, event| {
            // One place, keyed by id. A menu whose items silently do nothing is
            // worse than no menu, so every id below has a branch.
            let id = event.id().as_ref();
            lifecycle::log(&format!("menu: {id}"));
            match id {
                menu::ID_RESTART => {
                    if let Some(state) = app.try_state::<Arc<backend::BackendState>>() {
                        let _ = commands::restart_backend(app.clone(), state);
                    }
                }
                menu::ID_LOGS => {
                    if let Some(state) = app.try_state::<Arc<backend::BackendState>>() {
                        let _ = commands::open_log_folder(state);
                    }
                }
                menu::ID_FOCUS_SEARCH => {
                    // The command palette lives in the frontend; the menu asks it
                    // to open rather than reimplementing it here.
                    let _ = app.emit("menu://focus-search", ());
                }
                menu::ID_ABOUT => {
                    let _ = app.emit("menu://about", ());
                }
                menu::ID_API_DOCS => {
                    // Opened in the user's browser on purpose: API documentation
                    // is a web page, and forcing it into the application window
                    // would replace the app with a reference document.
                    let _ = std::process::Command::new("cmd")
                        .args(["/C", "start", "", "http://127.0.0.1:8000/docs"])
                        .creation_flags(CREATE_NO_WINDOW)
                        .spawn();
                }
                menu::ID_FULLSCREEN => {
                    if let Some(window) = app.get_webview_window("main") {
                        let current = window.is_fullscreen().unwrap_or(false);
                        let _ = window.set_fullscreen(!current);
                    }
                }
                _ => {}
            }
        })
        .setup(move |app| {
            let handle = app.handle().clone();

            // The menu is installed before the window is shown, so it is there
            // from the first frame rather than appearing a moment later.
            match menu::build(app.handle()) {
                Ok(menu) => {
                    if let Err(exc) = app.set_menu(menu) {
                        lifecycle::log(&format!("menu: could not be installed: {exc}"));
                    }
                }
                Err(exc) => lifecycle::log(&format!("menu: could not be built: {exc}")),
            }

            // Startup runs on its own thread so the splash window paints
            // immediately. Every step reports progress through events.
            backend::spawn_startup(handle, state);

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the AI Workbench window")
        .run(|app_handle, event| {
            // Every branch logs, because "the hook did not fire" was an inference
            // last time and an inference is not a diagnosis.
            match event {
                RunEvent::ExitRequested { .. } => {
                    lifecycle::log("event: ExitRequested");
                    match app_handle.try_state::<Arc<backend::BackendState>>() {
                        Some(state) => {
                            lifecycle::log("event: ExitRequested -> calling stop()");
                            state.stop();
                        }
                        None => lifecycle::log("event: ExitRequested -> NO STATE (cannot stop)"),
                    }
                }
                RunEvent::Exit => {
                    lifecycle::log("event: Exit (process is ending)");
                }
                _ => {}
            }
        });
}

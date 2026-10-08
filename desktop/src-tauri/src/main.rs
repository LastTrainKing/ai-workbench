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
            commands::set_startup_draft,
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

            // Make the window fit the screen it was opened on.
            //
            // Measured on this machine: the work area is 1536x912 logical (a 1920x1200
            // display at 125% scaling), and the configured window was 1600x1000 logical —
            // **larger than the screen in both directions**. The window hung off the right
            // and bottom edges, and the bottom is where the composer is: the one control
            // the user has to reach. A screenshot of the startup screen was cropped exactly
            // there, which is how this was found.
            //
            // **The two numbers are in different units.** `outer_size()` is logical;
            // `monitor.size()` is physical. Comparing them directly is why the first
            // version of this clamp never fired: 1309x886 logical against a 1920x1200
            // physical limit looks like it fits, and it does not — 886 logical is 1107
            // physical, which is 27 pixels past the bottom of a 1080-tall screen. The
            // scale factor is what converts between them.
            if let Some(window) = app.get_webview_window("main") {
                if let (Ok(Some(monitor)), Ok(size)) = (window.primary_monitor(), window.outer_size()) {
                    let scale = monitor.scale_factor();
                    let screen = monitor.size();
                    // Room for the taskbar and the window frame.
                    let max_width = screen.width.saturating_sub(96);
                    let max_height = screen.height.saturating_sub(140);
                    let physical_width = (f64::from(size.width) * scale).round() as u32;
                    let physical_height = (f64::from(size.height) * scale).round() as u32;

                    if physical_width > max_width || physical_height > max_height {
                        let fitted = tauri::LogicalSize::new(
                            f64::from(size.width).min(f64::from(max_width) / scale),
                            f64::from(size.height).min(f64::from(max_height) / scale),
                        );
                        let _ = window.set_size(fitted);
                        let _ = window.center();
                        lifecycle::log(&format!(
                            "window: {}x{} logical did not fit {}x{} physical at scale {scale}, resized",
                            size.width, size.height, screen.width, screen.height
                        ));
                    }
                }
            }

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

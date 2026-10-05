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

use std::sync::Arc;

use tauri::{Manager, RunEvent};

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
        .setup(move |app| {
            let handle = app.handle().clone();

            // Startup runs on its own thread so the splash window paints
            // immediately. Every step reports progress through events.
            backend::spawn_startup(handle, state);

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the AI Workbench window")
        .run(|app_handle, event| {
            // Closing the window must not leave a backend running behind it.
            if let RunEvent::ExitRequested { .. } = event {
                if let Some(state) = app_handle.try_state::<Arc<backend::BackendState>>() {
                    state.stop();
                }
            }
        });
}

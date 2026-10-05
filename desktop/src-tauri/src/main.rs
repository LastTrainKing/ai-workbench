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

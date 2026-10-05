//! Tauri commands exposed to the splash screen.
//!
//! SCOPE — read this before adding a command.
//!
//! These are *shell* operations only: window and process lifecycle for the
//! desktop host. They are deliberately narrow, take no free-form command string,
//! and cannot reach the operating system on the caller's behalf.
//!
//! There is intentionally no `run_anything(command)` here, and there never should
//! be. Everything the agent can do to the machine — files, terminal, git,
//! processes, network — goes through the FastAPI backend, because that is where
//! the Permission Manager, the workspace sandbox, the risk classification, the
//! audit log and the cancellation handling live. Putting a general-purpose
//! executor in the Tauri layer would bypass every one of those.
//!
//! Data flow, and the only one that is allowed:
//!
//!     React  ->  Tauri command (this file, lifecycle only)
//!            ->  FastAPI  ->  Permission Manager  ->  Tool Executor  ->  OS
//!
//! Every argument below is either absent or an enum; none of them is a path or a
//! command supplied by the page.

use std::sync::Arc;

use serde::Serialize;
use tauri::State;

use crate::backend::{BackendState, StartupStatus};

#[derive(Serialize)]
pub struct CommandResult {
    pub ok: bool,
    pub message: String,
}

/// Current startup phase. Read-only; the page cannot influence it.
#[tauri::command]
pub fn startup_status(state: State<'_, Arc<BackendState>>) -> StartupStatus {
    state.status()
}

/// The page reports that the application UI has finished loading.
///
/// Used only to dismiss the splash reliably, rather than guessing from a timer.
/// The argument is a bare boolean, so nothing can be injected through it.
#[tauri::command]
pub fn window_ready(state: State<'_, Arc<BackendState>>, loaded: bool) -> CommandResult {
    CommandResult {
        ok: state.is_ready() && loaded,
        message: if loaded {
            "ui-loaded".into()
        } else {
            "ui-loading".into()
        },
    }
}

/// Stop and start the backend again.
///
/// No arguments: the project path, the ports and the scripts are resolved inside
/// Rust. A restart is a lifecycle action, not a shell escape hatch.
#[tauri::command]
pub fn restart_backend(app: tauri::AppHandle, state: State<'_, Arc<BackendState>>) -> CommandResult {
    let state = state.inner().clone();
    state.stop();
    std::thread::sleep(std::time::Duration::from_millis(800));
    crate::backend::spawn_startup(app, state);
    CommandResult {
        ok: true,
        message: "Restarting".into(),
    }
}

/// Open the log directory in the file manager.
///
/// The path is computed in Rust from the discovered project root; the page cannot
/// pass a path, so this cannot be used to open an arbitrary location.
#[tauri::command]
pub fn open_log_folder(state: State<'_, Arc<BackendState>>) -> CommandResult {
    let root = state.project_root();
    let Some(root) = root else {
        return CommandResult {
            ok: false,
            message: "Project directory is not known yet".into(),
        };
    };
    let logs = root.join(".runtime").join("logs");
    if !logs.is_dir() {
        return CommandResult {
            ok: false,
            message: format!("No log directory at {}", logs.display()),
        };
    }

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        let opened = std::process::Command::new("explorer")
            .arg(&logs)
            .creation_flags(CREATE_NO_WINDOW)
            .spawn();
        return match opened {
            Ok(_) => CommandResult {
                ok: true,
                message: logs.display().to_string(),
            },
            Err(exc) => CommandResult {
                ok: false,
                message: exc.to_string(),
            },
        };
    }

    #[cfg(not(windows))]
    {
        CommandResult {
            ok: false,
            message: format!("Logs are in {}", logs.display()),
        }
    }
}

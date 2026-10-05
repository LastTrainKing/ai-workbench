//! Backend lifecycle for the desktop shell.
//!
//! Responsibilities, and deliberately nothing more:
//!   * find the project directory
//!   * if the backend is not already up, run `scripts\start-desktop.bat` with no
//!     console window
//!   * poll the API's health endpoint over a loopback socket until it answers
//!   * tell the window where to go, or why it could not
//!
//! The readiness rules mirror the CLI exactly: a listening port is not proof of
//! readiness, so the shell waits for a real HTTP 200 from `/api/v1/health`. That
//! endpoint only answers once the API has completed startup (database, worker,
//! vector store), so a 200 here means the application is genuinely usable.

use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

use crate::lifecycle;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

/// Suppresses the console window. Without this the launcher flashes a black box,
/// which is exactly what the desktop build exists to remove.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub const API_HOST: &str = "127.0.0.1";
pub const API_PORT: u16 = 8000;
pub const HEALTH_PATH: &str = "/api/v1/health";

/// How long the whole startup may take before it is reported as failed.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(300);
/// Interval between health probes.
const POLL_INTERVAL: Duration = Duration::from_millis(700);

#[derive(Debug, Clone, Serialize)]
pub struct StartupStatus {
    pub phase: String,
    pub message: String,
    pub detail: String,
    pub ok: bool,
    pub finished: bool,
}

impl StartupStatus {
    fn progress(phase: &str, message: &str) -> Self {
        Self {
            phase: phase.into(),
            message: message.into(),
            detail: String::new(),
            ok: false,
            finished: false,
        }
    }

    fn failed(message: &str, detail: &str) -> Self {
        Self {
            phase: "failed".into(),
            message: message.into(),
            detail: detail.into(),
            ok: false,
            finished: true,
        }
    }
}

pub struct BackendState {
    /// The launcher process, when this shell started one.
    child: Mutex<Option<Child>>,
    /// Kill-on-close job holding the launcher and everything it spawns.
    ///
    /// Held for the life of the process on purpose: the limit fires when the
    /// last handle closes, and the process exiting closes it for us. Dropping it
    /// early would kill the backend while the window is still open.
    job: Mutex<Option<lifecycle::JobGuard>>,
    /// True once `/api/v1/health` answered 200.
    ready: AtomicBool,
    cancelled: AtomicBool,
    project_root: Mutex<Option<PathBuf>>,
    status: Mutex<StartupStatus>,
}

impl BackendState {
    pub fn new() -> Self {
        Self {
            child: Mutex::new(None),
            job: Mutex::new(None),
            ready: AtomicBool::new(false),
            cancelled: AtomicBool::new(false),
            project_root: Mutex::new(None),
            status: Mutex::new(StartupStatus::progress("boot", "Starting")),
        }
    }

    pub fn is_ready(&self) -> bool {
        self.ready.load(Ordering::SeqCst)
    }

    pub fn status(&self) -> StartupStatus {
        self.status.lock().unwrap().clone()
    }

    /// The project directory, once it has been located.
    pub fn project_root(&self) -> Option<PathBuf> {
        self.project_root.lock().unwrap().clone()
    }

    fn set_status(&self, status: StartupStatus) {
        *self.status.lock().unwrap() = status;
    }

    /// Shut the backend down, so closing the window really closes the app.
    ///
    /// Two things have to happen, and the first alone is not enough:
    ///
    /// 1. the launcher process tree is killed — it may still be starting services;
    /// 2. `stop-desktop.bat` is run — the services themselves are started
    ///    *detached*, so they are not in the launcher's process tree and would
    ///    otherwise keep running after the window has gone.
    ///
    /// Leaving PostgreSQL, Redis, Qdrant and the API alive behind a closed window
    /// is not what a desktop application should do.
    pub fn stop(&self) {
        lifecycle::log("stop: entered");
        self.cancelled.store(true, Ordering::SeqCst);

        {
            let mut guard = self.child.lock().unwrap();
            match guard.as_mut() {
                Some(child) => {
                    let pid = child.id();
                    lifecycle::log(&format!("stop: killing launcher tree pid {pid}"));
                    let _ = Command::new("taskkill")
                        .args(["/PID", &pid.to_string(), "/T", "/F"])
                        .creation_flags(CREATE_NO_WINDOW)
                        .stdout(Stdio::null())
                        .stderr(Stdio::null())
                        .status();
                }
                None => lifecycle::log("stop: no launcher child (backend was already running)"),
            }
            *guard = None;
        }

        // Resolve the project root here rather than reading stored state.
        //
        // The stored root is only written once the startup sequence has located
        // it, and that sequence returns early when the backend is already
        // running. So on a second launch — exactly the case a user hits after
        // reopening the app — the stored root is None and the old code returned
        // here, silently stopping nothing. `locate_project_root` works at any
        // time because it walks up from the executable.
        let root = match self.project_root() {
            Some(root) => {
                lifecycle::log(&format!("stop: root from state: {}", root.display()));
                root
            }
            None => match lifecycle::locate_project_root() {
                Some(root) => {
                    lifecycle::log(&format!("stop: root located on demand: {}", root.display()));
                    root
                }
                None => {
                    lifecycle::log("stop: ABORT - project root could not be located");
                    return;
                }
            },
        };

        let script = root.join("scripts").join("stop-desktop.bat");
        if !script.is_file() {
            lifecycle::log(&format!("stop: ABORT - missing {}", script.display()));
            return;
        }
        lifecycle::log(&format!("stop: running {}", script.display()));

        let spawned = Command::new("cmd")
            .args(["/C", &script.to_string_lossy()])
            .current_dir(&root)
            .creation_flags(CREATE_NO_WINDOW)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .stdin(Stdio::null())
            .spawn();

        match spawned {
            Ok(mut child) => {
                lifecycle::log(&format!("stop: shutdown script started, pid {}", child.id()));
                let deadline = Instant::now() + Duration::from_secs(45);
                loop {
                    if Instant::now() >= deadline {
                        lifecycle::log("stop: shutdown script exceeded 45s, killing it");
                        break;
                    }
                    match child.try_wait() {
                        Ok(Some(status)) => {
                            lifecycle::log(&format!("stop: shutdown script exited with {status}"));
                            break;
                        }
                        Ok(None) => std::thread::sleep(Duration::from_millis(300)),
                        Err(exc) => {
                            lifecycle::log(&format!("stop: try_wait failed: {exc}"));
                            break;
                        }
                    }
                }
                let _ = child.kill();
            }
            Err(exc) => {
                lifecycle::log(&format!("stop: shutdown script FAILED to start: {exc}"));
            }
        }
        lifecycle::log("stop: finished");
    }
}

impl Default for BackendState {
    fn default() -> Self {
        Self::new()
    }
}

/// Locate the project directory.
///
/// Order matters. An explicit override wins so a user can move the checkout; then
/// the ancestors of the running executable are searched, which covers both the
/// development layout (`desktop/src-tauri/target/<profile>/`) and a portable
/// layout with the exe beside the project.
fn find_project_root() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("AI_WORKBENCH_HOME") {
        let candidate = PathBuf::from(explicit);
        if candidate.join("scripts").join("start-desktop.bat").is_file() {
            return Some(candidate);
        }
    }

    if let Ok(exe) = std::env::current_exe() {
        for ancestor in exe.ancestors().skip(1).take(8) {
            if ancestor.join("scripts").join("start-desktop.bat").is_file() {
                return Some(ancestor.to_path_buf());
            }
        }
    }

    // Conventional location used by this project's installer.
    if let Ok(profile) = std::env::var("USERPROFILE") {
        let candidate = Path::new(&profile).join("Desktop").join("ai-workbench");
        if candidate.join("scripts").join("start-desktop.bat").is_file() {
            return Some(candidate);
        }
    }

    None
}

/// Minimal HTTP GET over a loopback socket.
///
/// The shell only ever asks one local endpoint a yes/no question, so a full HTTP
/// client would be dead weight (and would drag a TLS stack into the build). This
/// speaks just enough HTTP/1.1 to read the status line.
fn http_status_ok(host: &str, port: u16, path: &str, timeout: Duration) -> bool {
    let addr = match (host, port).to_socket_addrs() {
        Ok(mut it) => match it.next() {
            Some(a) => a,
            None => return false,
        },
        Err(_) => return false,
    };

    let mut stream = match TcpStream::connect_timeout(&addr, timeout) {
        Ok(s) => s,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));

    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\nUser-Agent: ai-workbench-desktop\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }

    let mut buffer = [0u8; 512];
    let read = match stream.read(&mut buffer) {
        Ok(n) => n,
        Err(_) => return false,
    };
    let head = String::from_utf8_lossy(&buffer[..read]);
    head.starts_with("HTTP/1.1 200") || head.starts_with("HTTP/1.0 200")
}

fn backend_is_healthy() -> bool {
    http_status_ok(API_HOST, API_PORT, HEALTH_PATH, Duration::from_secs(3))
}

/// Read the tail of the startup error log, so a failure can be explained in the
/// window instead of only in a file the user has to find.
fn error_log_tail(root: &Path) -> String {
    let path = root.join(".runtime").join("logs").join("startup-error.log");
    match std::fs::read_to_string(&path) {
        Ok(text) => {
            let lines: Vec<&str> = text.lines().rev().take(6).collect();
            lines.into_iter().rev().collect::<Vec<_>>().join("\n")
        }
        Err(_) => String::new(),
    }
}

fn emit(app: &AppHandle, status: StartupStatus) {
    let _ = app.emit("startup://status", status);
}

/// Run the startup sequence on a background thread.
pub fn spawn_startup(app: AppHandle, state: Arc<BackendState>) {
    std::thread::spawn(move || {
        // Point the log at the project first, so one launch produces one file.
        // Without this the first few lines land in the temp fallback and the
        // record is split in two.
        if let Some(root) = lifecycle::locate_project_root() {
            lifecycle::set_log_root(&root);
        }

        lifecycle::log("startup: begin");

        // Create the job object before any branch is taken.
        //
        // It used to be created inside the spawn path, which meant a launch that
        // found the backend already running had no safety net at all: force-kill
        // that instance and PostgreSQL, Redis, Qdrant and the API all survive.
        // The lifecycle log is what made this visible — the early-return path
        // logged no `job: created` line.
        {
            let mut guard = state.job.lock().unwrap();
            if guard.is_none() {
                *guard = lifecycle::JobGuard::create();
            }
            match guard.as_ref() {
                Some(_) => lifecycle::log("startup: job object ready (before any branch)"),
                None => lifecycle::log("startup: job object unavailable; relying on the graceful path"),
            }
        }

        // ---------------------------------------------------------- already up --
        if backend_is_healthy() {
            // Note the consequence: the project root is never *stored* on this
            // path, which is why stop() must be able to find it itself.
            lifecycle::log("startup: backend already healthy, returning early");
            // Still point the log at the project so the shutdown that follows is
            // recorded next to everything else rather than in the temp folder.
            if let Some(root) = lifecycle::locate_project_root() {
                lifecycle::set_log_root(&root);
                lifecycle::log("startup: log root set on the early-return path");
            }
            state.set_status(StartupStatus {
                phase: "ready".into(),
                message: "Backend already running".into(),
                detail: format!("http://{API_HOST}:{API_PORT}"),
                ok: true,
                finished: true,
            });
            state.ready.store(true, Ordering::SeqCst);
            emit(&app, state.status());
            navigate_to_app(&app);
            return;
        }

        // ------------------------------------------------------------ project --
        let root = match find_project_root() {
            Some(path) => path,
            None => {
                let status = StartupStatus::failed(
                    "Project directory not found",
                    "The shell could not locate scripts\\start-desktop.bat. \
                     Set AI_WORKBENCH_HOME to the project directory and start again.",
                );
                state.set_status(status.clone());
                emit(&app, status);
                return;
            }
        };
        *state.project_root.lock().unwrap() = Some(root.clone());
        lifecycle::set_log_root(&root);
        lifecycle::log(&format!("startup: project root = {}", root.display()));

        // -------------------------------------------------------------- spawn --
        let launcher = root.join("scripts").join("start-desktop.bat");
        state.set_status(StartupStatus::progress(
            "launching",
            "Starting PostgreSQL, Redis, Qdrant and the API...",
        ));
        emit(&app, state.status());

        let child = Command::new("cmd")
            .args(["/C", &launcher.to_string_lossy()])
            .current_dir(&root)
            .creation_flags(CREATE_NO_WINDOW)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .stdin(Stdio::null())
            .spawn();

        match child {
            Ok(process) => {
                // The safety net: if this process dies for any reason — a crash,
                // a force-kill, the user ending it from Task Manager — the
                // operating system terminates everything in the job.
                let job_guard = state.job.lock().unwrap();
                match job_guard.as_ref() {
                    Some(job) => {
                        if !job.assign(&process) {
                            lifecycle::log("startup: job assignment failed; relying on the graceful path");
                        }
                    }
                    None => lifecycle::log("startup: no job object; relying on the graceful path"),
                }
                drop(job_guard);

                *state.child.lock().unwrap() = Some(process);
            }
            Err(exc) => {
                let status = StartupStatus::failed(
                    "Could not run the startup script",
                    &format!("{}: {exc}", launcher.display()),
                );
                state.set_status(status.clone());
                emit(&app, status);
                return;
            }
        }

        // --------------------------------------------------------------- wait --
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        let mut last_note = Instant::now();

        while Instant::now() < deadline {
            if state.cancelled.load(Ordering::SeqCst) {
                return;
            }
            if backend_is_healthy() {
                state.ready.store(true, Ordering::SeqCst);
                state.set_status(StartupStatus {
                    phase: "ready".into(),
                    message: "Ready".into(),
                    detail: format!("http://{API_HOST}:{API_PORT}"),
                    ok: true,
                    finished: true,
                });
                emit(&app, state.status());
                navigate_to_app(&app);
                return;
            }
            // Reassure the user that something is happening, without pretending
            // it is finished: the elapsed time is real, the state is not "ready".
            if last_note.elapsed() >= Duration::from_secs(5) {
                last_note = Instant::now();
                let elapsed = STARTUP_TIMEOUT.as_secs() - deadline.saturating_duration_since(Instant::now()).as_secs();
                let status = StartupStatus::progress(
                    "waiting",
                    &format!("Waiting for the backend... {elapsed}s"),
                );
                state.set_status(status.clone());
                emit(&app, status);
            }
            std::thread::sleep(POLL_INTERVAL);
        }

        let detail = error_log_tail(&root);
        let status = StartupStatus::failed(
            "The backend did not become ready in time",
            if detail.is_empty() {
                "See .runtime\\logs\\startup.log for the full sequence."
            } else {
                &detail
            },
        );
        state.set_status(status.clone());
        emit(&app, status);
    });
}

fn navigate_to_app(app: &AppHandle) {
    let url = format!("http://{API_HOST}:{API_PORT}/");
    if let Some(window) = app.get_webview_window("main") {
        if let Ok(parsed) = url.parse() {
            let _ = window.navigate(parsed);
        }
    }
}

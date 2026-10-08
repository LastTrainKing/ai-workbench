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

/// One service's readiness, as observed rather than assumed.
#[derive(Debug, Clone, Serialize)]
pub struct ServiceStatus {
    pub name: String,
    /// "pending" | "starting" | "ready" | "failed"
    ///
    /// `starting` exists so a row can light up when the work on it begins. Reporting a
    /// service that is being started as "pending" is what made the splash screen read
    /// as frozen for the first eleven seconds.
    pub state: String,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct StartupStatus {
    pub phase: String,
    pub message: String,
    pub detail: String,
    pub ok: bool,
    pub finished: bool,
    /// Per-service readiness, in the order the user should read it.
    pub services: Vec<ServiceStatus>,
}

/// The services the startup script brings up, with the port that proves it.
const SERVICES: [(&str, u16); 3] = [("PostgreSQL", 5432), ("Redis", 6379), ("Qdrant", 6333)];

/// How much of `startup.log` to read for the "what is it doing now" line.
///
/// The tail only: the file is append-only and grows across launches, and the last few
/// lines are the only ones that describe the current run.
const STARTUP_LOG_TAIL_BYTES: u64 = 8 * 1024;

/// One service's state as the *script* reports it, keyed by the name in the log.
///
/// The states here are deliberately the script's own vocabulary (`STARTING`, `READY`,
/// `SKIPPED`, `FAILED`) rather than a parallel set invented for the UI: two vocabularies
/// for one fact is one too many, and the log is the record the user is told to read.
fn log_states(root: &Path) -> std::collections::HashMap<String, String> {
    let mut states = std::collections::HashMap::new();
    let path = root.join(".runtime").join("logs").join("startup.log");
    let Ok(mut file) = std::fs::File::open(&path) else {
        return states;
    };

    // Read the tail rather than the file. `read_to_string` is not usable at all here:
    // `start-desktop.bat` writes its own lines with `echo`, which cmd.exe encodes as
    // ANSI, so the file contains bytes that are not valid UTF-8 (the localised weekday
    // in `2026/10/08 周四 09:16:24`). Decoding lossily keeps the lines that matter.
    let length = file.metadata().map(|m| m.len()).unwrap_or(0);
    if length > STARTUP_LOG_TAIL_BYTES {
        use std::io::Seek;
        let _ = file.seek(std::io::SeekFrom::End(-(STARTUP_LOG_TAIL_BYTES as i64)));
    }
    let mut bytes = Vec::new();
    let _ = file.read_to_end(&mut bytes);
    let text = String::from_utf8_lossy(&bytes);

    for line in text.lines() {
        let mut parts = line.split_whitespace();
        // `YYYY-MM-DD HH:MM:SS Service STATE - detail`. The .bat's lines have a different
        // shape (`2026/10/08 ...`) and simply do not match, which is correct: they are
        // milestones, not service states.
        let (Some(date), Some(_time), Some(service), Some(state)) =
            (parts.next(), parts.next(), parts.next(), parts.next())
        else {
            continue;
        };
        if date.len() != 10 || date.as_bytes()[4] != b'-' {
            continue;
        }
        if !matches!(state, "STARTING" | "READY" | "FAILED" | "SKIPPED" | "STOPPED") {
            continue;
        }
        states.insert(service.to_string(), state.to_string());
    }
    states
}

/// The last thing the startup script said, so the window can show it.
///
/// Read from the log rather than guessed from elapsed time: a splash that says
/// "starting PostgreSQL" on a timer would lie exactly when the user is looking for
/// truth — which is the complaint this addresses.
fn last_activity(root: &Path) -> String {
    let path = root.join(".runtime").join("logs").join("startup.log");
    let Ok(mut file) = std::fs::File::open(&path) else {
        return String::new();
    };
    let length = file.metadata().map(|m| m.len()).unwrap_or(0);
    if length > STARTUP_LOG_TAIL_BYTES {
        use std::io::Seek;
        let _ = file.seek(std::io::SeekFrom::End(-(STARTUP_LOG_TAIL_BYTES as i64)));
    }
    let mut bytes = Vec::new();
    let _ = file.read_to_end(&mut bytes);
    let text = String::from_utf8_lossy(&bytes);
    let Some(line) = text.lines().rev().find(|l| !l.trim().is_empty()) else {
        return String::new();
    };
    // Drop the timestamp so what is left is the sentence a person reads.
    let trimmed = line.trim();
    let body = match trimmed.find(" - ") {
        Some(at) => &trimmed[at + 3..],
        None => trimmed,
    };
    let body = body.rsplit(" - ").next().unwrap_or(body);
    body.trim().chars().take(160).collect()
}

/// Probe every service. Cheap TCP connects; called on each poll tick.
///
/// The API is not in this list because it is checked over HTTP elsewhere and a
/// port being open is not the same as the API being ready to answer.
///
/// The TCP result wins when it says "listening" — that is a measurement. When it does
/// not, the script's own log says whether the service has *started* yet, which is the
/// difference between "nothing has happened" and "this one is being worked on right
/// now". Without that, every row is grey for the whole launch and the screen reads as
/// frozen, which is what the user reported at the eleven-second mark.
fn probe_services(root: Option<&Path>) -> Vec<ServiceStatus> {
    let states = root.map(log_states).unwrap_or_default();
    let mut out: Vec<ServiceStatus> = SERVICES
        .iter()
        .map(|(name, port)| {
            let open = std::net::TcpStream::connect_timeout(
                &format!("127.0.0.1:{port}")
                    .parse()
                    .unwrap_or_else(|_| "127.0.0.1:1".parse().expect("literal")),
                Duration::from_millis(400),
            )
            .is_ok();
            let logged = states.get(*name).map(String::as_str).unwrap_or("");
            let state = if open {
                "ready"
            } else {
                match logged {
                    "STARTING" => "starting",
                    "FAILED" => "failed",
                    _ => "pending",
                }
            };
            ServiceStatus {
                name: (*name).to_string(),
                state: state.into(),
                detail: format!("127.0.0.1:{port}"),
            }
        })
        .collect();

    let api_logged = states.get("api").map(String::as_str).unwrap_or("");
    out.push(ServiceStatus {
        name: "API".into(),
        state: if backend_is_healthy() {
            "ready".into()
        } else if api_logged == "STARTING" {
            "starting".into()
        } else if api_logged == "FAILED" {
            "failed".into()
        } else {
            "pending".into()
        },
        detail: format!("http://{API_HOST}:{API_PORT}"),
    });
    out
}

impl StartupStatus {
    fn progress(phase: &str, message: &str, root: Option<&Path>) -> Self {
        Self {
            phase: phase.into(),
            message: message.into(),
            detail: root.map(last_activity).unwrap_or_default(),
            ok: false,
            finished: false,
            services: probe_services(root),
        }
    }

    fn failed(message: &str, detail: &str, root: Option<&Path>) -> Self {
        Self {
            phase: "failed".into(),
            message: message.into(),
            detail: detail.into(),
            ok: false,
            finished: true,
            services: probe_services(root),
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
    /// What the user typed on the startup screen, before the app was reachable.
    ///
    /// Pushed in on every keystroke by `commands::set_startup_draft`, and read once by
    /// `navigate_to_app`. See that function for why it is not read out of the page.
    ///
    /// `pub(crate)` because `commands` is a sibling module, not a child: a private field
    /// is reachable from the defining module and its descendants only, and `commands.rs`
    /// is neither.
    pub(crate) draft: Mutex<String>,
}

impl BackendState {
    pub fn new() -> Self {
        Self {
            child: Mutex::new(None),
            job: Mutex::new(None),
            ready: AtomicBool::new(false),
            cancelled: AtomicBool::new(false),
            project_root: Mutex::new(None),
            status: Mutex::new(StartupStatus::progress("boot", "Starting", None)),
            draft: Mutex::new(String::new()),
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
    match std::fs::read(&path) {
        // Lossy, because `start-desktop.bat` writes ANSI and `read_to_string` would
        // return nothing at all for the whole file over one unmappable byte.
        Ok(bytes) => {
            let text = String::from_utf8_lossy(&bytes);
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

        // Say something immediately, before anything slow happens.
        //
        // The window shows this file from the moment it opens, and the first event used
        // to be emitted only after the project had been located and the launcher
        // spawned. In between, the page fell back to its own initial state — every row
        // grey, no activity line — which is the "reads as frozen" the user reported.
        state.set_status(StartupStatus::progress(
            "launching",
            "Starting PostgreSQL, Redis, Qdrant and the API...",
            None,
        ));
        emit(&app, state.status());

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
            let mut early_root: Option<PathBuf> = None;
            if let Some(root) = lifecycle::locate_project_root() {
                lifecycle::set_log_root(&root);
                lifecycle::log("startup: log root set on the early-return path");
                // A backend that was already running was started by an *earlier* shell, and
                // if that shell was elevated its PostgreSQL belongs to the Task Scheduler
                // rather than to any job. Adopt it here too, or this instance's force-kill
                // would leave the database behind even though it never started it.
                lifecycle::adopt_escaped_services(state.job.lock().unwrap().as_ref(), &root);
                early_root = Some(root);
            }
            state.set_status(StartupStatus {
                phase: "ready".into(),
                message: "Backend already running".into(),
                detail: format!("http://{API_HOST}:{API_PORT}"),
                ok: true,
                finished: true,
                services: probe_services(early_root.as_deref()),
            });
            state.ready.store(true, Ordering::SeqCst);
            emit(&app, state.status());
            navigate_to_app(&app, &state);
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
                    None,
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
            Some(&root),
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
                    Some(&root),
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
                // The backend is answering, so the database is up and its pid file exists.
                // This is the first moment PostgreSQL can be adopted into the job — see
                // `lifecycle::adopt_escaped_services` for why it might not have inherited it.
                lifecycle::adopt_escaped_services(state.job.lock().unwrap().as_ref(), &root);
                state.set_status(StartupStatus {
                    phase: "ready".into(),
                    message: "Ready".into(),
                    detail: format!("http://{API_HOST}:{API_PORT}"),
                    ok: true,
                    finished: true,
                    services: probe_services(Some(&root)),
                });
                emit(&app, state.status());
                navigate_to_app(&app, &state);
                return;
            }
            // One second, not five. The per-service rows and the activity line come from
            // the startup log, and at a five-second cadence a service that starts and
            // becomes ready in between never appeared at all — the row jumped from grey
            // to green with nothing in between. One second is under the shortest service
            // this machine starts (Redis, measured at 0.3–1 s) and costs one file tail
            // read plus four TCP connects.
            if last_note.elapsed() >= Duration::from_secs(1) {
                last_note = Instant::now();
                let status = StartupStatus::progress(
                    "waiting",
                    "Starting PostgreSQL, Redis, Qdrant and the API...",
                    Some(&root),
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
            Some(&root),
        );
        state.set_status(status.clone());
        emit(&app, status);
    });
}

fn navigate_to_app(app: &AppHandle, state: &BackendState) {
    // Carry over whatever the user typed while the services were starting.
    //
    // The window now opens on the chat screen rather than a full-page "Starting" panel,
    // and that screen's textarea is a real one — so somebody can type their first message
    // while PostgreSQL is still coming up. Throwing that away at the moment the app
    // becomes ready would make "能打字" a lie, and it is the one thing that would be
    // worse than not offering it.
    //
    // The draft is pushed into the shell on every keystroke rather than read out of the
    // page here: this function runs on the shell's own thread, and asking the webview for
    // a value at this instant would be a race against the navigation itself.
    let draft = state.draft.lock().map(|value| value.clone()).unwrap_or_default();
    let url = if draft.trim().is_empty() {
        format!("http://{API_HOST}:{API_PORT}/")
    } else {
        format!("http://{API_HOST}:{API_PORT}/?draft={}", percent_encode(&draft))
    };
    if let Some(window) = app.get_webview_window("main") {
        if let Ok(parsed) = url.parse() {
            let _ = window.navigate(parsed);
        }
    }
}

/// Percent-encode a query-string value.
///
/// Hand-rolled rather than adding a dependency for one call. Unreserved characters are
/// passed through and everything else becomes `%XX` over the UTF-8 bytes, which is what
/// RFC 3986 asks for. Deliberately **not** form encoding: `+` means a space there and a
/// literal plus here, and a draft containing a `+` would come back changed.
fn percent_encode(value: &str) -> String {
    let mut out = String::with_capacity(value.len() * 3);
    for byte in value.as_bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*byte as char);
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

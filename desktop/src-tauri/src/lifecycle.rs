//! Lifecycle logging and the job-object safety net.
//!
//! Two problems this module exists to solve.
//!
//! **We could not tell what happened.** The window-close path had no logging at
//! all, so "the hook did not fire" was an inference rather than a fact. Every
//! branch here writes a line to an absolute path, so the next investigation
//! starts from evidence.
//!
//! **A force-killed app left everything running.** The backend services are
//! started detached, so they are not in the launcher's process tree and
//! `taskkill /T` cannot reach them. A Windows *job object* with
//! `KILL_ON_JOB_CLOSE` makes the operating system itself clean up: when this
//! process dies — gracefully, by crash, or by `taskkill /F` — everything
//! assigned to the job dies with it.
//!
//! **One service escapes the job.** When the shell runs elevated, PostgreSQL cannot be
//! started from an elevated process at all, so the launcher hands it to the Task
//! Scheduler service — and a child of the service is not a descendant of the launcher, so
//! it does not inherit the job. `adopt_escaped_services` puts it back once the backend is
//! answering. Measured before that existed: force-kill released every port except 5432.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

/// Where the lifecycle log lives, resolved once.
///
/// Deliberately absolute: the log has to be writable before the project
/// directory is known, and it must not depend on the process's working
/// directory, which differs between an Explorer launch and a terminal launch.
static LOG_PATH: Mutex<Option<PathBuf>> = Mutex::new(None);

/// Where this copy keeps its data: the database, the vectors, the uploads, the logs.
///
/// The same three steps, in the same order, as `scripts/start-desktop.bat`,
/// `scripts/lib.ps1` and `backend/app/core/config.py`:
///
///   1. `AI_WORKBENCH_DATA_DIR`
///   2. `<root>/data-dir.txt` - one line, written by the installer
///   3. `<root>/.runtime` - a development checkout
///
/// It is written down in four places because each runs before the next one exists: the
/// shell starts before Python, Python starts before the backend. Four copies of one
/// decision is four chances to disagree, and this function is the one that did.
///
/// It hardcoded `<root>\.runtime\logs` and created it unconditionally, so an installed
/// copy grew a `.runtime` inside its program directory even though the marker file
/// pointed somewhere else. That stray directory then stopped the uninstaller from
/// removing the install directory at all - a two-line assumption taking out a
/// seven-step uninstall.
fn resolve_runtime_root(root: &Path) -> PathBuf {
    if let Ok(explicit) = std::env::var("AI_WORKBENCH_DATA_DIR") {
        let trimmed = explicit.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }

    let marker = root.join("data-dir.txt");
    if let Ok(text) = fs::read_to_string(&marker) {
        let trimmed = text.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }

    root.join(".runtime")
}

/// Point the lifecycle log at this copy's runtime directory.
///
/// Called as soon as the root is known. Before that, entries go to the temp
/// directory so nothing is lost.
pub fn set_log_root(root: &Path) {
    let path = resolve_runtime_root(root)
        .join("logs")
        .join("desktop-lifecycle.log");
    if let Some(parent) = path.parent() {
        // Deliberately not fatal: logging must not be able to break the thing it is
        // observing. If the directory cannot be created, `log()` falls back to
        // dropping the entry rather than taking the application down with it.
        let _ = fs::create_dir_all(parent);
    }
    *LOG_PATH.lock().unwrap() = Some(path);
}

fn log_path() -> PathBuf {
    if let Some(path) = LOG_PATH.lock().unwrap().clone() {
        return path;
    }
    // Fallback: always writable, always findable.
    std::env::temp_dir().join("ai-workbench-lifecycle.log")
}

fn timestamp() -> String {
    match SystemTime::now().duration_since(UNIX_EPOCH) {
        Ok(duration) => format!("{}.{:03}", duration.as_secs(), duration.subsec_millis()),
        Err(_) => "0.000".to_string(),
    }
}

/// Append one line. Never panics, never propagates: logging must not be able to
/// break the thing it is observing.
pub fn log(message: &str) {
    let line = format!("{} [pid {}] {}\n", timestamp(), std::process::id(), message);
    let path = log_path();
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&path) {
        let _ = file.write_all(line.as_bytes());
    }
    // Also to stderr, which lands in the launcher's redirected log.
    eprint!("{line}");
}

// --------------------------------------------------------------------------- //
// Job object
// --------------------------------------------------------------------------- //
#[cfg(windows)]
mod job {
    use std::mem::{size_of, zeroed};
    use std::os::windows::io::AsRawHandle;

    use windows_sys::Win32::Foundation::{
        CloseHandle, GetLastError, BOOL, HANDLE, INVALID_HANDLE_VALUE,
    };
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, IsProcessInJob, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};

    use super::log;

    /// What happened when a process was offered to the job.
    pub enum AdoptOutcome {
        /// It was not in the job and now is.
        Adopted,
        /// It was already in the job. Success, but not by this call.
        AlreadyIn,
        /// It is still not in the job, for the stated reason.
        Failed(String),
    }

    /// Owns the job handle for the lifetime of the process.
    ///
    /// The handle is intentionally never closed: `KILL_ON_JOB_CLOSE` fires when
    /// the last handle closes, and the process exiting closes it for us. Closing
    /// it early would kill the backend while the window is still open.
    pub struct JobGuard {
        handle: HANDLE,
    }

    // The handle is only ever used for the calls below.
    unsafe impl Send for JobGuard {}
    unsafe impl Sync for JobGuard {}

    impl JobGuard {
        /// Create a job that kills everything in it when this process ends.
        pub fn create() -> Option<JobGuard> {
            unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() || handle == INVALID_HANDLE_VALUE {
                    log("job: CreateJobObjectW failed");
                    return None;
                }

                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let ok = SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &mut info as *mut _ as *mut _,
                    size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                );
                if ok == 0 {
                    log("job: SetInformationJobObject failed; the safety net is off");
                    let _ = CloseHandle(handle);
                    return None;
                }

                log("job: created with KILL_ON_JOB_CLOSE");
                Some(JobGuard { handle })
            }
        }

        /// Put a process into the job.
        ///
        /// Returns whether it worked. A failure is logged rather than ignored,
        /// because it means the safety net does not cover that process.
        pub fn assign(&self, child: &std::process::Child) -> bool {
            unsafe {
                let process_handle = child.as_raw_handle() as HANDLE;
                let ok = AssignProcessToJobObject(self.handle, process_handle);
                if ok == 0 {
                    log("job: AssignProcessToJobObject failed for the launcher");
                    return false;
                }
                log("job: launcher assigned");
                true
            }
        }

        /// Put a process that was started *outside* the job into it, by pid.
        ///
        /// Membership is normally inherited: the launcher is assigned to the job, so
        /// everything it spawns is in the job too. One service escapes that. When the
        /// shell runs elevated, PostgreSQL cannot be started from an elevated process at
        /// all, and `infra-start.ps1` hands it to the Task Scheduler service instead —
        /// which makes postgres a child of the *service*, not of the launcher, and so
        /// outside the job.
        ///
        /// The consequence was measured, not guessed: the installer acceptance reported
        ///
        ///     === 7. force-kill the window; everything must go ===
        ///     [FAIL] PostgreSQL port 5432 released
        ///
        /// while every other port was released. This call is what closes that gap. It
        /// checks membership first so that adopting a process that inherited the job —
        /// the ordinary, non-elevated launch — is reported as such rather than as a
        /// failed assignment.
        pub fn adopt(&self, pid: u32) -> AdoptOutcome {
            unsafe {
                // `PROCESS_SET_QUOTA` is what `AssignProcessToJobObject` requires on the
                // handle; `PROCESS_TERMINATE` is what the job will need in order to act on
                // it. `OpenProcess` can legitimately fail for a process that is gone.
                let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, pid);
                if process.is_null() || process == INVALID_HANDLE_VALUE {
                    return AdoptOutcome::Failed(format!(
                        "OpenProcess({pid}) win32={}",
                        GetLastError()
                    ));
                }

                let mut already: BOOL = 0;
                if IsProcessInJob(process, self.handle, &mut already) != 0 && already != 0 {
                    let _ = CloseHandle(process);
                    return AdoptOutcome::AlreadyIn;
                }

                let ok = AssignProcessToJobObject(self.handle, process);
                let error = if ok == 0 { GetLastError() } else { 0 };
                let _ = CloseHandle(process);
                if ok == 0 {
                    AdoptOutcome::Failed(format!(
                        "AssignProcessToJobObject({pid}) win32={error}"
                    ))
                } else {
                    AdoptOutcome::Adopted
                }
            }
        }
    }
}

#[cfg(windows)]
pub use job::{AdoptOutcome, JobGuard};

#[cfg(not(windows))]
pub struct JobGuard;

#[cfg(not(windows))]
pub enum AdoptOutcome {
    Adopted,
    AlreadyIn,
    Failed(String),
}

#[cfg(not(windows))]
impl JobGuard {
    pub fn create() -> Option<JobGuard> {
        None
    }
    pub fn assign(&self, _child: &std::process::Child) -> bool {
        false
    }
    pub fn adopt(&self, _pid: u32) -> AdoptOutcome {
        AdoptOutcome::Failed("job objects are a Windows feature".to_string())
    }
}

/// The pid of the running PostgreSQL postmaster, from its own pid file.
///
/// PostgreSQL writes `<data>/postmaster.pid` itself, and its first line is the
/// postmaster's pid. Read rather than remembered: the launcher would have to report a
/// pid back through a file of its own, which is a second place for "where is postgres"
/// to be written down and therefore a second place for it to be wrong.
pub fn postgres_pid(root: &Path) -> Option<u32> {
    let path = resolve_runtime_root(root).join("pgdata").join("postmaster.pid");
    let text = fs::read_to_string(path).ok()?;
    text.lines().next()?.trim().parse().ok()
}

/// Put any service that escaped the job back into it.
///
/// Only PostgreSQL can escape, and only on one path: an elevated shell cannot start it,
/// so `infra-start.ps1` asks the Task Scheduler to, and the Task Scheduler's child is not
/// a descendant of the launcher. Redis, Qdrant and the API are always started by the
/// launcher and inherit the job.
///
/// Called once the backend is answering, because that is the first moment the pid file is
/// guaranteed to exist. Without it a force-killed window leaves a database behind holding
/// port 5432 and the data directory.
pub fn adopt_escaped_services(job: Option<&JobGuard>, root: &Path) {
    let Some(job) = job else {
        return;
    };
    let Some(pid) = postgres_pid(root) else {
        // Not an error worth shouting about: the cluster may simply not be running yet.
        log("adopt: no postmaster.pid to adopt");
        return;
    };
    match job.adopt(pid) {
        AdoptOutcome::Adopted => log(&format!("adopt: postgres {pid} put into the job")),
        AdoptOutcome::AlreadyIn => {
            log(&format!("adopt: postgres {pid} already in the job (inherited)"))
        }
        AdoptOutcome::Failed(why) => {
            log(&format!("adopt: postgres {pid} is NOT in the job - a force-kill will leave it running ({why})"))
        }
    }
}

/// Find the project directory without relying on any stored state.
///
/// The stored root is only set once the startup sequence has located it. A
/// second launch — where the backend is already running — returns early and
/// never sets it, so anything that needs the root must be able to find it
/// independently. This is the function that makes `stop()` work in that case.
pub fn locate_project_root() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("AI_WORKBENCH_HOME") {
        let candidate = PathBuf::from(explicit);
        if candidate.join("scripts").join("stop-desktop.bat").is_file() {
            return Some(candidate);
        }
    }

    if let Ok(exe) = std::env::current_exe() {
        for ancestor in exe.ancestors().skip(1).take(8) {
            if ancestor.join("scripts").join("stop-desktop.bat").is_file() {
                return Some(ancestor.to_path_buf());
            }
        }
    }

    if let Ok(profile) = std::env::var("USERPROFILE") {
        let candidate = Path::new(&profile).join("Desktop").join("ai-workbench");
        if candidate.join("scripts").join("stop-desktop.bat").is_file() {
            return Some(candidate);
        }
    }

    None
}

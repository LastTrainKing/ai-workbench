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

/// Point the lifecycle log at the project's runtime directory.
///
/// Called as soon as the root is known. Before that, entries go to the temp
/// directory so nothing is lost.
pub fn set_log_root(root: &Path) {
    let path = root.join(".runtime").join("logs").join("desktop-lifecycle.log");
    if let Some(parent) = path.parent() {
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

    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    use super::log;

    /// Owns the job handle for the lifetime of the process.
    ///
    /// The handle is intentionally never closed: `KILL_ON_JOB_CLOSE` fires when
    /// the last handle closes, and the process exiting closes it for us. Closing
    /// it early would kill the backend while the window is still open.
    pub struct JobGuard {
        handle: HANDLE,
    }

    // The handle is only ever used for the two calls below.
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
    }
}

#[cfg(windows)]
pub use job::JobGuard;

#[cfg(not(windows))]
pub struct JobGuard;

#[cfg(not(windows))]
impl JobGuard {
    pub fn create() -> Option<JobGuard> {
        None
    }
    pub fn assign(&self, _child: &std::process::Child) -> bool {
        false
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

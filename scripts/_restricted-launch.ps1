# Launch a process with a *restricted* (non-elevated) token.
#
# Why this file exists
# -------------------
# Windows PostgreSQL refuses to start from an elevated process:
#
#     Execution of PostgreSQL by a user with administrative permissions is not
#     permitted.
#
# `Start-Process` cannot drop privileges, so when the stack is launched from an elevated
# shell — an elevated terminal, or a desktop host that runs elevated — the cluster never
# comes up and every downstream step fails for a reason that looks nothing like the real
# one.
#
# Three ways to get such a token, tried in this order because each one works somewhere the
# others do not. All three are measured on this machine:
#
#   1. `runas /trustlevel:0x20000` (called from `infra-start.ps1`, not from here).
#      Works from a normal elevated terminal. **Fails from the installed application**
#      with `exit 1` and no output at all — the desktop host gives it an environment it
#      cannot create a process in. Two rounds of guessing at its PATH did not help, and a
#      launcher that fails silently is worse than no launcher.
#   2. `Start-ProcessRestricted` below. Creates the token in-process with
#      `CreateRestrictedToken(DISABLE_MAX_PRIVILEGE | LUA_TOKEN)` and starts postgres with
#      it — no external process, no console, no PATH dependency. It needs
#      `SeImpersonatePrivilege`, which a normal elevated token has enabled and this agent
#      host's token does not: `AdjustTokenPrivileges` reports ERROR_NOT_ALL_ASSIGNED and
#      `CreateProcessWithTokenW` returns `win32=5` even with the sandbox disabled.
#   3. `Start-ProcessLimited` below. Asks the Task Scheduler service to start it with
#      `RunLevel = Limited`, which needs no privilege at all and works in every context
#      tried so far — including the one where 1 and 2 both fail.
#
# Every failure returns a distinct code and the underlying Win32 error, so a failure here
# says which call failed rather than "exit 1".

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class AiWorkbenchRestricted
{
    [StructLayout(LayoutKind.Sequential)]
    public struct STARTUPINFO
    {
        public int cb;
        public string lpReserved;
        public string lpDesktop;
        public string lpTitle;
        public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
        public short wShowWindow, cbReserved2;
        public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct PROCESS_INFORMATION
    {
        public IntPtr hProcess, hThread;
        public int dwProcessId, dwThreadId;
    }

    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool CreateRestrictedToken(IntPtr existing, uint flags,
        int disableCount, IntPtr disableSids,
        int deleteCount, IntPtr deleteSids,
        int restrictCount, IntPtr restrictSids,
        out IntPtr newToken);

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool CreateProcessWithTokenW(IntPtr token, uint logonFlags,
        string applicationName, string commandLine, uint creationFlags,
        IntPtr environment, string currentDirectory, ref STARTUPINFO startupInfo,
        out PROCESS_INFORMATION processInformation);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool CloseHandle(IntPtr handle);

    [StructLayout(LayoutKind.Sequential)]
    struct LUID_AND_ATTRIBUTES
    {
        public long Luid;
        public uint Attributes;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct TOKEN_PRIVILEGES
    {
        public uint PrivilegeCount;
        public LUID_AND_ATTRIBUTES Privileges;
    }

    [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool LookupPrivilegeValue(string systemName, string name, out long luid);

    [DllImport("advapi32.dll", SetLastError = true)]
    static extern bool AdjustTokenPrivileges(IntPtr token, bool disableAllPrivileges,
        ref TOKEN_PRIVILEGES newState, uint bufferLength, IntPtr previousState,
        IntPtr returnLength);

    const uint SE_PRIVILEGE_ENABLED = 0x00000002;

    static bool EnablePrivilege(IntPtr token, string name)
    {
        long luid;
        if (!LookupPrivilegeValue(null, name, out luid)) return false;
        TOKEN_PRIVILEGES state = new TOKEN_PRIVILEGES();
        state.PrivilegeCount = 1;
        state.Privileges.Luid = luid;
        state.Privileges.Attributes = SE_PRIVILEGE_ENABLED;
        if (!AdjustTokenPrivileges(token, false, ref state, 0, IntPtr.Zero, IntPtr.Zero))
            return false;
        // AdjustTokenPrivileges reports success even when it enabled nothing; the
        // documented way to tell is that GetLastError is ERROR_NOT_ALL_ASSIGNED (1300).
        return Marshal.GetLastWin32Error() != 1300;
    }

    // 0x0002 DUPLICATE | 0x0008 QUERY | 0x0020 ADJUST_PRIVILEGES | 0x0080 ADJUST_DEFAULT
    // | 0x0100 ADJUST_SESSIONID
    const uint TOKEN_ACCESS = 0x0002 | 0x0008 | 0x0020 | 0x0080 | 0x0100;
    // 0x01 DISABLE_MAX_PRIVILEGE | 0x04 LUA_TOKEN
    const uint RESTRICT_FLAGS = 0x01 | 0x04;
    // 0x08000000 CREATE_NO_WINDOW
    const uint CREATE_NO_WINDOW = 0x08000000;

    public static string LastError()
    {
        return "win32=" + Marshal.GetLastWin32Error();
    }

    /// Returns the new process id, or a negative code naming the call that failed.
    public static int Launch(string commandLine, string workingDirectory, out string error)
    {
        error = "";
        IntPtr token;
        if (!OpenProcessToken(new IntPtr(-1), TOKEN_ACCESS, out token))
        {
            error = "OpenProcessToken " + LastError();
            return -1;
        }
        try
        {
            // **Enabling these is the whole trick.** An elevated token *holds* these
            // privileges but leaves them disabled, so both CreateProcessAsUser and
            // CreateProcessWithTokenW fail with `win32=5` (ACCESS_DENIED) until they are
            // switched on. That is the failure this spent two attempts on: the error said
            // "access denied" about a process that was plainly an administrator.
            bool quota = EnablePrivilege(token, "SeIncreaseQuotaPrivilege");
            bool assign = EnablePrivilege(token, "SeAssignPrimaryTokenPrivilege");
            bool impersonate = EnablePrivilege(token, "SeImpersonatePrivilege");
            error = string.Format("privileges: quota={0} assign={1} impersonate={2}",
                                  quota, assign, impersonate);

            IntPtr restricted;
            if (!CreateRestrictedToken(token, RESTRICT_FLAGS, 0, IntPtr.Zero, 0, IntPtr.Zero,
                                       0, IntPtr.Zero, out restricted))
            {
                error = "CreateRestrictedToken " + LastError();
                return -2;
            }
            try
            {
                STARTUPINFO startup = new STARTUPINFO();
                startup.cb = Marshal.SizeOf(typeof(STARTUPINFO));
                PROCESS_INFORMATION process;
                // `CreateProcessWithTokenW`, not `CreateProcessAsUser`. The latter also
                // wants SeTcbPrivilege in some configurations; this one needs only the
                // impersonation privilege, which an elevated process can always enable.
                if (!CreateProcessWithTokenW(restricted, 0, null, commandLine,
                                             CREATE_NO_WINDOW, IntPtr.Zero, workingDirectory,
                                             ref startup, out process))
                {
                    error = "CreateProcessWithTokenW " + LastError() + " (" + error + ")";
                    return -3;
                }
                CloseHandle(process.hThread);
                CloseHandle(process.hProcess);
                return process.dwProcessId;
            }
            finally { CloseHandle(restricted); }
        }
        finally { CloseHandle(token); }
    }
}
'@ -ErrorAction Stop

function Start-ProcessRestricted {
    <#
      Start an executable with a restricted (non-elevated) token.

      Returns $true when the process was created. On failure it writes the failing call
      and its Win32 error to the startup log — `runas` reported nothing at all, and that
      silence is why this took two attempts to diagnose.

      Requires `lib.ps1` to have been dot-sourced first: it uses `Write-StartupLog` and
      `Write-Warn2`, and `Test-IsElevated` lives there rather than here so the question
      "is this shell elevated" has one answer.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )

    # The command line has to be one string for CreateProcessAsUser, and the first token
    # must be the executable itself.
    $commandLine = '"{0}" {1}' -f $FilePath, $Arguments
    $error = ''
    $result = [AiWorkbenchRestricted]::Launch($commandLine, $WorkingDirectory, [ref]$error)
    if ($result -gt 0) {
        Write-StartupLog -Service 'PostgreSQL' -State 'STARTING' -Detail "restricted pid $result"
        return $true
    }
    Write-StartupLog -Service 'PostgreSQL' -State 'FAILED' -Detail "restricted launch $result ($error)"
    Write-Warn2 "restricted launch failed: code=$result $error"
    return $false
}

function Start-ProcessLimited {
    <#
      Start an executable through Task Scheduler with `RunLevel = Limited`.

      The third way to get a non-elevated token, and the only one that worked on the
      machine this was written on. `RunLevel Limited` has the Task Scheduler service start
      the process with the same UAC-filtered token the user gets when they launch something
      normally — `whoami /groups` inside it shows

          BUILTIN\Administrators   ...   Group used for deny only
          Mandatory Label\Medium Mandatory Level

      which is exactly what postgres.exe insists on. It needs nothing but the Task
      Scheduler service: no privilege to enable, no external launcher, no console.

      The task definition is removed once the port is open, and **that does not stop the
      server** — the process belongs to the service, and removing a running task's
      definition leaves the process running. Measured: after `Unregister-ScheduledTask`,
      port 5432 was still listening and the task was gone.

      `logging_collector` is used for the same reason as in the in-process path: the launch
      has no inherited handles to redirect, so postgres has to write its own log file. That
      file is where the reason lands when the cluster will not start.

      Returns $true when the port opened. On failure it records the task's last result and
      the tail of postgres.log, because "port 5432 never opened" is not a diagnosis.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][int]$Port,
        [string]$TaskName = 'AIWorkbenchPostgres',
        [int]$TimeoutSeconds = 30
    )

    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    if (-not $identity) { $identity = "$env:USERDOMAIN\$env:USERNAME" }

    # `Register-ScheduledTask` and `Unregister-ScheduledTask` draw a progress bar, and in a
    # non-interactive host it is rendered into the console as a wall of half-drawn frames —
    # it landed in the middle of `start.bat`'s output, between "PostgreSQL starting" and
    # "PostgreSQL READY". Suppressed for the duration of this function only.
    $previousProgress = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'

    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        $action = New-ScheduledTaskAction -Execute $FilePath -Argument $Arguments `
            -WorkingDirectory $WorkingDirectory
        # `Interactive` = run in this logged-on session, with this user's token;
        # `Limited` is what filters it down from elevated. Both are required: dropping
        # `Limited` starts the cluster elevated and postgres refuses, which is the problem
        # this function exists to solve.
        $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive `
            -RunLevel Limited
        Register-ScheduledTask -TaskName $TaskName -InputObject (New-ScheduledTask `
            -Action $action -Principal $principal) -Force | Out-Null
        Start-ScheduledTask -TaskName $TaskName
    }
    catch {
        $ProgressPreference = $previousProgress
        Write-StartupLog -Service 'PostgreSQL' -State 'FAILED' `
            -Detail "scheduled task could not be registered ($($_.Exception.Message))"
        Write-Warn2 "scheduled-task launch failed: $($_.Exception.Message)"
        return $false
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $opened = $false
    while ((Get-Date) -lt $deadline) {
        if (Test-PortOpen $Port) { $opened = $true; break }
        Start-Sleep -Milliseconds 700
    }

    if ($opened) {
        Write-StartupLog -Service 'PostgreSQL' -State 'STARTING' `
            -Detail "started via scheduled task '$TaskName' (RunLevel Limited)"
        # Removed so nothing of ours is left in the user's Task Scheduler. The server keeps
        # running — see the note above.
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        $ProgressPreference = $previousProgress
        return $true
    }

    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-StartupLog -Service 'PostgreSQL' -State 'FAILED' `
        -Detail "scheduled-task launch did not open port $Port (last result $($info.LastTaskResult))"
    Write-Warn2 "scheduled-task launch did not open port $Port"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    $ProgressPreference = $previousProgress
    return $false
}

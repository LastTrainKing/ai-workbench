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
# The first attempt at this used `runas /trustlevel:0x20000`. It works from a terminal
# and **fails from the installed application** with `exit 1` and no output at all — the
# desktop host gives it an environment it cannot create a process in. Two rounds of
# guessing at its PATH did not help, and a launcher that fails silently is worse than no
# launcher.
#
# So the token is created in-process instead: `CreateRestrictedToken` with
# `DISABLE_MAX_PRIVILEGE | LUA_TOKEN` produces the same restricted token `runas
# /trustlevel` would, and `CreateProcessAsUser` starts postgres with it. No external
# process, no console, no PATH dependency.
#
# Every failure returns a distinct negative code and the Win32 error, so a failure here
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

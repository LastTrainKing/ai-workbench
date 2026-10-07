# Start postgres.exe with a NON-elevated token, via `runas /trustlevel:0x20000`.
#
# Why this file exists
# -------------------
# Windows PostgreSQL refuses to start from an elevated process:
#
#     Execution of PostgreSQL by a user with administrative permissions is not
#     permitted.
#
# `Start-Process` cannot drop privileges, so when the stack is launched from an elevated
# shell the cluster never comes up and every downstream step fails for a reason that
# looks nothing like the real one.
#
# `runas /trustlevel:0x20000` produces a restricted (Basic User) token without prompting
# for a password. It is tried first because it is the one that works from a normal
# elevated terminal — measured, not assumed. It fails when the parent is inside a job
# object it cannot escape, which is how the desktop host launches `infra-start.ps1`; the
# in-process alternative in `_restricted-launch.ps1` is the fallback.
#
# Takes no parameters on purpose: `runas` passes a single command string, so argument
# quoting through it is fragile. The paths are recomputed here from the same lib.ps1 the
# parent uses, which also guarantees the two cannot drift.
. "$PSScriptRoot\lib.ps1"
Initialize-Dirs

$postgres = Join-Path $PgHome 'bin\postgres.exe'
if (-not (Test-Path $postgres)) {
    Write-StartupError "postgres.exe not found at $postgres"
    exit 1
}

Start-Process -FilePath $postgres `
    -ArgumentList (ConvertTo-ProcessArgumentList @('-D', $PgData, '-p', "$PgPort", '-c', 'listen_addresses=127.0.0.1')) `
    -WorkingDirectory $PgHome -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogDir 'postgres.out.log') `
    -RedirectStandardError (Join-Path $LogDir 'postgres.err.log')

exit 0

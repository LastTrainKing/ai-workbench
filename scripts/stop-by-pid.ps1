# Stop exactly the process tree this product started, by PID.
#
# WHY THIS EXISTS
#
# The uninstaller used to finish with:
#
#     taskkill /F /IM "postgres.exe" /T
#     taskkill /F /IM "redis-server.exe" /T
#     taskkill /F /IM "qdrant.exe" /T
#
# `/IM` matches on the image name, not on "the copy this product started". A user who
# runs their own PostgreSQL - which is not unusual for the audience this is aimed at -
# had it force-killed by installing or uninstalling an unrelated application.
#
# `scripts/stop.bat` already refuses to do that, and says so in its header:
#
#     There is deliberately no `taskkill /IM python.exe` or `/IM node.exe`:
#     that would kill the user's other projects.
#
# The installer was doing the thing the stop script refuses to do. This script is the
# scoped replacement: every process this product starts is recorded in
# `<data>\.runtime\pids\<name>.pid` by `lib.ps1`, and PostgreSQL writes its own
# `postmaster.pid`. Kill those PIDs and nothing else dies.
#
# Usage (called by the uninstaller and by the desktop application, and usable by hand):
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop-by-pid.ps1 `
#         -RuntimeRoot "<path to the .runtime directory>"
#
# Exit codes: 0 = swept (or nothing to sweep), 2 = the runtime root could not be found.

param(
    [Parameter(Mandatory = $true)]
    [string]$RuntimeRoot,

    # Where the product is installed. Defaults to this script's own parent directory, which
    # is right in both layouts and is the only derivation that survives the installed one.
    [string]$ProductRoot = ''
)

$ErrorActionPreference = 'Continue'

if (-not (Test-Path -LiteralPath $RuntimeRoot)) {
    Write-Output "no runtime directory at $RuntimeRoot - nothing to stop"
    exit 0
}

# Where the product is installed. `$PSScriptRoot` is this script's directory, so its parent
# is the product root in both layouts (a checkout and an installed copy) and does not depend
# on where the data lives. Resolved here rather than further down because the PostgreSQL
# step below needs it.
if (-not $ProductRoot) { $ProductRoot = Split-Path -Parent $PSScriptRoot }

# ---------------------------------------------------------------- PostgreSQL, gracefully --
#
# PostgreSQL is stopped with `pg_ctl` **before** the sweep, not by it.
#
# It used to be swept like everything else: `taskkill /F` on the postmaster. PostgreSQL
# survives that — the next start replays the WAL — but it is a crash, and every start paid
# for it. `pg_ctl -m fast` asks the postmaster to disconnect clients, checkpoint and exit,
# which is what the shutdown path in `infra-stop.ps1` has always done for the development
# stack. The product was the one place that did not.
#
# The escalation is the same shape as `infra-stop.ps1`: one `-m immediate` if `fast` is
# ignored, then leave it to the sweep. `-m immediate` aborts transactions and skips the
# shutdown checkpoint; it is a documented mode, and its cost is a WAL replay on the next
# start — which is where this started.
$pgData = Join-Path $RuntimeRoot 'pgdata'
$pgCtl = Join-Path $ProductRoot 'vendor\runtime\pgsql\pgsql\bin\pg_ctl.exe'
if ((Test-Path -LiteralPath (Join-Path $pgData 'postmaster.pid')) -and (Test-Path -LiteralPath $pgCtl)) {
    # Output goes to a **file**, not into a variable.
    #
    # `$out = & pg_ctl ...` collects through a pipe, and on Windows the postmaster's children
    # inherit that pipe's write end — so `pg_ctl -w` waits for a handle that stays open, and
    # the call never returns. The first version of this block did exactly that: the graceful
    # stop hung, the sweep below never ran, and ten open-and-close cycles left PostgreSQL,
    # Redis and Qdrant running every time. A file redirect has no such handle.
    $pgLog = Join-Path $RuntimeRoot 'logs\postgres-stop.log'
    $null = New-Item -ItemType Directory -Force -Path (Split-Path -Parent $pgLog) -ErrorAction SilentlyContinue

    Write-Output "stopping postgres gracefully (pg_ctl -m fast)"
    & $pgCtl -D $pgData -m fast -w -t 20 stop *> $pgLog
    Write-Output "  pg_ctl exit $LASTEXITCODE"
    if (Test-Path -LiteralPath $pgLog) {
        Get-Content -LiteralPath $pgLog -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Output "  $_" }
    }

    if (Test-Path -LiteralPath (Join-Path $pgData 'postmaster.pid')) {
        Write-Output "  fast shutdown did not take effect; trying -m immediate"
        & $pgCtl -D $pgData -m immediate -w -t 20 stop *>> $pgLog
        Write-Output "  pg_ctl exit $LASTEXITCODE"
    }

    if (Test-Path -LiteralPath (Join-Path $pgData 'postmaster.pid')) {
        Write-Output "  postgres is still running; the sweep below will take it"
    } else {
        Write-Output "  postgres stopped"
    }
}

$targets = @()

# 1. Every pid file this product wrote.
$pidDir = Join-Path $RuntimeRoot 'pids'
if (Test-Path -LiteralPath $pidDir) {
    foreach ($file in Get-ChildItem -LiteralPath $pidDir -Filter '*.pid' -ErrorAction SilentlyContinue) {
        $value = (Get-Content -LiteralPath $file.FullName -Raw -ErrorAction SilentlyContinue)
        if ($null -ne $value) { $targets += @{ Name = $file.BaseName; Pid = $value.Trim() } }
    }
}

# 2. PostgreSQL keeps its own pid file, first line is the postmaster pid.
$pgPid = Join-Path $RuntimeRoot 'pgdata\postmaster.pid'
if (Test-Path -LiteralPath $pgPid) {
    $first = Get-Content -LiteralPath $pgPid -TotalCount 1 -ErrorAction SilentlyContinue
    if ($null -ne $first) { $targets += @{ Name = 'postgres'; Pid = $first.Trim() } }
}

if ($targets.Count -eq 0) {
    Write-Output "no pid files under $RuntimeRoot - nothing to stop"
    exit 0
}

$stopped = 0
$skipped = 0
foreach ($target in $targets) {
    if ($target.Pid -notmatch '^\d+$') {
        Write-Output "skip $($target.Name): '$($target.Pid)' is not a pid"
        $skipped++
        continue
    }
    $process = Get-Process -Id ([int]$target.Pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Write-Output "skip $($target.Name): pid $($target.Pid) is already gone"
        $skipped++
        continue
    }

    # The pid file is written by this product, but pids get reused. Only kill a process
    # whose executable really lives under the directory this product installed into -
    # otherwise a stale pid file would take out whatever inherited the number.
    #
    # **This check used to be wrong for every installed copy.** It derived the product root
    # from the runtime directory's parent (`<data>\.runtime` -> `<data>`), which is correct
    # for a development checkout, where the runtime is `<repo>\.runtime` and the engines are
    # `<repo>\vendor\runtime`. An installed copy keeps its data *outside* the program
    # directory - that is the whole point of `data-dir.txt` - so `<data>\vendor` does not
    # exist, the engines' real path under `%LOCALAPPDATA%\AI Workbench\vendor` matched
    # nothing, and **every** process was skipped. Uninstalling left PostgreSQL, Redis and
    # Qdrant running, holding their ports.
    #
    # `$ProductRoot` is the fix, and it is resolved at the top of this script.
    $exePath = $null
    try { $exePath = $process.Path } catch { $exePath = $null }
    $roots = @()
    if ($ProductRoot) {
        $roots += (Join-Path $ProductRoot 'vendor')
        $roots += $ProductRoot
    }
    $roots += (Join-Path (Split-Path -Parent $RuntimeRoot) 'vendor')
    $roots += (Split-Path -Parent $RuntimeRoot)
    $insideProduct = $false
    foreach ($root in $roots) {
        if ($exePath -and $root -and $exePath.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
            $insideProduct = $true
        }
    }
    if (-not $insideProduct) {
        Write-Output "skip $($target.Name): pid $($target.Pid) is '$exePath', outside this product"
        $skipped++
        continue
    }

    Write-Output "stopping $($target.Name) (pid $($target.Pid))"
    & taskkill.exe /PID $target.Pid /T /F 2>&1 | ForEach-Object { Write-Output "  $_" }
    $stopped++
}

Write-Output "swept: stopped=$stopped skipped=$skipped"
exit 0

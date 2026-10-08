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
# Usage (called by the uninstaller, and usable by hand):
#
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop-by-pid.ps1 `
#         -RuntimeRoot "<path to the .runtime directory>"
#
# Exit codes: 0 = swept (or nothing to sweep), 2 = the runtime root could not be found.

param(
    [Parameter(Mandatory = $true)]
    [string]$RuntimeRoot
)

$ErrorActionPreference = 'Continue'

if (-not (Test-Path -LiteralPath $RuntimeRoot)) {
    Write-Output "no runtime directory at $RuntimeRoot - nothing to stop"
    exit 0
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
    $exePath = $null
    try { $exePath = $process.Path } catch { $exePath = $null }
    $vendorRoot = Join-Path (Split-Path -Parent $RuntimeRoot) 'vendor'
    $insideProduct = $false
    foreach ($root in @($vendorRoot, (Split-Path -Parent $RuntimeRoot))) {
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

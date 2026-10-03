# Single-instance lock for the startup sequence.
#
#   powershell -ExecutionPolicy Bypass -File scripts\startup-lock.ps1 -Action Acquire
#   powershell -ExecutionPolicy Bypass -File scripts\startup-lock.ps1 -Action Release
#
# Why this exists
# ---------------
# Double-clicking the desktop shortcut twice in quick succession used to run two
# start sequences at once. Each step is individually idempotent, so the outcome
# was still correct, but two runs interleaving in the same log and racing on the
# same readiness probes is not something to rely on.
#
# The lock is a file created with `New-Item -ErrorAction Stop`, which fails if it
# already exists — an atomic test-and-set without extra machinery.
#
# A lock older than MaxAgeSeconds is treated as stale (the previous run crashed or
# was killed) and removed, so a crash can never wedge the launcher permanently.
#
# Exit codes: 0 = lock acquired, 1 = timed out waiting for another run.
#
param(
    [Parameter(Mandatory)]
    [ValidateSet('Acquire', 'Release', 'Status')]
    [string]$Action,

    [int]$TimeoutSeconds = 240,
    [int]$MaxAgeSeconds = 300
)

. "$PSScriptRoot\lib.ps1"
Initialize-Dirs

$lockFile = Join-Path $LogDir 'startup.lock'

function Test-Stale {
    if (-not (Test-Path $lockFile)) { return $false }
    $age = ((Get-Date) - (Get-Item $lockFile).LastWriteTime).TotalSeconds
    return ($age -gt $MaxAgeSeconds)
}

switch ($Action) {
    'Status' {
        if (Test-Path $lockFile) {
            Write-Host "lock held (age $([int]((Get-Date) - (Get-Item $lockFile).LastWriteTime).TotalSeconds)s)"
            exit 1
        }
        Write-Host 'lock free'
        exit 0
    }

    'Release' {
        Remove-Item -LiteralPath $lockFile -Force -ErrorAction SilentlyContinue
        exit 0
    }

    'Acquire' {
        $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
        while ($true) {
            if (Test-Stale) {
                Write-Warn2 'removing a stale startup lock from a previous run'
                Remove-Item -LiteralPath $lockFile -Force -ErrorAction SilentlyContinue
            }
            try {
                New-Item -Path $lockFile -ItemType File -ErrorAction Stop | Out-Null
                Set-Content -LiteralPath $lockFile -Value "$PID $(Get-Date -Format o)" -Encoding ascii
                exit 0
            } catch {
                # Held by another run — wait for it to finish.
            }
            if ((Get-Date) -ge $deadline) {
                Write-Warn2 "another startup has held the lock for more than ${TimeoutSeconds}s"
                exit 1
            }
            Start-Sleep -Milliseconds 700
        }
    }
}

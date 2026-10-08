# Stop the project-scoped infrastructure processes.
#
#   powershell -ExecutionPolicy Bypass -File scripts\infra-stop.ps1
#
# SCOPE GUARANTEE
# ---------------
# Only processes this project started are stopped:
#
#   PostgreSQL   `pg_ctl -D <our data dir>` — the data directory identifies the
#                cluster, so a PostgreSQL from another project is untouched.
#   Redis        pid file, verified to live under vendor\runtime
#   Qdrant       pid file, verified to live under vendor\runtime
#
# There is deliberately no `Stop-Process -Name redis-server` style fallback:
# that would kill a Redis or Qdrant belonging to the user's other projects.
#
# Data is never deleted — the cluster, the Qdrant storage and the uploaded files
# all survive a stop.
#
. "$PSScriptRoot\lib.ps1"
Initialize-Dirs

function Stop-Pg {
    $pidFile = Join-Path $PgData 'postmaster.pid'
    if (-not (Test-Path $pidFile)) {
        Write-StartupLog -Service 'PostgreSQL' -State 'STOPPED' -Detail 'was not running'
        return
    }

    $pgCtl = Join-Path $PgHome 'bin\pg_ctl.exe'

    # A normal shutdown, measured on this machine at **0.2 s** (the postmaster is
    # otherwise idle and the checkpoint is tiny). 20 s is already two orders of magnitude
    # of headroom; 60 was not a timeout, it was a wait for something that was not coming.
    Write-Step 'Stopping PostgreSQL (fast shutdown)'
    $started = Get-Date
    $result = Invoke-Native -FilePath $pgCtl `
        -Arguments @('-D', $PgData, '-m', 'fast', '-w', '-t', '20', 'stop')
    $seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 1)

    # **Report the outcome before acting on it.** The old code discarded pg_ctl's exit
    # code and output on the failure path and jumped straight to a 60-second wait for
    # postmaster.pid, so the log said only
    #
    #     PostgreSQL FAILED - shutdown timeout
    #
    # after a minute of nothing. That line is compatible with "the server ignored the
    # signal", "pg_ctl could not open the signal pipe" and "pg_ctl was not allowed to
    # touch the process at all" — three different problems with three different fixes,
    # and the one piece of evidence that separates them was being thrown away.
    Write-StartupLog -Service 'PostgreSQL' -State 'STOPPING' `
        -Detail "pg_ctl -m fast exited $($result.ExitCode) after ${seconds}s"
    if ($result.Output.Trim()) {
        Write-StartupLog -Service 'PostgreSQL' -State 'STOPPING' `
            -Detail "pg_ctl said: $($result.Output.Trim())"
    }

    # pg_ctl removes postmaster.pid once the server has really exited.
    if (Wait-Until -Condition { -not (Test-Path $pidFile) } -TimeoutSeconds 5) {
        Clear-TrackedPid -Name 'postgres'
        Write-StartupLog -Service 'PostgreSQL' -State 'STOPPED' `
            -Detail "pg_ctl exit $($result.ExitCode) in ${seconds}s"
        return
    }

    # It did not stop. Escalate once, rather than waiting for it to change its mind.
    #
    # `-m immediate` aborts transactions and exits without a shutdown checkpoint. It is
    # a documented mode, not a workaround: the next start replays the WAL, which costs a
    # moment of recovery and no data. The alternative — what happened to the user — is a
    # window that hangs for a minute and then leaves the database holding port 5432.
    Write-Warn2 'PostgreSQL ignored the fast shutdown - escalating to immediate'
    Write-StartupLog -Service 'PostgreSQL' -State 'STOPPING' `
        -Detail 'fast shutdown did not take effect; trying -m immediate'
    $immediate = Invoke-Native -FilePath $pgCtl `
        -Arguments @('-D', $PgData, '-m', 'immediate', '-w', '-t', '15', 'stop')
    Write-StartupLog -Service 'PostgreSQL' -State 'STOPPING' `
        -Detail "pg_ctl -m immediate exited $($immediate.ExitCode): $($immediate.Output.Trim())"

    if (Wait-Until -Condition { -not (Test-Path $pidFile) } -TimeoutSeconds 10) {
        Clear-TrackedPid -Name 'postgres'
        Write-StartupLog -Service 'PostgreSQL' -State 'STOPPED' `
            -Detail 'immediate shutdown; the next start will replay the WAL'
        return
    }

    # Still there. Say so in full — this is the state the user was left in, and the
    # whole reason the earlier failure could not be diagnosed was that nothing was said.
    Write-StartupLog -Service 'PostgreSQL' -State 'FAILED' `
        -Detail ("could not stop after fast (exit $($result.ExitCode)) and immediate " +
        "(exit $($immediate.ExitCode)) - port $PgPort may still be in use. " +
        "fast said: $($result.Output.Trim()) | immediate said: $($immediate.Output.Trim())")
    Write-Warn2 "PostgreSQL is still running - port $PgPort may still be held"
}

Stop-Pg
Stop-ProjectProcess -Name 'redis'  -Port $RedisPort  -MustBeUnder $VendorDir | Out-Null
Stop-ProjectProcess -Name 'qdrant' -Port $QdrantPort -MustBeUnder $VendorDir | Out-Null

Write-Host ''
Write-StartupLog -Service 'Infrastructure' -State 'STOPPED'
exit 0

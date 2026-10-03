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

    Write-Step 'Stopping PostgreSQL (fast shutdown)'
    # pg_ctl writes progress and warnings to stderr; Invoke-Native keeps that
    # from being treated as a terminating error.
    $result = Invoke-Native -FilePath (Join-Path $PgHome 'bin\pg_ctl.exe') `
        -Arguments @('-D', $PgData, '-m', 'fast', '-w', '-t', '60', 'stop')

    # pg_ctl removes postmaster.pid once the server has really exited.
    $gone = Wait-Until -Condition { -not (Test-Path $pidFile) } -TimeoutSeconds 60
    if (-not $gone) {
        Write-Warn2 'PostgreSQL did not shut down cleanly within 60s - see the postgres log'
        Write-StartupLog -Service 'PostgreSQL' -State 'FAILED' -Detail 'shutdown timeout'
        return
    }

    Clear-TrackedPid -Name 'postgres'
    Write-StartupLog -Service 'PostgreSQL' -State 'STOPPED' -Detail "pg_ctl exit $($result.ExitCode)"
}

Stop-Pg
Stop-ProjectProcess -Name 'redis'  -Port $RedisPort  -MustBeUnder $VendorDir | Out-Null
Stop-ProjectProcess -Name 'qdrant' -Port $QdrantPort -MustBeUnder $VendorDir | Out-Null

Write-Host ''
Write-StartupLog -Service 'Infrastructure' -State 'STOPPED'
exit 0

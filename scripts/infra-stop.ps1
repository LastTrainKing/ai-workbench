# Stop the project-scoped infrastructure processes.
#   powershell -ExecutionPolicy Bypass -File scripts\infra-stop.ps1
#
. "$PSScriptRoot\lib.ps1"

function Stop-Pg {
    if (Test-Path (Join-Path $PgData 'postmaster.pid')) {
        Write-Step 'Stopping PostgreSQL (fast shutdown)'
        & (Join-Path $PgHome 'bin\pg_ctl.exe') -D $PgData -m fast -w stop | Out-Null
        Write-Ok 'PostgreSQL stopped'
    } else {
        Write-Warn2 'PostgreSQL is not running'
    }
}

function Stop-ByExe([string]$ExeName, [string]$Label) {
    $procs = Get-Process -Name $ExeName -ErrorAction SilentlyContinue
    if ($procs) {
        Write-Step "Stopping $Label"
        $procs | Stop-Process -Force
        Write-Ok "$Label stopped"
    } else {
        Write-Warn2 "$Label is not running"
    }
}

Stop-Pg
Stop-ByExe 'redis-server' 'Redis'
Stop-ByExe 'qdrant' 'Qdrant'
Write-Host ''
Write-Ok 'Infrastructure is down.'

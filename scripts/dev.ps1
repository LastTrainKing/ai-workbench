# Start / stop / inspect the AI Workbench application processes.
#
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 start
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 stop
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 status
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 logs api
#
# Processes started here are project-scoped: PID files live in .runtime/pids and
# logs in .runtime/logs. Nothing is installed as a Windows service.
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'logs')]
    [string]$Action = 'start',

    [Parameter(Position = 1)]
    [string]$Target = 'api'
)

. "$PSScriptRoot\lib.ps1"
Initialize-Dirs

$PidDir = Join-Path $RuntimeDir 'pids'
if (-not (Test-Path $PidDir)) { New-Item -ItemType Directory -Path $PidDir -Force | Out-Null }

$ApiPort = 8000
$WebPort = 5173
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$BackendDir = Join-Path $RepoRoot 'backend'

function Get-PidFile([string]$name) { Join-Path $PidDir "$name.pid" }

function Test-Tracked([string]$name) {
    $file = Get-PidFile $name
    if (-not (Test-Path $file)) { return $false }
    $tracked = (Get-Content $file -Raw).Trim()
    if (-not $tracked) { return $false }
    return [bool](Get-Process -Id ([int]$tracked) -ErrorAction SilentlyContinue)
}

function Start-Tracked {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [int]$Port
    )
    if (Test-Tracked $Name) {
        Write-Ok "$Name already running (pid $((Get-Content (Get-PidFile $Name) -Raw).Trim()))"
        return
    }
    if ($Port -gt 0 -and (Test-PortOpen $Port)) {
        Write-Warn2 "$Name is not tracked but port $Port is already in use - skipping"
        return
    }

    $stdout = Join-Path $LogDir "$Name.out.log"
    $stderr = Join-Path $LogDir "$Name.err.log"
    Write-Step "Starting $Name"
    $proc = Start-Process -FilePath $FilePath -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    Set-Content -Path (Get-PidFile $Name) -Value $proc.Id -Encoding ascii
    if ($Port -gt 0) {
        if (-not (Wait-Port $Port 60 $Name)) {
            Write-Err "See logs: $stdout / $stderr"
            return
        }
    }
    Write-Ok "$Name started (pid $($proc.Id))"
}

function Stop-Tracked {
    param(
        [string]$Name,
        [int]$Port = 0
    )
    $stopped = $false

    # 1) The PID file written by Start-Tracked.
    $file = Get-PidFile $Name
    if (Test-Path $file) {
        $tracked = (Get-Content $file -Raw).Trim()
        if ($tracked) {
            $proc = Get-Process -Id ([int]$tracked) -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Step "Stopping $Name (pid $tracked)"
                Stop-Process -Id ([int]$tracked) -Force
                $stopped = $true
            }
        }
        Remove-Item $file -Force -ErrorAction SilentlyContinue
    }

    # 2) Fall back to whatever is actually listening on this service's port.
    #    A process started outside this script (manually, by an IDE, or by an
    #    earlier session) has no PID file, and `stop` must still stop it —
    #    otherwise "stop" silently leaves the app running.
    #    Only OUR app ports are ever inspected; nothing else is touched.
    if ($Port -gt 0) {
        $owners = @(
            Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
        foreach ($ownerPid in $owners) {
            $proc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
            if (-not $proc) { continue }
            Write-Step "Stopping $Name on port $Port (pid $ownerPid, untracked)"
            Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
            $stopped = $true
        }
        # Give the OS a moment to release the port before reporting.
        for ($i = 0; $i -lt 10 -and (Test-PortOpen $Port); $i++) {
            Start-Sleep -Milliseconds 400
        }
    }

    if ($stopped) {
        Write-Ok "$Name stopped"
    } else {
        Write-Warn2 "$Name is not running"
    }
}

switch ($Action) {
    'start' {
        $missing = Test-VendorPresent
        if ($missing.Count -gt 0) {
            Write-Err ("Missing runtime(s): " + ($missing -join ', '))
            Write-Warn2 'Run scripts\fetch-runtime.ps1 first'
            exit 1
        }
        foreach ($svc in @(@{n='PostgreSQL';p=$PgPort}, @{n='Redis';p=$RedisPort}, @{n='Qdrant';p=$QdrantPort})) {
            if (-not (Test-PortOpen $svc.p)) {
                Write-Warn2 "$($svc.n) is down. Run scripts\infra-start.ps1 first."
                exit 1
            }
        }

        # The API runs the in-process worker by default (WORKER_ENABLED=true).
        Start-Tracked -Name 'api' -FilePath $Python `
            -Arguments @('-W', 'ignore', '-m', 'app.cli', 'serve', '--host', '127.0.0.1', '--port', "$ApiPort") `
            -WorkingDirectory $BackendDir -Port $ApiPort

        $frontend = Join-Path $RepoRoot 'frontend'
        if (Test-Path (Join-Path $frontend 'package.json')) {
            if (Test-Path (Join-Path $frontend 'node_modules')) {
                $npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
                if ($npm) {
                    Start-Tracked -Name 'web' -FilePath $npm `
                        -Arguments @('run', 'dev', '--', '--host', '127.0.0.1', '--port', "$WebPort") `
                        -WorkingDirectory $frontend -Port $WebPort
                } else {
                    Write-Warn2 'npm not found on PATH - skipping the frontend'
                }
            } else {
                Write-Warn2 'frontend/node_modules is missing - run: cd frontend && npm install'
            }
        } else {
            Write-Warn2 'frontend/package.json not found - skipping the frontend'
        }

        Write-Host ''
        Write-Ok 'Application is up.'
        Write-Host "  API       http://127.0.0.1:$ApiPort"
        Write-Host "  Docs      http://127.0.0.1:$ApiPort/docs"
        Write-Host "  Frontend  http://127.0.0.1:$WebPort"
    }
    'stop' {
        Stop-Tracked -Name 'web' -Port $WebPort
        Stop-Tracked -Name 'api' -Port $ApiPort
        Write-Host ''
        Write-Ok 'Application processes stopped (infrastructure left running).'
    }
    'restart' {
        Stop-Tracked -Name 'web' -Port $WebPort
        Stop-Tracked -Name 'api' -Port $ApiPort
        Start-Sleep -Seconds 2
        & $PSCommandPath start
    }
    'status' {
        $rows = @()
        foreach ($name in @('api', 'web')) {
            $port = if ($name -eq 'api') { $ApiPort } else { $WebPort }
            # NOTE: $PID is a read-only automatic variable in PowerShell, so the
            # tracked process id must live in a differently named variable.
            $trackedId = ''
            $file = Get-PidFile $name
            if (Test-Path $file) { $trackedId = (Get-Content $file -Raw).Trim() }
            $rows += [pscustomobject]@{
                Service   = $name
                Port      = $port
                Listening = (Test-PortOpen $port)
                Pid       = $trackedId
            }
        }
        $rows | Format-Table -AutoSize
    }
    'logs' {
        $file = Join-Path $LogDir "$Target.out.log"
        $errFile = Join-Path $LogDir "$Target.err.log"
        if (Test-Path $file) { Write-Host "--- $file ---"; Get-Content $file -Tail 40 }
        if (Test-Path $errFile) {
            $content = Get-Content $errFile -Tail 20
            if ($content) { Write-Host "--- $errFile ---"; $content }
        }
    }
}

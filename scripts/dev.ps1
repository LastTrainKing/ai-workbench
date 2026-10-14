# Start / stop / inspect the AI Workbench application processes.
#
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 start
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 stop
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 restart
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 status
#   powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 logs api
#
# Processes started here are project-scoped: pid files live in .runtime\pids and
# logs in .runtime\logs. Nothing is installed as a Windows service.
#
# READINESS CONTRACT
# ------------------
# The API is ready when GET /api/v1/health returns 200, and the frontend is ready
# when GET / returns 200. A listening port is not readiness — Vite binds the port
# before it can serve a page, and the API binds before the worker has started.
# `start` exits non-zero if either does not become ready in time, so the caller
# never opens a browser on a half-started application.
#
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status', 'logs')]
    [string]$Action = 'start',

    [Parameter(Position = 1)]
    [string]$Target = 'api',

    # Which application processes to touch. The desktop shell runs the API only:
    # it loads the UI from the API origin, so there is no dev server and no
    # second port.
    [ValidateSet('all', 'api', 'web')]
    [string]$Only = 'all'
)

. "$PSScriptRoot\lib.ps1"
Initialize-Dirs

$ApiPort = 8000
$WebPort = 5173
$ApiReadyTimeout = 90
$WebReadyTimeout = 90

$Python = Resolve-PythonExe
if (-not $Python) {
    Write-Err "No Python interpreter found. An installed copy ships one at vendor\runtime\python; a development checkout needs .venv."
    exit 1
}
Write-Step "python: $Python ($PythonSource)"
$BackendDir = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'
$ViteEntry = Join-Path $FrontendDir 'node_modules\vite\bin\vite.js'

$ApiHealthUrl = "http://127.0.0.1:$ApiPort/api/v1/health"
$WebUrl = "http://127.0.0.1:$WebPort/"

function Test-ApiReady { Test-HttpOk -Url $ApiHealthUrl -TimeoutSeconds 5 }
function Test-WebReady { Test-HttpOk -Url $WebUrl -TimeoutSeconds 5 }

function Start-Tracked {
    <#
      Start one application process and wait until its readiness probe passes.

      Idempotent: an already-running, already-ready service is reported as such
      and left untouched, so double-clicking the desktop shortcut twice does not
      start a second copy.
    #>
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [int]$Port = 0,
        [Parameter(Mandatory)][scriptblock]$Probe,
        [string]$ProbeLabel = 'readiness probe',
        [int]$ReadyTimeout = 90,
        [string]$MustMatch = ''
    )

    if ((Get-TrackedPid -Name $Name) -and (& $Probe)) {
        Write-StartupLog -Service $Name -State 'SKIPPED' -Detail "pid $(Get-TrackedPid -Name $Name), already ready"
        return $true
    }
    if ($Port -gt 0 -and (Test-PortOpen $Port) -and (& $Probe)) {
        Write-StartupLog -Service $Name -State 'SKIPPED' -Detail "already serving on $Port"
        return $true
    }

    $stdout = Join-Path $LogDir "$Name.out.log"
    $stderr = Join-Path $LogDir "$Name.err.log"
    Write-StartupLog -Service $Name -State 'STARTING' -Detail "port $Port"

    $proc = Start-Process -FilePath $FilePath -ArgumentList (ConvertTo-ProcessArgumentList $Arguments) `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    Set-TrackedPid -Name $Name -ProcessId $proc.Id

    if (-not (Wait-ServiceReady -Name $Name -Probe $Probe `
                -TimeoutSeconds $ReadyTimeout -ProbeLabel $ProbeLabel)) {
        Write-StartupError "$Name failed to become ready within ${ReadyTimeout} seconds."
        Write-Host "    see $stdout / $stderr" -ForegroundColor DarkGray
        return $false
    }

    # Record the process that actually holds the port, not the one we spawned.
    # `python -m app.cli serve` hands the listening socket to a child process, so
    # the spawned pid can exit while the service keeps running; the pid file must
    # point at the process a later `stop` needs to end.
    if ($Port -gt 0) {
        $owner = @(
            Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique
        ) | Select-Object -First 1
        if ($owner -and $owner -ne $proc.Id) {
            Set-TrackedPid -Name $Name -ProcessId $owner
            Write-Host "    (listening socket held by pid $owner)" -ForegroundColor DarkGray
        }
    }

    Write-Ok "$Name started (pid $($proc.Id))"
    return $true
}

function Start-Api {
    return (Start-Tracked -Name 'api' -FilePath $Python `
        -Arguments @('-W', 'ignore', '-m', 'app.cli', 'serve', '--host', '127.0.0.1', '--port', "$ApiPort") `
        -WorkingDirectory $BackendDir -Port $ApiPort `
        -Probe { Test-ApiReady } -ProbeLabel "HTTP GET $ApiHealthUrl" `
        -ReadyTimeout $ApiReadyTimeout -MustMatch 'app.cli')
}

function Start-Web {
    if (-not (Test-Path (Join-Path $FrontendDir 'package.json'))) {
        Write-Warn2 'frontend/package.json not found - skipping the frontend'
        return $true
    }
    if (-not (Test-Path (Join-Path $FrontendDir 'node_modules'))) {
        Write-Warn2 'frontend/node_modules is missing - run: cd frontend && npm install'
        return $false
    }
    if (-not (Test-Path $ViteEntry)) {
        Write-Warn2 "Vite entry not found at $ViteEntry - run: cd frontend && npm install"
        return $false
    }
    # Vite is launched through node directly rather than via `npm run dev`: the
    # npm wrapper exits as soon as it has spawned the real server, so the pid we
    # recorded would be dead while the port stayed busy. Running vite.js gives us
    # the actual long-lived process.
    $node = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
    if (-not $node) {
        Write-Warn2 'node not found on PATH - skipping the frontend'
        return $false
    }
    return (Start-Tracked -Name 'web' -FilePath $node `
        -Arguments @($ViteEntry, '--host', '127.0.0.1', '--port', "$WebPort", '--strictPort') `
        -WorkingDirectory $FrontendDir -Port $WebPort `
        -Probe { Test-WebReady } -ProbeLabel "HTTP GET $WebUrl" `
        -ReadyTimeout $WebReadyTimeout -MustMatch $FrontendDir)
}

function Stop-Api {
    Stop-ProjectProcess -Name 'api' -Port $ApiPort -MustMatch 'app.cli' | Out-Null
}

function Stop-Web {
    Stop-ProjectProcess -Name 'web' -Port $WebPort -MustMatch $FrontendDir | Out-Null
}

function Assert-Infrastructure {
    <# The application needs a ready database, cache and vector store. #>
    $problems = @()
    if (-not (Test-PgReady))  { $problems += 'PostgreSQL (pg_isready)' }
    if (-not (Test-RedisReady)) { $problems += 'Redis (PING)' }
    if (-not (Test-QdrantReady)) { $problems += 'Qdrant (HTTP /healthz)' }
    if ($problems.Count -gt 0) {
        Write-StartupError ("Infrastructure is not ready: " + ($problems -join ', ') + ' - run scripts\infra-start.ps1')
        return $false
    }
    return $true
}

switch ($Action) {
    'start' {
        $missing = Test-VendorPresent
        if ($missing.Count -gt 0) {
            Write-StartupError ("Missing runtime(s): " + ($missing -join ', '))
            exit 1
        }
        if (-not (Assert-Infrastructure)) { exit 1 }

        if ($Only -ne 'web') {
            if (-not (Start-Api)) { exit 1 }
        }
        if ($Only -ne 'api') {
            if (-not (Start-Web)) { exit 1 }
        }

        Write-Host ''
        Write-StartupLog -Service 'Application' -State 'READY'
        Write-Host "  API       http://127.0.0.1:$ApiPort"
        Write-Host "  Docs      http://127.0.0.1:$ApiPort/docs"
        if ($Only -ne 'api') { Write-Host "  Frontend  http://127.0.0.1:$WebPort" }
        exit 0
    }
    'stop' {
        if ($Only -ne 'api') { Stop-Web }
        if ($Only -ne 'web') { Stop-Api }
        Write-Host ''
        Write-StartupLog -Service 'Application' -State 'STOPPED'
        exit 0
    }
    'restart' {
        if ($Only -ne 'api') { Stop-Web }
        if ($Only -ne 'web') { Stop-Api }
        # Wait for the ports to be genuinely free before starting again.
        Wait-Until -Condition { -not (Test-PortOpen $ApiPort) -and -not (Test-PortOpen $WebPort) } `
            -TimeoutSeconds 30 | Out-Null
        & $PSCommandPath start -Target $Target -Only $Only
        exit $LASTEXITCODE
    }
    'status' {
        $rows = @()
        foreach ($svc in @(
            @{ n = 'api'; p = $ApiPort; probe = { Test-ApiReady } },
            @{ n = 'web'; p = $WebPort; probe = { Test-WebReady } }
        )) {
            # NOTE: $PID is a read-only automatic variable in PowerShell, so the
            # tracked process id must live in a differently named variable.
            $trackedId = Get-TrackedPid -Name $svc.n
            $rows += [pscustomobject]@{
                Service   = $svc.n
                Port      = $svc.p
                Listening = (Test-PortOpen $svc.p)
                Ready     = (& $svc.probe)
                Pid       = if ($trackedId) { $trackedId } else { '' }
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

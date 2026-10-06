# Shared helpers for AI Workbench operational scripts (Windows / PowerShell).
# Dot-source this file: . "$PSScriptRoot\lib.ps1"

$ErrorActionPreference = 'Stop'
# Invoke-WebRequest renders a progress bar that is useless in a script and slows
# every readiness poll down; the probes are meant to be quick and quiet.
$ProgressPreference = 'SilentlyContinue'

# Resolve repository root (scripts/ -> root)
$Script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

$Script:RuntimeDir = Join-Path $RepoRoot '.runtime'
$Script:VendorDir  = Join-Path $RepoRoot 'vendor\runtime'

$Script:PgHome   = Join-Path $VendorDir 'pgsql\pgsql'
$Script:PgData   = Join-Path $RuntimeDir 'pgdata'
$Script:PgLog    = Join-Path $RuntimeDir 'logs\postgres.log'
$Script:PgPort   = 5432
$Script:PgUser   = 'postgres'
$Script:DbName   = 'aiworkbench'
$Script:DbUser   = 'aiworkbench'
$Script:DbPass   = 'aiworkbench'

$Script:RedisHome = Join-Path $VendorDir 'redis'
$Script:RedisDir  = Join-Path $RuntimeDir 'redis'
$Script:RedisConf = Join-Path $RuntimeDir 'redis.conf'
$Script:RedisLog  = Join-Path $RuntimeDir 'logs\redis.log'
$Script:RedisPort = 6379

$Script:QdrantHome = Join-Path $VendorDir 'qdrant'
$Script:QdrantDir  = Join-Path $RuntimeDir 'qdrant'
$Script:QdrantLog  = Join-Path $RuntimeDir 'logs\qdrant.log'
$Script:QdrantPort = 6333

# ---------------------------------------------------------------- python --
# Two layouts, both legitimate, and the choice between them is not a preference:
#
#   vendor\runtime\python\python.exe   an **installed product**. The interpreter
#                                      is a CPython embeddable distribution that
#                                      ships inside the install directory, so the
#                                      install depends on nothing outside itself.
#                                      This is what makes "delete the original
#                                      project directory" possible at all.
#
#   .venv\Scripts\python.exe           a **development checkout**. A venv is not
#                                      relocatable: `pyvenv.cfg` records the
#                                      absolute path of the interpreter that
#                                      created it, and copying the venv only works
#                                      while that interpreter is still installed.
#
# The bundled one wins. If a bundled interpreter is present, this directory is a
# product, and a stray `.venv` left over from development must not silently take
# over — that would make the installed copy depend on the machine it was built on.
$Script:PythonExe = $null
$Script:PythonSource = ''

function Resolve-PythonExe {
    <#
      The interpreter to run the backend with, or $null if there is none.

      Returning $null rather than throwing lets callers produce their own message
      ("run the installer" vs "create a venv") instead of a generic failure.
    #>
    if ($Script:PythonExe) { return $Script:PythonExe }

    $bundled = Join-Path $VendorDir 'python\python.exe'
    if (Test-Path $bundled) {
        $Script:PythonExe = $bundled
        $Script:PythonSource = 'bundled'
        return $bundled
    }

    $venv = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (Test-Path $venv) {
        $Script:PythonExe = $venv
        $Script:PythonSource = 'venv'
        return $venv
    }

    return $null
}

$Script:LogDir = Join-Path $RuntimeDir 'logs'
$Script:PidDir = Join-Path $RuntimeDir 'pids'

# Startup audit trail: one line per service per state transition.
$Script:StartupLog      = Join-Path $LogDir 'startup.log'
$Script:StartupErrorLog = Join-Path $LogDir 'startup-error.log'

function Initialize-Dirs {
    foreach ($d in @($RuntimeDir, $LogDir, $PidDir, $RedisDir, $QdrantDir)) {
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }
}

function Repair-ProcessEnvironment {
    <#
      Windows PowerShell 5.1 builds a case-sensitive dictionary when starting a
      child process. Shells such as Git Bash / MSYS export several casing
      variants of PATH ("Path", "PATH", "path") in the same environment block,
      which makes Start-Process fail with
      "Item has already been added. Key in dictionary: 'Path'".

      Collapsing every casing variant down to a single canonical "Path" entry
      makes process launching deterministic regardless of the parent shell.
    #>
    $names = @([System.Environment]::GetEnvironmentVariables('Process').Keys)
    $variants = $names | Where-Object { $_ -ieq 'path' }
    if ($variants.Count -le 1) { return }

    $value = [System.Environment]::GetEnvironmentVariable('Path', 'Process')
    foreach ($n in $variants) {
        Remove-Item -LiteralPath "Env:\$n" -ErrorAction SilentlyContinue
    }
    Set-Item -Path 'Env:\Path' -Value $value
}

Repair-ProcessEnvironment

function Write-Step($msg)  { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "[+] $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Err($msg)   { Write-Host "[x] $msg" -ForegroundColor Red }

# ------------------------------------------------------- process arguments --- #
function ConvertTo-ProcessArgument {
    <#
      One argument, quoted if it needs to be.

      `Start-Process -ArgumentList @('a', 'b c')` joins the array with spaces and
      does **not** quote the elements, so a value containing a space reaches the
      child as two arguments. That is not hypothetical: with the install directory
      at `C:\Temp\AIW Relocated Test\`, PostgreSQL was launched as

          -D C:\Temp\AIW Relocated Test\.runtime\pgdata

      and exited with `postgres: invalid argument: "Relocated"`.

      This matters far more than the odd test path suggests: the conventional
      install location is `C:\Program Files\...`, which contains a space, so
      without this every default install would fail to start its own database.
    #>
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Value)

    if ($Value -eq '') { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    # Double any backslash that precedes a quote, then escape the quote itself,
    # which is what the C runtime's argv parser expects.
    $escaped = $Value -replace '(\\*)"', '$1$1\"'
    return '"' + $escaped + '"'
}

function ConvertTo-ProcessArgumentList {
    <#
      A whole argument list as one properly-quoted string, for Start-Process.
    #>
    param([string[]]$Arguments)

    if (-not $Arguments -or $Arguments.Count -eq 0) { return '' }
    return (($Arguments | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' ')
}

# --------------------------------------------------------------- start log --- #
function Write-StartupLog {
    <#
      Append one line to logs/startup.log and echo a matching console line.
      States: STARTING | READY | FAILED | SKIPPED | STOPPED
    #>
    param(
        [Parameter(Mandatory)][string]$Service,
        [Parameter(Mandatory)][string]$State,
        [string]$Detail = ''
    )
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "$stamp $Service $State"
    if ($Detail) { $line = "$line - $Detail" }
    try { Add-Content -LiteralPath $StartupLog -Value $line -Encoding UTF8 } catch { }

    $suffix = if ($Detail) { " ($Detail)" } else { '' }
    switch ($State) {
        'READY'    { Write-Host "  [+] $Service READY$suffix" -ForegroundColor Green }
        'SKIPPED'  { Write-Host "  [+] $Service already running$suffix" -ForegroundColor Green }
        'STARTING' { Write-Host "  [*] $Service starting..." -ForegroundColor Cyan }
        'STOPPED'  { Write-Host "  [+] $Service stopped$suffix" -ForegroundColor Green }
        'FAILED'   { Write-Host "  [ERROR] $Service FAILED$suffix" -ForegroundColor Red }
        default    { Write-Host "  $Service $State$suffix" }
    }
}

function Write-StartupError {
    param([Parameter(Mandatory)][string]$Message)
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    try { Add-Content -LiteralPath $StartupErrorLog -Value "$stamp $Message" -Encoding UTF8 } catch { }
    Write-Err $Message
}

# ------------------------------------------------------------- readiness ---- #
function Wait-Until {
    <#
      Poll $Condition until it returns true or the deadline passes.
      A real readiness probe is always preferred over a fixed sleep: machine
      speed varies and a sleep either wastes time or is too short.
    #>
    param(
        [Parameter(Mandatory)][scriptblock]$Condition,
        [int]$TimeoutSeconds = 60,
        [int]$IntervalMs = 500
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) { return $true }
        Start-Sleep -Milliseconds $IntervalMs
    }
    return [bool](& $Condition)
}

function Test-PortOpen([int]$Port, [string]$Target = '127.0.0.1') {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($Target, $Port, $null, $null)
        $done = $iar.AsyncWaitHandle.WaitOne(700, $false)
        if (-not $done) { $client.Close(); return $false }
        $client.EndConnect($iar)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

function Wait-Port([int]$Port, [int]$TimeoutSeconds = 45, [string]$Label = 'service') {
    $ok = Wait-Until -Condition { Test-PortOpen $Port } -TimeoutSeconds $TimeoutSeconds
    if ($ok) { Write-Ok "$Label is listening on $Port" }
    else { Write-Err "$Label did not open port $Port within ${TimeoutSeconds}s" }
    return $ok
}

function Invoke-Native {
    <#
      Run a native executable and capture its output WITHOUT letting stderr
      become a terminating error.

      This matters more than it looks. lib.ps1 sets
      $ErrorActionPreference = 'Stop' so that genuine mistakes fail loudly. But
      in Windows PowerShell 5.1 a native command that writes to stderr produces
      an ErrorRecord, and under 'Stop' that aborts the script — even when the
      non-zero exit code is the expected, handled outcome.

      redis-cli is the clearest example: probing a Redis that is not running
      writes "Could not connect to Redis" to stderr, which used to kill the
      whole startup with an exception instead of being treated as "not ready
      yet, keep waiting".

      Returns both the merged output and the exit code so callers can decide.
    #>
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @()
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $FilePath @Arguments 2>&1
        $code = $LASTEXITCODE
        return [pscustomobject]@{
            Output   = (($output | ForEach-Object { "$_" }) -join "`n")
            ExitCode = if ($null -eq $code) { 0 } else { $code }
        }
    } catch {
        return [pscustomobject]@{ Output = "$_"; ExitCode = 1 }
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Test-PgReady {
    <#
      PostgreSQL readiness. A listening port is NOT readiness: the server binds
      the port before it finishes crash recovery, and answers
      "FATAL: the database system is starting up" until it is done.

      pg_isready reports the real state (exit code 0 = accepting connections).
    #>
    param([int]$Port = $PgPort)
    $pgIsReady = Join-Path $PgHome 'bin\pg_isready.exe'
    if (-not (Test-Path $pgIsReady)) { return $false }
    $result = Invoke-Native -FilePath $pgIsReady -Arguments @('-h', '127.0.0.1', '-p', "$Port", '-q', '-t', '5')
    return ($result.ExitCode -eq 0)
}

function Test-PgQuery {
    <# A real query, proving the cluster accepts authenticated connections. #>
    param([int]$Port = $PgPort)
    Get-PgEnv
    $psql = Join-Path $PgHome 'bin\psql.exe'
    if (-not (Test-Path $psql)) { return $false }
    $result = Invoke-Native -FilePath $psql -Arguments @('-U', $PgUser, '-p', "$Port", '-d', 'postgres', '-tAc', 'SELECT 1')
    return ($result.ExitCode -eq 0 -and $result.Output.Trim() -eq '1')
}

function Test-RedisReady {
    <# Redis readiness: a real PING returning PONG, not just an open socket. #>
    param([int]$Port = $RedisPort)
    $cli = Join-Path $RedisHome 'redis-cli.exe'
    if (-not (Test-Path $cli)) { return $false }
    $result = Invoke-Native -FilePath $cli -Arguments @('-h', '127.0.0.1', '-p', "$Port", 'ping')
    return ($result.Output.Trim() -eq 'PONG')
}

function Test-QdrantReady {
    <# Qdrant readiness: the HTTP API answers, not just a bound port. #>
    param([int]$Port = $QdrantPort)
    foreach ($path in @('/healthz', '/readyz', '/')) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port$path" -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
    }
    return $false
}

function Test-HttpOk {
    <# HTTP readiness for the API and the frontend. #>
    param([string]$Url, [int]$TimeoutSeconds = 5)
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds
        return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
    } catch {
        return $false
    }
}

function Wait-ServiceReady {
    <#
      Poll a readiness probe and report the outcome in the startup log.
      Returns $true on success, $false after the timeout — the caller MUST treat
      false as fatal and stop, never fall through to "ready".
    #>
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][scriptblock]$Probe,
        [int]$TimeoutSeconds = 60,
        [string]$ProbeLabel = 'readiness probe'
    )
    $started = Get-Date
    $ok = Wait-Until -Condition $Probe -TimeoutSeconds $TimeoutSeconds
    $elapsed = [int]((Get-Date) - $started).TotalSeconds

    if ($ok) {
        Write-StartupLog -Service $Name -State 'READY' -Detail "${elapsed}s via $ProbeLabel"
        return $true
    }
    Write-StartupLog -Service $Name -State 'FAILED' -Detail "not ready after ${TimeoutSeconds}s ($ProbeLabel)"
    return $false
}

# ------------------------------------------------------- project processes --- #
function Get-TrackedPid {
    <# The pid recorded for a service we started, or $null if it is gone. #>
    param([Parameter(Mandatory)][string]$Name)
    $file = Join-Path $PidDir "$Name.pid"
    if (-not (Test-Path $file)) { return $null }
    $raw = (Get-Content -LiteralPath $file -Raw -ErrorAction SilentlyContinue)
    if (-not $raw) { return $null }
    $raw = $raw.Trim()
    if (-not $raw) { return $null }
    $proc = Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue
    if (-not $proc) { return $null }
    return $proc.Id
}

function Set-TrackedPid {
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][int]$ProcessId)
    Set-Content -LiteralPath (Join-Path $PidDir "$Name.pid") -Value $ProcessId -Encoding ascii
}

function Clear-TrackedPid {
    param([Parameter(Mandatory)][string]$Name)
    Remove-Item -LiteralPath (Join-Path $PidDir "$Name.pid") -Force -ErrorAction SilentlyContinue
}

function Test-ProcessUnderPath {
    <#
      True only when the process image lives inside the given directory.
      This is what keeps `stop` from killing an unrelated Redis/Qdrant/Postgres
      that happens to run on the same port.
    #>
    param(
        [Parameter(Mandatory)][int]$ProcessId,
        [Parameter(Mandatory)][string]$Directory
    )
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        if (-not $proc -or -not $proc.ExecutablePath) { return $false }
        $exe = [System.IO.Path]::GetFullPath($proc.ExecutablePath)
        $root = [System.IO.Path]::GetFullPath($Directory).TrimEnd('\') + '\'
        return $exe.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
}

function Get-ProcessCommandLine {
    param([Parameter(Mandatory)][int]$ProcessId)
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        return "$($proc.CommandLine)"
    } catch {
        return ''
    }
}

function Test-ProcessBelongsToProject {
    <#
      Decide whether a process is ours, so `stop` never kills a foreign program.

        -Under  the image must live inside this directory (Redis/Qdrant, whose
                executables are vendored under vendor\runtime)
        -Match  the command line must contain this substring (the API and the
                frontend run on system Python/Node, so the path check does not
                apply — the module name or the project directory does)

      With neither given, everything matches; callers must pass one of them.
    #>
    param(
        [Parameter(Mandatory)][int]$ProcessId,
        [string]$Under = '',
        [string]$Match = ''
    )
    if ($Under) { return (Test-ProcessUnderPath -ProcessId $ProcessId -Directory $Under) }
    if ($Match) { return ((Get-ProcessCommandLine -ProcessId $ProcessId) -like "*$Match*") }
    return $true
}

function Stop-ProjectProcess {
    <#
      Stop one of OUR processes and nothing else.

      Identification, in order:
        1. the pid file this project wrote when it started the process;
        2. the owner of the service port — but only after confirming it belongs
           to this project (image under vendor\runtime, or a matching command
           line), so a foreign program on the same port is left alone.

      There is deliberately no `Stop-Process -Name python` / `-Name node`
      fallback: that would kill the user's other projects.
    #>
    param(
        [Parameter(Mandatory)][string]$Name,
        [int]$Port = 0,
        [string]$MustBeUnder = '',
        [string]$MustMatch = ''
    )
    $stopped = $false
    $handled = @()

    $tracked = Get-TrackedPid -Name $Name
    if ($tracked) {
        if (Test-ProcessBelongsToProject -ProcessId $tracked -Under $MustBeUnder -Match $MustMatch) {
            Write-Step "Stopping $Name (pid $tracked)"
            Stop-Process -Id $tracked -Force -ErrorAction SilentlyContinue
            $stopped = $true
            $handled += $tracked
        } else {
            Write-Warn2 "$Name pid $tracked does not belong to this project - refusing to stop it"
        }
    }
    Clear-TrackedPid -Name $Name

    if ($Port -gt 0) {
        $owners = @(
            Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty OwningProcess -Unique
        )
        foreach ($ownerPid in $owners) {
            # Already stopped above: the socket lingers for a moment while the
            # process tears down, and re-checking it would produce a bogus
            # "not part of this project" warning.
            if ($handled -contains $ownerPid) { continue }
            # A process that no longer exists holds nothing.
            if (-not (Get-Process -Id $ownerPid -ErrorAction SilentlyContinue)) { continue }

            if (-not (Test-ProcessBelongsToProject -ProcessId $ownerPid -Under $MustBeUnder -Match $MustMatch)) {
                Write-Warn2 "port $Port is held by pid $ownerPid, which is not part of this project - leaving it alone"
                continue
            }
            Write-Step "Stopping $Name on port $Port (pid $ownerPid, untracked)"
            Stop-Process -Id $ownerPid -Force -ErrorAction SilentlyContinue
            $stopped = $true
            $handled += $ownerPid
        }
        # Wait for the OS to actually release the port.
        for ($i = 0; $i -lt 25 -and (Test-PortOpen $Port); $i++) {
            Start-Sleep -Milliseconds 400
        }
    }

    if ($stopped) { Write-Ok "$Name stopped" } else { Write-Warn2 "$Name is not running" }
    return $stopped
}

function Test-VendorPresent {
    $missing = @()
    if (-not (Test-Path (Join-Path $PgHome 'bin\postgres.exe')))  { $missing += 'PostgreSQL' }
    if (-not (Test-Path (Join-Path $RedisHome 'redis-server.exe'))) { $missing += 'Redis' }
    if (-not (Test-Path (Join-Path $QdrantHome 'qdrant.exe')))     { $missing += 'Qdrant' }
    return $missing
}

function Get-PgEnv {
    $env:PGPASSWORD = $DbPass
    $env:PGCLIENTENCODING = 'UTF8'
}

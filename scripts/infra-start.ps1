# Start PostgreSQL, Redis and Qdrant as local, project-scoped processes.
#
#   powershell -ExecutionPolicy Bypass -File scripts\infra-start.ps1
#
# Nothing is registered as a Windows service and no system configuration is
# modified: every process, data file and log lives inside this repository.
#
# READINESS CONTRACT
# ------------------
# A listening port is NOT readiness. PostgreSQL binds its port before it finishes
# crash recovery and then answers "FATAL: the database system is starting up"
# until it is done, so a port check followed by provisioning fails intermittently.
# Every service here is therefore gated on a real probe:
#
#   PostgreSQL   pg_isready (exit 0)  then  psql -c "SELECT 1"
#   Redis        redis-cli ping  ->  PONG
#   Qdrant       HTTP GET /healthz -> 200
#
# Ordering is strictly serial: start -> ready -> provision -> verify. A service
# that does not become ready within its budget is reported FAILED and the script
# exits non-zero, so the caller never continues on a half-started stack.
#
. "$PSScriptRoot\lib.ps1"
Initialize-Dirs

$PgReadyTimeout    = 60
$RedisReadyTimeout = 30
$QdrantReadyTimeout = 60

Write-Host ''
Write-StartupLog -Service 'Infrastructure' -State 'STARTING'

$missing = Test-VendorPresent
if ($missing.Count -gt 0) {
    Write-StartupError ("Missing runtime(s): " + ($missing -join ', ') + ' - run scripts\fetch-runtime.ps1')
    exit 1
}

# --------------------------------------------------------------- PostgreSQL --
function Get-PgPidFromDataDir {
    <# The postmaster pid recorded by PostgreSQL itself, or $null. #>
    $pidFile = Join-Path $PgData 'postmaster.pid'
    if (-not (Test-Path $pidFile)) { return $null }
    $first = Get-Content -LiteralPath $pidFile -TotalCount 1 -ErrorAction SilentlyContinue
    if (-not $first) { return $null }
    $candidate = 0
    if (-not [int]::TryParse("$first".Trim(), [ref]$candidate)) { return $null }
    if (Get-Process -Id $candidate -ErrorAction SilentlyContinue) { return $candidate }
    return $null
}

function Start-Postgres {
    $ourPid = Get-PgPidFromDataDir

    if ($ourPid -and (Test-PgReady)) {
        Write-StartupLog -Service 'PostgreSQL' -State 'SKIPPED' -Detail "pid $ourPid"
    }
    else {
        if ($ourPid -and -not (Test-PgReady)) {
            # The process exists but is not accepting connections yet (for
            # example a slow crash-recovery run). Fall through to waiting.
            Write-Warn2 "PostgreSQL (pid $ourPid) is running but not accepting connections yet"
        }
        elseif (Test-PortOpen $PgPort) {
            # Something else holds the port and it is not our cluster.
            Write-StartupError "Port $PgPort is already in use, but it is not this project's PostgreSQL (no live pid in $PgData\postmaster.pid). Stop it or change PGPORT."
            return $false
        }

        if (-not $ourPid) {
            if (-not (Test-Path (Join-Path $PgData 'PG_VERSION'))) {
                Write-StartupLog -Service 'PostgreSQL' -State 'STARTING' -Detail 'initialising cluster'
                $pwfile = Join-Path $RuntimeDir '.pgpw'
                Set-Content -Path $pwfile -Value $DbPass -NoNewline -Encoding ascii
                & (Join-Path $PgHome 'bin\initdb.exe') -D $PgData -U $PgUser -A scram-sha-256 `
                    --pwfile=$pwfile -E UTF8 --locale=C | Out-Null
                Remove-Item $pwfile -Force -ErrorAction SilentlyContinue
                if ($LASTEXITCODE -ne 0) {
                    Write-StartupLog -Service 'PostgreSQL' -State 'FAILED' -Detail 'initdb failed'
                    Write-StartupError "PostgreSQL initdb failed - see $LogDir\postgres.err.log"
                    return $false
                }
            }

            Write-StartupLog -Service 'PostgreSQL' -State 'STARTING' -Detail "port $PgPort"
            if (Test-IsElevated) {
                # postgres.exe refuses to run with an elevated token, and Start-Process
                # cannot drop privileges. Two ways to get a restricted token, tried in
                # this order because each works where the other does not:
                #
                #   1. `runas /trustlevel:0x20000` — works from a normal elevated
                #      terminal (verified here: it writes a probe file and returns 0).
                #      It fails when the parent is inside a job object it cannot escape,
                #      which is how the desktop host launches this script.
                #   2. `Start-ProcessRestricted` — creates the token in-process, so the
                #      child stays in the parent's job. It needs SeImpersonatePrivilege,
                #      which a normal elevated token has enabled and this agent host's
                #      token does not (measured: `whoami /priv` shows it enabled, yet
                #      `AdjustTokenPrivileges` reports ERROR_NOT_ALL_ASSIGNED and
                #      CreateProcessWithTokenW returns win32=5).
                #
                # Either one failing is reported with the reason, because "port 5432
                # never opened" sent the last reader to a log file that took three
                # attempts to interpret.
                Write-Warn2 'Elevated shell detected - starting PostgreSQL with a restricted token'
                $restricted = $false

                $runas = Join-Path $env:SystemRoot 'System32\runas.exe'
                if (-not (Test-Path $runas)) { $runas = 'runas.exe' }
                $shell = Join-Path $PSHOME 'powershell.exe'
                if (-not (Test-Path $shell)) { $shell = 'powershell.exe' }
                $helper = Join-Path $PSScriptRoot '_start-postgres.ps1'
                if (Test-Path $helper) {
                    $payload = "`"$shell`" -NoProfile -ExecutionPolicy Bypass -File `"$helper`""
                    $launchOutput = & $runas /trustlevel:0x20000 $payload 2>&1
                    $launchExit = $LASTEXITCODE
                    if ($launchExit -eq 0) {
                        $restricted = $true
                    }
                    else {
                        # Recorded rather than swallowed: `runas` explains itself on
                        # stdout, and piping it to Out-Null threw away the only line
                        # that could have said why.
                        $detail = ($launchOutput | Out-String).Trim()
                        Write-StartupLog -Service 'PostgreSQL' -State 'STARTING' `
                            -Detail "runas exit $launchExit $detail"
                        Write-Warn2 "runas failed ($launchExit): $detail"
                    }
                }

                if (-not $restricted) {
                    . "$PSScriptRoot\_restricted-launch.ps1"
                    # `logging_collector`, because this launch cannot redirect
                    # stdout/stderr — it has no inherited handles to redirect, and
                    # postgres.log is the first place anyone looks when it will not start.
                    $pgArguments = ConvertTo-ProcessArgumentList @(
                        '-D', $PgData, '-p', "$PgPort",
                        '-c', 'listen_addresses=127.0.0.1',
                        '-c', 'logging_collector=on',
                        '-c', "log_directory=$LogDir",
                        '-c', 'log_filename=postgres.log'
                    )
                    $restricted = Start-ProcessRestricted `
                        -FilePath (Join-Path $PgHome 'bin\postgres.exe') `
                        -Arguments $pgArguments `
                        -WorkingDirectory $PgHome
                }

                if (-not $restricted) {
                    # Neither worked. Try the direct launch anyway: on a shell that is
                    # only *partly* elevated it succeeds, and on a genuinely elevated one
                    # it fails with postgres's own message rather than with a code.
                    Write-Warn2 'both restricted launches failed - trying a direct launch'
                    Start-Process -FilePath (Join-Path $PgHome 'bin\postgres.exe') `
                        -ArgumentList (ConvertTo-ProcessArgumentList @('-D', $PgData, '-p', "$PgPort", '-c', 'listen_addresses=127.0.0.1')) `
                        -WorkingDirectory $PgHome -WindowStyle Hidden `
                        -RedirectStandardOutput (Join-Path $LogDir 'postgres.out.log') `
                        -RedirectStandardError (Join-Path $LogDir 'postgres.err.log')
                }
            }
            else {
                # postgres.exe is launched directly instead of `pg_ctl start`: on
                # Windows pg_ctl keeps a handle on the inherited stdout pipe, which
                # makes a non-interactive shell block until the server exits.
                Start-Process -FilePath (Join-Path $PgHome 'bin\postgres.exe') `
                    -ArgumentList (ConvertTo-ProcessArgumentList @('-D', $PgData, '-p', "$PgPort", '-c', 'listen_addresses=127.0.0.1')) `
                    -WorkingDirectory $PgHome -WindowStyle Hidden `
                    -RedirectStandardOutput (Join-Path $LogDir 'postgres.out.log') `
                    -RedirectStandardError (Join-Path $LogDir 'postgres.err.log')
            }
        }
    }

    # --- 1. the port must appear (fast, not sufficient) -----------------------
    if (-not (Wait-Until -Condition { Test-PortOpen $PgPort } -TimeoutSeconds 30)) {
        Write-StartupLog -Service 'PostgreSQL' -State 'FAILED' -Detail "port $PgPort never opened"
        # Say *why* when the reason is knowable. "port 5432 never opened" sends the
        # reader to a log file; the log file says "Execution of PostgreSQL by a user with
        # administrative permissions is not permitted", which names the actual problem
        # and the actual fix (start it from a normal, non-elevated shell).
        $adminRefusal = $false
        foreach ($candidate in @('postgres.err.log', 'postgres.log')) {
            $path = Join-Path $LogDir $candidate
            if (Test-Path $path) {
                $text = Get-Content -LiteralPath $path -Raw -ErrorAction SilentlyContinue
                if ($text -and $text -match 'administrative permissions') { $adminRefusal = $true }
            }
        }
        if ($adminRefusal) {
            Write-StartupError ("PostgreSQL 拒绝以管理员身份运行（这层 shell 是提权的），" +
                "本机也无法创建受限令牌来绕开它。请从普通（非提权）终端启动，" +
                "或直接双击桌面上的 AI Workbench 快捷方式。详见 $LogDir\postgres.log")
        }
        else {
            Write-StartupError "PostgreSQL did not bind port $PgPort within 30s - see $LogDir\postgres.err.log"
        }
        return $false
    }

    # --- 2. pg_isready: the real "accepting connections" signal ---------------
    Write-Step 'Waiting for PostgreSQL readiness (pg_isready)...'
    if (-not (Wait-ServiceReady -Name 'PostgreSQL' -Probe { Test-PgReady } `
                -TimeoutSeconds $PgReadyTimeout -ProbeLabel 'pg_isready')) {
        Write-StartupError "PostgreSQL failed to become ready within ${PgReadyTimeout} seconds."
        return $false
    }

    # --- 3. provision only now that the cluster accepts connections ----------
    # Every psql call goes through Invoke-Native: psql reports problems on
    # stderr, and under $ErrorActionPreference='Stop' a raw call would abort the
    # script instead of returning a result we can report properly.
    Get-PgEnv
    $psql = Join-Path $PgHome 'bin\psql.exe'

    $roleCheck = Invoke-Native -FilePath $psql -Arguments @(
        '-U', $PgUser, '-p', "$PgPort", '-d', 'postgres', '-tAc',
        "SELECT 1 FROM pg_roles WHERE rolname='$DbUser'"
    )
    if ($roleCheck.Output.Trim() -ne '1') {
        Write-Step "Creating role $DbUser"
        # CREATEDB lets the test suite create its own isolated database.
        $created = Invoke-Native -FilePath $psql -Arguments @(
            '-U', $PgUser, '-p', "$PgPort", '-d', 'postgres', '-c',
            "CREATE ROLE $DbUser LOGIN CREATEDB PASSWORD '$DbPass'"
        )
        if ($created.ExitCode -ne 0) {
            Write-StartupLog -Service 'PostgreSQL' -State 'FAILED' -Detail 'could not create the role'
            Write-StartupError "Creating role $DbUser failed: $($created.Output.Trim())"
            return $false
        }
    } else {
        Invoke-Native -FilePath $psql -Arguments @(
            '-U', $PgUser, '-p', "$PgPort", '-d', 'postgres', '-c', "ALTER ROLE $DbUser CREATEDB"
        ) | Out-Null
    }

    $dbCheck = Invoke-Native -FilePath $psql -Arguments @(
        '-U', $PgUser, '-p', "$PgPort", '-d', 'postgres', '-tAc',
        "SELECT 1 FROM pg_database WHERE datname='$DbName'"
    )
    if ($dbCheck.Output.Trim() -ne '1') {
        Write-Step "Creating database $DbName"
        $createdDb = Invoke-Native -FilePath $psql -Arguments @(
            '-U', $PgUser, '-p', "$PgPort", '-d', 'postgres', '-c',
            "CREATE DATABASE $DbName OWNER $DbUser ENCODING 'UTF8'"
        )
        if ($createdDb.ExitCode -ne 0) {
            Write-StartupLog -Service 'PostgreSQL' -State 'FAILED' -Detail 'could not create the database'
            Write-StartupError "Creating database $DbName failed: $($createdDb.Output.Trim())"
            return $false
        }
        Invoke-Native -FilePath $psql -Arguments @(
            '-U', $PgUser, '-p', "$PgPort", '-d', $DbName, '-c',
            "GRANT ALL ON SCHEMA public TO $DbUser"
        ) | Out-Null
    }

    # --- 4. prove an authenticated query works before declaring ready --------
    if (-not (Test-PgQuery)) {
        Write-StartupLog -Service 'PostgreSQL' -State 'FAILED' -Detail 'SELECT 1 failed after provisioning'
        Write-StartupError "PostgreSQL is accepting connections but SELECT 1 failed - check the credentials in .env"
        return $false
    }

    $pgPid = Get-PgPidFromDataDir
    if ($pgPid) { Set-TrackedPid -Name 'postgres' -ProcessId $pgPid }
    Write-Ok "PostgreSQL ready: postgresql://${DbUser}:***@127.0.0.1:$PgPort/$DbName"
    return $true
}

# -------------------------------------------------------------------- Redis --
function Start-Redis {
    if (Test-RedisReady) {
        Write-StartupLog -Service 'Redis' -State 'SKIPPED' -Detail 'PONG'
        return $true
    }
    if (Test-PortOpen $RedisPort) {
        Write-StartupError "Port $RedisPort is in use but does not answer PING - it is not this project's Redis."
        return $false
    }

    if (-not (Test-Path $RedisConf)) {
        Write-Step 'Writing redis.conf'
        $conf = @"
# AI Workbench - project scoped Redis (generated by scripts/infra-start.ps1)
bind 127.0.0.1
protected-mode yes
port $RedisPort
timeout 0
tcp-keepalive 300
databases 16
daemonize no
loglevel notice
logfile "$($RedisLog -replace '\\','/')"
dir "$($RedisDir -replace '\\','/')"
# Cache-only usage: persistence disabled on purpose (see docs/architecture.md).
save ""
appendonly no
maxmemory 256mb
maxmemory-policy allkeys-lru
"@
        Set-Content -Path $RedisConf -Value $conf -Encoding ascii
    }

    Write-StartupLog -Service 'Redis' -State 'STARTING' -Detail "port $RedisPort"
    $proc = Start-Process -FilePath (Join-Path $RedisHome 'redis-server.exe') `
        -ArgumentList (ConvertTo-ProcessArgumentList @($RedisConf)) -WorkingDirectory $RedisDir `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir 'redis.out.log') `
        -RedirectStandardError (Join-Path $LogDir 'redis.err.log')
    Set-TrackedPid -Name 'redis' -ProcessId $proc.Id

    if (-not (Wait-ServiceReady -Name 'Redis' -Probe { Test-RedisReady } `
                -TimeoutSeconds $RedisReadyTimeout -ProbeLabel 'redis-cli ping')) {
        Write-StartupError "Redis failed to become ready within ${RedisReadyTimeout} seconds."
        return $false
    }
    Write-Ok "Redis ready: redis://127.0.0.1:$RedisPort/0"
    return $true
}

# ------------------------------------------------------------------- Qdrant --
function Start-Qdrant {
    if (Test-QdrantReady) {
        Write-StartupLog -Service 'Qdrant' -State 'SKIPPED' -Detail 'HTTP /healthz 200'
        return $true
    }
    if (Test-PortOpen $QdrantPort) {
        Write-StartupError "Port $QdrantPort is in use but does not answer HTTP /healthz - it is not this project's Qdrant."
        return $false
    }

    $cfgDir = Join-Path $QdrantDir 'config'
    if (-not (Test-Path $cfgDir)) { New-Item -ItemType Directory -Path $cfgDir -Force | Out-Null }
    $cfg = Join-Path $cfgDir 'config.yaml'
    if (-not (Test-Path $cfg)) {
        Write-Step 'Writing Qdrant config.yaml'
        $yaml = @"
log_level: INFO
telemetry_disabled: true
storage:
  storage_path: ./storage
  snapshots_path: ./snapshots
  on_disk_payload: true
service:
  host: 127.0.0.1
  http_port: $QdrantPort
  grpc_port: 6334
  enable_cors: true
  max_request_size_mb: 64
"@
        Set-Content -Path $cfg -Value $yaml -Encoding ascii
    }

    Write-StartupLog -Service 'Qdrant' -State 'STARTING' -Detail "port $QdrantPort"
    $proc = Start-Process -FilePath (Join-Path $QdrantHome 'qdrant.exe') `
        -WorkingDirectory $QdrantDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $LogDir 'qdrant.out.log') `
        -RedirectStandardError (Join-Path $LogDir 'qdrant.err.log')
    Set-TrackedPid -Name 'qdrant' -ProcessId $proc.Id

    if (-not (Wait-ServiceReady -Name 'Qdrant' -Probe { Test-QdrantReady } `
                -TimeoutSeconds $QdrantReadyTimeout -ProbeLabel 'HTTP /healthz')) {
        Write-StartupError "Qdrant failed to become ready within ${QdrantReadyTimeout} seconds."
        return $false
    }
    Write-Ok "Qdrant ready: http://127.0.0.1:$QdrantPort"
    return $true
}

# ------------------------------------------------------------------- driver --
# Strictly serial: each service must be genuinely ready before the next starts,
# and provisioning happens only after PostgreSQL accepts connections.
if (-not (Start-Postgres)) { exit 1 }
if (-not (Start-Redis))    { exit 1 }
if (-not (Start-Qdrant))   { exit 1 }

Write-Host ''
Write-StartupLog -Service 'Infrastructure' -State 'READY'
Write-Host '  PostgreSQL  127.0.0.1:5432   db=aiworkbench'
Write-Host '  Redis       127.0.0.1:6379'
Write-Host '  Qdrant      http://127.0.0.1:6333/dashboard'
exit 0

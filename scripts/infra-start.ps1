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
# Ordering: PostgreSQL first and alone, because nothing can be provisioned until it
# accepts connections; then Redis and Qdrant together, because neither depends on the
# other and waiting for one before launching the other is idle time. A service that
# does not become ready within its budget is reported FAILED and the script exits
# non-zero, so the caller never continues on a half-started stack.
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
            # `logging_collector`, because none of the restricted launches below can
            # redirect stdout/stderr — they have no inherited handles to redirect, and
            # postgres.log is the first place anyone looks when the cluster will not start.
            $pgArguments = ConvertTo-ProcessArgumentList @(
                '-D', $PgData, '-p', "$PgPort",
                '-c', 'listen_addresses=127.0.0.1',
                '-c', 'logging_collector=on',
                '-c', "log_directory=$LogDir",
                '-c', 'log_filename=postgres.log'
            )
            if (Test-IsElevated) {
                # postgres.exe refuses to run with an elevated token, and Start-Process
                # cannot drop privileges. Three ways to get a non-elevated one, tried in
                # this order because each works where the others do not — see the header of
                # `_restricted-launch.ps1` for the measurements:
                #
                #   1. `runas /trustlevel:0x20000` — works from a normal elevated terminal.
                #      Fails when the parent is inside a job object it cannot escape, which
                #      is how the desktop host launches this script.
                #   2. `Start-ProcessRestricted` — creates the token in-process. Needs
                #      SeImpersonatePrivilege, which this agent host's token does not have
                #      assignable (AdjustTokenPrivileges → ERROR_NOT_ALL_ASSIGNED,
                #      CreateProcessWithTokenW → win32=5).
                #   3. `Start-ProcessLimited` — has the Task Scheduler service do it with
                #      `RunLevel = Limited`. Needs no privilege and worked here where 1 and
                #      2 both failed.
                #
                # Each failure is reported with its own reason, because "port 5432 never
                # opened" sent the last reader to a log file that took three attempts to
                # interpret.
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
                    $restricted = Start-ProcessRestricted `
                        -FilePath (Join-Path $PgHome 'bin\postgres.exe') `
                        -Arguments $pgArguments `
                        -WorkingDirectory $PgHome
                }

                if (-not $restricted) {
                    # Neither of the in-session ways worked. Hand it to the Task Scheduler
                    # service, which can start a process with a filtered token without
                    # needing any privilege from us. It waits for the port itself and
                    # cleans up its own task definition.
                    $restricted = Start-ProcessLimited `
                        -FilePath (Join-Path $PgHome 'bin\postgres.exe') `
                        -Arguments $pgArguments `
                        -WorkingDirectory $PgHome `
                        -Port $PgPort
                }

                if (-not $restricted) {
                    # All three failed. Try the direct launch anyway: on a shell that is
                    # only *partly* elevated it succeeds, and on a genuinely elevated one
                    # it fails with postgres's own message rather than with a code.
                    Write-Warn2 'all restricted launches failed - trying a direct launch'
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
            # Every workaround has now been tried (see the branch above); reaching here
            # means the cluster really is refusing because this shell is elevated and none
            # of the three ways to drop the token worked. Say that, and say what to do —
            # the previous message here claimed the machine could not create a restricted
            # token, which stopped being true once the Task Scheduler path was added.
            Write-StartupError ("PostgreSQL 拒绝以管理员身份运行（这层 shell 是提权的），" +
                "而三种降权启动都没成功（runas / 进程内受限令牌 / 计划任务）。" +
                "请从普通（非提权）终端启动，或直接双击桌面上的 AI Workbench 快捷方式。" +
                "详见 $LogDir\startup.log 与 $LogDir\postgres.log")
        }
        else {
            Write-StartupError "PostgreSQL did not bind port $PgPort within 30s - see $LogDir\postgres.err.log"
        }
        return $false
    }

    # --- 2. pg_isready: the real "accepting connections" signal ---------------
    Write-Step 'Waiting for PostgreSQL readiness (pg_isready)...'
    $readyStarted = Get-Date
    if (-not (Wait-Until -Condition { Test-PgReady } -TimeoutSeconds $PgReadyTimeout)) {
        Write-StartupLog -Service 'PostgreSQL' -State 'FAILED' `
            -Detail "not ready after ${PgReadyTimeout}s (pg_isready)"
        Write-StartupError "PostgreSQL failed to become ready within ${PgReadyTimeout} seconds."
        return $false
    }
    $readySeconds = [math]::Round(((Get-Date) - $readyStarted).TotalSeconds, 1)

    # --- 3. is this cluster already set up for the application? --------------
    #
    # **One call, asked as the application's own role.** It answers four questions at
    # once — does the role exist, does the database exist, does the password work, and
    # may the role create objects in `public` — because it *is* the connection the
    # backend makes. A yes here is a yes for the thing that actually has to work, which
    # is more than the old four separate checks proved between them.
    #
    # Why this shape and not the old one: those four checks ran on **every** start, and
    # each cost 2.06 seconds (see `Get-PgClientArgs` in lib.ps1), which was the whole
    # twelve-second gap between `PostgreSQL READY` and `Redis STARTING`. None of them
    # needed to run when the answer had not changed.
    Get-PgEnv
    $psql = Join-Path $PgHome 'bin\psql.exe'
    $pgArgs = Get-PgClientArgs

    $provisionStarted = Get-Date
    # The SQL is built in a variable rather than inline: PowerShell's `,` binds looser
    # than `+`, so `@('a', "x" + "y")` is three elements, not two — and psql then
    # receives half a statement as a stray argument. Measured, from the failure that
    # this comment exists to prevent:
    #
    #     psql: warning: extra command-line argument "AND (SELECT rolcreatedb ..." ignored
    #     ERROR:  syntax error at end of input
    $provisionProbeSql = "SELECT (has_schema_privilege(current_user, 'public', 'CREATE') " +
    "AND (SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user))::int"
    $probe = Invoke-Native -FilePath $psql -Arguments ($pgArgs + @(
            '-U', $DbUser, '-d', $DbName, '-tAc', $provisionProbeSql
        ))
    $inPlace = ($probe.ExitCode -eq 0 -and $probe.Output.Trim() -eq '1')

    if ($inPlace) {
        $provisionDetail = 'already set up'
    }
    else {
        # The first run, or a cluster that lost something. Every step below is
        # idempotent and re-runs the full sequence, because a half-applied upgrade is
        # the one state that must not be guessed at.
        Write-Step 'Preparing the database for the application'

        $roleCheck = Invoke-Native -FilePath $psql -Arguments ($pgArgs + @(
                '-U', $PgUser, '-d', 'postgres', '-tAc',
                "SELECT 1 FROM pg_roles WHERE rolname='$DbUser'"
            ))
        if ($roleCheck.Output.Trim() -ne '1') {
            Write-Step "Creating role $DbUser"
            # CREATEDB lets the test suite create its own isolated database.
            $created = Invoke-Native -FilePath $psql -Arguments ($pgArgs + @(
                    '-U', $PgUser, '-d', 'postgres', '-c',
                    "CREATE ROLE $DbUser LOGIN CREATEDB PASSWORD '$DbPass'"
                ))
            if ($created.ExitCode -ne 0) {
                Write-StartupLog -Service 'PostgreSQL' -State 'FAILED' -Detail 'could not create the role'
                Write-StartupError "Creating role $DbUser failed: $($created.Output.Trim())"
                return $false
            }
        }
        else {
            Invoke-Native -FilePath $psql -Arguments ($pgArgs + @(
                    '-U', $PgUser, '-d', 'postgres', '-c', "ALTER ROLE $DbUser CREATEDB"
                )) | Out-Null
        }

        $dbCheck = Invoke-Native -FilePath $psql -Arguments ($pgArgs + @(
                '-U', $PgUser, '-d', 'postgres', '-tAc',
                "SELECT 1 FROM pg_database WHERE datname='$DbName'"
            ))
        if ($dbCheck.Output.Trim() -ne '1') {
            Write-Step "Creating database $DbName"
            $createdDb = Invoke-Native -FilePath $psql -Arguments ($pgArgs + @(
                    '-U', $PgUser, '-d', 'postgres', '-c',
                    "CREATE DATABASE $DbName OWNER $DbUser ENCODING 'UTF8'"
                ))
            if ($createdDb.ExitCode -ne 0) {
                Write-StartupLog -Service 'PostgreSQL' -State 'FAILED' -Detail 'could not create the database'
                Write-StartupError "Creating database $DbName failed: $($createdDb.Output.Trim())"
                return $false
            }
        }

        # Granted unconditionally: on PostgreSQL 15+ the `public` schema is owned by
        # `pg_database_owner`, so a fresh database hands the application role no CREATE
        # right and `create_all` fails on the first table.
        Invoke-Native -FilePath $psql -Arguments ($pgArgs + @(
                '-U', $PgUser, '-d', $DbName, '-c', "GRANT ALL ON SCHEMA public TO $DbUser"
            )) | Out-Null

        # --- 4. prove it, after provisioning, with the same call as step 3 -----
        $again = Invoke-Native -FilePath $psql -Arguments ($pgArgs + @(
                '-U', $DbUser, '-d', $DbName, '-tAc', $provisionProbeSql
            ))
        if ($again.ExitCode -ne 0 -or $again.Output.Trim() -ne '1') {
            Write-StartupLog -Service 'PostgreSQL' -State 'FAILED' -Detail 'provisioning did not take effect'
            Write-StartupError ("PostgreSQL is accepting connections but the application role still " +
                "cannot use the database - check the credentials in .env. " +
                "psql said: $($again.Output.Trim())")
            return $false
        }
        $provisionDetail = 'created'
    }

    $provisionSeconds = [math]::Round(((Get-Date) - $provisionStarted).TotalSeconds, 1)
    $pgPid = Get-PgPidFromDataDir
    if ($pgPid) { Set-TrackedPid -Name 'postgres' -ProcessId $pgPid }
    Write-Ok "PostgreSQL ready: postgresql://${DbUser}:***@127.0.0.1:$PgPort/$DbName"
    # One line carrying both numbers, because "READY" on its own was true for sixteen
    # seconds before the stack could actually be used and the log did not say so.
    Write-StartupLog -Service 'PostgreSQL' -State 'READY' `
        -Detail "${readySeconds}s via pg_isready, schema ${provisionSeconds}s ($provisionDetail)"
    return $true
}

# -------------------------------------------------------------------- Redis --
function Start-Redis {
    <#
      Launch Redis if it is not already up.

      Returns `'already'` when there was nothing to launch, `'started'` when a process
      was spawned. The caller waits for readiness separately, so that Redis and Qdrant
      can be launched together and then waited for together — they do not depend on
      each other or on PostgreSQL, and waiting for one before launching the other is
      pure idle time.

      **The order of the two checks matters, and it is the fix for seven seconds.**
      The old version asked `Test-RedisReady` first, which spawns `redis-cli`. That is
      cheap when Redis is running and expensive when it is not, because the probe then
      has to fail. `Test-QdrantReady` was far worse: three HTTP paths, each waiting out
      a connection timeout against a closed port, measured at **7 seconds** before
      Qdrant was even launched.

      A TCP connect to a closed loopback port costs about a millisecond, so the port is
      now asked first and the real probe runs only when something is actually listening.
    #>
    if (-not (Test-PortOpen $RedisPort)) {
        # Nothing is listening, so nothing of ours is up: no probe needed.
    }
    elseif (Test-RedisReady) {
        Write-StartupLog -Service 'Redis' -State 'SKIPPED' -Detail 'PONG'
        return 'already'
    }
    else {
        Write-StartupError "Port $RedisPort is in use but does not answer PING - it is not this project's Redis."
        return 'failed'
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
    return 'started'
}

function Wait-RedisReady {
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
    <# Launch Qdrant if it is not already up. See Start-Redis for the check order. #>
    if (-not (Test-PortOpen $QdrantPort)) {
        # Nothing is listening: skip the probe, which is the seven-second one.
    }
    elseif (Test-QdrantReady) {
        Write-StartupLog -Service 'Qdrant' -State 'SKIPPED' -Detail 'HTTP /healthz 200'
        return 'already'
    }
    else {
        Write-StartupError "Port $QdrantPort is in use but does not answer HTTP /healthz - it is not this project's Qdrant."
        return 'failed'
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
    return 'started'
}

function Wait-QdrantReady {
    if (-not (Wait-ServiceReady -Name 'Qdrant' -Probe { Test-QdrantReady } `
                -TimeoutSeconds $QdrantReadyTimeout -ProbeLabel 'HTTP /healthz')) {
        Write-StartupError "Qdrant failed to become ready within ${QdrantReadyTimeout} seconds."
        return $false
    }
    Write-Ok "Qdrant ready: http://127.0.0.1:$QdrantPort"
    return $true
}

# ------------------------------------------------------------------- driver --
#
# PostgreSQL first, alone: nothing else can be provisioned until it accepts
# connections, so there is nothing to overlap with it.
#
# Then Redis and Qdrant **together**. They do not depend on each other and nothing
# depends on them until the API starts, so the old serial order (launch Redis, wait
# for Redis, launch Qdrant, wait for Qdrant) was idle time in the middle of startup
# for no reason. Launching both and then waiting for both is the whole change.
#
# The failure contract is unchanged: any service that does not become ready is
# reported FAILED and this script exits non-zero, so the caller never continues on a
# half-started stack. The only difference is that a Redis failure no longer prevents
# Qdrant from being *launched* — it is still reported, and `stop-desktop.bat` still
# cleans it up.
$infraStarted = Get-Date
if (-not (Start-Postgres)) { exit 1 }

$redisState = Start-Redis
$qdrantState = Start-Qdrant
if ($redisState -eq 'failed' -or $qdrantState -eq 'failed') { exit 1 }

$redisOk = $true
if ($redisState -eq 'started') { $redisOk = Wait-RedisReady }
$qdrantOk = $true
if ($qdrantState -eq 'started') { $qdrantOk = Wait-QdrantReady }
if (-not $redisOk -or -not $qdrantOk) { exit 1 }

Write-Host ''
Write-StartupLog -Service 'Infrastructure' -State 'READY' `
    -Detail "$([math]::Round(((Get-Date) - $infraStarted).TotalSeconds, 1))s total"
Write-Host '  PostgreSQL  127.0.0.1:5432   db=aiworkbench'
Write-Host '  Redis       127.0.0.1:6379'
Write-Host '  Qdrant      http://127.0.0.1:6333/dashboard'
exit 0

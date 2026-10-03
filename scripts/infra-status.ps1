# Report the health of every local dependency.
#
#   powershell -ExecutionPolicy Bypass -File scripts\infra-status.ps1
#
# Every row is decided by a real readiness probe, never by a listening port:
#
#   PostgreSQL   pg_isready + SELECT 1
#   Redis        redis-cli ping -> PONG
#   Qdrant       HTTP GET /healthz -> 200
#   Backend API  HTTP GET /api/v1/health -> 200
#   Frontend     HTTP GET / -> 200
#
# "Up" therefore means "usable", which is the only thing worth reporting.
#
. "$PSScriptRoot\lib.ps1"

$rows = @()

# ------------------------------------------------------------- PostgreSQL ---
$pgReady = Test-PgReady
$pgDetail = ''
if ($pgReady) {
    Get-PgEnv
    $version = Invoke-Native -FilePath (Join-Path $PgHome 'bin\psql.exe') `
        -Arguments @('-U', $PgUser, '-p', "$PgPort", '-d', $DbName, '-tAc', 'select version()')
    if ($version.ExitCode -eq 0) {
        $pgDetail = ($version.Output -split ',')[0].Trim()
    } else {
        $pgReady = $false
        $pgDetail = 'pg_isready ok but the query failed'
    }
} elseif (Test-PortOpen $PgPort) {
    $pgDetail = 'port open but not accepting connections'
} else {
    $pgDetail = 'not running'
}
$rows += [pscustomobject]@{ Service = 'PostgreSQL'; Port = $PgPort; Ready = $pgReady; Detail = $pgDetail }

# ------------------------------------------------------------------ Redis ---
$redisReady = Test-RedisReady
$rows += [pscustomobject]@{
    Service = 'Redis'; Port = $RedisPort; Ready = $redisReady
    Detail  = if ($redisReady) { 'PONG' } elseif (Test-PortOpen $RedisPort) { 'port open but no PONG' } else { 'not running' }
}

# ----------------------------------------------------------------- Qdrant ---
$qdrantReady = Test-QdrantReady
$qdrantDetail = 'not running'
if ($qdrantReady) {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$QdrantPort/collections" -TimeoutSec 5
        $names = ($r.result.collections | ForEach-Object { $_.name }) -join ','
        $qdrantDetail = "collections=[$names]"
    } catch {
        $qdrantDetail = 'healthz ok but /collections failed'
    }
} elseif (Test-PortOpen $QdrantPort) {
    $qdrantDetail = 'port open but /healthz failed'
}
$rows += [pscustomobject]@{ Service = 'Qdrant'; Port = $QdrantPort; Ready = $qdrantReady; Detail = $qdrantDetail }

# ------------------------------------------------------------- application --
foreach ($svc in @(
    @{ n = 'Backend API'; p = 8000; url = 'http://127.0.0.1:8000/api/v1/health' },
    @{ n = 'Frontend';    p = 5173; url = 'http://127.0.0.1:5173/' }
)) {
    $ready = Test-HttpOk -Url $svc.url
    $rows += [pscustomobject]@{
        Service = $svc.n; Port = $svc.p; Ready = $ready
        Detail  = if ($ready) { $svc.url } elseif (Test-PortOpen $svc.p) { 'port open but no HTTP 200' } else { 'not running' }
    }
}

$rows | Format-Table -AutoSize

$core = $rows | Where-Object { $_.Service -in @('PostgreSQL', 'Redis', 'Qdrant') -and -not $_.Ready }
if ($core) {
    Write-Warn2 'Some core services are not ready. Run scripts\infra-start.ps1'
    exit 1
}
Write-Ok 'All core infrastructure services are ready.'
exit 0

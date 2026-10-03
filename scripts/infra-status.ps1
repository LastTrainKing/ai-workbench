# Report the health of every local dependency.
#   powershell -ExecutionPolicy Bypass -File scripts\infra-status.ps1
#
. "$PSScriptRoot\lib.ps1"

$rows = @()

# PostgreSQL
$pgOk = Test-PortOpen $PgPort
$pgVersion = ''
if ($pgOk) {
    Get-PgEnv
    try {
        $pgVersion = (& (Join-Path $PgHome 'bin\psql.exe') -U $PgUser -p $PgPort -d $DbName -tAc `
            'select version()' 2>$null)
    } catch { $pgVersion = 'query failed' }
}
$rows += [pscustomobject]@{ Service = 'PostgreSQL'; Port = $PgPort; Up = $pgOk; Detail = ($pgVersion -split ',')[0] }

# Redis
$redisOk = Test-PortOpen $RedisPort
$redisPong = ''
if ($redisOk) {
    try {
        $redisPong = (& (Join-Path $RedisHome 'redis-cli.exe') -p $RedisPort ping 2>$null)
    } catch { $redisPong = 'ping failed' }
}
$rows += [pscustomobject]@{ Service = 'Redis'; Port = $RedisPort; Up = $redisOk; Detail = $redisPong }

# Qdrant
$qdrantOk = Test-PortOpen $QdrantPort
$qdrantDetail = ''
if ($qdrantOk) {
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$QdrantPort/collections" -TimeoutSec 5
        $names = ($r.result.collections | ForEach-Object { $_.name }) -join ','
        $qdrantDetail = "collections=[$names]"
    } catch { $qdrantDetail = 'api error' }
}
$rows += [pscustomobject]@{ Service = 'Qdrant'; Port = $QdrantPort; Up = $qdrantOk; Detail = $qdrantDetail }

# Application ports
foreach ($p in @(@{n='Backend API';p=8000}, @{n='Frontend';p=5173})) {
    $rows += [pscustomobject]@{ Service = $p.n; Port = $p.p; Up = (Test-PortOpen $p.p); Detail = '' }
}

$rows | Format-Table -AutoSize

if ($rows | Where-Object { $_.Service -in @('PostgreSQL','Redis','Qdrant') -and -not $_.Up }) {
    Write-Warn2 'Some core services are down. Run scripts\infra-start.ps1'
    exit 1
}
Write-Ok 'All core infrastructure services are up.'

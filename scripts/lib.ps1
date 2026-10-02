# Shared helpers for AI Workbench operational scripts (Windows / PowerShell).
# Dot-source this file: . "$PSScriptRoot\lib.ps1"

$ErrorActionPreference = 'Stop'

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

$Script:LogDir = Join-Path $RuntimeDir 'logs'

function Initialize-Dirs {
    foreach ($d in @($RuntimeDir, $LogDir, $RedisDir, $QdrantDir)) {
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
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortOpen $Port) { Write-Ok "$Label is listening on $Port"; return $true }
        Start-Sleep -Milliseconds 600
    }
    Write-Err "$Label did not open port $Port within ${TimeoutSeconds}s"
    return $false
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

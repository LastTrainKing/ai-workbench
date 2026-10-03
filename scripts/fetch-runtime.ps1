# Download the native runtime binaries (PostgreSQL / Redis / Qdrant) into vendor/.
# The binaries are intentionally NOT committed to git (see .gitignore); this
# script makes a fresh clone reproducible with a single command.
#
#   powershell -ExecutionPolicy Bypass -File scripts\fetch-runtime.ps1
#
. "$PSScriptRoot\lib.ps1"
Initialize-Dirs

$targets = @(
    @{
        Name = 'Qdrant'
        Url  = 'https://github.com/qdrant/qdrant/releases/download/v1.12.4/qdrant-x86_64-pc-windows-msvc.zip'
        Zip  = Join-Path $VendorDir 'qdrant-win.zip'
        Dest = Join-Path $VendorDir 'qdrant'
        Exe  = 'qdrant.exe'
    },
    @{
        Name = 'Redis'
        Url  = 'https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip'
        Zip  = Join-Path $VendorDir 'redis-win.zip'
        Dest = Join-Path $VendorDir 'redis'
        Exe  = 'redis-server.exe'
    },
    @{
        Name = 'PostgreSQL 16.4'
        Url  = 'https://get.enterprisedb.com/postgresql/postgresql-16.4-1-windows-x64-binaries.zip'
        Zip  = Join-Path $VendorDir 'pgsql-win.zip'
        Dest = Join-Path $VendorDir 'pgsql'
        Exe  = 'pgsql\bin\postgres.exe'
    }
)

New-Item -ItemType Directory -Path $VendorDir -Force | Out-Null

foreach ($t in $targets) {
    $exePath = Join-Path $t.Dest $t.Exe
    if (Test-Path $exePath) {
        Write-Ok "$($t.Name) already present, skipping"
        continue
    }
    Write-Step "Downloading $($t.Name)"
    if (-not (Test-Path $t.Zip)) {
        curl.exe -L --retry 3 --retry-delay 2 -o $t.Zip $t.Url
        if ($LASTEXITCODE -ne 0) { Write-Err "Download failed for $($t.Name)"; exit 1 }
    }
    Write-Step "Extracting $($t.Name)"
    if (Test-Path $t.Dest) { Remove-Item -Recurse -Force $t.Dest }
    Expand-Archive -Path $t.Zip -DestinationPath $t.Dest -Force
    if (-not (Test-Path $exePath)) { Write-Err "Extraction failed for $($t.Name)"; exit 1 }
    Write-Ok "$($t.Name) ready"
}

Write-Ok "All runtimes are ready under vendor/runtime"

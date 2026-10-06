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

# ------------------------------------------------------------------- python --
# The interpreter ships with the product, so an installed copy depends on nothing
# outside its own directory.
#
# Why this package and not the obvious ones:
#
#   .venv                    not relocatable. `pyvenv.cfg` records the absolute path
#                            of the interpreter that created it, so a copy only runs
#                            while that interpreter is still installed on that machine.
#   python-3.x-embed-amd64   rejected on measurement. Its `python313._pth` puts the
#                            interpreter into isolated mode, where `sys.path` ignores
#                            both the working directory and PYTHONPATH -- and the
#                            backend is started as `python -m app.cli` from `backend/`,
#                            which then cannot find the `app` package:
#                                ModuleNotFoundError: No module named 'app'
#   nuget python             a full CPython layout, no `._pth`, normal path behaviour.
#                            Official python.org build, ~14 MB compressed.
#
# The result is ~320 MB on disk after the backend's dependencies, which is why it is
# fetched rather than committed.

$PythonHome = Join-Path $VendorDir 'python'
$PythonExe  = Join-Path $PythonHome 'python.exe'
$PythonZip  = Join-Path $VendorDir 'python-nuget.zip'
$PythonUrl  = 'https://www.nuget.org/api/v2/package/python/3.13.14'
$Requirements = Join-Path $RepoRoot 'backend\requirements.txt'

function Test-PythonReady {
    <#
      Ready means "can run the backend", not "the interpreter is there".

      An interpreter without the dependencies is the worst of both worlds: the file
      exists so every check passes, and the product fails at the first request. The
      document libraries are checked by name because they are imported lazily --
      nothing fails at startup if they are missing.
    #>
    if (-not (Test-Path $PythonExe)) { return $false }
    $probe = & $PythonExe -c "import fastapi, sqlalchemy, asyncpg, pptx, docx, openpyxl, reportlab, matplotlib; print('ok')" 2>&1
    return ($LASTEXITCODE -eq 0 -and "$probe" -match 'ok')
}

if (Test-PythonReady) {
    Write-Ok "Python runtime already present, skipping"
} else {
    Write-Step "Downloading Python 3.13.14 (official NuGet package)"
    if (-not (Test-Path $PythonZip)) {
        curl.exe -L --retry 3 --retry-delay 2 -o $PythonZip $PythonUrl
        if ($LASTEXITCODE -ne 0) { Write-Err "Download failed for Python"; exit 1 }
    }

    Write-Step "Extracting Python"
    $staging = Join-Path $VendorDir 'python-staging'
    if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
    Expand-Archive -Path $PythonZip -DestinationPath $staging -Force

    # The package keeps the interpreter under tools/; the rest is NuGet metadata.
    $toolsDir = Join-Path $staging 'tools'
    if (-not (Test-Path (Join-Path $toolsDir 'python.exe'))) {
        Write-Err "The Python package did not contain tools\python.exe"
        exit 1
    }
    if (Test-Path $PythonHome) { Remove-Item -Recurse -Force $PythonHome }
    Move-Item -Path $toolsDir -Destination $PythonHome
    Remove-Item -Recurse -Force $staging

    $version = & $PythonExe --version
    Write-Ok "Python ready: $version"

    Write-Step "Installing the backend's dependencies into it"
    # Deliberately NOT `-q`. The first version of this used `-q`, and when pip
    # returned non-zero the script exited 1 having printed nothing at all: the
    # reason was inside the output that `-q` suppressed. A silent failure is the one
    # shape this project keeps having to fix, so pip is left verbose and its output
    # is captured and shown on failure.
    $pipOutput = & $PythonExe -m pip install --no-warn-script-location --disable-pip-version-check -r $Requirements 2>&1
    $pipExit = $LASTEXITCODE
    if ($pipExit -ne 0) {
        Write-Err "pip install failed (exit $pipExit). Last lines of its output:"
        $pipOutput | Select-Object -Last 25 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        exit 1
    }

    if (-not (Test-PythonReady)) {
        Write-Err "The Python runtime is present but cannot import the backend's dependencies."
        $probe = & $PythonExe -c "import fastapi, sqlalchemy, asyncpg, pptx, docx, openpyxl, reportlab, matplotlib; print('ok')" 2>&1
        $probe | Select-Object -Last 15 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        exit 1
    }
    Write-Ok "Python runtime ready (with the backend's dependencies)"
}

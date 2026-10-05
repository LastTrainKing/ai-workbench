# ============================================================================
#  AI Workbench - create the desktop shortcut
#
#  The shortcut launches the DESKTOP APPLICATION, not a browser:
#
#      AI Workbench.lnk
#        -> desktop\src-tauri\target\release\ai-workbench-desktop.exe
#             -> starts PostgreSQL / Redis / Qdrant / API itself
#                  -> opens its own window on http://127.0.0.1:8000/
#
#  No browser. No port 5173. No console window.
#
#  If the executable has not been built, this FAILS LOUDLY with the build
#  command. It deliberately does not fall back to a browser-based launcher: a
#  shortcut that quietly opens Edge instead of the application is worse than no
#  shortcut at all, because the user only finds out after double-clicking it.
#
#  Run it once:
#      powershell -ExecutionPolicy Bypass -File scripts\create-shortcut.ps1
#
#  For a development shortcut that starts the Vite dev server and opens a
#  browser, pass -Browser. That is an explicit opt-in and never the default.
# ============================================================================

[CmdletBinding()]
param(
    [string]$Name = "AI Workbench",
    [string]$Destination,

    # Development mode: start the stack and open a browser at the dev server.
    # Never the default, because it is exactly what users complained about.
    [switch]$Browser
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\lib.ps1"
Repair-ProcessEnvironment

$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "desktop\src-tauri\target\release\ai-workbench-desktop.exe"
$icon = Join-Path $root "assets\ai-workbench.ico"

if (-not $Destination -or $Destination.Trim() -eq "") {
    $Destination = [Environment]::GetFolderPath('Desktop')
    if (-not $Destination -or -not (Test-Path $Destination)) {
        $Destination = Join-Path $env:USERPROFILE 'Desktop'
    }
}
if (-not (Test-Path $Destination)) { throw "Desktop directory not found: $Destination" }

$linkPath = Join-Path $Destination "$Name.lnk"

# --------------------------------------------------------------- desktop mode --
if (-not $Browser) {
    if (-not (Test-Path $exe)) {
        Write-Host ""
        Write-Host "[x] The desktop application has not been built yet." -ForegroundColor Red
        Write-Host ""
        Write-Host "    Expected at:" -ForegroundColor Yellow
        Write-Host "      $exe"
        Write-Host ""
        Write-Host "    Build it with:" -ForegroundColor Yellow
        Write-Host "      cd desktop"
        Write-Host "      npm install"
        Write-Host "      npm run tauri build"
        Write-Host ""
        Write-Host "    Refusing to create a browser-based shortcut as a fallback:" -ForegroundColor Red
        Write-Host "    the whole point of this shortcut is that it does NOT open a browser." -ForegroundColor Red
        Write-Host ""
        throw "ai-workbench-desktop.exe not found: $exe"
    }

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($linkPath)
    $shortcut.TargetPath = $exe
    $shortcut.Arguments = ""
    $shortcut.WorkingDirectory = $root
    if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" }
    $shortcut.Description = "AI Workbench - 私人 AI Agent（桌面应用）"
    $shortcut.WindowStyle = 1
    $shortcut.Save()

    if (-not (Test-Path $linkPath)) { throw "Shortcut was not created: $linkPath" }

    Write-Host ""
    Write-Host "[+] Desktop shortcut created" -ForegroundColor Green
    Write-Host "    path    : $linkPath"
    Write-Host "    target  : $exe"
    Write-Host "    workdir : $root"
    Write-Host "    icon    : $icon,0"
    Write-Host ""
    Write-Host "    Double-click it to open AI Workbench as a native window." -ForegroundColor Cyan
    Write-Host "    The application starts its own backend; there is no browser and no port 5173." -ForegroundColor Cyan
    Write-Host ""
    exit 0
}

# --------------------------------------------------- development (opt-in) mode --
Write-Host ""
Write-Warn2 "Development mode: the shortcut will start the Vite dev server and open a browser."
$vbs = Join-Path $root "scripts\start-hidden.vbs"
if (-not (Test-Path $vbs)) { throw "Hidden launcher not found: $vbs" }
$hostExe = Join-Path $env:SystemRoot 'System32\wscript.exe'
if (-not (Test-Path $hostExe)) { $hostExe = 'wscript.exe' }

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($linkPath)
$shortcut.TargetPath = $hostExe
$shortcut.Arguments = "`"$vbs`""
$shortcut.WorkingDirectory = $root
if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" }
$shortcut.Description = "AI Workbench - 开发模式（浏览器）"
$shortcut.Save()

Write-Host "[+] Development shortcut created: $linkPath" -ForegroundColor Green
Write-Host ""

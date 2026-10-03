# ============================================================================
#  AI Workbench - create the desktop shortcut
#
#  Produces a genuine Windows shortcut (AI Workbench.lnk) that launches the full
#  application with no console window:
#
#      AI Workbench.lnk
#        -> wscript.exe  scripts\start-hidden.vbs
#             -> scripts\start.bat --hidden
#                  -> PostgreSQL / Redis / Qdrant / API / frontend
#                       -> browser opens on http://127.0.0.1:5173
#
#  Failures are not swallowed: start-hidden.vbs shows a message box pointing at
#  .runtime\logs\startup-error.log, and the browser is not opened.
#
#  Run it once:
#      powershell -ExecutionPolicy Bypass -File scripts\create-shortcut.ps1
#
#  scripts\start.bat calls this automatically when the shortcut is missing, so
#  in practice you never need to run it by hand.
#
#  Why a script instead of shipping a pre-built .lnk: a shortcut is a shell
#  object, and the supported way to create one is the WScript.Shell COM API.
#  Generating the binary by hand is possible but fragile; asking the shell to do
#  it is what Explorer itself does.
# ============================================================================

[CmdletBinding()]
param(
    [string]$Name = "AI Workbench",
    [string]$Destination
)

$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\lib.ps1"
Repair-ProcessEnvironment

$root = Split-Path -Parent $PSScriptRoot
$vbs = Join-Path $root "scripts\start-hidden.vbs"
$icon = Join-Path $root "assets\ai-workbench.ico"

if (-not (Test-Path $vbs)) { throw "Hidden launcher not found: $vbs" }

# wscript.exe runs the .vbs with no console window. Resolved from System32 so the
# shortcut does not depend on PATH.
$wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
if (-not (Test-Path $wscript)) { $wscript = 'wscript.exe' }

if (-not $Destination -or $Destination.Trim() -eq "") {
    $Destination = [Environment]::GetFolderPath('Desktop')
    if (-not $Destination -or -not (Test-Path $Destination)) {
        $Destination = Join-Path $env:USERPROFILE 'Desktop'
    }
}
if (-not (Test-Path $Destination)) { throw "Desktop directory not found: $Destination" }

$linkPath = Join-Path $Destination "$Name.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($linkPath)
$shortcut.TargetPath = $wscript
# Quoted: the repository path may contain spaces.
$shortcut.Arguments = "`"$vbs`""
$shortcut.WorkingDirectory = $root
if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" }
$shortcut.Description = "AI Workbench - 启动完整项目（数据库 + API + 前端）"
$shortcut.WindowStyle = 1
$shortcut.Save()

if (-not (Test-Path $linkPath)) { throw "Shortcut was not created: $linkPath" }

Write-Host ""
Write-Host "[+] Shortcut created" -ForegroundColor Green
Write-Host "    path    : $linkPath"
Write-Host "    target  : $wscript"
Write-Host "    args    : `"$vbs`""
Write-Host "    workdir : $root"
Write-Host "    icon    : $icon,0"
Write-Host ""
Write-Host "    Double-click it to start AI Workbench. No console window appears;" -ForegroundColor Cyan
Write-Host "    the browser opens as soon as the frontend really serves HTTP 200." -ForegroundColor Cyan
Write-Host ""

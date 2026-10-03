# ============================================================================
#  AI Workbench — create the desktop shortcut
#
#  Produces a genuine Windows shortcut (AI Workbench.lnk) that launches the full
#  project: infrastructure, API and frontend, via scripts\start.bat.
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
$target = Join-Path $root "scripts\start.bat"
$icon = Join-Path $root "assets\ai-workbench.ico"

if (-not (Test-Path $target)) { throw "Launcher not found: $target" }

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
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $root
if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" }
$shortcut.Description = "AI Workbench - 启动完整项目（数据库 + API + 前端）"
$shortcut.WindowStyle = 1
$shortcut.Save()

if (-not (Test-Path $linkPath)) { throw "Shortcut was not created: $linkPath" }

Write-Host ""
Write-Host "[+] Shortcut created" -ForegroundColor Green
Write-Host "    path    : $linkPath"
Write-Host "    target  : $target"
Write-Host "    workdir : $root"
Write-Host "    icon    : $icon,0"
Write-Host ""
Write-Host "    Double-click it to start AI Workbench." -ForegroundColor Cyan
Write-Host ""

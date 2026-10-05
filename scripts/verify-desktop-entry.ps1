# Desktop entry acceptance test — the nine assertions from the task brief.
#
#   powershell -ExecutionPolicy Bypass -File scripts\verify-desktop-entry.ps1
#
# Runs against the real shortcut and the real executable. Every assertion prints
# PASS or FAIL with the evidence that decided it, so the result can be checked
# rather than believed.

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

$results = @()
function Assert-That {
    param([string]$Name, [bool]$Ok, [string]$Evidence)
    $script:results += [pscustomobject]@{ Name = $Name; Ok = $Ok; Evidence = $Evidence }
    $tag = if ($Ok) { 'PASS' } else { 'FAIL' }
    $colour = if ($Ok) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}" -f $tag, $Name) -ForegroundColor $colour
    Write-Host ("         {0}" -f $Evidence) -ForegroundColor DarkGray
}

function Get-ProcessNames {
    (Get-Process -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name) | Sort-Object -Unique
}

function Test-Port([int]$Port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $ok = $c.BeginConnect('127.0.0.1', $Port, $null, $null).AsyncWaitHandle.WaitOne(600, $false)
        $c.Close()
        return $ok
    } catch { return $false }
}

Write-Host ''
Write-Host '=== A. Stop every related process ===' -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -NoProfile -File 'scripts\stop.bat' *> $null
Get-Process -Name 'ai-workbench-desktop' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 4
$portsClear = -not (Test-Port 5432) -and -not (Test-Port 8000) -and -not (Test-Port 5173)
Assert-That 'A. 全部服务已停止' $portsClear "5432=$(Test-Port 5432) 8000=$(Test-Port 8000) 5173=$(Test-Port 5173)"

$before = Get-ProcessNames
$edgeBefore = @(Get-Process -Name msedge, chrome -ErrorAction SilentlyContinue).Count
$desktopBefore = @(Get-Process -Name 'ai-workbench-desktop' -ErrorAction SilentlyContinue).Count

Write-Host ''
Write-Host '=== B. Launch the shortcut (equivalent to a double-click) ===' -ForegroundColor Cyan
$link = Join-Path ([Environment]::GetFolderPath('Desktop')) 'AI Workbench.lnk'
Start-Process -FilePath $link
Write-Host "  launched $link"

# Give the shell, the backend and the window time to come up.
$deadline = (Get-Date).AddSeconds(180)
$apiUp = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 4
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/v1/health' -UseBasicParsing -TimeoutSec 4
        if ($r.StatusCode -eq 200) { $apiUp = $true; break }
    } catch { }
}
Start-Sleep -Seconds 6

Write-Host ''
Write-Host '=== C–H. Assertions ===' -ForegroundColor Cyan

# C
$desktopNow = @(Get-Process -Name 'ai-workbench-desktop' -ErrorAction SilentlyContinue)
Assert-That 'C. 原生窗口进程存在' ($desktopNow.Count -ge 1) "ai-workbench-desktop.exe 进程数 = $($desktopNow.Count)"

# D — the whole point of this fix
$edgeNow = @(Get-Process -Name msedge, chrome -ErrorAction SilentlyContinue).Count
Assert-That 'D. 没有启动浏览器' ($edgeNow -le $edgeBefore) "浏览器进程 $edgeBefore -> $edgeNow"

# E — no console window from the launcher
$console = @(Get-CimInstance Win32_Process -Filter "Name='cmd.exe' OR Name='conhost.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*ai-workbench*' })
Assert-That 'E. 没有 CMD 控制台窗口' ($console.Count -eq 0) "匹配 ai-workbench 的控制台进程 = $($console.Count)"

# F
Assert-That 'F. 5173 端口未被监听' (-not (Test-Port 5173)) "5173 listening = $(Test-Port 5173)"

# G
$health = ''
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/v1/health' -UseBasicParsing -TimeoutSec 6
    $health = "$($r.StatusCode)"
} catch { $health = "error: $($_.Exception.Message)" }
Assert-That 'G. 后端健康检查 200' ($health -eq '200') "/api/v1/health -> $health"

# H — the window must show the application, not the splash
$ui = ''
try {
    $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/' -UseBasicParsing -TimeoutSec 6
    $ui = if ($r.Content -match '<div id="root"') { 'app bundle served' } else { 'unexpected content' }
} catch { $ui = "error: $($_.Exception.Message)" }
Assert-That 'H. 应用 UI 可加载（非白屏）' ($ui -eq 'app bundle served') "GET / -> $ui"

Write-Host ''
Write-Host '=== I. Close the window and check for leftovers ===' -ForegroundColor Cyan
$proc = Get-Process -Name 'ai-workbench-desktop' -ErrorAction SilentlyContinue
if ($proc) {
    # Closing the window is what the user does; the shell stops the backend on exit.
    $proc | ForEach-Object { $_.CloseMainWindow() | Out-Null }
    Start-Sleep -Seconds 8
    Get-Process -Name 'ai-workbench-desktop' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 6
$leftover = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*ai-workbench*' -and $_.Name -in @('postgres.exe','redis-server.exe','qdrant.exe','python.exe') })
Assert-That 'I. 无僵尸进程残留' ($leftover.Count -eq 0) "残留的后端进程 = $($leftover.Count)"

Write-Host ''
Write-Host ('=' * 72)
$failed = @($results | Where-Object { -not $_.Ok })
Write-Host ("  {0}/{1} 条断言通过" -f ($results.Count - $failed.Count), $results.Count)
if ($failed.Count) {
    Write-Host '  失败项：' -ForegroundColor Red
    foreach ($f in $failed) { Write-Host ("    - {0}" -f $f.Name) -ForegroundColor Red }
}
Write-Host ('=' * 72)
exit $(if ($failed.Count) { 1 } else { 0 })

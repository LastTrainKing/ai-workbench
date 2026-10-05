# Desktop entry acceptance test.
#
#   powershell -ExecutionPolicy Bypass -File scripts\verify-desktop-entry.ps1
#
# Strictly serial: stop everything, then start, then assert, then close, then
# assert again. The previous version ran some steps in parallel and produced a
# false failure on the "everything stopped" assertion.
#
# Closing is done by posting WM_CLOSE to the window — the exact message Explorer
# sends when the user clicks X. Using taskkill to "close" the window is a test
# bug: it never exercises the graceful path being tested. Both paths are checked,
# because both have to leave nothing behind.

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)

$results = @()
function Assert-That {
    param([string]$Id, [string]$Name, [bool]$Ok, [string]$Evidence)
    $script:results += [pscustomobject]@{ Id = $Id; Name = $Name; Ok = $Ok; Evidence = $Evidence }
    $tag = if ($Ok) { 'PASS' } else { 'FAIL' }
    $colour = if ($Ok) { 'Green' } else { 'Red' }
    Write-Host ("  [{0}] {1}. {2}" -f $tag, $Id, $Name) -ForegroundColor $colour
    Write-Host ("         {0}" -f $Evidence) -ForegroundColor DarkGray
}

Add-Type -Namespace Win32 -Name User32 -MemberDefinition @'
[DllImport("user32.dll", SetLastError=true)]
public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
[DllImport("user32.dll", SetLastError=true)]
public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
[DllImport("user32.dll", SetLastError=true)]
public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
'@

$script:closeSent = 0

function Test-Port([int]$Port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $ok = $c.BeginConnect('127.0.0.1', $Port, $null, $null).AsyncWaitHandle.WaitOne(700, $false)
        $c.Close()
        return $ok
    } catch { return $false }
}

function Get-ApiProcesses {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -like '*ai-workbench*' -and
            $_.Name -in @('postgres.exe', 'redis-server.exe', 'qdrant.exe', 'python.exe')
        })
}

function Get-BrowserCount {
    @(Get-Process -Name msedge, chrome, firefox -ErrorAction SilentlyContinue).Count
}

function Send-WindowClose([int]$ProcessId) {
    $script:closeSent = 0
    $callback = [Win32.User32+EnumWindowsProc] {
        param($hWnd, $lParam)
        $owner = 0
        [void][Win32.User32]::GetWindowThreadProcessId($hWnd, [ref]$owner)
        if ($owner -eq $ProcessId) {
            if ([Win32.User32]::PostMessage($hWnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)) {
                $script:closeSent++
            }
        }
        return $true
    }
    [void][Win32.User32]::EnumWindows($callback, [IntPtr]::Zero)
    return $script:closeSent
}

function Wait-Api {
    param([int]$Seconds = 180)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 4
        try {
            if ((Invoke-WebRequest 'http://127.0.0.1:8000/api/v1/health' -UseBasicParsing -TimeoutSec 4).StatusCode -eq 200) { return $true }
        } catch { }
    }
    return $false
}

$link = Join-Path ([Environment]::GetFolderPath('Desktop')) 'AI Workbench.lnk'
$lifecycle = '.runtime\logs\desktop-lifecycle.log'

Write-Host ''
Write-Host '=== 0. 卫生：清理遗留的 5173 监听者 ===' -ForegroundColor Cyan
$stale = @(Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)
foreach ($stalePid in $stale) {
    $p = Get-Process -Id $stalePid -ErrorAction SilentlyContinue
    Write-Host ("  killing stale 5173 listener: pid {0} ({1})" -f $stalePid, $p.Name)
    Stop-Process -Id $stalePid -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
Write-Host ("  5173 listeners now: {0}" -f @(Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue).Count)

Write-Host ''
Write-Host '=== A. 严格串行：先停全部服务 ===' -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -NoProfile -File 'scripts\stop.bat' *> $null
Get-Process -Name 'ai-workbench-desktop' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
# Poll instead of sleeping a fixed amount: PostgreSQL needs a moment to release.
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) {
    if (-not (Test-Port 5432) -and -not (Test-Port 8000) -and -not (Test-Port 5173)) { break }
    Start-Sleep -Seconds 2
}
$allDown = -not (Test-Port 5432) -and -not (Test-Port 8000) -and -not (Test-Port 5173)
Assert-That 'A' '全部服务已停止' $allDown "5432=$(Test-Port 5432) 8000=$(Test-Port 8000) 5173=$(Test-Port 5173)"

Remove-Item $lifecycle -Force -ErrorAction SilentlyContinue
$edgeBefore = Get-BrowserCount

Write-Host ''
Write-Host '=== B. 双击快捷方式 ===' -ForegroundColor Cyan
Start-Process -FilePath $link
Write-Host "  launched $link"
[void](Wait-Api)
Start-Sleep -Seconds 5

Write-Host ''
Write-Host '=== C–H. 断言 ===' -ForegroundColor Cyan

$desktop = @(Get-Process -Name 'ai-workbench-desktop' -ErrorAction SilentlyContinue)
Assert-That 'C' '原生窗口进程存在' ($desktop.Count -ge 1) "ai-workbench-desktop.exe = $($desktop.Count)"

$edgeNow = Get-BrowserCount
Assert-That 'D' '没有启动浏览器' ($edgeNow -le $edgeBefore) "浏览器进程 $edgeBefore -> $edgeNow"

$console = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @('cmd.exe', 'conhost.exe') -and $_.CommandLine -like '*ai-workbench*' })
Assert-That 'E' '没有 CMD 控制台窗口' ($console.Count -eq 0) "匹配的控制台进程 = $($console.Count)"

Assert-That 'F' '5173 端口未被监听' (-not (Test-Port 5173)) "5173 listening = $(Test-Port 5173)"

$health = 'error'
try { $health = "$((Invoke-WebRequest 'http://127.0.0.1:8000/api/v1/health' -UseBasicParsing -TimeoutSec 6).StatusCode)" } catch { $health = "error: $($_.Exception.Message)" }
Assert-That 'G' '后端健康检查 200' ($health -eq '200') "/api/v1/health -> $health"

$ui = 'error'
try {
    $content = (Invoke-WebRequest 'http://127.0.0.1:8000/' -UseBasicParsing -TimeoutSec 6).Content
    $ui = if ($content -match '<div id="root"') { 'app bundle served' } else { 'unexpected content' }
} catch { $ui = "error: $($_.Exception.Message)" }
Assert-That 'H' '应用 UI 可加载（非白屏）' ($ui -eq 'app bundle served') "GET / -> $ui"

Write-Host ''
Write-Host '=== I-1. 优雅关窗（WM_CLOSE，等价于点 X）===' -ForegroundColor Cyan
$proc = Get-Process -Name 'ai-workbench-desktop' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($proc) {
    $sent = Send-WindowClose -ProcessId $proc.Id
    Write-Host "  WM_CLOSE posted to $sent window(s) of pid $($proc.Id)"
} else {
    Write-Host '  app was not running' -ForegroundColor Red
}
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    if (-not (Get-Process -Name 'ai-workbench-desktop' -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Seconds 3
}
$left = Get-ApiProcesses
$leftNames = ($left | Select-Object -ExpandProperty Name | Sort-Object -Unique) -join ','
Assert-That 'I-1' '优雅关窗后无残留' ($left.Count -eq 0) "残留进程 = $($left.Count) [$leftNames]"

Write-Host ''
Write-Host '--- 关窗链路日志（证据）---' -ForegroundColor DarkGray
if (Test-Path $lifecycle) {
    Get-Content $lifecycle | Select-Object -Last 20 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
} else {
    Write-Host '    (没有生命周期日志)' -ForegroundColor Red
}

Write-Host ''
Write-Host '=== I-2. 强制结束（taskkill /F）—— 作业对象兜底 ===' -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -NoProfile -File 'scripts\stop.bat' *> $null
Start-Sleep -Seconds 4
Start-Process -FilePath $link
[void](Wait-Api)
Start-Sleep -Seconds 5
Write-Host ("  app running: {0}" -f @(Get-Process -Name 'ai-workbench-desktop' -ErrorAction SilentlyContinue).Count)
& taskkill /F /IM ai-workbench-desktop.exe *> $null
Start-Sleep -Seconds 20
$left2 = Get-ApiProcesses
$left2Names = ($left2 | Select-Object -ExpandProperty Name | Sort-Object -Unique) -join ','
Assert-That 'I-2' '强杀后无残留（作业对象生效）' ($left2.Count -eq 0) "残留进程 = $($left2.Count) [$left2Names]"

Write-Host ''
Write-Host ('=' * 72)
$failed = @($results | Where-Object { -not $_.Ok })
Write-Host ("  {0}/{1} 条断言通过" -f ($results.Count - $failed.Count), $results.Count)
if ($failed.Count) {
    Write-Host '  失败项：' -ForegroundColor Red
    foreach ($f in $failed) { Write-Host ("    - {0}. {1}" -f $f.Id, $f.Name) -ForegroundColor Red }
}
Write-Host ('=' * 72)
exit $(if ($failed.Count) { 1 } else { 0 })

# Wait until a URL answers with HTTP 2xx/3xx, then exit 0. On timeout, exit 1.
#
#   powershell -ExecutionPolicy Bypass -File scripts\wait-ready.ps1 -Url http://127.0.0.1:5173
#
# Used by start.bat as the final gate before the browser is opened: the frontend
# must actually serve a page, not merely have bound its port.
#
param(
    [Parameter(Mandatory)][string]$Url,
    [int]$TimeoutSeconds = 60,
    [string]$Label = ''
)

. "$PSScriptRoot\lib.ps1"

if (-not $Label) { $Label = $Url }

$started = Get-Date
$ok = Wait-Until -Condition { Test-HttpOk -Url $Url -TimeoutSeconds 5 } `
    -TimeoutSeconds $TimeoutSeconds -IntervalMs 700
$elapsed = [int]((Get-Date) - $started).TotalSeconds

if ($ok) {
    Write-Ok "$Label is ready (HTTP 200 after ${elapsed}s)"
    exit 0
}

Write-StartupError "$Label failed to become ready within ${TimeoutSeconds} seconds."
exit 1

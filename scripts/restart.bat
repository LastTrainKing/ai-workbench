@echo off
REM ===========================================================================
REM  AI Workbench - restart (Windows)
REM
REM  stop -> wait for the processes to really exit -> start -> wait for every
REM  service to be ready -> open the browser.
REM
REM  By default only the application is restarted and the infrastructure stays
REM  up: that is faster and avoids re-opening the database. Use --full to
REM  restart PostgreSQL / Redis / Qdrant as well.
REM
REM  Data is never deleted: the database, the Qdrant collection and the uploaded
REM  files survive a restart unchanged.
REM
REM  Usage:
REM    scripts\restart.bat            restart the application only
REM    scripts\restart.bat --full     restart infrastructure too
REM    scripts\restart.bat --hidden   no console window (used by the shortcut)
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0\.."
set "ROOT=%CD%"
set "PS=powershell -ExecutionPolicy Bypass -NoProfile -File"
set "HIDDEN=0"
set "FULL=0"
set "EXITCODE=0"

:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--full" set "FULL=1"
if /i "%~1"=="--hidden" set "HIDDEN=1"
shift
goto parse
:parsed

echo.
echo ==========================================================
echo   AI Workbench - restarting
echo ==========================================================
echo.

if "%FULL%"=="1" (
    echo [*] Full restart: application + infrastructure
    call "scripts\stop.bat"
    if errorlevel 1 (
        echo [ERROR] Stop failed - not starting.
        endlocal & exit /b 1
    )
    REM start.bat opens the browser once the frontend really serves HTTP 200.
    call "scripts\start.bat" --no-browser
    set "EXITCODE=%ERRORLEVEL%"
) else (
    echo [*] Restarting the application only ^(infrastructure stays up^)
    %PS% "scripts\dev.ps1" restart
    if errorlevel 1 (
        echo [ERROR] The application failed to restart. See .runtime\logs\startup-error.log
        set "EXITCODE=1"
        goto :theend
    )
    %PS% "scripts\wait-ready.ps1" -Url "http://127.0.0.1:5173/" -TimeoutSeconds 60 -Label "Frontend"
    if errorlevel 1 (
        echo [ERROR] The frontend did not become reachable.
        set "EXITCODE=1"
        goto :theend
    )
    echo.
    echo [+] AI Workbench restarted.
    echo.
    echo   Web UI   http://127.0.0.1:5173
    echo   API      http://127.0.0.1:8000/docs
    echo.
    start "" "http://127.0.0.1:5173"
)

:theend
if "%HIDDEN%"=="0" if "%EXITCODE%"=="1" pause
endlocal & exit /b %EXITCODE%

@echo off
REM ===========================================================================
REM  AI Workbench - desktop shutdown
REM
REM  Called by the desktop shell when its window closes, so "close the app"
REM  really means closed. Without it the window disappears while PostgreSQL,
REM  Redis, Qdrant and the API keep running in the background.
REM
REM  ASCII ONLY. This file previously contained UTF-8 em dashes in the comments.
REM  cmd.exe reads a .bat as ANSI, so those bytes were mangled and the words
REM  after them were executed as commands, making the script exit 255 before it
REM  stopped anything. Keep every byte in this file below 128.
REM
REM  Scope guarantee: only processes this project started are stopped.
REM    PostgreSQL   pg_ctl -D <our data dir>
REM    Redis/Qdrant pid file, verified to live under vendor\runtime
REM    API          pid file + the owner of port 8000
REM  There is deliberately no "taskkill /IM python.exe" - that would kill the
REM  user's other projects.
REM
REM  Data is never deleted.
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0\.."
set "ROOT=%CD%"

REM Where this copy keeps its data - outside the program directory for an installed
REM copy. Same three-step order as scripts\lib.ps1 and backend\app\core\config.py.
set "RUNTIME=%ROOT%\.runtime"
if exist "%ROOT%\data-dir.txt" set /p RUNTIME=<"%ROOT%\data-dir.txt"
if defined AI_WORKBENCH_DATA_DIR set "RUNTIME=%AI_WORKBENCH_DATA_DIR%"
if not defined RUNTIME set "RUNTIME=%ROOT%\.runtime"
set "PS=powershell -ExecutionPolicy Bypass -NoProfile -File"
set "EXITCODE=0"

call :log "----- desktop shutdown requested -----"

%PS% "scripts\dev.ps1" stop -Only api
if errorlevel 1 (
    call :log "api stop reported an error"
    set "EXITCODE=1"
)

%PS% "scripts\infra-stop.ps1"
if errorlevel 1 (
    call :log "infrastructure stop reported an error"
    set "EXITCODE=1"
)

if "%EXITCODE%"=="0" (
    call :log "desktop shutdown complete"
) else (
    call :log "desktop shutdown finished with errors"
)

endlocal & exit /b %EXITCODE%

:log
>>"%RUNTIME%\logs\startup.log" echo %DATE% %TIME% %~1
goto :eof

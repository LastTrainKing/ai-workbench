@echo off
REM ===========================================================================
REM  AI Workbench - desktop shutdown
REM
REM  Called by the desktop shell when its window closes, so "close the app"
REM  actually means closed. Without it the window disappears while PostgreSQL,
REM  Redis, Qdrant and the API keep running in the background — which is not
REM  what a user expects from a desktop application.
REM
REM  Scope guarantee: only processes this project started are stopped.
REM    PostgreSQL   pg_ctl -D <our data dir>
REM    Redis/Qdrant pid file, verified to live under vendor\runtime
REM    API          pid file + the owner of port 8000
REM  There is deliberately no `taskkill /IM python.exe` — that would kill the
REM  user's other projects.
REM
REM  Data is never deleted.
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0\.."
set "ROOT=%CD%"
set "PS=powershell -ExecutionPolicy Bypass -NoProfile -File"
set "EXITCODE=0"

call :log "----- desktop shutdown requested -----"

%PS% "scripts\dev.ps1" stop -Only api
if errorlevel 1 call :log "api stop reported an error"

%PS% "scripts\infra-stop.ps1"
if errorlevel 1 call :log "infrastructure stop reported an error"

call :log "desktop shutdown complete"
endlocal & exit /b %EXITCODE%

:log
>>"%ROOT%\.runtime\logs\startup.log" echo %DATE% %TIME% %~1
goto :eof

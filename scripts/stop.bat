@echo off
REM ===========================================================================
REM  AI Workbench - stop (Windows)
REM
REM  Stops the application processes and the project-scoped infrastructure
REM  (PostgreSQL / Redis / Qdrant).
REM
REM  SCOPE GUARANTEE - only what this project started is stopped:
REM    PostgreSQL   `pg_ctl -D <our data dir>`, so a PostgreSQL belonging to
REM                 another project is untouched
REM    Redis        pid file, verified to live under vendor\runtime
REM    Qdrant       pid file, verified to live under vendor\runtime
REM    API/Frontend pid file, verified by command line, plus the port owner
REM
REM  There is deliberately no `taskkill /IM python.exe` or `/IM node.exe`:
REM  that would kill the user's other projects.
REM
REM  What it does NOT do:
REM    * it does not delete the database
REM    * it does not clear the Qdrant collection
REM    * it does not remove uploaded files
REM    * it does not touch anything outside this project folder
REM
REM  Usage:
REM    scripts\stop.bat              stop everything (data preserved)
REM    scripts\stop.bat --app-only   stop only the API and the frontend
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0\.."
set "ROOT=%CD%"
set "PS=powershell -ExecutionPolicy Bypass -NoProfile -File"

echo.
echo ==========================================================
echo   AI Workbench - stopping
echo ==========================================================
echo.

echo [*] Stopping the application (API + frontend)...
%PS% "scripts\dev.ps1" stop
if errorlevel 1 (
    echo [ERROR] Failed to stop the application processes.
    endlocal & exit /b 1
)

if /i "%~1"=="--app-only" (
    echo.
    echo [+] Application stopped. Infrastructure left running ^(--app-only^).
    echo.
    endlocal & exit /b 0
)

echo.
echo [*] Stopping PostgreSQL, Redis and Qdrant...
%PS% "scripts\infra-stop.ps1"
if errorlevel 1 (
    echo [ERROR] Failed to stop the infrastructure.
    endlocal & exit /b 1
)

echo.
echo [+] AI Workbench stopped. All data preserved.
echo.
endlocal & exit /b 0

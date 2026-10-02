@echo off
REM ===========================================================================
REM  AI Workbench - stop everything (Windows)
REM
REM  Stops the application processes and the project-scoped infrastructure.
REM  Nothing outside this repository is touched.
REM ===========================================================================
setlocal
cd /d "%~dp0"

echo.
echo [*] Stopping the application...
powershell -ExecutionPolicy Bypass -NoProfile -File "scripts\dev.ps1" stop

echo.
echo [*] Stopping PostgreSQL, Redis and Qdrant...
powershell -ExecutionPolicy Bypass -NoProfile -File "scripts\infra-stop.ps1"

echo.
echo [+] AI Workbench stopped.
echo.
endlocal

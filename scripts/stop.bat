@echo off
REM ===========================================================================
REM  AI Workbench - stop (Windows)
REM
REM  Stops the application processes and the project-scoped infrastructure
REM  (PostgreSQL / Redis / Qdrant).
REM
REM  What it does NOT do:
REM    * it does not delete the database
REM    * it does not clear the Qdrant collection
REM    * it does not remove uploaded files
REM    * it does not touch anything outside this project folder
REM
REM  All state is preserved, so start.bat brings the system back exactly as it
REM  was. Use --app-only to stop just the API and the frontend and leave the
REM  infrastructure running (faster to restart).
REM ===========================================================================
setlocal
cd /d "%~dp0\.."

echo.
echo ==========================================================
echo   AI Workbench - stopping
echo ==========================================================
echo.

echo [*] Stopping the application (API + frontend)...
powershell -ExecutionPolicy Bypass -NoProfile -File "scripts\dev.ps1" stop

if /i "%~1"=="--app-only" (
    echo.
    echo [+] Application stopped. Infrastructure left running ^(--app-only^).
    echo.
    endlocal
    exit /b 0
)

echo.
echo [*] Stopping PostgreSQL, Redis and Qdrant...
powershell -ExecutionPolicy Bypass -NoProfile -File "scripts\infra-stop.ps1"

echo.
echo [+] AI Workbench stopped. All data preserved.
echo.
endlocal
exit /b 0

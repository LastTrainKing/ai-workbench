@echo off
REM ===========================================================================
REM  AI Workbench - one-click start (Windows)
REM
REM  Starts everything with native project-scoped runtimes:
REM    1. checks the environment
REM    2. downloads PostgreSQL / Redis / Qdrant into vendor\ if missing
REM    3. starts the three infrastructure processes
REM    4. starts the API (with the in-process worker) and the frontend
REM    5. prints the URLs
REM
REM  Nothing is installed as a Windows service and no system setting is changed.
REM  To use Docker instead, run:  docker compose up -d
REM ===========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ==========================================================
echo   AI Workbench - starting
echo ==========================================================
echo.

REM ---------------------------------------------------------------- prerequisites
where powershell >nul 2>&1
if errorlevel 1 (
    echo [x] PowerShell was not found on PATH.
    echo     Windows 10/11 ships it by default; check your PATH.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [x] Python virtual environment not found: .venv\Scripts\python.exe
    echo.
    echo     Create it once with:
    echo         python -m venv .venv
    echo         .venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
    echo.
    pause
    exit /b 1
)

if not exist "frontend\node_modules" (
    echo [!] frontend\node_modules is missing - the UI will not start.
    echo     Install it once with:  cd frontend ^&^& npm install
    echo.
)

REM ------------------------------------------------------------------- runtimes
if not exist "vendor\runtime\qdrant\qdrant.exe" goto :fetch
if not exist "vendor\runtime\redis\redis-server.exe" goto :fetch
if not exist "vendor\runtime\pgsql\pgsql\bin\postgres.exe" goto :fetch
goto :infra

:fetch
echo [*] Native runtimes are missing - downloading them now.
echo     This is a one-time ~350 MB download.
echo.
powershell -ExecutionPolicy Bypass -NoProfile -File "scripts\fetch-runtime.ps1"
if errorlevel 1 (
    echo [x] Failed to download the runtimes. See the message above.
    pause
    exit /b 1
)

REM ------------------------------------------------------------------ infrastructure
:infra
echo.
echo [*] Starting PostgreSQL, Redis and Qdrant...
powershell -ExecutionPolicy Bypass -NoProfile -File "scripts\infra-start.ps1"
if errorlevel 1 (
    echo [x] Infrastructure failed to start. See the message above.
    pause
    exit /b 1
)

REM ------------------------------------------------------------------- application
echo.
echo [*] Starting the API and the frontend...
powershell -ExecutionPolicy Bypass -NoProfile -File "scripts\dev.ps1" start

echo.
echo ==========================================================
echo   AI Workbench is up
echo ==========================================================
echo.
echo   Web UI        http://127.0.0.1:5173
echo   API           http://127.0.0.1:8000
echo   API docs      http://127.0.0.1:8000/docs
echo   Qdrant UI     http://127.0.0.1:6333/dashboard
echo.
echo   Demo account  demo / demo1234
echo.
echo   Stop everything with:  stop.bat
echo ==========================================================
echo.

if /i "%~1"=="--no-browser" goto :end
start "" "http://127.0.0.1:5173"

:end
endlocal

@echo off
REM ===========================================================================
REM  AI Workbench - start (Windows)
REM
REM  Idempotent. Running it twice is safe:
REM    * already-running infrastructure is detected and left alone
REM    * already-running application processes are detected and left alone
REM    * the database, the Qdrant collection and the uploaded files are NEVER
REM      deleted, truncated or re-initialised by this script
REM
REM  Steps:
REM    1. verify the Python virtual environment
REM    2. download the native runtimes if they are missing (one-time, ~350 MB)
REM    3. start PostgreSQL / Redis / Qdrant  (scripts\infra-start.ps1)
REM    4. start the API (with the in-process worker) and the frontend
REM       (scripts\dev.ps1 start)
REM    5. print the URLs
REM
REM  For Docker instead:  docker compose up -d
REM ===========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0\.."
set "ROOT=%CD%"

echo.
echo ==========================================================
echo   AI Workbench - starting
echo   %ROOT%
echo ==========================================================
echo.

REM ------------------------------------------------------------ prerequisites
where powershell >nul 2>&1
if errorlevel 1 (
    echo [x] PowerShell was not found on PATH.
    echo     Windows 10/11 ships it by default; check your PATH.
    goto :fail
)

if not exist "%ROOT%\.venv\Scripts\python.exe" (
    echo [x] Python virtual environment not found.
    echo.
    echo     Create it once with:
    echo         python -m venv .venv
    echo         .venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
    echo.
    goto :fail
)

if not exist "%ROOT%\frontend\node_modules" (
    echo [!] frontend\node_modules is missing - the UI will not start.
    echo     Install it once with:  cd frontend ^&^& npm install
    echo.
)

REM ---------------------------------------------------- desktop shortcut
REM Created on first run if absent. A .lnk is a shell object, so the shell is
REM asked to build it (scripts\create-shortcut.ps1). Non-fatal if it fails.
set "SHORTCUT=%USERPROFILE%\Desktop\AI Workbench.lnk"
if not exist "%SHORTCUT%" (
    echo [*] Creating the desktop shortcut...
    powershell -ExecutionPolicy Bypass -NoProfile -File "scripts\create-shortcut.ps1" >nul 2>&1
    if exist "%SHORTCUT%" (
        echo [+] Desktop shortcut created: %SHORTCUT%
    ) else (
        echo [!] Could not create the desktop shortcut. Run scripts\create-shortcut.ps1 manually.
    )
)

REM ------------------------------------------------------------------ runtimes
REM Only downloaded when actually absent; never re-downloaded, never deleted.
set "NEED_FETCH="
if not exist "%ROOT%\vendor\runtime\qdrant\qdrant.exe"          set "NEED_FETCH=1"
if not exist "%ROOT%\vendor\runtime\redis\redis-server.exe"     set "NEED_FETCH=1"
if not exist "%ROOT%\vendor\runtime\pgsql\pgsql\bin\postgres.exe" set "NEED_FETCH=1"

if defined NEED_FETCH (
    echo [*] Native runtimes are missing - downloading them now.
    echo     One-time download of roughly 350 MB.
    echo.
    powershell -ExecutionPolicy Bypass -NoProfile -File "scripts\fetch-runtime.ps1"
    if errorlevel 1 (
        echo [x] Failed to download the runtimes. See the message above.
        goto :fail
    )
) else (
    echo [+] Native runtimes present.
)

REM ------------------------------------------------------------ infrastructure
echo.
echo [*] Ensuring PostgreSQL, Redis and Qdrant are running...
echo     (services already running are detected and left untouched)
powershell -ExecutionPolicy Bypass -NoProfile -File "scripts\infra-start.ps1"
if errorlevel 1 (
    echo [x] Infrastructure failed to start. See the message above.
    goto :fail
)

REM -------------------------------------------------------------- application
echo.
echo [*] Ensuring the API and the frontend are running...
echo     (processes already running are detected and left untouched)
powershell -ExecutionPolicy Bypass -NoProfile -File "scripts\dev.ps1" start

REM ------------------------------------------------------------------- report
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
echo   Restart with: scripts\restart.bat
echo   Stop with:    scripts\stop.bat
echo ==========================================================
echo.

if /i "%~1"=="--no-browser" goto :ok
start "" "http://127.0.0.1:5173"

:ok
endlocal
exit /b 0

:fail
echo.
pause
endlocal
exit /b 1

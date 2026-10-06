@echo off
REM ===========================================================================
REM  AI Workbench - start (Windows)
REM
REM  Idempotent. Running it twice is safe:
REM    * already-running infrastructure is detected via a real readiness probe
REM      and left alone
REM    * already-running application processes are detected and left alone
REM    * the database, the Qdrant collection and the uploaded files are NEVER
REM      deleted, truncated or re-initialised by this script
REM
REM  Readiness is proven, never assumed:
REM    PostgreSQL  pg_isready + psql "SELECT 1"
REM    Redis       redis-cli ping -> PONG
REM    Qdrant      HTTP GET /healthz -> 200
REM    API         HTTP GET /api/v1/health -> 200
REM    Frontend    HTTP GET / -> 200
REM  A service that does not become ready stops the run with a non-zero exit
REM  code: the browser is not opened and logs\startup-error.log is written.
REM
REM  Usage:
REM    scripts\start.bat              interactive (visible console, no browser)
REM    scripts\start.bat --hidden     no console window
REM    scripts\start.bat --browser    also open a browser at the dev server
REM
REM  For the desktop application, launch ai-workbench-desktop.exe instead: it
REM  starts its own backend and never opens a browser.
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0\.."
set "ROOT=%CD%"
set "LOGSDIR=%ROOT%\.runtime\logs"
set "STARTUPLOG=%LOGSDIR%\startup.log"
set "ERRORLOG=%LOGSDIR%\startup-error.log"
set "HIDDEN=0"
REM A browser is opt-in. The desktop application is the normal way to use
REM this product; opening Edge from a launcher is what users complained about.
set "OPENBROWSER=0"
set "EXITCODE=0"
set "PS=powershell -ExecutionPolicy Bypass -NoProfile -File"

if not exist "%LOGSDIR%" mkdir "%LOGSDIR%" >nul 2>&1

:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--hidden" set "HIDDEN=1"
if /i "%~1"=="--browser" set "OPENBROWSER=1"
if /i "%~1"=="--no-browser" set "OPENBROWSER=0"
shift
goto parse
:parsed

call :log "----- start requested (hidden=%HIDDEN%) -----"

if "%HIDDEN%"=="0" (
    echo.
    echo ==========================================================
    echo   AI Workbench - starting
    echo   %ROOT%
    echo ==========================================================
    echo.
)

REM ------------------------------------------------ single-instance lock ----
REM A second launch (an impatient double-click) waits here instead of running a
REM parallel start sequence, then simply opens the browser on the frontend the
REM first run started.
%PS% "scripts\startup-lock.ps1" -Action Acquire -TimeoutSeconds 240
if errorlevel 1 (
    call :log "another startup is already in progress - waiting for the frontend"
    %PS% "scripts\wait-ready.ps1" -Url "http://127.0.0.1:5173/" -TimeoutSeconds 120 -Label "Frontend"
    if errorlevel 1 (
        call :fail "Another startup is in progress and the frontend did not become reachable."
        goto :theend
    )
    call :log "frontend ready (started by the other launch)"
    if "%OPENBROWSER%"=="1" start "" "http://127.0.0.1:5173"
    goto :theend
)
set "LOCKHELD=1"

REM ------------------------------------------------------------ prerequisites
where powershell >nul 2>&1
if errorlevel 1 (
    call :fail "PowerShell was not found on PATH."
    goto :theend
)

set "PYTHON="
if exist "%ROOT%\vendor\runtime\python\python.exe" set "PYTHON=%ROOT%\vendor\runtime\python\python.exe"
if not defined PYTHON if exist "%ROOT%\.venv\Scripts\python.exe" set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
if not defined PYTHON (
    call :fail "No Python interpreter found. An installed copy ships one at vendor\runtime\python; a development checkout needs .venv (python -m venv .venv)."
    goto :theend
)

if not exist "%ROOT%\frontend\node_modules" (
    call :fail "frontend\node_modules is missing. Install it with: cd frontend  then  npm install"
    goto :theend
)

REM ------------------------------------------------------- desktop shortcut --
set "SHORTCUT=%USERPROFILE%\Desktop\AI Workbench.lnk"
if not exist "%SHORTCUT%" (
    call :log "creating the desktop shortcut"
    %PS% "scripts\create-shortcut.ps1" >nul 2>&1
    if exist "%SHORTCUT%" ( call :log "desktop shortcut created" ) else ( call :log "could not create the desktop shortcut (non-fatal)" )
)

REM ---------------------------------------------------------------- runtimes --
set "NEED_FETCH="
if not exist "%ROOT%\vendor\runtime\qdrant\qdrant.exe"           set "NEED_FETCH=1"
if not exist "%ROOT%\vendor\runtime\redis\redis-server.exe"      set "NEED_FETCH=1"
if not exist "%ROOT%\vendor\runtime\pgsql\pgsql\bin\postgres.exe" set "NEED_FETCH=1"

if defined NEED_FETCH (
    if "%HIDDEN%"=="0" echo [*] Downloading native runtimes ^(one time, ~350 MB^)...
    call :log "fetching native runtimes"
    %PS% "scripts\fetch-runtime.ps1"
    if errorlevel 1 (
        call :fail "Failed to download the native runtimes."
        goto :theend
    )
)

REM ---------------------------------------------------------- infrastructure --
if "%HIDDEN%"=="0" (
    echo.
    echo [*] Infrastructure
)
call :log "infrastructure: start requested"
%PS% "scripts\infra-start.ps1"
if errorlevel 1 (
    call :fail "Infrastructure failed to start. See .runtime\logs\startup.log and .runtime\logs\startup-error.log"
    goto :theend
)

REM ------------------------------------------------------------ application --
if "%HIDDEN%"=="0" (
    echo.
    echo [*] Application
)
call :log "application: start requested"
%PS% "scripts\dev.ps1" start
if errorlevel 1 (
    call :fail "The application failed to start. See .runtime\logs\startup.log and .runtime\logs\startup-error.log"
    goto :theend
)

REM --------------------------------------------------- final readiness gate --
call :log "waiting for the frontend to serve HTTP 200"
%PS% "scripts\wait-ready.ps1" -Url "http://127.0.0.1:5173/" -TimeoutSeconds 60 -Label "Frontend"
if errorlevel 1 (
    call :fail "The frontend did not become reachable."
    goto :theend
)

REM ------------------------------------------------------------------ report --
call :log "startup complete"
if "%HIDDEN%"=="0" (
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
    echo   Stop with:    scripts\stop.bat
    echo   Restart with: scripts\restart.bat
    echo ==========================================================
    echo.
)

if "%OPENBROWSER%"=="1" (
    call :log "opening the browser"
    start "" "http://127.0.0.1:5173"
)

goto :theend

REM ----------------------------------------------------------------- helpers --
:log
>>"%STARTUPLOG%" echo %DATE% %TIME% %~1
if "%HIDDEN%"=="0" echo [*] %~1
goto :eof

:fail
>>"%STARTUPLOG%" echo %DATE% %TIME% FAILED - %~1
>>"%ERRORLOG%" echo %DATE% %TIME% %~1
echo.
echo [ERROR] %~1
echo         See %ERRORLOG%
set "EXITCODE=1"
goto :eof

:theend
if defined LOCKHELD (
    %PS% "scripts\startup-lock.ps1" -Action Release >nul 2>&1
)
if "%HIDDEN%"=="0" if "%EXITCODE%"=="1" pause
endlocal & exit /b %EXITCODE%

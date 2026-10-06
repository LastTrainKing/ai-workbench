@echo off
REM ===========================================================================
REM  AI Workbench - desktop startup (Windows)
REM
REM  Started by the Tauri shell (AI Workbench.exe). Brings up exactly what the
REM  desktop application needs and nothing else:
REM
REM    PostgreSQL -> Redis -> Qdrant -> FastAPI
REM
REM  No frontend dev server and no browser: the desktop window loads the UI from
REM  the API origin, so there is no second port and no Edge/Chrome involvement.
REM
REM  Readiness is proven, never assumed:
REM    PostgreSQL  pg_isready + psql "SELECT 1"
REM    Redis       redis-cli ping -> PONG
REM    Qdrant      HTTP GET /healthz -> 200
REM    API         HTTP GET /api/v1/health -> 200
REM
REM  Exits non-zero on any failure, after writing .runtime\logs\startup-error.log.
REM  The shell polls the health endpoint itself, so this script's job is to make
REM  the stack ready and to leave an honest record of what happened.
REM
REM  Running it twice is safe: every step is idempotent and no data is ever
REM  deleted, truncated or re-initialised.
REM ===========================================================================
setlocal EnableExtensions
cd /d "%~dp0\.."
set "ROOT=%CD%"
REM Where this copy keeps its database, vectors, uploads and logs.
REM
REM An installed copy points this OUTSIDE the program directory via data-dir.txt, so
REM that uninstalling the program cannot touch the user's data. The same three-step
REM order is implemented in scripts\lib.ps1 and backend\app\core\config.py; it is
REM repeated here because this script runs before either of them and must not create a
REM stray .runtime inside the install directory.
set "RUNTIME=%ROOT%\.runtime"
if exist "%ROOT%\data-dir.txt" set /p RUNTIME=<"%ROOT%\data-dir.txt"
REM Guard the empty-file case: a zero-byte marker would leave RUNTIME undefined
REM and "%RUNTIME%\logs" would resolve to the drive root.
if defined AI_WORKBENCH_DATA_DIR set "RUNTIME=%AI_WORKBENCH_DATA_DIR%"
if not defined RUNTIME set "RUNTIME=%ROOT%\.runtime"
set "LOGSDIR=%RUNTIME%\logs"
set "STARTUPLOG=%LOGSDIR%\startup.log"
set "ERRORLOG=%LOGSDIR%\startup-error.log"
set "EXITCODE=0"
set "PS=powershell -ExecutionPolicy Bypass -NoProfile -File"

if not exist "%LOGSDIR%" mkdir "%LOGSDIR%" >nul 2>&1

call :log "----- desktop start requested -----"

REM Python: a bundled interpreter for an installed copy, a venv for a checkout.
REM The bundled one is checked first, so an installed directory never falls back
REM to a .venv that happens to be lying around.
set "PYTHON="
if exist "%ROOT%\vendor\runtime\python\python.exe" set "PYTHON=%ROOT%\vendor\runtime\python\python.exe"
if not defined PYTHON if exist "%ROOT%\.venv\Scripts\python.exe" set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
if not defined PYTHON (
    call :fail "No Python interpreter found. An installed copy ships one at vendor\runtime\python; a development checkout needs .venv."
    goto :theend
)

REM ---------------------------------------------------------------- runtimes --
set "NEED_FETCH="
if not exist "%ROOT%\vendor\runtime\qdrant\qdrant.exe"            set "NEED_FETCH=1"
if not exist "%ROOT%\vendor\runtime\redis\redis-server.exe"       set "NEED_FETCH=1"
if not exist "%ROOT%\vendor\runtime\pgsql\pgsql\bin\postgres.exe" set "NEED_FETCH=1"

if defined NEED_FETCH (
    call :log "fetching native runtimes (first run)"
    %PS% "scripts\fetch-runtime.ps1"
    if errorlevel 1 (
        call :fail "Failed to download the native runtimes."
        goto :theend
    )
)

REM --------------------------------------------------------- infrastructure --
call :log "infrastructure: start requested"
%PS% "scripts\infra-start.ps1"
if errorlevel 1 (
    call :fail "Infrastructure failed to start. See .runtime\logs\startup.log"
    goto :theend
)

REM ------------------------------------------------------------------- API --
REM The UI is served by the API itself, so the frontend must have been built.
if not exist "%ROOT%\frontend\dist\index.html" (
    call :log "frontend/dist is missing - building it"
    pushd "%ROOT%\frontend"
    call npm run build
    popd
    if not exist "%ROOT%\frontend\dist\index.html" (
        call :fail "frontend/dist/index.html is missing. Run 'cd frontend && npm run build'."
        goto :theend
    )
)

call :log "api: start requested"
%PS% "scripts\dev.ps1" start -Only api
if errorlevel 1 (
    call :fail "The API failed to start. See .runtime\logs\startup.log"
    goto :theend
)

REM --------------------------------------------------- final readiness gate --
call :log "confirming the API serves HTTP 200"
%PS% "scripts\wait-ready.ps1" -Url "http://127.0.0.1:8000/api/v1/health" -TimeoutSeconds 60 -Label "API"
if errorlevel 1 (
    call :fail "The API did not become reachable."
    goto :theend
)

call :log "desktop startup complete"
goto :theend

REM ----------------------------------------------------------------- helpers --
:log
>>"%STARTUPLOG%" echo %DATE% %TIME% %~1
goto :eof

:fail
>>"%STARTUPLOG%" echo %DATE% %TIME% FAILED - %~1
>>"%ERRORLOG%" echo %DATE% %TIME% %~1
set "EXITCODE=1"
goto :eof

:theend
endlocal & exit /b %EXITCODE%

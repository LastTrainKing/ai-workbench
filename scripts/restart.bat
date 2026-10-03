@echo off
REM ===========================================================================
REM  AI Workbench - restart (Windows)
REM
REM  Stops the application processes (leaving the infrastructure up, which is
REM  faster and avoids re-opening the database), then starts everything again.
REM
REM  Use --full to also restart PostgreSQL / Redis / Qdrant.
REM
REM  Data is never deleted: the database, the Qdrant collection and the
REM  uploaded files survive a restart unchanged.
REM ===========================================================================
setlocal
cd /d "%~dp0\.."

echo.
echo ==========================================================
echo   AI Workbench - restarting
echo ==========================================================
echo.

if /i "%~1"=="--full" (
    echo [*] Full restart: application + infrastructure
    call "scripts\stop.bat"
    echo.
    timeout /t 2 /nobreak >nul
    call "scripts\start.bat" --no-browser
) else (
    echo [*] Restarting the application only ^(infrastructure stays up^)
    powershell -ExecutionPolicy Bypass -NoProfile -File "scripts\dev.ps1" restart
    echo.
    echo [+] AI Workbench restarted.
    echo.
    echo   Web UI   http://127.0.0.1:5173
    echo   API      http://127.0.0.1:8000/docs
    echo.
)

endlocal
exit /b 0

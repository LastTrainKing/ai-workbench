@echo off
REM ===========================================================================
REM  AI Workbench - root launcher
REM
REM  Thin wrapper so `start.bat` works from the repository root.
REM  The real implementation lives in scripts\start.bat - there is exactly one
REM  copy of the logic, so the two can never drift apart.
REM ===========================================================================
call "%~dp0scripts\start.bat" %*
exit /b %errorlevel%

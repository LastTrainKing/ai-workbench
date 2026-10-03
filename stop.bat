@echo off
REM ===========================================================================
REM  AI Workbench - root stopper
REM
REM  Thin wrapper so `stop.bat` works from the repository root.
REM  The real implementation lives in scripts\stop.bat.
REM ===========================================================================
call "%~dp0scripts\stop.bat" %*
exit /b %errorlevel%

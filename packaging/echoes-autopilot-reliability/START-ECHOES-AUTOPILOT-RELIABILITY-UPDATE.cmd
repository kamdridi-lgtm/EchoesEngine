@echo off
setlocal
cd /d "%~dp0"
title Echoes Autopilot Reliability Update

echo ================================================================
echo   ECHOES AUTOPILOT - RELIABILITY UPDATE
echo ================================================================
echo.
set "INSTALLER=%~dp0scripts\install-echoes-autopilot-reliability-update.ps1"
if not exist "%INSTALLER%" (
  echo Extract the complete ZIP before running this file.
  echo Missing: %INSTALLER%
  pause
  exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%INSTALLER%" -SourceRoot "%~dp0"
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
  echo Echoes Autopilot reliability update failed with code %EXITCODE%.
  pause
  exit /b %EXITCODE%
)

echo Echoes Autopilot reliability update is installed.
echo The configured scan interval is now respected.
echo The startup fallback now repeats instead of running once.
echo FFmpeg path propagation and evidence preservation are enabled.
pause
exit /b 0

@echo off
setlocal
title ECHOES STEM AUTOPILOT
echo ================================================
echo   ECHOES STEM AUTOPILOT - INSTALL AND SEPARATE
echo ================================================
echo.
set "ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\install-echoes-stem-runtime.ps1" -SourceRoot "%ROOT%"
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo Echoes Stem Autopilot stopped with error %EXITCODE%.
  echo Read D:\A.I\EchoesControl\STEM-STATUS.txt or the visible error above.
  pause
  exit /b %EXITCODE%
)
echo.
echo ECHOES STEM AUTOPILOT IS INSTALLED AND RUNNING.
echo Results: D:\A.I\EchoesResults
echo Control: D:\A.I\EchoesControl\Echoes-Stem-Control-Bundle-Latest.zip
pause
exit /b 0

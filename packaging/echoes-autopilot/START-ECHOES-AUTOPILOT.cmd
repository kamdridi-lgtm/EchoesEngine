@echo off
setlocal
cd /d "%~dp0"
title Echoes Autopilot - Kam Dridi
echo.
echo ================================================
echo   ECHOES AUTOPILOT - INSTALL AND RUN EVERYTHING
echo ================================================
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-echoes-autopilot.ps1" -SourceRoot "%~dp0"
set EXITCODE=%ERRORLEVEL%
echo.
if not "%EXITCODE%"=="0" (
  echo Echoes Autopilot stopped with error %EXITCODE%.
  echo Read D:\A.I\EchoesControl\STATUS.txt or the visible error above.
  pause
  exit /b %EXITCODE%
)
echo Echoes Autopilot is installed and running.
echo Put songs in D:\A.I\EchoesInbox. The controller checks automatically.
echo Results are in D:\A.I\EchoesResults.
echo Control bundle is D:\A.I\EchoesControl\Echoes-Control-Bundle-Latest.zip.
pause
exit /b 0

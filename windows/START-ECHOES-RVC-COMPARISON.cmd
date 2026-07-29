@echo off
setlocal
title Echoes RVC 700 1000 1500 Comparison
set "ROOT=%~dp0"
set "SCRIPT=%ROOT%scripts\run-recovered-rvc-comparison.ps1"
if not exist "%SCRIPT%" (
  echo.
  echo ECHOES RVC COMPARISON FAILED
  echo Missing launcher: %SCRIPT%
  echo.
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" (
  echo ECHOES RVC COMPARISON FAILED - CODE %CODE%
  echo No recovered model or source audio was deleted.
  pause
  exit /b %CODE%
)
echo ECHOES RVC COMPARISON COMPLETE
echo Results: D:\A.I\EchoesRvcRecovered\comparison_output
pause
exit /b 0

@echo off
setlocal
title Echoes RVC Manual Listening Choice
set "ROOT=%~dp0"
set "SCRIPT=%ROOT%scripts\review-recovered-rvc-comparison.ps1"
if not exist "%SCRIPT%" (
  echo.
  echo ECHOES RVC LISTENING REVIEW FAILED
  echo Missing launcher: %SCRIPT%
  echo.
  pause
  exit /b 2
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" (
  echo ECHOES RVC LISTENING REVIEW FAILED - CODE %CODE%
  echo No model or audio file was modified.
  pause
  exit /b %CODE%
)
echo ECHOES RVC LISTENING DECISION SAVED
echo Decision: D:\A.I\EchoesRvcRecovered\comparison_output\control\RVC-COMPARISON-LISTENING-REVIEW.json
pause
exit /b 0

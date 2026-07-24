@echo off
setlocal EnableExtensions
title ECHOES CINEMA - AUTONOME

set "REPO=%~dp0"
if "%REPO:~-1%"=="\" set "REPO=%REPO:~0,-1%"

if not exist "%REPO%\scripts\one-click-echoes-cinema.ps1" (
  echo ERROR: Autonomous orchestrator is missing.
  echo Missing: %REPO%\scripts\one-click-echoes-cinema.ps1
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%REPO%\scripts\one-click-echoes-cinema.ps1" -RepoRoot "%REPO%" -WorkspaceRoot "D:\A.I\EchoesCinema"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%

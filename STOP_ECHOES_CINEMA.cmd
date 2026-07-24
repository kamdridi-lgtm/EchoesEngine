@echo off
setlocal EnableExtensions
set "REPO_ROOT=%~dp0"
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"
cd /d "%REPO_ROOT%"

set "STOP_SCRIPT=%REPO_ROOT%\scripts\stop-echoes-cinema-stack.ps1"
if not exist "%STOP_SCRIPT%" (
    echo ERROR: Echoes Cinema stop script not found: "%STOP_SCRIPT%"
    pause
    exit /b 2
)

echo Stopping Echoes Cinema safely...
powershell -NoProfile -ExecutionPolicy Bypass -File "%STOP_SCRIPT%" -WorkspaceRoot "D:\A.I\EchoesCinema"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%

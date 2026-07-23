@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo Stopping Echoes Cinema safely...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-echoes-cinema-stack.ps1" -WorkspaceRoot "D:\A.I\EchoesCinema"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" pause
exit /b %EXIT_CODE%

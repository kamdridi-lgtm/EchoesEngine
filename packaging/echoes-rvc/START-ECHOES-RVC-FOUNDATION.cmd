@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title ECHOES RVC RUNTIME FOUNDATION

echo ============================================================
echo   ECHOES RVC RUNTIME FOUNDATION
echo   Pinned official source - Python 3.12 - CUDA 11.8 or CPU
echo ============================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-echoes-rvc-runtime-foundation.ps1" -SourceRoot "%~dp0"
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
  echo Echoes RVC foundation failed with exit code %EXITCODE%.
  echo Nothing was converted and no audio was uploaded.
) else (
  echo Echoes RVC foundation completed.
  echo See D:\A.I\EchoesRvcRuntime\RVC-RUNTIME-STATUS.txt
)
echo.
pause
exit /b %EXITCODE%

@echo off
setlocal
cd /d "%~dp0"
echo.
echo ============================================================
echo  ECHOES CINEMA - FIRST REAL AI CLIP
echo ============================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run-first-real-ai-clip.ps1"
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" (
  echo ECHOES CINEMA FAILED - read the exact blocker above.
) else (
  echo ECHOES CINEMA REAL AI PROOF PASS.
)
echo.
pause
exit /b %EXIT_CODE%

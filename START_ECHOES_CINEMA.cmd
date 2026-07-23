@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "D:\" (
  echo Echoes Cinema requires drive D:. Drive C: will not be used for model or job storage.
  pause
  exit /b 1
)

echo.
echo ============================================================
echo  ECHOES CINEMA - ONE CLICK CONTROL CENTER
echo  The browser opens only after localhost is truly reachable.
echo  Stack supervisor and provider recovery are automatic.
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-echoes-cinema-stack.ps1" -WorkspaceRoot "D:\A.I\EchoesCinema" -RepoRoot "%~dp0"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Echoes Cinema startup failed before opening the browser.
  echo Exact logs: D:\A.I\EchoesCinema\logs
  pause
)
exit /b %EXIT_CODE%

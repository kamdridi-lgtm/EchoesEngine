@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "D:\" (
  echo Echoes Cinema requires drive D:. Drive C: will not be used for model or job storage.
  pause
  exit /b 1
)

rem IMPORTANT: %%~dp0 always ends with a backslash. Passing that value as the
rem final quoted command-line argument can escape the closing quote and produce
rem a literal trailing quote such as D:\A.I\EchoesEngine" in PowerShell.
set "REPO_ROOT=%~dp0"
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"

if not exist "%REPO_ROOT%\scripts\start-echoes-cinema-stack.ps1" (
  echo Echoes Cinema launcher is incomplete.
  echo Missing: %REPO_ROOT%\scripts\start-echoes-cinema-stack.ps1
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

powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_ROOT%\scripts\start-echoes-cinema-stack.ps1" -WorkspaceRoot "D:\A.I\EchoesCinema" -RepoRoot "%REPO_ROOT%"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Echoes Cinema startup failed before opening the browser.
  echo Exact logs: D:\A.I\EchoesCinema\logs
  pause
)
exit /b %EXIT_CODE%

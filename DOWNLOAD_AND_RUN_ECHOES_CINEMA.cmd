@echo off
setlocal EnableExtensions EnableDelayedExpansion
title ECHOES CINEMA - ONE CLICK DOWNLOAD AND RUN

set "REPO=D:\A.I\EchoesEngine"
set "WORKSPACE=D:\A.I\EchoesCinema"
set "BOOTSTRAP_DIR=%WORKSPACE%\temp\bootstrap"
set "ORCHESTRATOR=%BOOTSTRAP_DIR%\echoes-cinema-one-click-%RANDOM%-%RANDOM%.ps1"
set "MAIN_REF=refs/remotes/origin/main"

echo.
echo ============================================================
echo  ECHOES CINEMA - AUTONOMOUS ONE CLICK
echo  Update, verify, repair, start and monitor automatically
echo ============================================================
echo.

if not exist "D:\" (
  echo ERROR: Drive D: is required.
  pause
  exit /b 1
)

where git >nul 2>nul
if errorlevel 1 (
  echo ERROR: Git is not available in PATH.
  pause
  exit /b 1
)

if not exist "%WORKSPACE%" mkdir "%WORKSPACE%" >nul 2>nul
if not exist "%BOOTSTRAP_DIR%" mkdir "%BOOTSTRAP_DIR%" >nul 2>nul

if not exist "%REPO%\.git" (
  echo Installing EchoesEngine on D:...
  if not exist "D:\A.I" mkdir "D:\A.I" >nul 2>nul
  git clone --branch main --single-branch https://github.com/kamdridi-lgtm/EchoesEngine.git "%REPO%"
  if errorlevel 1 (
    echo ERROR: EchoesEngine could not be cloned.
    pause
    exit /b 1
  )
)

echo Fetching the tested canonical version...
git -C "%REPO%" fetch --no-tags origin +refs/heads/main:%MAIN_REF%
if errorlevel 1 (
  echo ERROR: GitHub could not be reached or authenticated.
  pause
  exit /b 1
)

git -C "%REPO%" rev-parse --verify "%MAIN_REF%^{commit}" >nul 2>nul
if errorlevel 1 (
  echo ERROR: The canonical main reference was not created after fetch.
  pause
  exit /b 1
)

git -C "%REPO%" show "%MAIN_REF%:scripts/one-click-echoes-cinema.ps1" > "%ORCHESTRATOR%"
if errorlevel 1 (
  echo ERROR: The autonomous orchestrator could not be retrieved.
  del /q "%ORCHESTRATOR%" >nul 2>nul
  pause
  exit /b 1
)

for %%A in ("%ORCHESTRATOR%") do set "ORCHESTRATOR_SIZE=%%~zA"
if not defined ORCHESTRATOR_SIZE (
  echo ERROR: The autonomous orchestrator is empty.
  del /q "%ORCHESTRATOR%" >nul 2>nul
  pause
  exit /b 1
)
if !ORCHESTRATOR_SIZE! LSS 1000 (
  echo ERROR: The autonomous orchestrator is incomplete.
  del /q "%ORCHESTRATOR%" >nul 2>nul
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "[void][scriptblock]::Create((Get-Content -LiteralPath '%ORCHESTRATOR%' -Raw))"
if errorlevel 1 (
  echo ERROR: PowerShell validation failed.
  del /q "%ORCHESTRATOR%" >nul 2>nul
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ORCHESTRATOR%" -RepoRoot "%REPO%" -WorkspaceRoot "%WORKSPACE%"
set "EXIT_CODE=%ERRORLEVEL%"

del /q "%ORCHESTRATOR%" >nul 2>nul

if not "%EXIT_CODE%"=="0" (
  echo.
  echo ECHOES CINEMA ENCOUNTERED AN ERROR.
  echo Exact diagnostics are saved in:
  echo D:\A.I\EchoesCinema\logs
  start "" explorer.exe "D:\A.I\EchoesCinema\logs"
  pause
)

exit /b %EXIT_CODE%

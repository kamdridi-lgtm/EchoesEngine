@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Echoes Cinema - Refresh and Start

set "REPO=%~dp0"
set "BRANCH=main"
cd /d "%REPO%"

echo.
echo ============================================================
echo  ECHOES CINEMA - SAFE REFRESH + SELF-HEALING START
echo  No destructive reset. Local work is preserved.
echo ============================================================
echo.

if not exist "D:\" (
  echo ERROR: drive D: is required. Model and job storage will not use C:.
  pause
  exit /b 1
)

where git >nul 2>nul
if errorlevel 1 (
  echo ERROR: Git is not available in PATH.
  pause
  exit /b 1
)

if not exist ".git" (
  echo ERROR: run this launcher from the EchoesEngine repository root.
  pause
  exit /b 1
)

git fetch origin "%BRANCH%"
if errorlevel 1 (
  echo WARNING: refresh failed. Starting the installed version without deleting anything.
  goto START_STACK
)

git diff --quiet
set "WORKTREE_DIRTY=!ERRORLEVEL!"
git diff --cached --quiet
set "INDEX_DIRTY=!ERRORLEVEL!"

if "!WORKTREE_DIRTY!"=="0" if "!INDEX_DIRTY!"=="0" (
  git checkout "%BRANCH%"
  if errorlevel 1 (
    echo WARNING: branch checkout failed. Starting the installed version.
    goto START_STACK
  )
  git pull --ff-only origin "%BRANCH%"
  if errorlevel 1 echo WARNING: fast-forward refresh failed. No destructive reset was used.
) else (
  echo NOTICE: local modifications detected and preserved. Refresh was skipped.
)

:START_STACK
if not exist "START_ECHOES_CINEMA.cmd" (
  echo ERROR: START_ECHOES_CINEMA.cmd is missing.
  pause
  exit /b 1
)

call "%REPO%START_ECHOES_CINEMA.cmd"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Echoes Cinema failed before localhost became reachable.
  echo Exact logs: D:\A.I\EchoesCinema\logs
  echo Runtime state: D:\A.I\EchoesCinema\runtime\stack-state.json
  pause
)
exit /b %EXIT_CODE%

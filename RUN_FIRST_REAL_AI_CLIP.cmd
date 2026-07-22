@echo off
setlocal EnableExtensions

if not exist "D:\" (
  echo Drive D: is required. Drive C: will not be used for Echoes Cinema.
  pause
  exit /b 1
)

cd /d "%~dp0"
set "ECHOES_CINEMA_WORKSPACE=D:\A.I\EchoesCinema"
set "HF_HOME=%ECHOES_CINEMA_WORKSPACE%\cache\huggingface"
set "HF_HUB_CACHE=%ECHOES_CINEMA_WORKSPACE%\cache\huggingface\hub"
set "HUGGINGFACE_HUB_CACHE=%HF_HUB_CACHE%"
set "TRANSFORMERS_CACHE=%ECHOES_CINEMA_WORKSPACE%\cache\huggingface\transformers"
set "TORCH_HOME=%ECHOES_CINEMA_WORKSPACE%\cache\torch"
set "PIP_CACHE_DIR=%ECHOES_CINEMA_WORKSPACE%\cache\pip"
set "XDG_CACHE_HOME=%ECHOES_CINEMA_WORKSPACE%\cache\xdg"
set "CUDA_CACHE_PATH=%ECHOES_CINEMA_WORKSPACE%\cache\cuda"
set "NUMBA_CACHE_DIR=%ECHOES_CINEMA_WORKSPACE%\cache\numba"
set "PYTHONPYCACHEPREFIX=%ECHOES_CINEMA_WORKSPACE%\cache\python-bytecode"
set "TEMP=%ECHOES_CINEMA_WORKSPACE%\temp"
set "TMP=%TEMP%"
set "TMPDIR=%TEMP%"

for %%D in (
  "%ECHOES_CINEMA_WORKSPACE%"
  "%HF_HOME%"
  "%HF_HUB_CACHE%"
  "%TRANSFORMERS_CACHE%"
  "%TORCH_HOME%"
  "%PIP_CACHE_DIR%"
  "%XDG_CACHE_HOME%"
  "%CUDA_CACHE_PATH%"
  "%NUMBA_CACHE_DIR%"
  "%PYTHONPYCACHEPREFIX%"
  "%TEMP%"
) do if not exist "%%~D" mkdir "%%~D"

echo.
echo ============================================================
echo  ECHOES CINEMA - FIRST REAL AI CLIP
echo  STORAGE ROOT: D:\A.I\EchoesCinema
echo  DRIVE C: STORAGE: DISABLED
echo  SAFE CLEANUP: BEFORE AND AFTER EVERY RUN
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\cleanup-cinema-storage.ps1" -WorkspaceRoot "%ECHOES_CINEMA_WORKSPACE%"
if errorlevel 1 (
  echo Safe pre-run cleanup failed. Cinema will not continue.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run-first-real-ai-clip.ps1" -WorkspaceRoot "%ECHOES_CINEMA_WORKSPACE%"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
echo Cleaning disposable files from D: ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\cleanup-cinema-storage.ps1" -WorkspaceRoot "%ECHOES_CINEMA_WORKSPACE%" -AfterRun
set "CLEANUP_EXIT=%ERRORLEVEL%"
if not "%CLEANUP_EXIT%"=="0" echo WARNING: post-run cleanup reported an error.

echo.
if not "%EXIT_CODE%"=="0" (
  echo ECHOES CINEMA FAILED - read the exact blocker above.
) else (
  echo ECHOES CINEMA REAL AI PROOF PASS.
  echo Video folder: D:\A.I\EchoesCinema\proofs\first-real-ai-clip
)
echo.
pause
exit /b %EXIT_CODE%

@echo off
setlocal EnableExtensions

set "WORKSPACE=D:\A.I\EchoesCinema"
set "PYTHON=%WORKSPACE%\.venv-cinema\Scripts\python.exe"
set "REPORT=%WORKSPACE%\p0-preflight-only-report.json"

if not exist "D:\" (
  echo Drive D: is required. No Cinema storage will be placed on C:.
  pause
  exit /b 1
)

cd /d "%~dp0"
set "HF_HOME=%WORKSPACE%\cache\huggingface"
set "HF_HUB_CACHE=%HF_HOME%\hub"
set "HUGGINGFACE_HUB_CACHE=%HF_HUB_CACHE%"
set "TRANSFORMERS_CACHE=%HF_HOME%\transformers"
set "TORCH_HOME=%WORKSPACE%\cache\torch"
set "PIP_CACHE_DIR=%WORKSPACE%\cache\pip"
set "XDG_CACHE_HOME=%WORKSPACE%\cache\xdg"
set "CUDA_CACHE_PATH=%WORKSPACE%\cache\cuda"
set "NUMBA_CACHE_DIR=%WORKSPACE%\cache\numba"
set "PYTHONPYCACHEPREFIX=%WORKSPACE%\cache\python-bytecode"
set "TEMP=%WORKSPACE%\temp"
set "TMP=%TEMP%"
set "TMPDIR=%TEMP%"

for %%D in (
  "%WORKSPACE%"
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
echo  ECHOES CINEMA - FAST P0 PREFLIGHT ONLY
echo  No model load. No package reinstall. D drive only.
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\cleanup-cinema-storage.ps1" -WorkspaceRoot "%WORKSPACE%"
if errorlevel 1 (
  echo Safe cleanup failed. Preflight stopped.
  pause
  exit /b 1
)

if not exist "%PYTHON%" (
  echo Cinema virtual environment Python is missing:
  echo %PYTHON%
  echo Run the full Echoes Cinema launcher to repair the environment.
  pause
  exit /b 1
)

if not exist "%~dp0tools\cinema_p0_preflight.py" (
  echo P0 preflight tool is missing from the repository.
  pause
  exit /b 1
)

"%PYTHON%" "%~dp0tools\cinema_p0_preflight.py" ^
  --workspace "%WORKSPACE%" ^
  --output "%REPORT%" ^
  --minimum-free-gib 35 ^
  --expected-drive "D:" ^
  --provider-host "127.0.0.1" ^
  --provider-port 8081 ^
  --require-cuda
set "EXIT_CODE=%ERRORLEVEL%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\cleanup-cinema-storage.ps1" -WorkspaceRoot "%WORKSPACE%" -AfterRun

echo.
if "%EXIT_CODE%"=="0" (
  echo ECHOES CINEMA P0 PREFLIGHT PASS.
) else (
  echo ECHOES CINEMA P0 PREFLIGHT FAILED.
)
echo Report: %REPORT%
echo.
pause
exit /b %EXIT_CODE%

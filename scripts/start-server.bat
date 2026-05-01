@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "SOURCE_DIR=%%~fI"
set "BUILD_DIR=C:\Users\Administrator\echoes-build\echoesengine-canonical-vs2026-release"
set "PRIMARY_EXE=%BUILD_DIR%\Release\ECHoesEnginePro.exe"
set "ALT_EXE=%BUILD_DIR%\Release\EchoesEngine.exe"
set "STANDALONE_EXE=%BUILD_DIR%\EchoesPlugin_artefacts\Release\Standalone\EchoesEngine.exe"

cd /d "%SOURCE_DIR%"

rem Conservative 24/7 runtime defaults: keep growing, avoid filling disk too fast.
set "ECHOES_MIN_CONCURRENT_JOBS=1"
set "ECHOES_MAX_CONCURRENT_JOBS=1"
set "ECHOES_MAX_RETRIES=1"
set "ECHOES_AUTOJOB_ENABLED=true"
set "ECHOES_AUTOJOB_INTERVAL_SECONDS=300"
set "ECHOES_AUTOJOB_DURATION_SECONDS=15"
set "ECHOES_AUTOJOB_STYLE=dark_cinematic"
set "ECHOES_AGENT_TICK_RATE=8"

echo ========================================
echo   EchoesEngine Start Server
echo ========================================
echo [INFO] Source: %SOURCE_DIR%
echo [INFO] Build : %BUILD_DIR%

call "%SCRIPT_DIR%build.bat"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Build failed. Server not started.
    exit /b 1
)

if exist "%PRIMARY_EXE%" (
    echo [INFO] Starting %PRIMARY_EXE%
    "%PRIMARY_EXE%"
    exit /b %ERRORLEVEL%
)

if exist "%ALT_EXE%" (
    echo [INFO] Starting %ALT_EXE%
    "%ALT_EXE%"
    exit /b %ERRORLEVEL%
)

if exist "%STANDALONE_EXE%" (
    echo [INFO] Starting %STANDALONE_EXE%
    "%STANDALONE_EXE%"
    exit /b %ERRORLEVEL%
)

echo [ERROR] No EchoesEngine executable found after build.
echo Checked:
echo   %PRIMARY_EXE%
echo   %ALT_EXE%
echo   %STANDALONE_EXE%
exit /b 2


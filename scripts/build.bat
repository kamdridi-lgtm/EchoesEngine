@echo off
setlocal

echo ========================================
echo   EchoesEngine Build Script (MSVC x64)
echo ========================================

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "SOURCE_DIR=%%~fI"
set "BUILD_DIR=C:\Users\Administrator\echoes-build\echoesengine-canonical-vs2026-release"
set "GENERATOR=Visual Studio 18 2026"

if not exist "%BUILD_DIR%" mkdir "%BUILD_DIR%"

echo [INFO] Source: %SOURCE_DIR%
echo [INFO] Build : %BUILD_DIR%
echo [INFO] Generator: %GENERATOR%

cmake -S "%SOURCE_DIR%" -B "%BUILD_DIR%" -G "%GENERATOR%" -A x64
if %ERRORLEVEL% neq 0 (
    echo [ERROR] CMake configuration failed.
    exit /b 1
)

cmake --build "%BUILD_DIR%" --config Release
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Release build failed.
    exit /b 1
)

echo.
echo [OK] Build complete.
echo Outputs:
echo   %BUILD_DIR%\Release\EchoesEngine.lib
echo   %BUILD_DIR%\Release\ECHoesEnginePro.exe
echo   %BUILD_DIR%\Release\EchoesEngine.exe
if exist "%BUILD_DIR%\EchoesPlugin_artefacts\Release\VST3\EchoesEngine.vst3\Contents\x86_64-win\EchoesEngine.vst3" (
echo   %BUILD_DIR%\EchoesPlugin_artefacts\Release\VST3\EchoesEngine.vst3\Contents\x86_64-win\EchoesEngine.vst3
)
if exist "%BUILD_DIR%\EchoesPlugin_artefacts\Release\Standalone\EchoesEngine.exe" (
echo   %BUILD_DIR%\EchoesPlugin_artefacts\Release\Standalone\EchoesEngine.exe
)


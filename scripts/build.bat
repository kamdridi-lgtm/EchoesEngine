@echo off
setlocal

echo ========================================
echo   EchoesEngine — Build Script (MSVC x64)
echo ========================================

:: Create build dir
if not exist build mkdir build
cd build

:: Configure
cmake -G "Visual Studio 17 2022" -A x64 ..
if %ERRORLEVEL% neq 0 (
    echo [ERROR] CMake configuration failed.
    exit /b 1
)

:: Build Release
cmake --build . --config Release --parallel
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Build failed.
    exit /b 1
)

echo.
echo [OK] Build complete.
echo Output: build\bin\Release\EchoesEngine.exe

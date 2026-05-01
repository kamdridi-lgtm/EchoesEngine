@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "SOURCE_DIR=%%~fI"
set "LOG_DIR=%SOURCE_DIR%\runtime\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ========================================
echo   EchoesEngine 24/7 Daemon
echo ========================================
echo [INFO] Source: %SOURCE_DIR%
echo [INFO] Logs  : %LOG_DIR%

:loop
echo [%date% %time%] Starting EchoesEngine server... >> "%LOG_DIR%\daemon.log"
call "%SCRIPT_DIR%start-server.bat" >> "%LOG_DIR%\server.out.log" 2>> "%LOG_DIR%\server.err.log"
set "EXIT_CODE=%ERRORLEVEL%"
echo [%date% %time%] Server exited with code %EXIT_CODE%. Restarting in 5 seconds... >> "%LOG_DIR%\daemon.log"
timeout /t 5 /nobreak > nul
goto loop

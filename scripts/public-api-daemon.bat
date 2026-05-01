@echo off
setlocal
cd /d "%~dp0.."
:loop
powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',4000); $c.Close(); exit 0 } catch { exit 1 }"
if %ERRORLEVEL% equ 0 (
  timeout /t 30 /nobreak >nul
  goto loop
)
echo [INFO] Starting EchoesEngine Public API on 127.0.0.1:4000
npx.cmd tsx agents\_phase4_runner.ts
echo [WARN] Public API exited. Restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop

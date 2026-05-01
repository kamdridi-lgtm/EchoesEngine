@echo off
setlocal
cd /d "%~dp0.."
:loop
echo [INFO] Starting EchoesEngine worker control plane on 127.0.0.1:8081 and :8082
python agents\worker-control-plane.py
echo [WARN] Worker control plane exited. Restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop

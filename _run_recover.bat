@echo off
REM Trigger state recovery on VPS - use full path to avoid wsl.exe intercept
set SSH=C:\Windows\System32\OpenSSH\ssh.exe
set VPS=root@187.77.130.144
set PY=/home/trading/binance-engine/venv/bin/python3

REM Step 1: Write recovery script to VPS
echo Writing script to VPS...
%SSH% -o ConnectTimeout=20 %VPS% "cat > /tmp/recover_vps.py" < "c:\Users\Administrator\Desktop\eth-webhook-server-main\_recover_vps.py"
if errorlevel 1 (
    echo Failed to write script
    exit /b 1
)

REM Step 2: Execute recovery
echo Executing recovery script...
%SSH% -o ConnectTimeout=20 %VPS% "cd /home/trading/binance-engine && %PY% /tmp/recover_vps.py"
echo Done. Exit code: %errorlevel%

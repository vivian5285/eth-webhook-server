@echo off
setlocal
set PLINK="C:\Program Files\PuTTY\plink.exe"
set HOST=root@187.77.130.144

echo === Step 1: Accept host key (no batch) ===
echo y | %PLINK% %HOST% "exit" 2>&1
echo returned %ERRORLEVEL%
echo.

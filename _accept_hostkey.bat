@echo off
REM 接受 VPS SSH 主机密钥
echo y | "C:\Program Files\PuTTY\plink.exe" -hostkey "ssh-ed25519 255 XdtBkzkR/A3hF4t2EBYGVGlVr1Ry92eyz9/o+tiJeIw" root@187.77.130.144 "echo connected" 2>nul
if %errorlevel%==0 (
    echo === host key accepted and cached ===
) else (
    echo === host key may already be cached ===
)
pause

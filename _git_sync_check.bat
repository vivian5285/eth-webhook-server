@echo off
chcp 65001 >nul
echo ================================================
echo VPS Git状态检查
echo ================================================

echo.
echo [VPS trading用户下的binance-engine]
echo.
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && git log -1 --oneline'"
echo.
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && git status --short'"
echo.

echo.
echo [VPS trading用户下的eth-webhook-server]
echo.
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "su - trading -c 'cd ~/eth-webhook-server && git log -1 --oneline 2>/dev/null || echo 无git目录'"
echo.

echo.
echo [本地最新commit]
type "%~dp0.git_commit_msg.txt"
echo.

echo.
echo ================================================
echo 按任意键退出...
pause >nul

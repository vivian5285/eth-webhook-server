@echo off
chcp 65001 >nul
echo ================================================
echo VPS Git状态全面检查
echo ================================================
echo.

echo [1/6] VPS trading/binance-engine 最新commit...
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && git log -1 --format=%%H && git log -1 --oneline'"
echo.

echo [2/6] VPS trading/binance-engine 未提交更改...
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && git status --short'"
echo.

echo [3/6] VPS trading/binance-engine 远程仓库...
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && git remote -v'"
echo.

echo [4/6] VPS trading/binance-engine 与远程差异...
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && git fetch origin && git log HEAD..origin/main --oneline 2>/dev/null || git log HEAD..origin/master --oneline 2>/dev/null || echo 无差异或无法获取'"
echo.

echo [5/6] 旧残留目录检查...
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "ls -la /home/trading/eth-webhook-server/ 2>/dev/null && echo 存在旧残留目录 || echo 无旧残留目录"
echo.

echo [6/6] 本地commit消息...
type "%~dp0.git_commit_msg.txt"
echo.

echo.
echo ================================================
echo 按任意键退出...
pause >nul

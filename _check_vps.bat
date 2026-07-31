@echo off
chcp 65001 >nul
echo ================================================
echo VPS系统状态检查
echo ================================================

echo.
echo [1] 检查当前目录结构...
echo.

echo === /root目录 ===
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "ls -la /root/ 2>/dev/null | head -30"
echo.

echo === trading用户目录 ===
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "ls -la /home/trading/ 2>/dev/null || echo trading用户不存在"
echo.

echo === binance-engine项目 ===
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "ls -la /home/trading/binance-engine/ 2>/dev/null || echo 项目不存在"
echo.

echo.
echo [2] 检查运行进程...
echo.
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "ps aux | grep -E 'gunicorn|webhook' | grep -v grep || echo 无相关进程"
echo.

echo.
echo [3] 检查端口监听...
echo.
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "netstat -tlnp 2>/dev/null | grep -E '5003|5000' || echo 端口未监听"
echo.

echo.
echo [4] 检查supervisor服务...
echo.
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "supervisorctl status 2>/dev/null || echo supervisor未运行"
echo.

echo.
echo [5] 检查Git状态...
echo.
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "cd /home/trading/binance-engine && git log -1 --oneline && git status --short 2>/dev/null || echo git目录不可用"
echo.

echo.
echo ================================================
echo 检查完成！按任意键退出...
echo ================================================
pause >nul

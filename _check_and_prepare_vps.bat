@echo off
chcp 65001 >nul
echo ================================================
echo VPS系统全面清理和部署脚本
echo ================================================
echo.
echo [步骤1] 切换到trading用户，检查项目目录...
echo.

echo === 切换到trading用户 ===
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "su - trading -c 'echo 用户: && whoami && echo 主目录: && pwd'" 2>nul
echo.

echo === trading用户下的binance-engine项目 ===
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "su - trading -c 'ls -la ~/binance-engine/ 2>/dev/null || echo 项目不存在'" 2>nul
echo.

echo [步骤2] 检查root目录下是否有旧项目（应该删除）...
echo.
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "ls -la /root/*.py /root/*.sh /root/app.py /root/gunicorn* 2>/dev/null || echo root下无Python/项目文件" 2>nul
echo.

echo [步骤3] 检查当前运行的gunicorn进程（应该由trading用户运行）...
echo.
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "ps aux | grep gunicorn | grep -v grep" 2>nul
echo.

echo [步骤4] 检查supervisor配置（应该用trading用户运行）...
echo.
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "cat /etc/supervisor/conf.d/binance-engine.conf 2>/dev/null || cat /etc/supervisord.d/binance-engine.ini 2>/dev/null || echo 未找到supervisor配置" 2>nul
echo.

echo [步骤5] 检查端口5003监听状态...
echo.
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "netstat -tlnp | grep 5003 || ss -tlnp | grep 5003 || echo 端口5003未监听" 2>nul
echo.

echo [步骤6] 检查Git远程状态...
echo.
plink -batch -pw "w'tFzgg2vPZ0D,Z" root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && git remote -v && echo --- && git log -1 --oneline && echo --- && git status'" 2>nul
echo.

echo ================================================
echo 检查完成！
echo ================================================
echo.
echo 如需清理root下的旧项目，请运行: _cleanup_root.bat
echo 如需重新部署，请运行: _deploy_vps.bat
echo ================================================
pause

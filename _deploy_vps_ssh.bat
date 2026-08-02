@echo off
chcp 65001 >nul
echo ================================================
echo VPS部署脚本 - 同步代码到trading用户
echo ================================================
echo.

echo [1/7] 停止gunicorn服务...
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "pkill -f 'gunicorn.*5003' && echo 停止成功 || echo 停止失败或进程不存在"
echo.

echo [2/7] 删除旧残留目录...
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "rm -rf /home/trading/eth-webhook-server && echo 删除成功"
echo.

echo [3/7] 备份旧项目（保留.git）...
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && mv .git /tmp/binance-engine-git-backup && echo 备份成功'"
echo.

echo [4/7] 删除旧文件...
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && rm -rf * && echo 删除成功'"
echo.

echo [5/7] 恢复.git目录...
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "mv /tmp/binance-engine-git-backup /home/trading/binance-engine/.git && chown -R trading:trading /home/trading/binance-engine/.git && echo 恢复成功"
echo.

echo [6/7] 切换到binance-engine并拉取最新代码...
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && git fetch origin && git reset --hard origin/main && git log -1 --oneline'"
echo.

echo [7/7] 重启gunicorn服务...
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && source venv/bin/activate && nohup gunicorn -w 1 --threads 1 -b 0.0.0.0:5003 app:app > logs/gunicorn.log 2>&1 &' && sleep 3 && ps aux | grep gunicorn | grep 5003"
echo.

echo.
echo [验证] 检查health接口...
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "curl -s http://127.0.0.1:5003/health" 2>nul
echo.
echo.
echo ================================================
echo 部署完成！
echo ================================================

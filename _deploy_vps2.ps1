Write-Host "================================================"
Write-Host "VPS部署脚本 - 同步代码到trading用户"
Write-Host "================================================"
Write-Host ""

Write-Host "[1/7] 停止gunicorn服务..."
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "pkill -f 'gunicorn.*5003'; echo 停止完成"
Write-Host ""

Write-Host "[2/7] 删除旧残留目录..."
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "rm -rf /home/trading/eth-webhook-server; echo 删除完成"
Write-Host ""

Write-Host "[3/7] 备份旧项目（保留.git）..."
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && mv .git /tmp/binance-engine-git-backup && echo 备份成功'"
Write-Host ""

Write-Host "[4/7] 删除旧文件..."
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && rm -rf * && echo 删除成功'"
Write-Host ""

Write-Host "[5/7] 恢复.git目录..."
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "mv /tmp/binance-engine-git-backup /home/trading/binance-engine/.git && chown -R trading:trading /home/trading/binance-engine/.git && echo 恢复成功"
Write-Host ""

Write-Host "[6/7] 拉取最新代码..."
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && git fetch origin && git reset --hard origin/main && git log -1 --oneline'"
Write-Host ""

Write-Host "[7/7] 重启gunicorn服务..."
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && source venv/bin/activate && nohup gunicorn -w 1 --threads 1 -b 0.0.0.0:5003 app:app > logs/gunicorn.log 2>&1 &'"
Start-Sleep -Seconds 3
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "ps aux | grep gunicorn | grep 5003"
Write-Host ""

Write-Host "[验证] 检查health接口..."
ssh -o StrictHostKeyChecking=no root@187.77.130.144 "curl -s http://127.0.0.1:5003/health"
Write-Host ""
Write-Host ""
Write-Host "================================================"
Write-Host "部署完成！"
Write-Host "================================================"

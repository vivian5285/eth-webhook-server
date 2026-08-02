git add -f README.md position_supervisor_binance.py
$msg = "v16.17: 开仓重试逻辑 - 市价失败后等退避重试 + TV指导价限价兜底单; 修复平仓净场retry bug"
git commit -m $msg
git push

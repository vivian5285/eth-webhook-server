# Deploy: 本地 push 后，在 VPS 拉取更新并重启服务
# 1. 先运行 _git_commit.ps1 提交推送
# 2. 然后运行本脚本

scp _restart.sh root@187.77.130.144:/tmp/restart.sh
ssh root@187.77.130.144 "cat /tmp/restart.sh | sed 's/\r$//' > /tmp/restart2.sh && chmod +x /tmp/restart2.sh && bash /tmp/restart2.sh"

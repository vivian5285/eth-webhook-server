#!/bin/bash
# 每周数据导出+提交推送——由strategy-weekly-export.timer每周触发一次。
# 只做三件事：导出JSON快照、git add该目录、commit+push(用专门配置的
# strategy-engine-weekly-export部署密钥，权限只到这一个repo)。
# 推送失败(比如网络抖动)不影响任何交易服务，只是这一周的快照晚一点点
# 才会出现在git历史里，下次定时器触发时反正会带新数据重新导出一次。
set -euo pipefail
cd /root/strategy-engine

venv/bin/python3 -m strategy_engine.weekly_export

git add strategy_engine/reports/
if git diff --cached --quiet; then
    echo "无变化，跳过提交"
    exit 0
fi

git commit -m "chore(strategy_engine): 每周数据快照 $(date -u +%Y-%m-%d)

自动导出，由strategy-weekly-export.timer触发，不涉及任何策略代码改动。"
git push origin main
echo "已推送"

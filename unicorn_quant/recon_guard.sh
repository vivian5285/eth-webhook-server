#!/bin/bash
# 2026-10-19 擂台/网站守护 + 自动对账修复(root crontab 每小时 :35)。详见 engine/reconciler.py
D=/home/stratroster/unicorn_quant/optimal_data
for s in strategy-roster roster-dashboard unicorn-quant nginx; do
  if ! systemctl is-active --quiet "$s"; then
    systemctl restart "$s"
    echo "{\"ts\": $(date +%s), \"service\": \"$s\"}" >> "$D/guard_events.jsonl"
    chown stratroster:stratroster "$D/guard_events.jsonl"
  fi
done
cd /home/stratroster/unicorn_quant && sudo -u stratroster nice -n 10 /home/stratroster/eth-webhook-server/venv/bin/python -m engine.reconciler >> "$D/reconciler.log" 2>&1

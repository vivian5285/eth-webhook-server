# ops/ — 不在git checkout里的VPS部署代码快照

`/root/binance-dashboard`、`/root/watchdog` 在 VPS1(187.77.130.144) 上不是 git 仓库，
这里存一份与线上逐字节一致的快照 + 相关 systemd unit，方便追溯和恢复。
凭据全部走各目录自己的 `.env`（不入库）。

- `vps1/binance-dashboard/` → `/root/binance-dashboard/`
- `vps1/watchdog/` → `/root/watchdog/`（含 2026-09-30 新增的 `direction_audit.py`：
  净额一致/孤儿仓/止损方向/擂台方向 四项只读核查，15 分钟一次，新问题发钉钉）
- `vps1/systemd/` → `/etc/systemd/system/`

仓库分支对应关系：
- `main` = 币安 B/C/E 实盘引擎目录 `/home/binance{B,C,E}/binance-engine`
- `arena-vps` = 擂台 VPS2(187.53.133.188) `/home/stratroster/eth-webhook-server`

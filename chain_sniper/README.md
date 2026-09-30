# chain_sniper

全自动 BSC + Solana 链上动量狙击机器人。完全独立于本仓库的合约交易引擎——不 import
任何交易/持仓模块，只复用主程序 `.env` 里已在用的 Telegram 通知凭证。设计细节见
`C:\Users\Administrator\.claude\plans\greedy-swinging-plum.md`。

## ⚠️ 先读这个：风险现实

- **不是抢跑机器人。** 普通 retail 基础设施打不过专业 MEV 机器人的 mempool 抢跑速度，
  本系统的策略是"聪明钱信号 + 安全过滤 + 延迟确认后跟上"，不是"抢第一笔"。
- **安全过滤不是100%保险。** GoPlus 之类的检测能挡掉大部分明显蜜罐/rug，但挡不住
  所有花样——单笔仓位上限、单日熔断存在的意义就是假设"有些币还是会判断失误"。
- **私钥只放VPS环境变量。** 绝不进代码、git、日志、Telegram消息。`notifier.py` 有
  脱敏过滤，但那是最后一道防线，不是唯一防线。
- **DRY_RUN 是默认值，也是必经阶段。** 不允许跳过空跑直接实盘。

## 目录结构

```
config.py, db.py, models.py, notifier.py, main.py   — 核心骨架
adapters/base.py                                     — 链无关接口（BSC/Solana 各自实现在 Phase 1/2 补齐）
signals/                                              — 安全过滤/增长过滤/聪明钱追踪/复合门控（Phase 1+）
execution/risk_gate.py, buyer.py, exit_manager.py     — 风控闸门、买入、离场轮询
watchlist/smart_wallets.json                          — 人工维护的聪明钱种子名单
data/                                                 — SQLite 数据库（gitignore 已覆盖）
logs/                                                 — 运行日志（gitignore 已覆盖）
```

## 当前进度：Phase 0（骨架 + 空跑模式）

已完成：目录结构、config/db/notifier/models、风控闸门、离场轮询循环、买入执行的
DRY_RUN 分支、心跳。**尚未接入任何真实链上数据源**——`main.py` 里 `adapters = {}`
是有意为之，Phase 1 才会填入真实的 `SolanaAdapter`。

## 部署前必须准备

1. `python -m venv venv && venv/bin/pip install -r requirements-chainsniper.txt`
2. `cp .env.example .env`，填入 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
   （可以直接复用仓库根目录 `.env` 里现成的值）
3. 跑一次 notifier 自检：`python notifier.py` —— 应该在 Telegram 收到一条"✅ 自检"消息
4. `python main.py` —— 应该看到 db 初始化日志、启动播报、之后每
   `HEARTBEAT_INTERVAL_HOURS` 一条心跳

## 分阶段计划（详见方案文档）

- **Phase 0**（当前）：骨架 + 空跑
- **Phase 1**：Solana 单链跑通，安全过滤 + 增长过滤 + 小种子名单，全程 DRY_RUN
- **Phase 2**：聪明钱层做实（Helius webhook）+ 接入 BSC
- **Phase 3**：两个热钱包充值小额资金，`DRY_RUN=false`，最小仓位/最小并发数冒烟测试
- **Phase 4**：MEV防护RPC调优、可选移动止损、名单自动升降权重

## 生产部署（VPS）

```
cp chain_sniper.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now chain_sniper.service
journalctl -u chain_sniper -f
```

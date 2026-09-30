# polymarket_quant

Polymarket 5分钟/15分钟加密Up/Down市场统计价差交易机器人。完全独立于本仓库的
合约交易引擎和`chain_sniper/`——不共用代码、不共用钱包/资金。设计细节见
`C:\Users\Administrator\.claude\plans\greedy-swinging-plum.md`（"polymarket_quant"章节）。

## ⚠️ 先读这个：策略现实

- **唯一目标策略是统计价差交易**，不是聪明钱跟单（已验证是输家策略）、不是领域专精
  （那是主观判断力，不是机器人能造出来的）、不是跨链套利个例。
- **公允价值模型没有被验证过。** Phase 1的启发式模型存在的意义是让空跑数据先跑起来、
  每笔都能看懂为什么触发，不代表公式本身是对的——要靠Phase 2的真实数据迭代校准。
- **taker手续费在平值附近约吃掉3.5%名义金额**，这不是小数字，策略必须把"边际减去手续费
  还剩多少"当成硬门槛，不能只看方向对不对。
- **DRY_RUN 是默认值，也是必经阶段。** 不允许跳过空跑直接实盘。

## 部署前必须准备

1. `python -m venv venv && venv/bin/pip install -r requirements-polymarket.txt`
2. `cp .env.example .env`，填入`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`（可直接复用根目录`.env`的值）
3. **新建一个专用Polygon钱包**（不要用chain_sniper的BSC/Solana钱包），只充值这个机器人
   被允许亏损的USDC额度
4. **一次性手动前置步骤（容易忘）**：钱包首次交易前需要给Polymarket交易合约批准
   USDC/CTF代币额度（通常在Polymarket网页端连接钱包手动点一次）。忘了这步会表现成
   "下单静默失败"
5. 跑一次notifier自检：`python notifier.py` —— 应该在Telegram收到"✅ 自检"消息
6. `python main.py` —— 应该看到db初始化日志、启动播报、离场循环启动日志，之后每
   `HEARTBEAT_INTERVAL_HOURS`一条心跳

## 当前进度：Phase 0（骨架 + 空跑模式）

已完成：目录结构、config/db/notifier/models、风控闸门、离场管理循环骨架（早退+结算对账
两条逻辑，Phase 0下`client=None`时安全跳过不做任何事）、开仓执行的DRY_RUN分支、心跳。
**尚未接入任何真实Polymarket数据/交易**——`main.py`里`client = None`是有意为之，
Phase 1才会填入真实的`polymarket_client.py` + `market_feed.py`。

## 分阶段计划（详见方案文档）

- **Phase 0**（当前）：骨架 + 空跑
- **Phase 1**：BTC单币种feed(`market_feed.py`接`wss://ws-live-data.polymarket.com`)
  + 公允价值模型(`signals/edge.py`) + 空跑记录开仓信号，先不做反转早退/结算对账
- **Phase 2**：结算对账循环接真实数据上线，用空跑积累的`edge_signals`数据校准模型
- **Phase 3**：专用钱包充值极小额USDC，`DRY_RUN=false`，最小仓位/最小并发冒烟测试
- **Phase 4**：扩展ETH/SOL/XRP，根据真实数据放宽阈值/频率

## 生产部署（VPS）

```
cp polymarket_quant.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now polymarket_quant.service
journalctl -u polymarket_quant -f
```

# 币安 Binance · ETH 永续 Webhook 交易系统

**当前版本：`v13.4.6-flat-reconcile`**

TradingView Webhook → 币安 ETHUSDT 永续合约自动化引擎。与深币 VPS 逻辑对齐，单位按 **ETH** 计算，实盘 **10 倍杠杆**，钉钉为 **黄金主题**。

---

## VPS 部署信息

| 项目 | 值 |
|------|-----|
| 目录 | `~/binance-engine` |
| 端口 | **5003** |
| 杠杆 | **10x**（开仓前自动 set-leverage） |
| 健康检查 | `GET /health` |
| 主日志 | `logs/binance_brain.log` |
| 部署脚本 | `bash deploy_binance.sh` |

---

## 系统架构

```
TradingView Webhook
        ↓
    app.py（网关，异步线程）
        ↓
position_supervisor_binance.py（智慧大脑）
├── TV/开仓日志持久化
├── 重启闪电接管 + TV 对账
├── TP123 比例审计 + 增量/核武补挂
├── 雷达移动保本（WS 推价 + STOP_MARKET）
├── 空仓对账 + 蚂蚁仓扫尾（flat-reconcile）
└── 哨兵循环（持仓/人工异动/定期扫描）
        ↓
binance_client.py（REST 交易 + 公开 WS 行情）
position_manager.py（持仓查询封装）
dingtalk.py（黄金钉钉播报）
```

> 旧版模块（`order_executor.py`、`tp_monitor.py`、`position_supervisor.py` 等）已不在热路径，当前以 `position_supervisor_binance.py` 为准。

---

## 核心能力（v13.4.x）

### 1. 重启闪电接管
- 读取 `binance_vps_state.json` + **TV 日志** + **开仓日志**
- **TV 方向强制对齐**：`last_tv_side` 始终同步 TV 日志最新 LONG/SHORT
- **方向背离 / TV 已 CLOSE** → 核武清场，不盲目接管
- **人工加减仓**：账本 ETH ≠ 实盘 → 写开仓日志 + 钉钉 + 按比例重挂 TP
- TP123 **价位 + 数量** 严格审计（regime 比例）
- **雷达恢复**：按现价刷新 `best_price` / 激活状态 → 补挂 STOP_MARKET
- 已齐全 → **跳过补挂**；不齐 → 增量补挂 → 仍失败 → **核武清场重挂**
- 启动 **WS 推价** + **哨兵循环**（与运行中一致）

### 2. 限价止盈 TP123
- 比例随档位（regime 1~4）变化，例如 3 档：`18% / 32% / 50%`
- `reduceOnly` 限价单，价格/数量对齐 tick/step
- 重复单自动核武撤净重挂

### 3. 雷达移动保本
- 价格达 TP1 距离的 60%（3 档默认）→ 激活雷达
- 跟踪 `best_price`，ATR × 档位倍数推升/下压 STOP_MARKET
- **WebSocket** 订阅 `ethusdt@markPrice@1s`，REST 查价仅 ≥30s 兜底
- 哨兵轮询：常态 6s / 接近激活 3s / 雷达激活 2s
- 推止损时只撤 STOP，**TP123 保留**

### 4. 人工异动
- 手动加/减仓、部分止盈 → 智能重对齐
- 人工全平 → 撤单复位 + 钉钉
- 方向与 TV 背离 → 核武全平

### 5. 空仓对账与蚂蚁仓扫尾（v13.4.6）
- **重启首检**：≤0.004 ETH 蚂蚁仓，或 TP 吃完后残量 ≤12% 且无 TP 单 → 自动 reduceOnly 扫尾
- **宕机补发**：服务重启期间已全平但账本仍有仓 → 补发「完美胜利」钉钉
- **空闲巡检**：空仓待命时每 30s 扫描，发现孤立残量自动扫平
- 平仓钉钉带 REST 核查重试，避免误报

### 6. 日志与审计
| 文件 | 说明 |
|------|------|
| `logs/binance_tv_journal.jsonl` | 每条 TV 信号 |
| `logs/binance_open_journal.jsonl` | 开仓 / 接管记录 |
| `logs/binance_brain.log` | 大脑主日志 |
| `binance_vps_state.json` | 运行时状态（自动生成） |

---

## 环境变量（`.env`）

```env
BINANCE_API_KEY=
BINANCE_API_SECRET=
WEBHOOK_SECRET=528586
DINGTALK_WEBHOOK=
DINGTALK_SECRET=
FLASK_HOST=0.0.0.0
FLASK_PORT=5003
```

---

## TradingView Webhook

**URL：** `http://你的VPS:5003/webhook`

```json
{
  "action": "SHORT",
  "secret": "528586",
  "regime": 3,
  "atr": 30.0,
  "price": 1560.0,
  "tv_tp1": 1537.85,
  "tv_tp2": 1517.40,
  "tv_tp3": 1499.22,
  "reason": "可选说明"
}
```

| action | 说明 |
|--------|------|
| `LONG` / `SHORT` | 先平后开 → 挂 TP123 → 启动哨兵 + WS |
| `CLOSE` | 换防清场 |
| `CLOSE_PROTECT` | 保护性全平 |
| `CLOSE_TP3` | TP3 吃满收网 |

---

## 本地开发

```bash
pip install -r requirements.txt
python app.py
# 或
gunicorn --bind 0.0.0.0:5003 --workers 1 --threads 10 --daemon app:app
```

---

## VPS 部署（标准流程）

```bash
cd ~/binance-engine
git fetch origin && git reset --hard origin/main

# 版本门控
grep v13.4.6-flat-reconcile binance_client.py position_supervisor_binance.py

pip3 install -r requirements.txt   # 含 websocket-client
bash deploy_binance.sh

# 验收
tail -60 logs/binance_brain.log
curl -s http://127.0.0.1:5003/health
```

**部署成功日志示例：**

```
🟢 Binance Client v13.4.6-flat-reconcile 已加载
🧠 币安 VPS [v13.4.6-flat-reconcile] 军师托管版已加载
📡 币安公开 WS 启动: ETHUSDT@markPrice@1s
Websocket connected
🔄 [系统重启点火] 检测到实盘持仓 SHORT 0.406 ETH ...
✅TP1 0.073@1537.85 | ✅TP2 0.13@1517.40 | ✅TP3 0.203@1499.22
```

**健康检查响应：**

```json
{"service":"binance_webhook","status":"ok","version":"v13.4.6-flat-reconcile"}
```

---

## 与深币系统区别

| 项目 | 币安 | 深币 |
|------|------|------|
| 单位 | ETH | 张 |
| 杠杆 | **10x** | **10x** |
| 端口 | 5003 | 5004 |
| 钉钉主题 | 黄金 | 紫金 |
| 止损类型 | STOP_MARKET | 条件单 trigger |
| WS 频道 | markPrice@1s | market-latest |
| 空仓对账 | v13.4.6 蚂蚁仓扫尾 | v13.4.6 蚂蚁仓扫尾 |

---

## 版本演进摘要

| 版本 | 要点 |
|------|------|
| v13.3-smart-guard | TV/开仓日志、比例审计、增量补挂 |
| v13.4-nuclear-guard | 核武清场重挂（重复单/0-3 对齐失败） |
| v13.4.1-qtyfix | 深币张数 `'1.000000'` 解析修复 |
| v13.4.2-radar-live | 雷达自适应轮询 2~6s |
| v13.4.3-ws-radar | WS 推价 + REST 限频兜底 |
| **v13.4.6-flat-reconcile** | **空仓对账、蚂蚁仓扫尾、宕机补发钉钉** |

---

## 注意事项

1. 部署务必 `git reset --hard origin/main`。
2. 币安首次升级 v13.4.3 需 `pip install -r requirements.txt`（`websocket-client`）。
3. 重启后钉钉「闪电接管报告」应显示 TP **3/3 比例审计 ✅**。
4. 仅同时持有一个方向；新信号 **先撤单 → 平仓 → 再开仓**。
5. 建议 one-way 持仓模式，ETHUSDT 永续。
6. 开仓量按 `余额 × regime.margin × 10x ÷ 价格` 取整，步长 0.001 ETH。

---

*Quant AI · 币安黄金趋势大波段引擎*

# GEMINI 双轨交易工厂 · VPS 实盘（最终需求）

**当前版本：`v15.0.6-sentinel-05`**  
**TV 策略 schema：`v6.5.6`**  
**生产唯一大脑：`position_supervisor_binance.py`**

TradingView Webhook → VPS 接收/验证 → 交易所下单 → WebSocket 阶梯雷达。

| 工厂 | VPS 目录 | 端口 | 品种 | 仓位 | 钉钉 |
|------|----------|------|------|------|------|
| **币安** | `~/binance-engine` | **5003** | ETH + XAU | **RISK20_NOTIONAL5** | 黄金 |
| **深币** | `~/deepcoin-hft-server` | **5004** | ETH + XAU 张 | 同逻辑 | 紫金 |

```bash
curl -s http://127.0.0.1:5003/health
# version: v15.0.6-sentinel-05
# sizing: RISK20_NOTIONAL5 · leverage: fixed_5 · tv_strategy: v6.5.6

python check_vps_logic.py
# 清单：docs/VPS实盘检查清单.md
```

---

## 核心铁律

```
TV 到达 → 1s 缓存 → 同窗先平后开 → 按风险公式开仓 → 挂 TP1+TP2 + stop_loss
       → 阶梯雷达候命(WS) → 钉钉确认
```

| 铁律 | 说明 |
|------|------|
| **TV方向为准** | 实盘/重启与最新 TV 反向 → **先全平对齐** + 钉钉 |
| **风险仓位** | 风险资金=权益×20%；名义上限=权益×5；`min(风险/止损距, 名义/价, TV.qty)` |
| **硬止损** | webhook `stop_loss` 原值 `closePosition`（三轨：硬止损 / 雷达 / TP 限价 `reduceOnly`） |
| **分腿 TP** | 30/30/40；只挂 TP1+TP2；**TP3 永不挂限价** |
| **先平后开** | 同窗平仓优先执行一次，再开最新一条；开仓前强制清仓兜底（~~有平仓则忽略开仓~~ 已废弃） |
| **平仓确认** | CLOSE_TP/TRAIL/SL_* 只对账+调止损，**不主动市价平仓** |
| **反转保护** | CLOSE_QUICK_EXIT / CLOSE_RSI_EXIT → 市价全平 |
| **阶梯雷达** | TP1 路程 85% 激活 → entry±n×0.5/0.3 ATR → TP3 后 2.0 ATR 追踪 |
| **防螺旋** | 未成交 TP 取消移交雷达禁重挂；60s 去重 `action+symbol+price`；改单重试 3 次 |
| **哨兵** | 订单监控 **0.5s**（币安/深币统一） |

---

## 仓位公式（RISK20_NOTIONAL5）

```
风险资金 = 账户权益 × 20%
名义上限 = 账户权益 × 5
理论数量 = min(风险资金/|开仓价-stop_loss|, 名义上限/开仓价, TV.qty)
qty      = floor(理论数量 × 1000) / 1000
```

示例：权益 1000U · 价 3300.5 · SL 3200.5 · TV.qty=12 → **qty=1.514**

`set_leverage` 固定 **5**。

---

## Webhook

**Endpoint**：`POST /webhook`  
**验证**：`token`（或 `secret`）必须等于 `528586`，否则 **403**。

### 开仓 LONG / SHORT

```json
{
  "bot_id": "Trillion_God_v6.5.6",
  "token": "528586",
  "action": "LONG",
  "symbol": "ETHUSDT",
  "price": 3300.5,
  "qty": 12,
  "qty1": 3, "qty2": 3, "qty3": 6,
  "stop_loss": 3200.5,
  "tp1": 3350.0, "tp2": 3480.0, "tp3": 3560.0
}
```

### 平仓确认（只对账）

| Action | 含义 |
|--------|------|
| `CLOSE_TP` leg=1/2 | 确认止盈；leg1 保本；leg2 → entry±1.5ATR |
| `CLOSE_TRAIL` leg=2/3 | 追踪止盈对账；leg3 确认清零重置 |
| `CLOSE_SL_INITIAL` / `CLOSE_SL_BREAKEVEN` | 确认清零重置 |

### 反转保护（主动全平）

`CLOSE_QUICK_EXIT` / `CLOSE_RSI_EXIT` + `reason`

---

## 阶梯雷达

| 参数 | 默认 | 说明 |
|------|------|------|
| 提前激活 | 0.85 | TP1 **路程** 85%（非 tp1 绝对值×0.85） |
| 阶梯间隔 | 0.5×ATR | 从 entry 起算 |
| 每步幅度 | 0.3×ATR | stop = entry ± n×0.3×ATR |
| TP1/TP2 底线 | 0.5 / 1.5×ATR | |
| TP3 追踪 | 2.0×ATR | 只向有利方向 |
| ATR 刷新 | 5 分钟 | 已触发阶梯不回溯 |
| 挂单超时 | 5 分钟 | 取消移交雷达 |
| 去重 | 60 秒 | 同 action+symbol+price |

激活价示例：LONG entry=1800, tp1=1840.5 → **1834.425**

---

## 重启恢复

1. 查询交易所持仓与未成交挂单  
2. 有仓：恢复持久化 tp/ATR/stepCount/activated，闪电接管 + 钉钉  
3. 实盘与最新 TV 方向不一致 → **先市价全平对齐 TV** + 钉钉（不以暂停留仓）  
4. 重挂止损 + 仍有利的 TP1/TP2  
5. 启动雷达 WS  
6. 无仓：清状态 · 空仓待命钉钉  

版本：`v15.0.4-tv-flat-ladder`
---

## 生产模块

| 模块 | 说明 |
|------|------|
| `app.py` | Flask 网关 · token 校验 · health |
| `position_supervisor_binance.py` | 唯一军师大脑 |
| `webhook_parser.py` | 解析 / RISK20 仓位 / 阶梯雷达 |
| `binance_client.py` | REST + WS |
| `dingtalk.py` | 播报 |
| `check_vps_logic.py` | 静态自查 |

日志：`logs/binance_brain.log`（滚动保留约 30 天）

---

## 部署

```bash
cd ~/binance-engine
git fetch origin && git reset --hard origin/main
grep 'BINANCE_VPS_VERSION' position_supervisor_binance.py
# 期望: v15.0.4-tv-flat-ladder

bash deploy_binance.sh
curl -s http://127.0.0.1:5003/health | python3 -m json.tool
tail -f logs/binance_brain.log
```

---

*GEMINI Quant · v15.0.4-tv-flat-ladder / TV v6.5.6*

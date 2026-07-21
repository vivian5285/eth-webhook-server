# GEMINI 双轨交易工厂 · VPS 实盘（最终需求）

**当前版本：`v15.1.0-breath-stop`**  
**TV 策略 schema：`v6.5.6`**  
**生产唯一大脑：`position_supervisor_binance.py`**

TradingView Webhook → VPS 接收/验证 → 交易所下单 → WebSocket 呼吸止损。

| 工厂 | VPS 目录 | 端口 | 品种 | 仓位 | 钉钉 |
|------|----------|------|------|------|------|
| **币安** | `~/binance-engine` | **5003** | ETH + XAU | **RISK20_NOTIONAL5** | 黄金 |
| **深币** | `~/deepcoin-hft-server` | **5004** | ETH + XAU 张 | 同逻辑 | 紫金 |

```bash
curl -s http://127.0.0.1:5003/health
# version: v15.1.0-breath-stop
# sizing: RISK20_NOTIONAL5 · leverage: fixed_5 · tv_strategy: v6.5.6

python check_vps_logic.py
# 清单：docs/VPS实盘检查清单.md
```

---

## 核心铁律

```
TV 到达 → 1s 缓存 → 同窗先平后开 → 按风险公式开仓 → 挂 TP1+TP2+TP3 + 呼吸止损
       → 呼吸止损开仓即追踪(WS) → 钉钉确认
```

| 铁律 | 说明 |
|------|------|
| **TV方向为准** | 实盘/重启与最新 TV 反向 → **先全平对齐** + 钉钉 |
| **风险仓位** | 风险资金=权益×20%；名义上限=权益×5；止损距=**1.5×ATR**；`min(风险/止损距, 名义/价, TV.qty)` |
| **呼吸止损** | 开仓 `entry±1.5×ATR` closePosition；阶段一阶梯锁本；浮盈≥3×ATR 切入 ADX 追踪（硬止损+雷达合并单槽） |
| **分腿 TP** | 30/30/40 硬编码；挂 **TP1+TP2+TP3** 限价（价格用 TV）；与止损并行先到先得 |
| **先平后开** | 同窗平仓优先执行一次，再开最新一条；开仓前强制清仓兜底（~~有平仓则忽略开仓~~ 已废弃） |
| **平仓确认** | CLOSE_TP/TRAIL/SL_* 只对账+调止损，**不主动市价平仓** |
| **反转保护** | CLOSE_QUICK_EXIT / CLOSE_RSI_EXIT → 市价全平 |
| **防螺旋** | 未成交 TP 取消移交禁重挂；60s 去重 `action+symbol+price`；改单重试 3 次 |
| **哨兵** | 订单监控 **0.5s**（币安/深币统一） |

---

## 仓位公式（RISK20_NOTIONAL5）

```
风险资金 = 账户权益 × 20%
名义上限 = 账户权益 × 5
理论数量 = min(风险资金/|开仓价-呼吸止损|, 名义上限/开仓价, TV.qty)
# 呼吸止损距 = 1.5×ATR（entry±1.5×ATR）；TV stop_loss 仅参考不挂盘
qty      = floor(理论数量 × 1000) / 1000
```

示例：权益 1000U · 价 1800 · ATR 40 → SL 1740 · TV.qty=12 → 按风险公式取整

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

## 呼吸止损（硬止损 + 雷达合并）

| 参数 | 默认 | 说明 |
|------|------|------|
| 初始止损 | 1.5×ATR | entry±1.5×ATR，开仓即挂 closePosition |
| 阶梯触发 | 0.75×ATR | 阶段一：每推进 0.75×ATR 浮盈 |
| 阶梯跟进 | 0.4×ATR | 以 initial_stop 为基准推进 |
| 保本切入 | 3.0×ATR | 浮盈达标 → 阶段二 ADX 追踪 |
| TP1/TP2 底线 | 0.5 / 1.5×ATR | 价达 1.35 / 2.5×ATR 时抬底 |
| ADX 追踪 | 1.2~2.5×ATR | ADX 15→35 线性插值，弱紧强宽 |
| initialAtr | 开仓锁定 | 全程固定，不用实时 ATR 重算距离 |
| 挂单超时 | 5 分钟 | 取消移交追踪 |
| 去重 | 60 秒 | 同 action+symbol+price |

---

## 重启恢复

1. 查询交易所持仓与未成交挂单  
2. 有仓：恢复持久化 tp/ATR/initial_stop/breakeven_phase + 钉钉  
3. 实盘与最新 TV 方向不一致 → **先市价全平对齐 TV** + 钉钉（不以暂停留仓）  
4. 重挂呼吸止损 + 仍有利的 TP123  
5. 启动价格 WS  
6. 无仓：清状态 · 空仓待命钉钉  

版本：`v15.1.0-breath-stop`
---

## 生产模块

| 模块 | 说明 |
|------|------|
| `app.py` | Flask 网关 · token 校验 · health |
| `position_supervisor_binance.py` | 唯一军师大脑 |
| `breath_stop.py` | 两阶段呼吸止损 |
| `webhook_parser.py` | 解析 / RISK20 仓位 / ATR·ADX |
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
# 期望: v15.1.0-breath-stop

bash deploy_binance.sh
curl -s http://127.0.0.1:5003/health | python3 -m json.tool
tail -f logs/binance_brain.log
```

---

*GEMINI Quant · v15.0.4-tv-flat-ladder / TV v6.5.6*

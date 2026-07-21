# GEMINI 双轨交易工厂 · VPS 实盘（架构对齐版）

**当前版本：`v15.5.0-final-spec`**  
**TV 策略 schema：`v6.5.6`**  
**生产唯一大脑：`position_supervisor_binance.py`**

TradingView Webhook → VPS 接收/验证 → 行情引擎(90m ATR/ADX) → 下单 → 呼吸止损。

| 工厂 | VPS 目录 | 端口 | 品种 | 仓位 | 钉钉 |
|------|----------|------|------|------|------|
| **币安** | `~/binance-engine` | **5003** | ETH + XAU | **RISK20_NOTIONAL5** | 黄金 |
| **深币** | `~/deepcoin-hft-server` | **5004** | ETH + XAU 张 | **同逻辑** | 紫金 |

```bash
curl -s http://127.0.0.1:5003/health
# version: v15.5.0-final-spec
# sizing: RISK20_NOTIONAL5 · leverage: fixed_5 · tv_strategy: v6.5.6

python check_vps_logic.py
```

---

## 核心铁律

```
TV LONG/SHORT → 查实盘非空则全平撤单并等待确认 → 独立算 qty → 市价开仓
              → 挂 TP1+TP2 + 呼吸止损(qty=全仓)
              → 仅 CLOSE_QUICK_EXIT / CLOSE_RSI_EXIT 反转保护
```

| 铁律 | 说明 |
|------|------|
| **先平后开** | 任意方向、不论盈亏，有仓必先市价全平+撤单，确认空仓后再开 |
| **单仓位** | pyramiding=1；禁止加仓/合并净仓 |
| **仓位** | `min(权益20%/\|价−initialStop\|, 权益×5/价, TV.qty)`；无状态纯函数 |
| **行情引擎** | 30m×3 合成 **90m** → Wilder ATR/ADX；webhook **不读** atr/adx |
| **呼吸止损** | 唯一写止损；reduceOnly+数量；TP1/TP2 成交后原子收缩 70%/40% |
| **分腿 TP** | 只挂 **TP1+TP2**（不挂 TP3）；余仓交给阶段二 |
| **反转保护** | 仅 TV QUICK/RSI → 市价全平 |
| **Webhook** | 仅 LONG/SHORT/CLOSE_QUICK_EXIT/CLOSE_RSI_EXIT（+PING）；token=`528586` |

---

## 仓位公式（RISK20_NOTIONAL5）

```
risk_capital  = equity * 0.20
notional_cap  = equity * 5
qty = min(risk_capital / |entry - initialStop|, notional_cap / entry, TV.qty)
# initialStop = entry ± 1.5×ATR(VPS 90m)
```

`set_leverage` 固定 **5x**。每次开仓独立计算，不读历史仓位。

---

## 呼吸止损

| 参数 | 默认 | 说明 |
|------|------|------|
| 初始止损 | 1.5×ATR | 开仓即挂，quantity=全仓 |
| 阶梯 | 0.75 / 0.4×ATR | 阶段一 |
| 保本切入 | 3.0×ATR | → 阶段二 ADX 追踪 1.2~2.5×ATR |
| TP 成交 | — | 撤旧止损→按剩余 qty 重挂（暂停 tick） |

---

## 钉钉（对齐文案）

- 先平后开：检测到已有持仓，已市价全平并撤单，准备执行新开仓
- 阶段切换：止损已进入阶段二（趋势追踪），当前ADX=…，追踪距离=…×ATR
- 止损平仓（阶段一）/（阶段二/趋势追踪）
- 反转保护平仓：{reason}
- 无：雷达激活、保护性全平、TP3止盈、加仓成交

---

## 生产模块

| 模块 | 职责 |
|------|------|
| `app.py` | Flask 网关 · health |
| `position_supervisor_binance.py` | 唯一大脑 |
| `market_engine.py` | 90m 合成 · ATR/ADX |
| `breath_stop.py` | 两阶段呼吸止损 |
| `webhook_parser.py` | 解析 / RISK20 仓位 |
| `binance_client.py` | REST + WS |
| `dingtalk.py` | 播报 |
| `check_vps_logic.py` | 静态自查 |

---

## 部署

```bash
cd ~/binance-engine
git fetch origin && git reset --hard origin/main
grep 'BINANCE_VPS_VERSION' position_supervisor_binance.py
# 期望: v15.5.0-final-spec

bash deploy_binance.sh
curl -s http://127.0.0.1:5003/health | python3 -m json.tool
```

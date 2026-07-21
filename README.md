# GEMINI 双轨交易工厂 · VPS 实盘（最终需求）

**当前版本：`v15.2.0-market-90m`**  
**TV 策略 schema：`v6.5.6`**  
**生产唯一大脑：`position_supervisor_binance.py`**

TradingView Webhook → VPS 接收/验证 → 行情引擎(90m ATR/ADX) → 下单 → 呼吸止损。

| 工厂 | VPS 目录 | 端口 | 品种 | 仓位 | 钉钉 |
|------|----------|------|------|------|------|
| **币安** | `~/binance-engine` | **5003** | ETH + XAU | **RISK20_NOTIONAL5** | 黄金 |
| **深币** | `~/deepcoin-hft-server` | **5004** | ETH + XAU 张 | 同逻辑 | 紫金 |

```bash
curl -s http://127.0.0.1:5003/health
# version: v15.2.0-market-90m
# sizing: RISK20_NOTIONAL5 · leverage: fixed_5 · tv_strategy: v6.5.6

python check_vps_logic.py
# 清单：docs/VPS实盘检查清单.md
```

---

## 核心铁律

```
TV 到达 → 1s 缓存 → 同窗先平后开 → VPS自算ATR开仓 → 挂 TP1+TP2 + 呼吸止损
       → 呼吸止损开仓即追踪(WS) → 钉钉确认
```

| 铁律 | 说明 |
|------|------|
| **TV方向为准** | 实盘/重启与最新 TV 反向 → **先全平对齐** + 钉钉 |
| **风险仓位** | 风险资金=权益×20%；名义上限=权益×5；止损距=**VPS 1.5×ATR**；`min(风险/止损距, 名义/价, TV.qty)` |
| **行情引擎** | 30m×3 合成 **90m** K 线 → Wilder **ATR(14)/ADX(14)**；webhook **不传** ATR/ADX |
| **呼吸止损** | 开仓 `entry±1.5×ATR` closePosition；阶段一阶梯；浮盈≥3×ATR → ADX 追踪 |
| **分腿 TP** | 30/30 挂 **TP1+TP2**（reduceOnly）；余仓 40% **不挂 TP3**，由阶段二追踪收网 |
| **先平后开** | 同窗平仓优先执行一次，再开最新一条；开仓前强制清仓兜底 |
| **平仓确认** | 旧 CLOSE_TP/TRAIL/SL_* 对账类若仍到达 → 只对账不主动市价平；主路径仅 QUICK/RSI 快平 |
| **反转保护** | CLOSE_QUICK_EXIT / CLOSE_RSI_EXIT → 市价全平 |
| **防螺旋** | 未成交 TP 取消移交禁重挂；60s 去重；改单重试 3 次 |
| **哨兵** | 订单监控 **0.5s** |

---

## 仓位公式（RISK20_NOTIONAL5）

```
风险资金 = 账户权益 × 20%
名义上限 = 账户权益 × 5
理论数量 = min(风险资金/|开仓价-呼吸止损|, 名义上限/开仓价, TV.qty)
# 呼吸止损距 = VPS 自算 1.5×ATR（90m）；TV stop_loss 仅调试对比
qty      = floor(理论数量 × 1000) / 1000
```

`set_leverage` 固定 **5**。

---

## Webhook

**Endpoint**：`POST /webhook`  
**验证**：`token`（或 `secret`）必须等于 `528586`，否则 **403**。

开仓只需 **price**（+ 可选 `stop_loss` 作日志对比）。**不传 ATR/ADX**。

---

## 行情引擎（90m）

币安无原生 90m → 拉 **30m** K 线，每 3 根合并成 90m，再算 Wilder ATR(14)/ADX(14)。  
`initialAtr` 开仓锁定；ADX 可随新 K 线刷新（只影响阶段二追踪倍数）。  
可选：开仓时用 TV `stop_loss` 反推隐含 ATR，与 VPS ATR 差≥20% 仅告警日志/钉钉，**不进决策**。

---

## 呼吸止损（硬止损 + 雷达合并）

| 参数 | 默认 | 说明 |
|------|------|------|
| 初始止损 | 1.5×ATR | entry±1.5×ATR，开仓即挂 |
| 阶梯触发/跟进 | 0.75 / 0.4×ATR | 基准=`initial_stop` |
| 保本切入 | 3.0×ATR | → 阶段二 ADX 追踪 |
| TP1/TP2 底线 | 0.5 / 1.5×ATR | 价达 1.35 / 2.5×ATR 时抬底 |
| ADX 追踪 | 1.2~2.5×ATR | ADX 15→35 线性插值 |
| 挂单超时 | 5 分钟 | 取消移交追踪 |

---

## 重启恢复

1. 查持仓与挂单  
2. 恢复呼吸态；**无持久化/态不全 → 暂停新开仓 + 钉钉**，仍尽量补挂防线  
3. 方向与 TV 不一致 → 先全平  
4. 重挂呼吸止损 + 未成交且仍有利的 TP1/TP2  
5. 启动行情引擎 + 哨兵  

版本：`v15.2.0-market-90m`

---

## 生产模块

| 模块 | 职责 |
|------|------|
| `app.py` | Flask 网关 · token 校验 · health |
| `position_supervisor_binance.py` | 唯一军师大脑 |
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
# 期望: v15.2.0-market-90m

bash deploy_binance.sh
curl -s http://127.0.0.1:5003/health | python3 -m json.tool
```

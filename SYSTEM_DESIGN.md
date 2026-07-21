# ETH Webhook Trading System - 系统设计文档

> **当前生产架构**见 [`README.md`](README.md)：  
> **TV v6.5.6** · **VPS v15.0.5-order-persist** · sizing **RISK20_NOTIONAL5** · 阶梯雷达 · `position_supervisor_binance.py` 唯一大脑。

---

## 当前有效架构（2026-07 · v15.0.5-order-persist）

```
TradingView v6.5.6 Alert (token=528586)
        ↓
app.py (网关 + health: RISK20_NOTIONAL5 / fixed_5)
        ↓
position_supervisor_binance.py   ← 唯一生产大脑
├── TV 消息缓存固定 1.0s + 同窗先平后开折叠（tv_seq.py）
├── 订单 ID 持久化（TP1/TP2/止损）
├── 仓位：风险20%/止损距 ∩ 名义×5 ∩ TV.qty
├── TP 30/30/40，只挂 TP1+TP2（TP3 交雷达）
├── stop_loss closePosition + 阶梯 radar（TP1路程85%激活）
├── RECONCILE 对账+调止损 / FLATTEN 快平
├── ATR 5min 刷新 · 挂单 5min 超时 · 60s 去重 · 哨兵≈1s
├── 开仓前强制清仓（无菌空仓闸）
├── 无持久化有仓 → trading_paused
├── 日志按日滚动保留 30 天
└── dingtalk.report_tv_reconcile
```

静态自查：`python check_vps_logic.py` · 清单：`docs/VPS实盘检查清单.md`

---

## TV 消息顺序处理（VPS 铁律）

同一根 K 线 TV 可能连发多条（开仓 / `CLOSE_QUICK_EXIT` / `CLOSE_RSI_EXIT`），网络到达顺序不保证。

| 规则 | 实现 |
|------|------|
| 缓存窗口 **固定 1.0s** | `SAME_BAR_SETTLE_SEC` / `LEGACY_SETTLE_SEC` = 1.0 |
| 同窗有平仓 → **一律先平后开** | 平仓一次 + 最新开仓（不忽略开仓） |
| 优先级 P0>P1 | 平仓（`CLOSE_QUICK_EXIT`/`CLOSE_RSI_EXIT`）> 开仓（`LONG`/`SHORT`） |
| 平仓幂等 | `collapse_batch_for_execution` |
| 开仓取最新 | 同窗口多条开仓只执行 `entry_msgs[-1]` |
| 60s 去重 | `SIGNAL_DEDUP_SEC`：同 action+symbol+price（平/开成功后清指纹） |
| 开仓前强制清仓 | `_sterile_flat_gate` / `_full_reentry` |
| 超时兜底 | settle 到期立即冲刷 |

一句话：收到 TV 消息缓存 1.0 秒 → 先平一次 → 再开最新一条；开仓第一步强制清仓。

币安 / 深币共用同一套逻辑。

---

## 以下为历史设计存档（仅供参考，勿按此部署）

> 早期 profit_taker / 40-40-20 / EQUITY_20PCT_X5 等设计均已被 v15 需求 supersede。

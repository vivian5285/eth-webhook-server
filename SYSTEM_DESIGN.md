# ETH Webhook Trading System - 系统设计文档

> **当前生产架构**见 [`README.md`](README.md)：  
> **TV v6.5.6** · **VPS v15.0.4-tv-flat-ladder** · sizing **RISK20_NOTIONAL5** · 阶梯雷达 · `position_supervisor_binance.py` 唯一大脑。

---

## 当前有效架构（2026-07 · v15.0.4-tv-flat-ladder）

```
TradingView v6.5.6 Alert (token=528586)
        ↓
app.py (网关 + health: RISK20_NOTIONAL5 / fixed_5)
        ↓
position_supervisor_binance.py   ← 唯一生产大脑
├── TV 消息缓存 1s + 先平后开折叠（tv_seq.py）
├── 仓位：风险20%/止损距 ∩ 名义×5 ∩ TV.qty
├── TP 30/30/40，只挂 TP1+TP2（TP3 交雷达）
├── stop_loss closePosition + 阶梯 radar（TP1路程85%激活）
├── RECONCILE 对账+调止损 / FLATTEN 快平
├── ATR 5min 刷新 · 挂单 5min 超时 · 60s 去重
├── 开仓前强制清仓（无菌空仓闸）
├── 重启方向背离 → trading_paused
└── dingtalk.report_tv_reconcile
```

静态自查：`python check_vps_logic.py` · 清单：`docs/VPS实盘检查清单.md`

---

## TV 消息顺序处理（VPS 铁律）

同一根 K 线 TV 可能连发多条（开仓 / `CLOSE_QUICK_EXIT` / `CLOSE_RSI_EXIT`），网络到达顺序不保证。

| 规则 | 实现 |
|------|------|
| 缓存窗口 1~2s | `SAME_BAR_SETTLE_SEC` / `LEGACY_SETTLE_SEC`（默认 1.0） |
| 优先级 P0>P1 | 平仓（`CLOSE_QUICK_EXIT`/`CLOSE_RSI_EXIT`）> 开仓（`LONG`/`SHORT`） |
| 平仓幂等 | `collapse_batch_for_execution` 窗口内只执行一条平仓 |
| 开仓取最新 | 同窗口多条开仓只执行 `entry_msgs[-1]` |
| 60s 去重 | `SIGNAL_DEDUP_SEC`：同 action+symbol+price |
| 开仓前强制清仓 | `_sterile_flat_gate` / `_full_reentry`（最终安全网） |
| 超时兜底 | settle 到期立即冲刷，不无限等待 |

一句话：收到 TV 消息不立即执行 → 缓存约 1 秒 → 先平一次 → 再开最新一条；开仓第一步强制清仓。

币安 / 深币共用同一套逻辑。

---

## 以下为历史设计存档（仅供参考，勿按此部署）

> 早期 profit_taker / 40-40-20 / EQUITY_20PCT_X5 等设计均已被 v15 需求 supersede。

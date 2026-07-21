# ETH Webhook Trading System - 系统设计文档

> **当前生产架构**见 [`README.md`](README.md)：  
> **TV v6.5.6** · **VPS v15.0.7-tp123-hang** · sizing **RISK20_NOTIONAL5** · 阶梯雷达 · `position_supervisor_binance.py` 唯一大脑。

---

## 当前有效架构（2026-07 · v15.0.7-tp123-hang）

```
TradingView v6.5.6 Alert (token=528586)
        ↓
app.py (网关 + health: RISK20_NOTIONAL5 / fixed_5)
        ↓
position_supervisor_binance.py   ← 唯一生产大脑
├── TV 消息缓存固定 1.0s + 同窗先平后开折叠（tv_seq.py）
├── 订单 ID 持久化（TP1/TP2/TP3/止损）
├── 仓位：风险20%/止损距 ∩ 名义×5 ∩ TV.qty
├── TP 30/30/40 硬编码，挂 TP1+TP2+TP3 限价（价格用 TV）
├── 限价止盈 vs 雷达止损并行，先到先得
├── stop_loss closePosition + 阶梯 radar（TP1路程85%激活）
├── RECONCILE 对账+调止损 / FLATTEN 快平
├── ATR 5min 刷新 · 挂单 5min 超时 · 60s 去重(action+symbol+price) · 哨兵 0.5s
├── 开仓前强制清仓（无菌空仓闸）
├── 无持久化有仓 → trading_paused
├── 日志按日滚动保留 30 天
└── dingtalk.report_tv_reconcile
```

静态自查：`python check_vps_logic.py` · 清单：`docs/VPS实盘检查清单.md`

---

## TV 消息顺序处理（VPS 铁律）

| 规则 | 实现 |
|------|------|
| 缓存窗口 **固定 1.0s** | `SAME_BAR_SETTLE_SEC` / `LEGACY_SETTLE_SEC` = 1.0 |
| ~~有平仓则忽略开仓~~（**已废弃**） | — |
| 同窗有平仓 → **一律先平后开** | 平仓一次 + 最新开仓；开仓内强制清仓兜底 |
| 60s 去重 | **保持** `action+symbol+price` |
| TP 分腿 | **硬编码 30/30/40**，挂三档限价；不依赖 TV qty1/2/3 |

币安 / 深币共用同一套逻辑（哨兵统一 **0.5s**）。

---

## 以下为历史设计存档（仅供参考，勿按此部署）

> 早期「只挂 TP1+TP2、TP3 永不挂限价」已被 v15.0.7 需求 supersede。

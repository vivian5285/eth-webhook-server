# ETH Webhook Trading System - 系统设计文档

> **唯一权威**：见 [`README.md`](README.md)  
> **TV v6.5.6** · **VPS `v15.9.3-prod-gate`** · sizing **RISK20_NOTIONAL5** · 三层防线 · `position_supervisor_binance.py` 唯一大脑。

---

## 当前有效架构（2026-07 · v15.9.3-prod-gate）

```
TradingView v6.5.6 Alert (secret)
        ↓
app.py (网关 + health: RISK20_NOTIONAL5 / fixed_5)
        ↓
position_supervisor_binance.py   ← 唯一生产大脑
├── TV 消息缓存固定 1.0s + 15s OPEN/CLOSE 铁律（tv_seq.py）
├── 订单标签持久化（TP1/TP2/TP3/HARD/RADAR · SHA-256 clientOrderId）
├── 仓位：名义=权益×20%×5 / 价（可选 SL/TV.qty 收紧）
├── TP 10/20/70 常挂三级限价；TP3↔雷达 exit_ownership 互斥
├── 永久硬止损 |TV−SL|×1.2 @ 成交价 + 独立雷达呼吸止损
├── 挂单 fail-closed · 硬上限 5 · 竞态/限流 → trading_paused
├── 档位 config/reentry_tiers.json · 30s 状态快照 · ops_log 四级
├── Webhook 仅 LONG/SHORT/CLOSE_QUICK_EXIT/CLOSE_RSI_EXIT（+PING）
├── CAP_ALIGN 已废除；保留 HARD_SL_FAIL_ABORT / FORCE_ALIGN
└── 开仓永远先平后开（无菌空仓闸）
```

静态自查：`python check_vps_logic.py`

币安 / 深币共用同一套逻辑。

---

## TV 消息顺序处理（VPS 铁律）

| 规则 | 实现 |
|------|------|
| 缓存窗口 **固定 1.0s** | `SAME_BAR_SETTLE_SEC` / `LEGACY_SETTLE_SEC` = 1.0 |
| 同窗有平仓 → **一律先平后开** | 平仓一次 + 最新开仓 |
| 60s 去重 | `action+symbol+price` |
| TP 分腿 | **10/20/70**，常挂 TP1+TP2+TP3 |

---

## 四条硬性原则

1. 开仓永远先平后开
2. 单仓位，不加仓（pyramiding=1）
3. 下单数量每次独立计算
4. 宁可错过，不要做错（查不到单绝不狂挂）

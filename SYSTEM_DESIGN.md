# ETH Webhook Trading System - 系统设计文档

> **唯一权威**：见 [`README.md`](README.md)  
> **TV v6.5.6** · **VPS v15.5.1-qty-tv-sl-adj** · sizing **RISK20_NOTIONAL5** · 呼吸止损 · `position_supervisor_binance.py` 唯一大脑。

---

## 当前有效架构（2026-07 · v15.5.1-qty-tv-sl-adj）

```
TradingView v6.5.6 Alert (token=528586)
        ↓
app.py (网关 + health: RISK20_NOTIONAL5 / fixed_5)
        ↓
position_supervisor_binance.py   ← 唯一生产大脑
├── TV 消息缓存固定 1.0s + 同窗先平后开折叠（tv_seq.py）
├── 订单 ID 持久化（TP1/TP2/止损；不挂 TP3）
├── 仓位：风险20%/VPS止损距 ∩ 名义×5 ∩ TV.qty×(TV距/VPS距)
├── TP 30/30/40 比例；盘口只挂 TP1+TP2；余仓40%交阶段二
├── 呼吸止损唯一写止损（entry±1.5×ATR → 0.75/0.4 阶梯 → ADX 1.2~2.5）
├── Webhook 仅 LONG/SHORT/CLOSE_QUICK_EXIT/CLOSE_RSI_EXIT（+PING）
├── 行情引擎：30m×3→90m Wilder ATR/ADX(14)；webhook 不读 atr/adx
├── CAP_ALIGN 已废除；保留 HARD_SL_FAIL_ABORT / FORCE_ALIGN
├── 旧 schema（缺 initial_stop/open_atr/breakeven_phase）→ 暂停，不自动转换
├── ATR 随 90m 闭合刷新 · 挂单 5min 超时 · 60s 去重 · 哨兵 0.5s
└── 开仓永远先平后开（无菌空仓闸）
```

静态自查：`python check_vps_logic.py`

币安 / 深币共用同一套逻辑（哨兵统一 **0.5s**）。

---

## TV 消息顺序处理（VPS 铁律）

| 规则 | 实现 |
|------|------|
| 缓存窗口 **固定 1.0s** | `SAME_BAR_SETTLE_SEC` / `LEGACY_SETTLE_SEC` = 1.0 |
| 同窗有平仓 → **一律先平后开** | 平仓一次 + 最新开仓；开仓内强制清仓兜底 |
| 60s 去重 | `action+symbol`（同窗折叠另计） |
| TP 分腿 | **硬编码 30/30/40**，只挂 TP1+TP2 |

---

## 四条硬性原则

1. 开仓永远先平后开
2. 单仓位，不加仓（pyramiding=1）
3. 下单数量每次独立计算，无状态
4. 止损单全局唯一写入方 = 呼吸止损引擎

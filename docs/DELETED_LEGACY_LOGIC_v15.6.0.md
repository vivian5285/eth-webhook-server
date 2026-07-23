# 已删除 / 已废除旧逻辑清单（续 · v15.6.0-two-scenario）

交付日期: 2026-07-23  
前置: `docs/DELETED_LEGACY_LOGIC_v15.5.13.md`

## 本轮相对 v15.5.x 废除 / 改写

| 旧逻辑 | 状态 | 说明 |
|--------|------|------|
| TV.atr **永久**锁定 initial_atr，1h ATR 仅作呼吸系数 | **改写** | 场景一：VPS 原生 1h 真实 ATR = initial_atr；场景二才用 TV.atr |
| 「永不挂 TP3」绝对化 | **改写** | 场景一不挂；场景二挂 TP3 兜底；恢复场景一时撤 TP3 |
| `ATR_DEGRADE_MANUAL_RESUME` 成交后暂停 symbol | **废除** | 两场景定稿：不暂停、不裸奔；失败走场景二 |
| 开仓直接挂 entry±1.5×ATR 为第一笔止损 | **改写** | 共同第一步先挂 \|entry−TV.stop_loss\|×1.2 临时硬止损 |
| webhook qty1/2/3 驱动 TP 切片 | **废除主路径** | TP 数量固定 30/30/40（相对 VPS 实开） |
| 90m 合成作止损权威 | **仍非权威** | 原生 1h K 线算 ATR；90m 仅对比/ADX 日志 |

## 两场景要点

1. 成交后：临时止损 + TP1/TP2 → 同步拉 1h ATR  
2. 场景一成功：真实 ATR 重算 initialStop，不挂 TP3  
3. 场景二失败：TV.atr + 挂 TP3；tick 可持续恢复场景一并撤 TP3  

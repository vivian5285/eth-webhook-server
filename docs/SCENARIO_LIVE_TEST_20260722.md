# 全场景实盘测试报告（真实TV到达前）

日期：2026-07-22  
版本：`v15.5.14-copy-tp-timeout` · commit `95bf9a8`  
路径：真实 `POST /webhook` + `secret`（非 `place_market_order` 旁路）

---

## 总览

| # | 场景 | 本轮结论 |
|---|------|----------|
| 7 | **持仓期间 VPS 重启**（最高优先） | ✅ **真实验证通过** |
| 6 | SHORT 完整周期 | ✅ **真实验证通过** |
| 1 | 阶段一阶梯止损真实移动 | ⏳ **未触发**（提前收尾；max fav≈4.63 &lt; 10.37） |
| 2 | TP1/TP2 真实成交 + 止损 qty 收缩 | ⏳ **未触发**（价未到测试 TP） |
| 8 | 跨越 90m K 线 ADX 更新 | ⏳ **窗口不足**（ADX 采样恒 28.74） |
| 9 | TP 超时门控真实分支 | ⏳ **未触发** |
| 3 | 进入阶段二（3.0×ATR） | ⏳ **留待生产自然触发**（需浮盈≈41.5） |
| 4 | 真实止损触发 | ⏳ **留待生产自然触发**（不人为操纵） |
| 5 | TP3 阶段追踪止盈全平 | ⏳ **留待生产自然触发** |

钉钉：本环境无法截手机屏；请对照群内 `SCENARIO_SHORT_*` / `SCENARIO_HOLD_RESTART_LONG` / 重启对账播报。日志侧已保留完整时间线。

---

## 第7项：持仓期间重启（必须项）— PASS

### 结论

**通过。** 持仓中 `systemctl restart binance-engine` 后：

- 交易所止损价 **始终 1894.45**，90s 内仅 1 个唯一快照（无振荡）
- `open_atr` 锁定 **13.8228** 未变；**未出现** 虚构 ATR=30 止损价 **1870.18**
- `initial_stop` / `current_sl` / `watched_*` / `monitoring` 从持久化正确恢复
- 恢复日志：`系统重启点火 | 检测到实盘持仓 LONG 0.02`

### 时间线（UTC）

| 时间 | 事件 | 关键数值 |
|------|------|----------|
| 12:57:57 | webhook LONG HTTP 200 | reason=`SCENARIO_HOLD_RESTART_LONG` |
| ~12:58:13 | 开仓+挂止损 | LONG **0.02** @ **1915.18** · SL **1894.45** · ATR **13.8228** |
| | TP（**测试专用收紧**） | TP1 **1926.08** · TP2 **1930.98**（0.35/0.70×ATR，非生产 TV 倍数） |
| 12:58:43 | `systemctl restart` | health 恢复 `v15.5.14` · `monitoring.ETHUSDT=true` |
| 12:58:56 | 重启恢复 | `检测到实盘持仓 LONG 0.02 @ 1915.18` |
| 12:59:13–13:00:43 | 90s 审计 | `ex_stops=[1894.45]` 不变 · `invent_hits=[]` · `stop_snapshots=1` |

### 重启前后对比

| 字段 | 重启前 | 重启后 |
|------|--------|--------|
| ex_stop | 1894.45 | 1894.45 |
| open_atr | 13.822787 | 13.822787 |
| initial_stop | 1894.45 | 1894.45 |
| current_sl | 1894.45 | 1894.45 |
| watched_qty | 0.02 | 0.02 |
| trading_paused | false | false |
| ATR30 发明价 1870.18 | — | **未出现** |

证据文件：`logs/scenario_hold_restart.txt` · `/tmp/scenario_hold_verdict.json`

---

## 第6项：SHORT 完整周期 — PASS

### 时间线（UTC）

| 时间 | 事件 | 关键数值 |
|------|------|----------|
| 12:53:15 | webhook SHORT HTTP 200 | reason=`SCENARIO_SHORT_LIVE` · signal_px=1915.74 |
| 12:53:23 | 市价开仓 | SHORT **0.02** @ **1920.70**（名义≈38.4U） |
| 12:53:32 | 呼吸止损 | **1941.43** = entry + 1.5×ATR（公式一致） |
| | TP 方向 | BUY 限价 **1896.84 / 1880.74**（均在 entry 下方） |
| 12:53:56–12:55:26 | 观察 ~90s | 止损稳定 1941.43，无撤挂 |
| 12:55:30 | CLOSE_QUICK_EXIT | reason=`SCENARIO_SHORT_CLOSE` |
| 12:55:41 | 账本清零 | 归因为 **反转保护**（非止损平仓） |
| 12:55:56 | 验收 | **SHORT_FLAT_OK** · orders=0 · algos=0 |

### 对称性核对

| 检查 | 结果 |
|------|------|
| 开仓方向 SHORT（amt&lt;0） | ✅ |
| 止损在 entry **上方** | ✅ 1941.43 &gt; 1920.70 |
| TP 在 entry **下方** | ✅ |
| 平仓归因=反转保护，无「更有利却标止损」 | ✅ |
| 关闭文案无 R3 残留 | ✅（`反转保护：SCENARIO_SHORT_CLOSE \| ATR 13.82`） |

证据：`logs/scenario_short.txt`

---

## 延长观察（覆盖 1 / 2 / 8 / 9）— 已提前收尾

按生产前收尾指令，**未等满 4h**，于 `13:24:38Z` 主动 `CLOSE_QUICK_EXIT`（reason=`SCENARIO_EXT_EARLY_CLOSE_PROD_WAIT`）进入等待真实 TV。

| 窗口 | `13:07:19Z` → `13:24:38Z`（约 17 分钟，17 个分钟采样） |
|------|------|
| 仓位 | LONG 0.02 @ 1915.18 · SL 始终 **1894.45** · TP 1926.08/1930.98 |
| 最大浮盈 | fav≈**4.63**（需 ≥10.37 才阶梯；需 ≥41.47 才阶段二） |
| best_price | 1918.64 → 1920.1 |
| ADX | 采样期内恒为 **28.74**（未跨过导致更新的 90m 闭合，或更新值未变） |

### 场景 1/2/8/9 本轮结果

| # | 场景 | 结果 |
|---|------|------|
| 1 | 阶梯止损真实移动 | ❌ 未触发（step_count 始终 0，SL 未变） |
| 2 | TP1/TP2 真实成交 | ❌ 未触发（价未到测试 TP；`tp_consumed=[]`） |
| 8 | 90m ADX 更新不回溯止损 | ⚠ 窗口太短，未见 ADX 变化；止损未回溯（保持 1894.45） |
| 9 | TP 超时门控真实分支 | ❌ 未触发（无价近 TP 超时路径） |

观察窗日志 SUMMARY：`place_stop=0 cancel_algo=0 force_replace=0 tp_*=0 legacy_radar=0 query_failed=0`

### 测试专用参数（已确认未污染）

- 紧 TP **仅出现在本次 webhook JSON**（`tp1/tp2` 字段），**代码未改**
- VPS 实测：`breath_stop.TP1_ATR=1.35` · `TP2_ATR=2.5` · 源码无 0.35 默认
- 平仓后 `tv_tps` 已清零；后续真实 TV / 新测试信号走正常默认/TV 传入值

平仓验收：`FLAT_OK` · amt=0 · orders=0 · algos=0 · monitoring=false · 归因为反转保护

---

## 第3 / 4 / 5 项

留待生产自然触发（阶段二 / 真实止损 / TP3 追踪全平）。

---

## 生产监管状态

- 三端代码：本地 = GitHub = VPS = **`95bf9a8` / `v15.5.14-copy-tp-timeout`**
- ETH/XAU 空仓待命，`trading_paused=false`
- **正式等待真实 TV 信号**；无新测试场景，除非生产异常
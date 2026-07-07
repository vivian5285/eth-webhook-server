# 币安 Binance · ETH 永续 Webhook 交易系统

**当前版本：`v13.8.7-radar-handoff`**

TradingView Webhook → 币安 **ETHUSDT 永续** 自动化引擎。与深币 VPS **实盘逻辑完全对齐**（仅单位 / 交易所 API 不同），按 **ETH** 计量，**15 倍杠杆**，钉钉 **黄金主题**。

---

## 目录

1. [VPS 部署信息](#vps-部署信息)
2. [系统架构](#系统架构)
3. [生产模块 vs 遗留模块](#生产模块-vs-遗留模块)
4. [实盘全链路逻辑](#实盘全链路逻辑)
5. [双轨防线：10% 硬止损 + 雷达移动保本](#双轨防线10-硬止损--雷达移动保本)
6. [同向智能筛选](#同向智能筛选)
7. [哨兵循环](#哨兵循环)
8. [重启闪电接管](#重启闪电接管)
9. [钉钉推送链条](#钉钉推送链条)
10. [四档 Regime 矩阵](#四档-regime-矩阵)
11. [仓位预算公式](#仓位预算公式)
12. [持久化状态](#持久化状态)
13. [TradingView Webhook](#tradingview-webhook)
14. [环境变量](#环境变量)
15. [VPS 部署](#vps-部署)
16. [日志与审计](#日志与审计)
17. [与深币系统对比](#与深币系统对比)
18. [版本演进](#版本演进)
19. [注意事项](#注意事项)

---

## VPS 部署信息

| 项目 | 值 |
|------|-----|
| GitHub | `vivian5285/eth-webhook-server` |
| VPS 目录 | `~/binance-engine` |
| 端口 | **5003** |
| 杠杆 | **15x**（开仓前自动 `set-leverage`） |
| 健康检查 | `GET /health` |
| 主日志 | `logs/binance_brain.log` |
| Gunicorn 日志 | `logs/supervisor_binance.log` |
| 状态文件 | `binance_vps_state.json` |
| 部署脚本 | `bash deploy_binance.sh` |
| Workers | **1** worker × 10 threads（避免多 worker 重复接管） |

**健康检查响应示例：**

```json
{"service":"binance_webhook","status":"ok","version":"v13.8.7-radar-handoff","leverage":15}
```

---

## 系统架构

```
TradingView Alert (JSON)
        ↓
app.py                          ← 网关层：Secret 校验 → 异步线程 → 即时 200
        ↓
position_supervisor_binance.py  ← 智慧大脑（唯一实盘决策层）
├── 信号队列 + 120s 互斥锁（串行执行，防竞态）
├── 同向智能筛选（ATR → 档位 → 价差）
├── 开仓：市价 → TP123 比例对齐 → 10% 硬止损 closePosition
├── 哨兵循环（2~6s 自适应轮询）
│   ├── 浮亏/未达雷达 → 维护 10% 硬止损
│   └── 浮盈达 TP1 激活比 → 撤硬止损 → 雷达移动保本 STOP_MARKET
├── 重启闪电接管 + 全域健康审计 + 防线路由
├── 空仓对账 + 蚂蚁仓扫尾
└── 钉钉全链路核实播报
        ↓
binance_client.py               ← REST 交易 + 公开 WS markPrice
dingtalk.py                     ← 黄金主题钉钉（必须与 supervisor 成对更新）
```

**设计原则：**

- 网关 **不做实盘决策**，只验签、入队、快速应答（避免 TV 超时）
- 所有开仓 / 平仓 / 止盈 / 风控在 **单一信号工作线程** 中串行执行
- 每个关键动作 **先核实盘口** 再推送钉钉
- 硬止损与雷达止损均为 `closePosition` 全平单，**同一方向只能存在一个** → 雷达激活前必须撤净硬止损

---

## 生产模块 vs 遗留模块

| 模块 | 状态 | 说明 |
|------|------|------|
| `app.py` | ✅ 生产 | Flask 网关，端口 5003 |
| `position_supervisor_binance.py` | ✅ 生产 | **唯一** 实盘大脑 |
| `binance_client.py` | ✅ 生产 | 币安 REST + WS |
| `dingtalk.py` | ✅ 生产 | 钉钉播报 |
| `deploy_binance.sh` | ✅ 生产 | 标准部署脚本 |
| `gunicorn.conf.py` | ⚠️ 参考 | 实际部署以 `deploy_binance.sh` 参数为准 |
| `position_supervisor.py` | ❌ 遗留 | 旧版监督层，**未接入** `app.py` |
| `profit_taker.py` | ❌ 遗留 | 旧版 40/40/20 市价 scale-out |
| `order_executor.py` | ❌ 遗留 | 旧版下单封装 |
| `state_manager.py` | ❌ 遗留 | 旧版状态管理 |
| `risk_manager.py` | ❌ 遗留 | 未接入生产链路 |

> `SYSTEM_DESIGN.md` 描述的是 2026-06 旧架构，**以本 README 与 `position_supervisor_binance.py` 为准**。

---

## 实盘全链路逻辑

### 1. 信号总线

| 阶段 | 行为 |
|------|------|
| TV 推送 | `POST /webhook`，JSON 含 `action / regime / atr / price / tv_tp1~3 / secret` |
| 网关 | 校验 `secret` → `threading.Thread` 调用 `handle_signal` → **立即返回 200** |
| 信号队列 | `_signal_worker_loop` 逐条 `_process_signal`（120s 锁超时则重新入队） |
| 状态更新 | 每条信号更新 `regime / current_atr / tv_price / tv_tps`，写入 TV 日志 |

### 2. 信号动作矩阵

| action | 行为 |
|--------|------|
| `LONG` / `SHORT` | 经 **同向智能筛选** 或 **先平后开** 后开仓 / 刷新 |
| `CLOSE` | 换防清场：撤单 → 市价全平 → 再撤单 → 复位状态 |
| `CLOSE_PROTECT` | 保护性全平（盘口已空则撤单复位） |
| `CLOSE_TP3` | TP3 吃满收网，钉钉「完美胜利」 |

### 3. 反向信号（一律先平后开）

持 **多** 收到 `SHORT`，或持 **空** 收到 `LONG`：

```
撤销全部挂单
  → 市价全平
  → 再次撤单清场
  → 空仓验证通过
  → 按 TV 信号市价开仓
  → TP123 比例对齐（核武撤净重挂）
  → 挂 10% 硬止损 closePosition
  → 启动哨兵 + WebSocket
  → 记录 open_regime / open_atr
  → 钉钉 report_supervisor_open
```

**不做** 同向智能筛选；反方向永远原子换防。

### 4. 开仓后防线对齐（`_protect_and_monitor`）

```
current_sl = entry, best_price = entry
shield_active = False, _radar_activation_notified = False
  ↓
核实持仓（超标则裁减至 Regime 目标）
  ↓
_scorched_earth_cancel_for_recover() 撤净旧单
  ↓
_enforce_defense_alignment(rounds=4)
  ├── 按比例挂 TP1/TP2/TP3 reduceOnly 限价
  └── 不挂雷达止损（雷达未激活）
  ↓
_place_shield_stops() → STOP_MARKET closePosition @ entry±10%
  ↓
钉钉 report_supervisor_open + report_adverse_shield_armed（硬止损挂齐后）
  ↓
启动 _sentinel_loop + ethusdt@markPrice@1s WS
```

**TP123 与硬止损的关系：**

- **TP123**：`reduceOnly` 限价止盈，按 Regime 比例拆分仓位（例 R3 → 18% / 32% / 50%）
- **10% 硬止损**：`closePosition=true` 的 STOP_MARKET **全平单**，与 TP123 **不抢 reduceOnly 额度**
- 币安限制：同一方向仅允许 **一个** `closePosition` 止损 → 硬止损与雷达保本互斥

### 5. 平仓清场（`_close_all`）

```
monitoring = False → 停止哨兵
撤销全部挂单（含 TP / 硬止损 / 雷达止损）
市价全平
再次撤单
清空状态文件
钉钉 report_supervisor_close
```

---

## 双轨防线：10% 硬止损 + 雷达移动保本

### 防线状态机

```
                    ┌─────────────────────────────────────┐
                    │           开仓完成                   │
                    │   TP123 限价 + 10% 硬止损全平        │
                    └──────────────┬──────────────────────┘
                                   │
                    价格朝 TP1 方向移动（浮盈）
                                   │
              ┌────────────────────┴────────────────────┐
              │  未达 TP1 × activation 比例              │
              │  状态：SHIELD                           │
              │  → _maintain_hard_shield() 维护硬止损    │
              └────────────────────┬────────────────────┘
                                   │
                    现价 ≥ entry + tp1_dist × activation
                                   │
              ┌────────────────────┴────────────────────┐
              │  达雷达激活条件                         │
              │  状态：FAVORABLE                        │
              │  ① _force_disarm_shield_before_radar() │
              │     撤净 10% 硬止损 + 钉钉 disarmed     │
              │  ② _process_radar_trailing()           │
              │     挂保本 STOP_MARKET closePosition    │
              │     钉钉 report_radar_activated         │
              │  ③ 后续推升/下压止损                     │
              │     钉钉 report_intervention            │
              └─────────────────────────────────────────┘
```

### 硬止损参数

| 常量 | 值 | 含义 |
|------|-----|------|
| `SHIELD_HARD_STOP_PCT` | **10%** | 开仓价 ±10% 触发全平 |
| `SHIELD_TIER_PCTS` | `(0.10,)` | 单档硬止损 |
| 订单类型 | STOP_MARKET + `closePosition` | 全仓一次性平仓 |
| `SHIELD_MAINTAIN_COOLDOWN_SEC` | 60s | 维护冷却，防 API 风暴 |
| `SHIELD_QTY_TOLERANCE_PCT` | 4% | 数量漂移容忍 |

**LONG 示例：** 开仓 1785.96 → 硬止损 @ 1607.36（-10%）

### 雷达激活条件

```
tp1_dist = |tv_tp1 - entry|  （无 TV TP1 时用 ATR×1.5）
activation_price = entry ± tp1_dist × regime.activation

LONG: curr_px >= activation_price → 激活
SHORT: curr_px <= activation_price → 激活
```

| 档位 | activation | 含义 |
|------|------------|------|
| R1 | 40% | 走完 TP1 距离的 40% 即激活雷达 |
| R2 | 50% | |
| R3 | 60% | |
| R4 | 70% | |

### 雷达止损计算

```
trail_offset = ATR × regime.trail_offset
fee_buffer = entry × 0.15%

LONG: SL = max(best_price - trail_offset, entry + fee_buffer)
SHORT: SL = min(best_price + trail_offset, entry - fee_buffer)
```

- `best_price` 由 WS markPrice 实时更新（REST 30s 兜底）
- 推止损时 **只撤 STOP 类订单，保留 TP123 限价**
- 止损变动 ≥ 1 USDT 才触发 `_realign_radar_defenses`

### v13.8.7 雷达交棒强化（`_force_disarm_shield_before_radar`）

**问题：** 旧逻辑仅在 `shield_active` 标志为真时撤单；标志与盘口不一致时，雷达会直接挂 `closePosition`，与硬止损冲突。

**修复顺序（无论内存标志如何，以盘口为准）：**

```
1. _shield_present_on_exchange() 检测盘口是否有硬止损
2. _cancel_stop_orders(scope="shield")
3. _purge_shield_stop_orders() × 最多 2 轮
4. _wait_shield_cleared() 轮询确认（最多 6 次 × 0.35s）
5. 清零 shield_active / shield_tiers_consumed
6. 钉钉 report_shield_disarmed
7. _ensure_radar_sl() 挂雷达前兜底再撤一次（notify=False，防双推）
8. _report_radar_first_activation() → 钉钉 report_radar_activated
```

---

## 同向智能筛选

已有持仓且 TV 方向与实盘 **相同** 时，按 **严格顺序** 决策：

```
┌─────────────────────────────────────────────────────────┐
│  ① ATR 是否变化？（open_atr vs TV atr，偏差 > 3%）       │
│     是 → 先平后开 + 钉钉「刷新仓位 · ATR变化」            │
├─────────────────────────────────────────────────────────┤
│  ② 档位 regime 是否变化？（open_regime vs TV regime）    │
│     是 → 先平后开（保证金/TP比例/雷达参数均变）           │
├─────────────────────────────────────────────────────────┤
│  ③ 理论开仓价差是否 ≥ 0.15%？                           │
│     比较：实盘 entry vs TV price，相对 ETH 现价           │
│     是 → 先平后开 + 钉钉「刷新仓位 · 价差达标」           │
├─────────────────────────────────────────────────────────┤
│  ④ 以上均未触发                                         │
│     → 不重复市价开仓                                     │
│     → 核实当前持仓                                       │
│     → 按新 TV 价刷新 TP123（保留雷达状态）               │
│     → 钉钉「同向持仓 · 仅刷新止盈」                      │
└─────────────────────────────────────────────────────────┘
```

### 空仓短时去重（5 分钟）

盘口 **无持仓** 时，若 5 分钟内再次收到几乎相同的同向信号（ATR 相似 + 同档位 + 价差 < 0.15%）→ **忽略开仓**，钉钉「短时重复同向 · 已忽略」。

### 智能筛选参数

| 常量 | 值 | 含义 |
|------|-----|------|
| `SAME_DIR_MIN_SPREAD_PCT` | 0.15% | 理论开仓价差阈值 |
| `SAME_DIR_DEDUP_SEC` | 300s | 空仓重复信号去重窗口 |
| `ATR_SIMILAR_RATIO` | 3% | ATR 相似判定 |
| `SIGNAL_DEDUP_SEC` | 45s | 信号指纹去重 |

### 先平后开安全闸门（v13.5.2+）

- 使用 **walletBalance（本金）** 计算目标仓位，不用含浮盈的 marginBalance
- 先平后开必须 **空仓验证通过** 才允许新开
- 平仓失败 → **拒绝叠仓** + 钉钉系统告警
- 开仓超标（> 目标 × 110%）→ 自动裁减 + 告警

---

## 哨兵循环

`_sentinel_loop` 在 `monitoring=True` 时运行，自适应轮询：

| 状态 | 轮询间隔 |
|------|----------|
| 常态 | 6s (`SENTINEL_POLL_NORMAL`) |
| 雷达预热（进度 ≥ 50% 或 shield_active） | 3s (`SENTINEL_POLL_ARMING`) |
| 雷达已激活 | 2s (`SENTINEL_POLL_RADAR`) |

**每 tick 执行：**

1. 核实持仓是否存在；归零 → 扫尾 / 钉钉收网
2. 检测 TP 吃完残量（≤ 12% 且无 TP 单）→ 蚂蚁仓扫尾
3. 方向背离（实盘 vs `last_tv_side`）→ 强制清场
4. 更新 `best_price`（WS / REST 现价）
5. 人工加减仓检测 → 按比例重挂 TP123 + 钉钉
6. Regime 档位裁减（超标时 `_radar_enforce_regime_cap`）
7. **`_process_directional_defenses`**：SHIELD 或 FAVORABLE 路由
8. TP 成交检测 → 雷达推进 / 重算 TP123
9. Guardian 定期 TP 审计（叠单 / 缺档 → 核武重挂）

**重启宽限期：** 接管后 45s 内（`SENTINEL_GRACE_AFTER_RECOVER_SEC`）硬止损维护使用 `force=True` 绕过冷却。

---

## 重启闪电接管

进程启动时 `position_supervisor_binance.py` import 即触发 `recover_state_on_startup()`。

### 单例锁（v13.8.6）

- 锁文件：`logs/.recover_singleton.lock`
- 防止 gunicorn 多 worker 重复接管
- 检测锁内 PID 是否存活；`deploy_binance.sh` 清场时删除陈旧锁

### 接管流程

```
读取 binance_vps_state.json + TV 日志 + 开仓日志
  ↓
蚂蚁仓扫描 / 漏报平仓补发
  ↓
有持仓？
  ├─ 否 → 撤单复位 → 钉钉 report_recover_standby
  └─ 是 ↓
      TV 方向对账（背离 / TV=CLOSE → 核武清场）
      人工加减仓检测
      _refresh_radar_state_on_recover()
      Regime 超标裁减
      _enforce_defense_alignment(rounds=4) 核武 TP 对齐
      _build_recover_health_report() 全域审计
      _apply_recover_defense_policy()
        ├─ 浮盈达雷达区 → 撤硬止损 + 补雷达止损
        └─ 浮亏/未达雷达 → 补挂 10% 硬止损
      钉钉 report_recover_takeover（含盈亏态 / 硬止损 / 雷达 / TP 审计）
      启动哨兵 + WS
```

### 健康报告字段

| 字段 | 说明 |
|------|------|
| `pnl_label` | 浮盈·雷达区 / 浮亏 X% / 微盈·未达雷达 / 保本附近 |
| `defense_plan` | 当前应执行的防线策略 |
| `shield_status` | 硬止损挂单位置或待补挂 |
| `radar_progress` | 距 TP1 激活比的进度 |
| `tp_matched/expected` | TP123 对齐档数 |

---

## 钉钉推送链条

所有推送经 `_call_dingtalk()`，**先核实盘口再发送**；旧版 `dingtalk.py` 缺少新参数时自动降级。

### 开仓 / 平仓

| 函数 | 标题 | 触发 |
|------|------|------|
| `report_supervisor_open` | 开仓成功 | 核实持仓 + TP123 对齐后 |
| `report_adverse_shield_armed` | 10%硬止损 · 已挂 | 硬止损 closePosition 核实后 |
| `report_supervisor_close` | 平仓收网 | 空仓验证后 |
| `report_principal_snapshot` | 本金快照 | 开仓预算计算时 |

### 同向智能筛选

| 函数 | 标题 | 触发 |
|------|------|------|
| `report_smart_same_dir_decision` | 同向持仓 · 仅刷新止盈 / 刷新仓位 / 已忽略 | ATR/档位/价差决策后 |

### 硬止损 ↔ 雷达

| 函数 | 标题 | 触发 |
|------|------|------|
| `report_shield_disarmed` | 10%硬止损 · 已撤销（转雷达） | 雷达激活前撤硬止损 |
| `report_radar_activated` | 雷达 · 移动保本已激活 | **首次**雷达激活 + 保本止损核实 |
| `report_intervention` | 雷达推升/下压 | 后续止损移动（120s 冷却） |
| `report_shield_tier_fill` | 10%硬止损 · 成交 | 硬止损触发平仓 |

### 止盈 / 人工 / 重启

| 函数 | 标题 | 触发 |
|------|------|------|
| `report_tp_fill` | TP 成交 | 检测到 TP 限价成交 |
| `report_manual_position_change` | 人工加减仓 | 哨兵 / 重启检测到数量变化 |
| `report_recover_takeover` | 闪电接管报告 | VPS 重启有持仓 |
| `report_recover_standby` | 待命报告 | VPS 重启无持仓 |
| `report_force_align` | 方向强制对齐 | 实盘 vs TV 背离清场 |
| `report_radar_guardian_realigned` | 雷达 Guardian 重挂 | TP 叠单核武后 |
| `report_radar_regime_cap_trim` | 档位裁减 | 超标仓位裁减 |
| `report_system_alert` | 系统告警 | TP 未对齐 / 叠仓拒绝等 |

### 雷达首次激活双推（v13.8.7，预期行为）

1. `report_shield_disarmed` — 硬止损撤净
2. `report_radar_activated` — 保本止损挂出

---

## 四档 Regime 矩阵

| 档位 | 保证金占比 | TP 比例 (1/2/3) | 雷达激活比 | 追踪倍数 (×ATR) |
|------|-----------|-----------------|------------|-----------------|
| R1 | 15% | 25% / 35% / 40% | 40% | 0.40 |
| R2 | 25% | 20% / 35% / 45% | 50% | 0.60 |
| R3 | 35% | 18% / 32% / 50% | 60% | 0.90 |
| R4 | 50% | 5% / 20% / 75% | 70% | 1.30 |

---

## 仓位预算公式

```
保证金 = walletBalance × regime.margin
名义价值 = 保证金 × 15x
目标数量 = 名义价值 ÷ ETH 现价     （步长 0.001 ETH）
```

| 档位 | 保证金占比 | 700U 本金示例（15x） |
|------|-------------|---------------------|
| R1 | 15% | 105U 保证金 → 1575U 名义 |
| R2 | 25% | 175U → 2625U |
| R3 | 35% | 245U → 3675U |
| R4 | 50% | 350U → 5250U |

**裁减规则：** 实盘数量超过目标 × 110% 时触发 Regime Cap 裁减；裁减后重算 TP123。

---

## 持久化状态

文件：`binance_vps_state.json`

| 字段 | 说明 |
|------|------|
| `monitoring` | 是否哨兵运行中 |
| `current_side` | LONG / SHORT |
| `last_tv_side` | TV 最新方向 |
| `watched_qty / watched_entry` | 账本持仓 |
| `initial_qty` | 初始开仓量（TP 比例基准） |
| `open_regime / open_atr` | 开仓时档位与 ATR（同向筛选用） |
| `regime / current_atr` | 最新 TV 档位与 ATR |
| `tv_tps / tv_price` | TV 止盈目标与理论价 |
| `current_sl / best_price` | 雷达止损位与最优价 |
| `shield_active` | 硬止损是否激活 |
| `shield_tiers_consumed` | 已成交硬止损档位 |
| `shield_sized_qty` | 硬止损挂出时的仓位 |
| `sizing_principal` | 开仓预算本金（walletBalance） |

---

## TradingView Webhook

**URL：** `http://你的VPS:5003/webhook`

```json
{
  "action": "LONG",
  "secret": "528586",
  "regime": 3,
  "atr": 30.0,
  "price": 1785.96,
  "tv_tp1": 1810.0,
  "tv_tp2": 1835.0,
  "tv_tp3": 1860.0,
  "reason": "可选说明"
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `action` | ✅ | LONG / SHORT / CLOSE / CLOSE_PROTECT / CLOSE_TP3 |
| `secret` | ✅ | 与 `.env` 中 `WEBHOOK_SECRET` 一致 |
| `regime` | ✅ | 1~4 |
| `atr` | ✅ | TV 策略 ATR |
| `price` | 建议 | TV 理论开仓价（同向筛选用） |
| `tv_tp1~3` | 建议 | TV 止盈目标（TP123 挂单价 + 雷达激活距离） |

---

## 环境变量

```env
BINANCE_API_KEY=
BINANCE_API_SECRET=
WEBHOOK_SECRET=528586
DINGTALK_WEBHOOK=
DINGTALK_SECRET=
FLASK_HOST=0.0.0.0
FLASK_PORT=5003
```

---

## VPS 部署

### 标准流程

```bash
cd ~/binance-engine
git fetch origin && git reset --hard origin/main

# 版本门控
grep v13.8.7-radar-handoff position_supervisor_binance.py app.py
grep -E 'report_radar_activated|report_shield_disarmed|_force_disarm' position_supervisor_binance.py dingtalk.py

pip3 install -r requirements.txt
bash deploy_binance.sh

# 验收
curl -s http://127.0.0.1:5003/health
tail -80 logs/binance_brain.log
```

### deploy_binance.sh 做了什么

1. 加载 `.env`
2. 强制清场：kill 端口 5003 / gunicorn / 删除 recover 锁
3. Python 语法检查（`dingtalk.py` + `position_supervisor_binance.py`）
4. 版本校验（v13.4.6+ / v13.5+ dingtalk 函数）
5. Gunicorn `--daemon` 启动（1 worker × 10 threads）
6. 健康检查重试 6 次

### 部署成功日志示例

```
🟢 Binance Client v13.4.6-flat-reconcile 已加载
🧠 币安 VPS [v13.8.7-radar-handoff] 军师托管版已加载：双轨智慧雷达 · 15x 杠杆
📡 币安公开 WS 启动: ETHUSDT@markPrice@1s
🔄 [系统重启点火] 检测到实盘持仓 LONG 1.234 ETH @ 1785.96 ...
🛡️ [雷达交棒] 雷达激活(进度 100%)，先撤 10% 硬止损 | 撤 1 笔硬止损
📡 雷达首次激活：保本止损 @ 1792.50 | best=1805.00
```

---

## 日志与审计

| 文件 | 说明 |
|------|------|
| `logs/binance_tv_journal.jsonl` | 每条 TV 信号（action/regime/atr/tps/ts） |
| `logs/binance_open_journal.jsonl` | 开仓 / 接管记录 |
| `logs/binance_brain.log` | 大脑主日志（哨兵 / 雷达 / 硬止损 / 对账） |
| `logs/supervisor_binance.log` | Gunicorn 进程日志 |
| `logs/gunicorn_access.log` | HTTP 访问日志 |
| `binance_vps_state.json` | 运行时状态快照 |

**辅助脚本（非生产链路）：**

- `check_system.py` / `check_full_system.py` — 本地自检
- `check_balance.py` — 余额查询
- `generate_report.py` / `daily_report_scheduler.py` — 日报

---

## 与深币系统对比

| 项目 | 币安（本仓库） | 深币 |
|------|---------------|------|
| GitHub | `eth-webhook-server` | `deepcoin-hft-server-main` |
| 单位 | ETH | 张（0.1 ETH/张） |
| 杠杆 | 15x | 15x |
| 端口 | 5003 | 5004 |
| 钉钉主题 | 黄金 | 紫金 |
| 硬止损 | STOP_MARKET closePosition | 条件触发单 |
| 雷达止损 | STOP_MARKET closePosition | 触发止损单 |
| WS 频道 | markPrice@1s | market-latest |
| 同向智能筛选 | ✅ | ✅（逻辑一致） |
| 10% 硬止损 + 雷达交棒 | ✅ v13.8.7 | ✅ v13.8.7 |
| 蚂蚁仓阈值 | ≤ 0.004 ETH | ≤ 1 张 |

---

## 版本演进

| 版本 | 要点 |
|------|------|
| v13.3-smart-guard | TV/开仓日志、比例审计、增量补挂 |
| v13.4-nuclear-guard | 核武清场重挂 |
| v13.4.3-ws-radar | WS 推价 + REST 限频兜底 |
| v13.4.6-flat-reconcile | 空仓对账、蚂蚁仓扫尾、宕机补发钉钉 |
| v13.5.0-smart-same-dir | 同向价差/档位筛选、信号队列 |
| v13.5.1-atr-priority | ATR 第一优先级、open_atr 持久化 |
| v13.5.2-flat-gate | 空仓闸门、walletBalance 预算、叠仓拒绝 |
| v13.8.2 | 杠杆 15x |
| v13.8.4 | 重启全域健康审计 + 浮盈/浮亏防线路由 |
| v13.8.5 | 硬止损改 closePosition；recover 审计重试 |
| v13.8.6 | recover 锁 PID 存活检测；deploy 清陈旧锁 |
| **v13.8.7-radar-handoff** | **雷达激活前强制撤 10% 硬止损；首次激活钉钉 report_radar_activated** |

---

## 注意事项

1. **成对更新：** 部署务必 `git reset --hard origin/main`，且 **supervisor + dingtalk 同步更新**。
2. **单 worker：** 生产使用 1 gunicorn worker，避免重复 recover / 重复哨兵。
3. **硬止损 vs 雷达：** 同一方向只能有一个 closePosition 止损；雷达激活前必须撤净硬止损（v13.8.7 强制交棒）。
4. **TP123 vs 硬止损：** TP 是 reduceOnly 限价；硬止损是 closePosition 全平，**互不抢额度**。
5. **仅单方向持仓：** 反向永远先平后开；同向走智能筛选链。
6. **建议配置：** One-way 持仓模式，ETHUSDT 永续，API 需合约交易权限。
7. **重启钉钉：** 应收到「闪电接管报告」，含 TP 比例审计、硬止损状态、雷达进度。
8. **雷达激活钉钉：** 预期连续两条——硬止损撤销 + 雷达移动保本已激活。

---

*Quant AI · 币安黄金趋势大波段引擎 · v13.8.7-radar-handoff*

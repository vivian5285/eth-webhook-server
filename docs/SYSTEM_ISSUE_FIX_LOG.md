# 系统问题与修复日志（币安单账户）

> **用途**：记录实盘曾出现的故障、根因与防护，方便以后排查时对照。  
> **权威规格**仍以白皮书 / README 为准；本文件只记「出事 → 怎么堵」。  
> README 入口见「十四、生产监管状态」表格。

---

## 怎么用

1. 新事故：在本文件 **顶部**（最新在上）加一节 `## YYYY-MM-DD · 短标题`  
2. 每节固定四块：**现象 / 根因 / 修复 / 复查点**  
3. 版本号与 `BINANCE_VPS_VERSION` 对齐  

---

## 2026-07-26 · 本地防御标签永久拒挂 → 核武后 TP=0 / 雷达无法上移（v16.4.5）

### 现象
- 盘口限价止盈「又没了」；核武清场后 journal 连续 `新挂 0 笔限价止盈`。
- TP1+TP2 成交后雷达目标已到（如 `@1907`），但一直 `HARD_SL_FAIL_ABORT`，盘口雷达腿卡在旧价（如 `1895`）。
- 刷屏：`本地未完成标签 tag=DERADAR… → 拒挂雷达止损`。

### 根因
1. 下单成功后标签写成 `status=open` + `orderId`，但 `_has_open_pending_defense_tag` **把 open 也当飞行中** → **永久拒挂**同 kind。
2. 核武 `_cancel_all_tp_limit_orders` 撤掉 TP 后 **未清 TP 标签** → 补挂同样被拒 → **撤光却挂不回**。
3. 成交记账循环到 TP3：`PLACE_TP_LEVELS=2` 时仍可能写出 `consumed=[1,2,3]`（TP3 从未挂限价）。

### 修复（v16.4.5-pending-tag-gc）
- 仅 `pending` 且无 `orderId` 拦截再挂；已落地标签 GC 掉
- 撤 TP / 核武前强制清 TP 标签 + GC
- 哨兵每轮 GC；`consumed` 封顶到 `PLACE_TP_LEVELS`
- 深币 v13.82：修 `_tp_audit_ok` 误要求 3 档；核武补挂=0 时紧急再补

### 复查点
- [ ] `/health` version ≥ `v16.4.5-pending-tag-gc`
- [ ] 有仓时不应再刷 `本地未完成标签…拒挂雷达`
- [ ] 雷达目标上移后盘口 STOP 应跟随（允许短暂保留旧硬止损腿）
- [ ] 核武/重启后 `新挂 N 笔` 且 N≥应挂档；禁止连续三轮 `新挂 0`
- [ ] `tp_levels_consumed` 在 PLACE=2 时最多 `[1,2]`

---

## 2026-07-26 · TP2 已成交但雷达不启动（v16.4.4）

### 现象
TP1+TP2 限价都成交、现价已过激活中点/TP2，盘口只剩硬止损，呼吸雷达未挂、无激活通知。

### 根因
哨兵 `_process_directional_defenses` 仅在 `_should_radar_trail or _is_radar_active` 时才进 `_process_radar_trailing`；  
而休眠时 `_should_radar_trail≡False` → **永远调不到** `_maybe_arm_radar_on_activation`。  
另有「现价已达激活线」分支**只打日志不武装**。

### 修复（v16.4.4-radar-arm-fix）
- 休眠时每轮先 `_maybe_arm_radar_on_activation`
- TP1+TP2 已成交 / 价过 TP2 / 过激活线 → `_force_arm_radar_after_tp` 兜底
- 哨兵「价达激活线」改为真正调用武装，而非只 log

### 复查点
- [ ] 价过 `(TP1+TP2)/2` 后 journal 出现 `雷达已激活`
- [ ] 盘口除硬止损外另有雷达 STOP（qty=剩余仓，非 closePosition 或独立腿）
- [ ] TP2 成交后即使重启，也应在哨兵一轮内重新武装

---

## 2026-07-26 · REST 过密打爆 IP（v16.4.3 继续降速）

### 现象
即使已有 pause 休眠，哨兵仍曾以 **~1s** 轮询；双品种 + 查仓/查单很容易逼近 2400/min。

### 修复（v16.4.3-slow-rest）
| 项 | 旧 | 新 |
|----|----|----|
| 哨兵常态 | 1s | **8s** |
| 哨兵雷达 | 1s | **4s** |
| 空闲巡检 | 45s | **90s** |
| 持仓 REST 对账 | 30s | **90s** |
| 限流退避 | 120–180s | **300s** |
| 单品种 REST 间隔 | 100ms | **350ms** |
| 全账户 REST 间隔 | 无 | **250ms** |

原则：**价格/成交走 WebSocket**；REST 只做低频核对与下单。

---

## 2026-07-26 · API 限流雪崩 + TP1 后全仓堆 TP2（v16.4.1 → v16.4.2）

### 现象
- TV 只发了 **ETHUSDT.P LONG**，TG 却报 **`API限流暂停 [XAUUSDT]`**（随后 ETH 也暂停）。  
- 开仓时 TP1+TP2 正常；TP1 成交后盘口只剩 **1 张限价**，且数量一度变成 **整笔现仓**（本应 ≈开仓 20%）。  
- Journal 刷屏：`拒认 TP1 假成交 … base=live=0.842 → 视为漏挂`（约 3 分钟近百条）。  
- 币安 `-1003`：同 IP `2400 requests/min`。

### 根因（链条）
1. **IP 共享配额**：ETH 哨兵 + XAU 空闲巡检共用 `187.77.130.144`；谁先撞 `-1003` 谁发告警 → **不是 TV 发了 XAU**。  
2. **`trading_paused` 未挡住哨兵 REST**：暂停后仍对账/补挂/查挂单 → 限流死亡螺旋 + 告警轰炸。  
3. **开仓基线被压扁**：`_resync_tp_baseline` 在「非 TP 减仓」路径把 `initial_qty` 写成现仓；TP1 成交后 `base==live` → 永远无减仓证据 → 死循环「漏挂」。  
4. **单档剩余拆量 bug**：`_split_remaining_tp_quantities` 在只剩 TP2 时 `return {2: live_qty}` → **全仓堆到 TP2 价**。  
5. **假 TP3 drift**：`PLACE_TP_LEVELS=2` 时限价合计仅 30%，对账却拿合计去比 100% 开仓量 → 误报 `drift≈0.655`。

### 修复
| 版本 | 内容 |
|------|------|
| **v16.4.1** | 暂停/限流时哨兵休眠；空闲巡检跳过；限流告警去重；TP2 按开仓 **20% 绝对量**；核武/补挂/30s 对账在 pause 下跳过 |
| **v16.4.2** | **禁止压扁开仓基线**（监控中只升不降，改记账 TP）；减仓证据优先 `_trusted_initial_qty`；限价档对账按 PLACE 比例；**IP 全局 REST 冷却 180s** + `-1003` 广播 `_GLOBAL` 双品种暂停；哨兵/巡检尊重 `ip_rate_limit_remaining` |

### 复查点（以后出问题先看这些）
- [ ] `/health`：`version` ≥ `v16.4.2`；`trading_paused`  
- [ ] 开仓后 journal：**TP挂出=2**；不应再出现「头寸与TP123偏差 drift=0.65x」类假警报  
- [ ] TP1 成交后：`initial_qty` **仍≈开仓量**；`tp_levels_consumed` 含 `1`；盘口 TP2 qty ≈ **开仓×0.20**（不是现仓全量）  
- [ ] `-1003` 后：两侧都 pause；哨兵日志出现 `哨兵休眠` / `IP限流`；**不应**继续每秒「拒认 TP1」  
- [ ] TG 报 XAU 限流时：先查 ETH 是否也在打 REST，勿误判为「TV 配错品种」

### 相关代码
- `position_supervisor_binance.py`：`_resync_tp_baseline` / `_split_remaining_tp_quantities` / `_pause_symbol_trading` / 哨兵 pause 门闸 / `_reconcile_open_qty_vs_tp123`  
- `binance_client.py`：`mark_ip_rate_limited` / `_note_api_error` `_GLOBAL` 广播 / `_throttle_rest`  

---

## 2026-07-23 · 同价 LIMIT 叠单（历史，详见 README 防叠单专章）

### 现象
`openOrders` 失败时当「无挂单」→ 同价 LIMIT 叠到 50+。

### 防护摘要
fail-closed + 本地标签 + 同价去重 + 硬上限 5。详见 README「防叠单专章」。

---

## 2026-07-22 · 天文 TV.qty / 保证金不足

详见 [`INCIDENT_20260722_HUGE_TV_QTY.md`](INCIDENT_20260722_HUGE_TV_QTY.md)。

---

## 索引（按症状查）

| 症状 | 先看章节 |
|------|----------|
| 本地标签拒挂 / 核武后 TP=0 / 雷达卡旧价 | 2026-07-26 · v16.4.5 |
| TG 报 XAU 限流但 TV 是 ETH | 2026-07-26 |
| 暂停后仍狂打 REST / 告警刷屏 | 2026-07-26 |
| TP1 后只剩一张限价且数量=全仓 | 2026-07-26 |
| 同价几十张 LIMIT | 2026-07-23 / README 防叠单 |
| 开仓 qty 天文 / -2019 | 2026-07-22 |

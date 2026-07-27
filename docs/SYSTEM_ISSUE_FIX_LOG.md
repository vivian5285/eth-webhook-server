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

## 2026-07-27 · 雷达启动 ADX 70%~90%（v16.7.0-radar-adx-act）

### 现象
固定 50% 过早锁保本；等待过晚则 TV 已保本离场而 VPS 仍停在宽硬止损。

### 修复
- 第一层：`ratio=lerp(0.70,0.90,(ADX−17)/(35−17))`；`启动价=entry±ratio×1.35×initial_atr`
- 首次/重入同一公式；开仓冻结 ratio+价；独立于 TP1 成交
- 废除中点/TP2 绝对价主路径；第二层 trail 插值不动
- Deepcoin 同步 `v13.91.0-radar-adx-act`

### 复查点
- [ ] `/health`=`v16.7.0-radar-adx-act`；Deepcoin=`v13.91.0-radar-adx-act`
- [x] 单测 `test_radar_reentry` ADX 边界 + TP1 已成交仍可武装
- [ ] 空仓待命真实 TV（禁止测试 webhook）

---

## 2026-07-27 · API 限流绝对封死（v16.6.2-rate-iron）

### 现象
同 VPS IP 反复 `-1003`（2400/min）；冷却窗内仍有 REST/「等待后重试」；Deepcoin 哨兵 0.5s 狂打；公网 K 线也吃币安配额；人工 resume 加剧雪崩。

### 根因
预算偏松（48/min）+ REST 间隔偏短；账户/名义/K 线绕过节流；Deepcoin 轮询过密；公开 K 线未计入节流阀。

### 修复
- `api_throttle`：默认预算 **24/min**、soft **0.60**、静默 **900s**、acquire 硬间隔 **1.8s**
- `binance_client`：REST≥**2.0/1.5s**；挂单缓存 **45s**；冷却期零 REST；账户概览缓存；K线/名义走节流；`force_rest` 冷却降级
- 哨兵 **45/30/25s**、空闲 **300s**、对账 **300s**（禁周期性 force_rest）
- `/admin/resume` 冷却期默认 **429**（`force=1` 可强开）
- Deepcoin：哨兵 **25s**、公开/私有 REST 硬间隔 + 节流；公网 Binance K 线走 `binance` 节流阀 + 90s 缓存

### 复查点
- [x] `/health`=`v16.6.2-rate-iron`；Deepcoin=`v13.90.2-rate-iron`（已上线）
- [ ] 持续观察日志无新增 `-1003` / `REST 等待` 循环
- [x] ETH 持仓 monitoring=true、trading_paused=false

---

## 2026-07-27 · 流水线补强（开仓+补挂预算闸）（v16.6.1）

### 现象
v16.6.0 首轮督察只在开仓瞬间跑；TP1 成交后补挂 TP2 仍可能把余仓堆进限价。

### 修复
- `_assert_place_tp_budget`：开仓与补挂均过预算闸（剩余比例帽 + 绝对≤35%）
- `chief_auditor` / `tp_slice` 暂停空仓可自动清除
- `get_recent_user_trades` 走节流阀；督察 `hard_sl_live` 禁止「有价=已挂」
- Deepcoin：PLACE=2 硬帽 + TP 自检（`v13.90.1-pipeline`）

### 复查点
- [x] README 流水线岗位表 + 今日问题拦截表
- [ ] `/health`=`v16.6.1-pipeline`；Deepcoin=`v13.90.1-pipeline`
- [ ] 单测 `test_pipeline_workflow` 通过

---

## 2026-07-27 · 全域流水线编制（总账本/督察官/节流阀）（v16.6.0）

### 现象
今日实盘多类故障（TP 切片吞仓、限流螺旋、暂停卡死）共性：模块各凭“私房账本”判断，缺少统一阶段交接与事后复查。

### 根因
无显式状态机与岗位边界；REST 节流虽有，但账号级预算/静默未收口；开仓完成后无自动「领导复查」。

### 修复
- 新模块：`pipeline_ledger.py` / `chief_auditor.py` / `api_throttle.py` / `pipeline_bridge.py`
- 币安 supervisor 挂岗位交接（信号→清场→开仓→挂单→督察→汇报→监控）；`initial_qty` 仅确认阶段写入
- 执行官挂 TP 前强制 30% 自检；督察官硬项（方向/切片/硬止损）失败可暂停
- `binance_client` 账号节流阀与 -1003 静默打通（ETH/XAU 共用）
- 默认 `PIPELINE_SOFT_GATES=1`：非法阶段只记日志，不硬挡现有路径
- Deepcoin（5004）同步同套编制与节流

### 复查点
- [x] `/health` 含 `pipeline` 字段（已上线）
- [x] `python -m unittest test_pipeline_workflow.py` 本地通过
- [x] 持仓中重启后哨兵/雷达仍正常（ETH LONG 恢复）

---

## 2026-07-27 · Console 管理页（多 API 档案 + 热改仓位）（v16.5.0）

### 能力
- `http://IP:5003/console` 苹果毛玻璃 UI；口令 `CONSOLE_PASSWORD`
- 多套币安 API 档案（命名 / 切换 / 更换密钥）；有仓默认禁止切换
- 每档案独立 `risk_pct` + `leverage`，改完下一笔开仓即生效
- Webhook secret、Brain 日志、近 30 日已实现盈亏与胜率

### 复查点
- [ ] `/health` version=`v16.5.0-console`，含 `console:"/console"`
- [ ] 浏览器可打开 Console 并登录
- [ ] 改风险%/杠杆后，新开仓日志出现 `set_leverage=Nx(档案)`

---

## 2026-07-27 · GEMINI对照：TP限价预算硬帽 + 空仓自清暂停（v16.4.8）

### 对照结论（币安单系统 vs 双子星今日清单）

| GEMINI 问题 | 币安状态 |
|-------------|----------|
| open_orders_gt_5 + TG 风暴 | 已有硬帽；**本次**改走统一 `_pause_symbol_trading` 同因去重 |
| -1003 IP 限流螺旋 | 已修（v16.4.6） |
| 空仓后仍暂停 | **本次**可恢复族（限流/挂单帽/本地标签）空仓自动清 |
| initial_qty 压扁 | 已修（v16.4.2） |
| 假 TP3 drift | 已修（v16.4.5） |
| pause/cool 仍 REST | 已修（v16.4.1/6） |
| 雷达余仓责任偏弱 | 已修（v16.4.7） |
| TP1+TP2=整仓（吞掉 70%） | 拆量逻辑 v16.4.1 已按绝对比例；**本次** normalize 硬帽再堵 + 深币 v13.83 同步 |

### 修复（v16.4.8-tp-budget-cap / 深币 v13.83.0-tp-abs-split）
- PLACE=2：限价合计硬帽 ≤ 开仓×(10%+20%)，超帽按比例压回
- `open_orders_cap` 走统一暂停闸（抑制 TG 风暴）
- 空仓自动解除 `api_rate_limit` / `open_orders_cap` / `local_tags`
- 深币：废除「仅余一档=全仓」「最后一档吞 budget」；改绝对 10/20

### 复查点
- [ ] `/health` ≥ `v16.4.8-tp-budget-cap`；深币 ≥ `v13.83.0`
- [ ] 开仓后盘口 TP1+TP2 合计 ≈ 开仓×30%，不得 ≈ 现仓
- [ ] TP1 成交后 TP2 qty ≈ 开仓×20%（不是现仓全量）
- [ ] flat 后 `trading_paused` 对限流/挂单帽类应自清

---

## 2026-07-26~27 · 当日实盘问题总览（币安单系统）

> 给下次排查的「一天速查」。细节见下方各节。

| # | 问题 | 版本 | 一句话 |
|---|------|------|--------|
| 1 | API 限流雪崩 + 暂停后仍打 REST | v16.4.1→2→3→**6** | IP 共享配额撞 `-1003`；冷却曾只睡 5s 仍狂请求 |
| 2 | TP1 后全仓堆到 TP2 | v16.4.1→2 | 单档剩余拆量把现仓全塞进 TP2 |
| 3 | 开仓基线被压扁 →「假漏挂」刷屏 | v16.4.2 | `initial_qty` 被写成现仓，减仓证据永远对不上 |
| 4 | 假 TP3 drift / 误记 consumed=3 | v16.4.2 / **5** | PLACE=2 却按 100% 对账或记到 TP3 |
| 5 | REST 过密（哨兵过快） | v16.4.3→6 | 继续降到 20/15/12s + 硬冷却禁 REST |
| 6 | TP2 成交后雷达假死不武装 | **v16.4.4** | 休眠时调不到 `_maybe_arm` |
| 7 | 本地标签永久拒挂 → 核武后 TP=0、雷达卡旧价 | **v16.4.5** | `open` 标签当飞行中；撤 TP 不清标签 |
| 8 | 无 TP3 时雷达收尾不够机智（地板未生效/数量可落后） | **v16.4.7** | 启用 TP1/TP2 利润地板 + 雷达 qty 贴合现仓 + 收尾区加速追随 |

---

## 2026-07-27 · 雷达收尾增强（无TP3·利润地板+数量贴合）（v16.4.7）

### 现象 / 风险
仅挂 TP1+TP2，余仓 ~70% 全靠雷达；TP 全成/部分成交后若雷达数量不缩、或不锁 TP2 区利润，回吐风险大。

### 根因
1. `tp1_floor_atr` / `tp2_floor_atr` 写在 profile 里但呼吸引擎**未真正应用**。  
2. `_sync_exchange_stop` 同价幂等跳过时**不核对 qty** → 部分成交后雷达腿可残留旧量。  
3. TP 成交路径对「必须武装 + 收尾加速」不够硬。

### 修复（v16.4.7-radar-runner）
- 呼吸引擎：过 TP1/TP2 区强制抬止损到 entry±floor×ATR  
- 哨兵/成交：雷达 qty 与现仓对账；落后则撤雷达腿按现仓重挂（硬止损保留）  
- TP2→TP3 收尾区：更短追随冷却、更小步进；TP 进度强制武装并抬利润地板

### 复查点
- [ ] `/health` ≥ `v16.4.7-radar-runner`
- [ ] TP1 部分/全成后：雷达 STOP qty ≈ 现仓（非开仓全量）
- [ ] 价过 TP2 或 TP1+TP2 记账后：账本 SL ≥ entry+1.5×open_atr（多）
- [ ] 无 TP3 限价属正常；盘口只应有 TP1/TP2 + 硬止损 +（激活后）雷达腿

---

## 2026-07-27 · IP限流死亡螺旋（冷却只睡5s仍打REST）（v16.4.6）

### 现象
实盘频繁 TG/钉钉：`API限流暂停`、持仓/挂单查询失败；冷却名义 300s 却仍反复 `-1003`。

### 根因
`_wait_ip_rate_limit` **最多只 sleep(5s) 就继续请求** → 冷却窗内持续撞墙 → 钩子/告警连环。  
另：每轮 `get_open_orders` = 普通+Algo **两记 REST**，哨兵 4–8s 一轮，双品种共享 IP。

### 修复（v16.4.6-ip-hard-block）
- 冷却期内 **禁止 REST**（`IpRateLimitedError`），只用缓存 / fail-closed
- 挂单短缓存 12s；place/cancel 失效
- REST 间隔 0.80/0.55s；冷却 600s；哨兵 20/15/12s
- 限流钩子 120s 去重；查询失败在冷却/暂停期不告警
- 冷却结束后自动解除 `api_rate_limit` 暂停

### 复查点
- [ ] `/health` ≥ `v16.4.6-ip-hard-block`
- [ ] `-1003` 后日志出现 `拒绝 REST` / `哨兵休眠…禁止REST`，冷却内无新的交易所 REST
- [ ] 同一次限流 TG 不应连环刷屏
- [ ] 冷却结束后自动 `trading_paused=false`（限流类）

---


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
| 雷达收尾不锁利 / 数量不贴现仓 / 无TP3 | 2026-07-27 · v16.4.7 |
| 当日问题一览表 | 2026-07-26~27 · 当日实盘问题总览 |
| 本地标签拒挂 / 核武后 TP=0 / 雷达卡旧价 | 2026-07-26 · v16.4.5 |
| TG 报 XAU 限流但 TV 是 ETH | 2026-07-26 |
| 暂停后仍狂打 REST / 告警刷屏 | 2026-07-26 / v16.4.6 |
| TP1 后只剩一张限价且数量=全仓 | 2026-07-26 |
| 同价几十张 LIMIT | 2026-07-23 / README 防叠单 |
| 开仓 qty 天文 / -2019 | 2026-07-22 |

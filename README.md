# GEMINI 双轨交易工厂 · 统一实盘逻辑

**当前版本：`v13.69.0-tp-price-gone-lanes`**  
**TV 策略 schema：`v6.9.108`**（`webhook_parser.TV_STRATEGY_VERSION`）

TradingView Webhook → 交易所永续自动化引擎。**币安 ETH+XAU** 与 **深币** 两套 VPS 共用同一套「军师大脑」逻辑（`position_supervisor_*.py` 镜像实现），仅 **计量单位 / 交易所 API / 钉钉主题** 不同。

| 工厂 | GitHub | VPS 目录 | 端口 | 品种 | 杠杆 | 钉钉 |
|------|--------|----------|------|------|------|------|
| **币安** | `vivian5285/eth-webhook-server` | `~/binance-engine` | **5003** | ETH + XAU | **25x** | 黄金 |
| **深币** | `vivian5285/deepcoin-hft-server-main` | `~/deepcoin-hft-server` | **5004** | ETH + XAU 张 | **25x** | 紫金 |

**健康检查：**

```bash
curl -s http://127.0.0.1:5003/health   # 币安
curl -s http://127.0.0.1:5004/health   # 深币
# 期望 version 含 v13.69.0-tp-price-gone-lanes
# 期望 tv_strategy: v6.9.108
```

**Cursor / VPS 逻辑自查：**

```bash
python check_vps_logic.py          # 静态对账（无需 API Key）→ 期望全部通过
# 完整清单见 docs/VPS实盘检查清单.md
```

---

## 目录

1. [统一架构](#统一架构)
2. [VPS 实盘检查清单](#vps-实盘检查清单)
3. [防线总线：TP123 + VPS硬止损 + 雷达](#防线总线tp123--vps硬止损--雷达)
4. [开仓裸仓闸（v13.64）](#开仓裸仓闸v1364)
5. [雷达状态机（价触激活线）](#雷达状态机价触激活线)
6. [信号与开仓逻辑](#信号与开仓逻辑)
7. [哨兵 + 空闲巡检](#哨兵--空闲巡检)
8. [重启 / 人工接管](#重启--人工接管)
9. [钉钉推送链条](#钉钉推送链条)
10. [Regime 矩阵](#regime-矩阵对齐-tv-v69108)
11. [VPS 部署与更新](#vps-部署与更新)
12. [日志与排错](#日志与排错)
13. [版本演进](#版本演进)
14. [双工厂差异速查](#双工厂差异速查)

---

## 统一架构

```
TradingView Alert (JSON + symbol + bar_index/seq)
        ↓
app.py                          ← 网关：symbol 路由 → Secret → 异步入队 → 200
        ↓
position_supervisor_binance.py  ← 每品种独立军师（ETH / XAU 互不串单）
├── TV 时序：bar_index↑ → 同 bar 内 seq↑（tv_seq.py 幂等）
├── VPS 自主开仓 sizing（总权益 × 档位 8/14/20/26% × 25x）
├── VPS 自主硬止损（开仓价 × 档位%，tv_sl 仅参考）
├── TP123 限价（TV 价优先；空/不全 → ATR×regime 本地补全）
├── 开仓后：强制挂 TP123 + closePosition 硬止损（禁止裸仓）
├── 雷达待命 → 现价距TP1剩15%(路程85%)或TP1成交后交棒保本
├── 雷达交棒钉钉必达（失败哨兵补发）· 全平 exit_source 归因
├── WS mark@1s + UserData 脉冲哨兵 · TP2/TP3 逐级锁利防回吐
├── 13x 总名义硬顶（双品种合计）
├── 哨兵 5~8s · 开仓宽限 90s（纯缺失补挂优先）· 空闲巡检 12s
└── 钉钉：攒批摘要（5~10s / 满 8 条）+ 失败指数退避 + 企微备用
```

**设计原则（两工厂一致）：**

- **TV 只发信号**：网关不做实盘决策；`symbol` / `ticker` 区分 ETH / XAU；缺品种拒绝默念 ETH
- **时序铁律**：Webhook 带 `bar_index` + `seq` 时，**先 bar 升序、同 bar 内 seq 升序**；严禁按到达时间消费
- **幂等键**：`{symbol}_{bar_index}_{seq}`（Redis `REDIS_URL` 优先，否则 `logs/tv_seq_idempotency.json`，TTL 24h）
- **硬止损 VPS 自主**：`开仓价 × 档位%`（锁定 `open_regime`）；TV `tv_sl` → `tv_sl_ref` **仅日志**
- **开仓档位以 TV OPEN 为准**（v13.63）：禁止 recover / 粘性日志误锁 R4 覆盖本次开仓 R
- **开仓基数 = 账户总权益**（marginBalance），非可用余额
- **雷达统一 85% 启动**（v13.65）：距 TP1 还剩 15% 即交棒；**TP1 成交强制交棒**；WS mark 实时脉冲；废除三重强制门槛
- **硬止损与雷达不抢份额**：二者合并为 **单槽 closePosition**；TP123 用 reduceOnly 限价
- **头寸微漂 ≠ TP1 成交**：伪 TP 仍拦截记账；不挡雷达启动
- **开仓禁止裸仓**（v13.64）：TV 空字段必须本地补全 TP；`expected=0` 不得假齐；硬止损强制终检
- **重启禁止无故平仓**：未达激活线且 TP1 未成交 → 只挂 VPS 宽硬止损
- 雷达交棒：**先挂保本 STOP 核实 → 再撤宽硬止损 → 再钉钉**；随后 TP2/TP3 逐级锁利
- **平仓归因**（v13.67）：以 `_radar_handoff_done` 判定雷达保本，**不以钉钉是否发出为准**；钉钉带 `exit_source`（radar_be / tp3 / vps_hard_sl）
- **TP 成交禁补挂**（v13.68/v13.69）：**价到 + 限价消失 = 成交**（微漂不算）；记账后耐心等 TP23，禁止再挂已成交档
- **三轨不抢份额**：TP123=`reduceOnly`；雷达保本+VPS宽硬止损=`closePosition` 单槽；各自独立
- **时序 1-2-1**（v13.68）：幂等键含 action；CLOSE 后释放开仓键，同 K 线允许再开

### 生产模块 vs 遗留

| 模块 | 币安 | 深币 | 说明 |
|------|------|------|------|
| `app.py` | ✅ | ✅ | Flask 网关 |
| `position_supervisor_*.py` | ✅ | ✅ | **唯一** 实盘大脑 |
| `*_client.py` | ✅ | ✅ | 交易所 API（币安含 Algo closePosition） |
| `dingtalk.py` | ✅ | ✅ | 钉钉播报（攒批+重试） |
| `tv_seq.py` | ✅ | ✅ | TV `bar_index`+`seq` 有序消费/幂等 |
| `webhook_parser.py` | ✅ | ✅ | Regime / 硬止损% / 雷达比例 / enrich |
| `symbol_config.py` | ✅ | ✅ | ETH/XAU 元数据与路由 |
| `check_vps_logic.py` | ✅ | ✅ | 静态逻辑自查 |
| `deploy_*.sh` | ✅ | ✅ | 标准部署 |
| `position_supervisor.py` 等 | ❌ | ❌ | 遗留，未接入生产路径 |

---

## VPS 实盘检查清单

完整清单：[docs/VPS实盘检查清单.md](docs/VPS实盘检查清单.md)

```bash
python check_vps_logic.py    # 7 大模块静态对账（期望全部通过）
```

| 模块 | 要点 | 优先级 |
|------|------|--------|
| 品种路由 | `symbol` / `ticker` → ETHUSDT / XAUUSDT，未知拒绝 | 🔴 P0 |
| 开单 sizing | 总权益 × R1~R4 **8/14/20/26%** × 25x | 🔴 P0 |
| VPS 硬止损 | 开仓价 × **2.78% / 3.89% / 5.56% / 8.33%**，忽略 TV 紧止损 | 🔴 P0 |
| 开仓裸仓闸 | TV 空 TP → ATR 补全；强制 closePosition；终检钉钉 | 🔴 P0 |
| 13x 名义硬顶 | ETH+XAU 合计 ≤ 权益×13 | 🔴 P0 |
| 雷达距TP1剩15%启动 | 全档统一 **85%** 路程；TP1成交强制交棒；TP2/TP3锁利 | 🟡 P1 |
| 钉钉全链路 | 开单/TP/雷达/拦截/平仓/裸仓告警 | 🟢 P2 |

---

## 防线总线：TP123 + VPS硬止损 + 雷达

所有「补挂 / 重启 / 人工同向 / 空闲接管」统一走 `_ensure_full_defense_stack()`：

```
_disarm_premature_radar()     ← 清除伪 TP1 / 过早保本线
  ↓
_reconcile_stale_tp_consumed() ← 账本 TP 标记 vs 实盘数量对账
  ↓
_ensure_tp123_prices_from_tv() ← TV / 日志 / 盘口 / ATR 补全 TP1/2/3
  ↓
_enforce_defense_alignment()   ← TP123 比例限价 reduceOnly（纯缺失优先补挂）
  ↓
_maintain_hard_shield()        ← VPS 宽硬止损 closePosition（雷达激活后合并）
  ↓
[若价触激活线]
  _perform_radar_handoff()     ← 原子雷达交棒
  _process_radar_trailing()    ← TP2/TP3 逐级收紧
```

### TP123

- Regime 比例拆分仓位（例 R3 → **18% / 32% / 50%**）
- `reduceOnly` 限价，与全平硬止损 **不抢额度**（硬止损用 `closePosition`）
- 已成交档位写入 `tp_levels_consumed`，**不再补挂**
- 审计异常（叠单 / 缺档）：
  - **纯缺失** → 增量补挂（v13.62 thrash 刹车，禁止先撤再挂）
  - **严重叠单/偏差** → 核武撤 TP 重挂（有最短间隔退避）
  - **不动**已齐雷达线，除非交棒

### VPS 自主硬止损（开仓价百分比 · closePosition）

- **计算**：`硬止损距离 = 开仓价 × 档位%`（锁定 **`open_regime`**，禁止 UPDATE/recover 改窄）
- **TV `tv_sl`**：仅写入 `tv_sl_ref` 对比日志，**绝不挂盘**
- **执行（币安）**：Algo / 普通通道 **`STOP_MARKET` + `closePosition=true`**（全平保护，不占 reduceOnly）
- **持仓期**：硬止损价位不随波动重算；仅开仓/接管等 source 刷新；雷达交棒后由保本线取代
- **优先级**：`CLOSE_STOPLOSS` 市价全平 > VPS 宽硬止损 / 雷达保本 > 忽略 TV 紧价

| Regime | 档位百分比 | 示例@1800 多头止损 |
|--------|------------|-------------------|
| R1 | **2.78%** | ≈1750.0 |
| R2 | **3.89%** | ≈1730.0 |
| R3 | **5.56%** | ≈1700.0 |
| R4 | **8.33%** | ≈1650.1 |

### 伪 TP1 记账（非雷达门槛）

`_tp1_filled_verified()` 仍用于 **TP 成交记账 / 伪 TP 拦截**（不挡雷达启动）：

1. 价格达 TP1 区
2. 账本已消费 + 盘口无 TP1 限价残留
3. 减仓量匹配 TP1 切片（> 噪声阈值，约开仓量 2%）

**雷达启动**仅看价触激活线；未达激活线却出现保本线 → `_disarm_premature_radar()` / `_enforce_pre_tp1_radar_standby()` 恢复 VPS 宽硬止损。

---

## 开仓裸仓闸（v13.64）

历史事故：TV 推送价/TP 为空时，`tv_tps=[0,0,0]` → `expected=0` 被当成「TP 已齐」跳过挂单；再叠加开仓 90s 宽限挡住 `force=False` 硬止损维护 → **有仓无 TP、无硬止损**。

### 开仓保护路径（`_protect_and_monitor`）

```
市价成交核实持仓
  → _ensure_tp123_prices_from_tv(实盘 entry)   # TV 空 → ATR×regime 合成
  → _refresh_vps_hard_sl(open_regime)         # 账本写入 VPS 宽价
  → cancel_all 残留挂单（仅一次）
  → _enforce_defense_alignment(recover=False) # 挂 TP123
  → _sync_exchange_stop(force=True)           # 强制 closePosition
  → 终检：无 STOP → 再补挂 + 钉钉「裸仓无硬止损」
  → 防线齐才 _mark_defense_align_ok()
  → 再开哨兵宽限 90s（抑制连环核武）
```

### 硬性规则

| 规则 | 说明 |
|------|------|
| `expected=0` ≠ 已齐 | 有仓且 TP 未吃完时 `_tp_audit_ok` 返回 False |
| TV 空价/空 ATR | 用盘口价 + ATR 默认 30 补全 TP（`enrich_entry_tp_prices`） |
| 宽限与挂单顺序 | **先挂后宽限**；宽限内若盘口无保护 STOP 仍允许补挂 |
| 钉钉核实 | `expected=0` 或无硬止损 → `verified=false`，禁止假成功 |
| 持仓核查失败 | 钉钉告警：可能未挂防线，需人工检查 |

### thrash 刹车（v13.62 起，与裸仓闸配合）

- 纯缺失：**补挂优先**，禁止秒撤秒挂
- 核武：最短间隔退避；失败连败加长
- 硬止损：目标已正确时 **force 仍幂等**（不撤再挂）
- 止损审计：默认计入 closePosition（`exclude_shield=False`，v13.62.1）

---

## 雷达状态机（价触激活线）

**核心理念**：**价格朝 TP1 推进达到档位比例即激活雷达保本**；激活线前只有 VPS 宽硬止损。TP 限价成交仅辅助记账与后续锁利。

| 阶段 | 触发条件 | 多头止损 | 空头止损 |
|------|----------|----------|----------|
| 0 | 未达激活线 | VPS 宽硬止损 | 同左 |
| 1 | 价触激活线交棒 | 成本 + 0.1% | 成本 − 0.1% |
| 2 | 价格达 TP1→TP2 50% | 最高价 − ATR×1.0 | 最低价 + ATR×1.0 |
| 3 | 达 TP2 | 最高价 − ATR×0.6 | 最低价 + ATR×0.6 |
| 4 | 价格达 TP2→TP3 50% | 最高价 − ATR×0.5 | 最低价 + ATR×0.5 |
| 5 | 达 TP3 | 最高价 − ATR×0.3 | 最低价 + ATR×0.3 |

### 锁存原则

- 止损只向有利方向移动（多头只上移 / 空头只下移）
- 价格回调时止损保持不动，**永不回退**
- 雷达止损优先级高于 VPS 宽硬止损（合并为单槽 closePosition）
- 开仓后 **`POST_OPEN_RADAR_BLOCK_SEC=180s`** 禁止近市雷达挂单
- SHORT 保本线 **禁止抬到开仓价及以上**（防近市秒平）
- 无 TP1 时激活距离下限：`max(ATR×1.5, entry×0.5%)`，禁止激活线=成本误触交棒

### 流程

```
开仓
  → TP123 + VPS 宽硬止损（closePosition）
  → 雷达待命（阶段0）· 开仓冷却内禁止交棒

现价距 TP1 还剩 15%（路程 85%）或 TP1 已成交
  → _perform_radar_handoff()（for_handoff 修交棒死锁）
     ① 理想保本 SL = 成本 ±0.1%
     ② 须距市价足够安全（禁止贴市毛刺）
     ③ 挂雷达 STOP（closePosition 单槽合并，不抢 TP）核实 → 再撤宽硬止损
     ④ 钉钉 report_radar_activated + report_shield_disarmed

后续向 TP2/TP3 推进（WS mark + 哨兵）
  → 阶段 2~5 逐级收紧，只升不降 · 锁住利润防回吐
```

### 交棒安全

- 先挂保本 STOP → 核实成功才撤硬止损 / 发钉钉
- `_ideal_radar_sl_is_safe()` / `_clamp_radar_sl_for_market()` 保证距 mark ≥ gap
- 空间不足 → **延迟交棒**，保留 VPS 硬止损呼吸空间
- 重启：现价未达 85% 且 TP1 未成交 → **禁止**恢复保本线（防贴成本误平）

---

## 信号与开仓逻辑

### 动作矩阵

| action | 行为 |
|--------|------|
| `LONG` / `SHORT` | 同向筛选 或 反向先平后开；空字段本地补全 regime/atr/tp |
| `UPDATE_SL` | **仅更新** `tv_sl_ref`（TV 参考）；**不**用 TV 紧价换挂；VPS/雷达自主 |
| `UPDATE_TP` | 更新账本 TP 价并按需重挂限价止盈 |
| `CLOSE` / `CLOSE_PROTECT` / `CLOSE_TP3` / `CLOSE_STOPLOSS` | 撤单 → 全平 → 复位 |

### 反向信号

持多收 `SHORT`（或反之）→ **一律先平后开**，不做同向筛选。

### 同向智能筛选

```
① ATR 变化 (>3%)     → 先平后开
② Regime 变化        → 先平后开
③ 价差 ≥ 0.15%       → 先平后开
④ 否则               → 不重复开仓，仅刷新 TP123 + VPS 硬止损
```

空仓短时重复同向信号 → 忽略 + 钉钉（无 `bar_index`/`seq` 时指纹去重约 45s）。

### 动态加仓

对齐 TV **gemini止损_动态加仓**：

| 类型 | sizing 规则 |
|------|-------------|
| **OPEN** | VPS 自主（档位保证金% × 25x），**不以 TV risk_pct 为准** |
| **PYRAMID** | `add_qty = base_qty × TV qty_ratio`（首仓 base 不变） |
| **PROFIT_ADD** | 同上，比例由 TV 按档位动态下发 |

**档位默认加仓比例 / 次数上限**（TV 未传 qty_ratio 时回退）：

| 档位 | 加仓比例 | 最多次数 | TP123 减仓比例 |
|------|----------|----------|----------------|
| R1 | 0%（禁止） | 1 | **25/35/40** |
| R2 | 30% | 2 | **20/35/45** |
| R3 | 50% | 2 | **18/32/50** |
| R4 | 70% | 3 | **5/20/75** |

加仓后：撤旧 TP → 按 TV `tv_tp1/2/3`（缺则补全）+ 新总头寸重挂；同步 VPS 硬止损 + 雷达（已交棒则推升），钉钉实盘核实。

```
_add_to_position()
  → 市价加仓核实
  → _realign_after_position_add()
     ① 刷新 TP 价格
     ② 撤全部旧 TP 限价单
     ③ _enforce_defense_alignment() 按新仓重挂
     ④ 未齐 → 核武重挂（受 thrash 刹车约束）
     ⑤ _maintain_hard_shield() + 雷达推升（已交棒）
```

### 人工 / orphan 持仓（空闲巡检 12s）

VPS 账本空仓但交易所有仓：

- **同向** → `_perform_live_takeover()`：`_ensure_full_defense_stack()` 挂 TP123 + VPS 硬止损 + 雷达待命
- **反向 TV** → 强制全平 + 钉钉
- **加减仓** → 按比例重算 TP123；TV 加仓信号走 `_realign_after_position_add()`

### 误清场防护

- `_confirm_position_flat()`：多次 REST 复核才认定全平
- 重启后哨兵宽限期 **45s**；开仓后宽限期 **90s**（纯缺失补挂优先）
- 全平分类：`_infer_flat_close_meta()` 区分 TP 吃完 / 交易所 STOP / 人工

---

## 哨兵 + 空闲巡检

### 哨兵轮询

| 状态 | 间隔 |
|------|------|
| 常态（有仓、雷达未激活） | **8s** |
| 雷达已激活 / 交棒后 | **5s** |

每 tick：持仓核实 → best_price → 人工异动 → `_process_directional_defenses()` → Guardian TP 审计 → 雷达追踪。

宽限期内：盘口**无**保护 STOP 时仍允许硬止损补挂（v13.64.1）；纯缺失 TP 补挂优先，避免连环核武。

### 空闲巡检

`IDLE_PATROL_INTERVAL_SEC = 12`：仅在 **monitoring=False 且 VPS 空仓** 时扫描 orphan 持仓并接管。

---

## 重启 / 人工接管

```
recover_state_on_startup()
  → 单例锁 logs/.recover_singleton_{SYMBOL}.lock（按品种隔离）
  → REST 多轮探仓（禁止 ETH 空仓锁跳过 XAU）
  → 读 state + 分品种 TV/开仓日志
  → 有仓：_ensure_full_defense_stack(source="recovery")
       · open_regime 优先 TV OPEN 信源（拒 recover 粘性误锁）
  → _bootstrap_live_defenses_after_recover()
  → 钉钉 report_recover_takeover（含 TV档 vs 硬止损档对账）
  → 启动哨兵 + User Data / 标记价 WS
```

有挂单时禁止误报「空仓清场」。接管异常仍尝试挂 TP123+VPS 硬止损，**禁止**因异常自动平仓。

---

## 钉钉推送链条

| 场景 | 函数 | 说明 |
|------|------|------|
| 开仓 | `report_supervisor_open` | 核实持仓 + TP 对齐；含 VPS硬止损价 / 雷达激活线 / 头寸对账 |
| VPS 硬止损已挂 | `report_adverse_shield_armed` | 激活线前宽止损 |
| 雷达交棒 | `report_shield_disarmed` | **保本 STOP 核实后** |
| 雷达激活 | `report_radar_activated` | 首次保本；含**启动闸门**(价触85%/TP1成交)；失败哨兵补发 |
| 雷达推升 | `report_intervention` | 后续移动 |
| TP 成交 | `report_tp_fill` | 减仓检测 |
| 全平收网 | `report_supervisor_close` | 含 **平仓归因 exit_source**：radar_be / tp3 / vps_hard_sl |
| 人工异动 | `report_manual_position_change` | 加减仓 / 全平 |
| 重启接管 | `report_recover_takeover` | 含 TP / 雷达 / VPS 硬止损审计 |
| 系统告警 | `report_system_alert` | 裸仓、伪 TP、TP 未齐、核查失败等 |
| 反向强平 | `report_force_align` | TV 反向 |

**雷达交棒双推：** 先 `report_radar_activated`（保本已挂），交棒通知 `report_shield_disarmed` 中 **live_qty > 0**。标题含品种 `[ETHUSDT]` / `[XAUUSDT]`。

**平仓一眼辨：** 钉钉「🧭 平仓归因」——`📡 雷达保本止损` ≠ `🏆 TP3止盈收网` ≠ `🛡️ VPS宽硬止损`。归因看 `_radar_handoff_done`，不看钉钉是否曾发出。

---

## Regime 矩阵（对齐 TV v6.9.108）

**TP123 减仓比例按档位不同**（Pine `qty_percent`）：

| 档位 | 保证金（总权益） | TP1/TP2/TP3 | 硬止损% | 雷达激活（→TP1） |
|------|------------------|-------------|---------|------------------|
| R1 | **8%** | **25/35/40** | **2.78%** | **85%（剩15%）** |
| R2 | **14%** | **20/35/45** | **3.89%** | **85%（剩15%）** |
| R3 | **20%** | **18/32/50** | **5.56%** | **85%（剩15%）** |
| R4 | **26%** | **5/20/75** | **8.33%** | **85%（剩15%）** |

- 名义 ≈ 保证金 × **25x**；双品种合计 ≤ 权益 × **13**
- 实盘以 **`open_regime`（开仓档位）** 锁定：TP 比例、硬止损%、雷达激活线；整笔单不变
- TV UPDATE 改 `regime` 可改展示，**不得**收窄本仓硬止损档

```
activation_price = entry ± |tp1 - entry| × activation_ratio(open_regime)
stage1_SL = entry ± 0.1%
trail_SL = best ∓ ATR × stage_mult   # 只向有利方向
```

---

## VPS 部署与更新

### ⚠️ git pull 报错：local changes would be overwritten

**原因：** VPS 上 `deploy_binance.sh`（或 `.env`）有本地未提交修改，与 GitHub 新版本冲突。  
**GitHub 推送是成功的**，问题在 VPS 本地。

**推荐（生产环境强制对齐 remote）：**

```bash
cd ~/binance-engine          # 深币则 ~/deepcoin-hft-server
git fetch origin
git reset --hard origin/main   # 覆盖本地误改，含 deploy_*.sh
bash deploy_binance.sh         # 深币: bash deploy_deepcoin.sh
```

**若需保留 VPS 本地改动：**

```bash
git stash push -m "vps-local" deploy_binance.sh
git pull origin main
git stash drop    # 确认 remote 版本正确后丢弃 stash
bash deploy_binance.sh
```

> ❌ 不要只用 `git pull` 而不处理 local changes。  
> ✅ 日常更新统一用 `git fetch && git reset --hard origin/main`。

### 标准部署流程

```bash
cd ~/binance-engine
git fetch origin && git reset --hard origin/main

# 版本门控
grep 'BINANCE_VPS_VERSION' position_supervisor_binance.py
# 期望: v13.69.0-tp-price-gone-lanes
grep 'DEPLOY_SCRIPT_VERSION' deploy_binance.sh

source venv/bin/activate    # 如有 venv
pip install -r requirements.txt
bash deploy_binance.sh

# 验收
curl -s http://127.0.0.1:5003/health | python3 -m json.tool
# version 含 v13.69.0 · tv_strategy 含 v6.9.108
tail -f logs/binance_brain.log
```

### deploy_binance.sh 流程

1. 校验脚本完整性（防 dingtalk 误覆盖）
2. kill 端口 / gunicorn / 删 recover 锁
3. Python 语法检查
4. supervisor 版本门控（v13.4.6+ / v13.10+）
5. Gunicorn **1 worker × 10 threads**（`--daemon`）
6. 健康检查重试 6 次

### 环境变量

```env
BINANCE_API_KEY=
BINANCE_API_SECRET=
WEBHOOK_SECRET=
DINGTALK_WEBHOOK=
DINGTALK_SECRET=
WECHAT_WEBHOOK=          # 可选：钉钉 3 次失败后的企业微信备用
REDIS_URL=               # 可选：redis://127.0.0.1:6379/0 — TV 时序幂等
DINGTALK_BATCH_FLUSH_SEC=6
DINGTALK_BATCH_MAX=8
TV_SEQ_PENDING_WAIT=3    # 前置 seq 缺失等待秒数（2~5）
FLASK_HOST=0.0.0.0
FLASK_PORT=5003
```

### TradingView Webhook 示例

```json
{
  "action": "LONG",
  "secret": "YOUR_SECRET",
  "symbol": "ETHUSDT.P",
  "regime": 3,
  "atr": 30.0,
  "price": 1785.96,
  "tv_tp1": 1810.0,
  "tv_tp2": 1835.0,
  "tv_tp3": 1860.0,
  "tv_sl": 1744.35,
  "entry_type": "OPEN",
  "bar_index": 200,
  "seq": 1
}
```

| 字段 | 说明 |
|------|------|
| `symbol` | **必填** — `ETHUSDT.P` / `XAUUSDT.P`，网关按品种路由 |
| `bar_index` | **时序主键** — 当前 K 线索引；同 bar 内再按 `seq` |
| `seq` | **时序次键** — 同 bar 内从 1 递增（如先平后开：seq1 平 → seq2 开） |
| `tv_sl` | TV 紧止损，**仅参考**；实盘挂单价 = VPS 开仓×档位% |
| `tv_tp1~3` | TP123 挂单价 + 雷达距离基准；**缺档由 VPS 按 ATR×regime 补全** |
| `price` / `atr` / `regime` | 优先 TV；缺失则本地补全（价←盘口，ATR←30 或 K 线） |
| `entry_type` | OPEN / PYRAMID / PROFIT_ADD |

---

## 日志与排错

| 文件 | 说明 |
|------|------|
| `logs/binance_brain.log` | 大脑主日志（哨兵/雷达/交棒/TP/裸仓） |
| `logs/binance_tv_journal_{SYMBOL}.jsonl` | TV 信号流水（按品种隔离） |
| `logs/binance_open_journal_{SYMBOL}.jsonl` | 开仓/接管流水（按品种隔离） |
| `binance_vps_state_{SYMBOL}.json` | 运行时状态（按品种；兼容旧 `binance_vps_state.json`） |

**常用 grep：**

```bash
tail -f logs/binance_brain.log
grep -E '雷达交棒|交棒延迟|开仓终检|裸仓|TP123 补全|核武|expected TP|空闲巡检' logs/binance_brain.log | tail -80
```

| 现象 | 排查 |
|------|------|
| git pull 失败 | 见上文 `git reset --hard` |
| 开仓后无 TP、无硬止损 | 升级 ≥ v13.65；查「开仓 TP123 补全」「开仓终检裸仓」「开仓滞后核实」；人工先挂 closePosition |
| 只有 TP23 无 TP1 | 查 `伪TP` / `trusted_initial` / 重启 `_ensure_full_defense_stack` |
| TP1成交雷达未启 | 升级 ≥ v13.65（修交棒死锁 for_handoff）；查「雷达交棒」「开仓冷却」 |
| 微盈就全平、无雷达钉钉 | 升级 ≥ v13.67；查「交棒延迟」/「补发雷达激活钉钉」；平仓看「平仓归因」 |
| 不知是雷达还是 TP 平的 | 钉钉「🧭 平仓归因」：radar_be / tp3 / vps_hard_sl；日志 `启动闸门=` |
| 硬止损横跳 R3↔R4 | 查 `open_regime` 粘性 / TV OPEN 锁定（≥ v13.63） |
| 秒挂秒撤 TP/止损 | thrash 刹车（≥ v13.62）；查核武退避与宽限日志 |
| Permission denied 日志 | 用 `tail -f logs/binance_brain.log`，不要直接执行日志文件名 |

---

## 版本演进

| 版本 | 要点 |
|------|------|
| **v13.69.0** | **TP成交主判=价到+限价消失（微漂忽略）；已成交档禁止补挂、耐心等TP23；三轨钉钉：TP reduceOnly / 雷达+宽硬止损 closePosition 单槽互不抢份额** |
| **v13.68.0** | **TP成交头寸对账：减仓+限价消失先记账再挂剩余档；禁把已成交TP当漏挂按现仓重挂（修TP1附近循环）；R4小额TP强制检测；WS多档成交提示；时序1-2-1 CLOSE后释放再开** |
| **v13.67.0** | **平仓归因 exit_source（雷达保本 vs TP3 vs VPS硬止损）；雷达激活钉钉必达+哨兵补发；开单钉钉含硬止损/激活线对账；归因以 handoff_done 为准不以钉钉标志** |
| **v13.66.0** | **TP成交对账增强：多档TP1+TP2识别；禁TP2误判伪TP1/人工减仓；开仓头寸↔TP123切片对账；硬止损/雷达closePosition单槽与TP reduceOnly共存** |
| **v13.65.0** | **雷达统一距TP1剩15%(85%)启动；TP1成交强制交棒；修交棒死锁(for_handoff)；WS mark脉冲；TP23锁利防回吐；硬止损/雷达单槽closePosition不抢份额** |
| **v13.64.2** | **交棒/重启激活线只用现价（禁历史 best 误触保本）；硬止损撤后 3 次重挂；开仓 REST 滞后补探挂防线；接管终检裸仓** |
| **v13.64.1** | **宽限内盘口无保护 STOP 仍允许补挂硬止损（禁裸仓空转）** |
| **v13.64** | **开仓裸仓闸：TV空字段强制 ATR 补全 TP123；expected=0 不再假齐；宽限后挂单；强制 VPS 硬止损终检** |
| **v13.63** | **开仓档位以 TV OPEN 为准：禁 recover/粘性误锁 R4；钉钉对账 TV档 vs 硬止损档；雷达激活线按开仓R显示** |
| **v13.62.1** | **止损检测默认计入 VPS closePosition（禁 exclude_shield 误判缺失）+ thrash 刹车** |
| **v13.62** | **防线 thrash 刹车：纯缺失优先补挂；核武最短间隔；force 止损仍幂等；开仓后 90s 宽限** |
| **v13.61** | **雷达改价触激活线启动：R1/R2=70% R3=75% R4=80%；废除三重强制门槛；TP2/TP3 仍逐级锁利** |
| **v13.60** | **短周期仓位权重 8/14/20/26% + 名义硬顶 13x（ETH45m/XAU50m；硬止损/25x 不变）** |
| **v13.59** | **TV `bar_index`+`seq` 有序消费/幂等去重 + 乱序暂存3s + 钉钉攒批/退避/企微备用** |
| **v13.58** | **hydrate 拒 None 崩溃 + 双品种逐一恢复汇总；接管异常仍挂 TP123+VPS硬止损、禁平仓** |
| **v13.57** | **重启锁按品种隔离 + REST多轮探仓：禁ETH空仓锁跳过XAU接管；有挂单禁报空仓清场** |
| **v13.56** | **开仓日志按品种隔离 + open_regime 粘性锁定：杜绝 XAU R3↔R4 硬止损横跳抢挂** |
| **v13.55** | **开仓后禁雷达近市止损：仅 TP123+VPS宽止损；SHORT保本禁止抬过开仓价** |
| **v13.54** | **硬止损锁定 open_regime（禁 UPDATE/recover 改窄）+ 钉钉全面 VPS宽硬止损文案同步** |
| **v13.53** | **硬止损强制实时 VPS 宽价：拒 TV 紧价挂盘/合并；重启强制清 TV 残留改挂 VPS** |
| **v13.52** | **硬止损只挂 VPS 宽价（拒 TV 紧止损污染）+ 重启不自动平仓** |
| **v13.51** | **硬止损改 closePosition（不抢 TP reduceOnly）+ 全平勿误标 TV tv_sl + 开仓禁 recover 核武撤** |
| **v13.50** | **短周期仓位权重 6/12/18/22% + 名义硬顶 11x（ETH45m/XAU50m）** |
| **v13.49** | **雷达交棒安全距：理想保本须距市价安全；禁止贴市毛刺止损；钉钉标题标注 ETH/XAU** |
| **v13.48** | **钉钉 TP/头寸单位按品种透传 XAU/ETH；缺 symbol 拒绝默念 ETH** |
| **v13.47** | **VPS 检查清单 + check_vps_logic.py；总权益 sizing；README 对齐** |
| **v13.46** | **双品种 ETH+XAU：保证金 sizing、VPS 硬止损、9x 名义硬顶** |
| v13.45 | TP1 三角对账 — 防 R4 5% 开仓微漂误启雷达 |
| v13.29 | 全平后核武撤净 TP123+止损 |

---

## 双工厂差异速查

| 项目 | 币安 | 深币 |
|------|------|------|
| 大脑文件 | `position_supervisor_binance.py` | `position_supervisor_deepcoin.py` |
| 止损实现 | `closePosition` STOP_MARKET 单槽合并 | tv_sl 条件单 + 雷达触发单分离（以实现为准） |
| 蚂蚁仓 | ≤ 0.004 ETH | ≤ 1 张 |
| WS | User Data + 标记价 | `market-latest` 等 |

逻辑、Regime、雷达公式、钉钉语义 **保持一致**；改一侧必须镜像另一侧并同版本号推送。

---

*GEMINI Quant · 双轨智慧雷达 · v13.66.0-tp-reconcile-quota-guard*

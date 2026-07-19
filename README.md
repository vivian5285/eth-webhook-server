# GEMINI 双轨交易工厂 · 统一实盘逻辑

**当前版本：`v13.77.0-open-defense-takeover-safe`**  
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
# 期望 version 含 v13.77.0-open-defense-takeover-safe
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
13. [实盘事故与优化备忘（必读）](#实盘事故与优化备忘必读)
14. [版本演进](#版本演进)
15. [双工厂差异速查](#双工厂差异速查)

---

## 统一架构

```
TradingView Alert (JSON + symbol + bar_index/seq)
        ↓
app.py                          ← 网关：symbol 路由 → Secret → 异步入队 → 200
        ↓
position_supervisor_binance.py  ← 每品种独立军师（ETH / XAU 互不串单）
├── TV 时序：bar_index↑ → 同 bar **动作优先** CLOSE→OPEN（无视 seq 颠倒）
├── 同秒开+平：短停聚合 → 强制先平后开（终态必须有仓）
├── VPS 自主开仓 sizing（总权益 × 档位 8/14/20/26% × 25x）
├── VPS 自主硬止损（开仓价 × 档位%，tv_sl 仅参考）
├── TP123 限价（TV 价优先；空/不全 → ATR×regime 本地补全）
├── 开仓后：强制挂 TP123 + closePosition 硬止损（禁止裸仓）
├── 雷达待命 → mark@1s WS最快盯价；接近/达档位激活线秒级交棒；适度追随保本
├── 雷达交棒钉钉必达（失败哨兵补发）· 全平 exit_source 归因
├── WS mark@1s + UserData 脉冲哨兵 · TP2/TP3 逐级锁利防回吐
├── 13x 总名义硬顶（双品种合计）
├── 哨兵 5~8s · 开仓宽限 90s（纯缺失补挂优先）· 空闲巡检 12s
└── 钉钉：攒批摘要（5~10s / 满 8 条）+ 失败指数退避 + 企微备用
```

**设计原则（两工厂一致）：**

- **TV 只发信号**：网关不做实盘决策；`symbol` / `ticker` 区分 ETH / XAU；缺品种拒绝默念 ETH
- **时序铁律**：Webhook 带 `bar_index` + `seq` 时，**先 bar 升序**；同 bar 内 **永远先平后开**（动作优先于 seq）
- **同秒开平铁律**（v13.75/v13.76）：同时收到开仓+平仓 → 短停聚合 → **先平后开**（终态必须有仓）
- **开仓铁律**（v13.76）：**凡带开仓的 TV → 一律先平现有仓再开（刷新）**；单独平仓 → 清零挂单/状态，干净等待下次 TV
- **幂等键**：`{symbol}_{bar_index}_{seq}`（Redis `REDIS_URL` 优先，否则 `logs/tv_seq_idempotency.json`，TTL 24h）
- **硬止损 VPS 自主**：`开仓价 × 档位%`（锁定 `open_regime`）；TV `tv_sl` → `tv_sl_ref` **仅日志**
- **开仓档位以 TV OPEN 为准**（v13.63）：禁止 recover / 粘性日志误锁 R4 覆盖本次开仓 R
- **开仓基数 = 账户总权益**（marginBalance），非可用余额
- **雷达适度追随**（v13.73/v13.74）：按档启动 R1=85%/R2=80%/R3=75%/R4=70%；步进 35/30/25/20%；呼吸 1.0/0.8/0.65/0.5 ATR
- **WS 最快盯价**（v13.74）：`markPrice@1s`；接近激活线90%即加速；达线/交棒 0.25s 紧急跑；雷达进行中新TV一律先平后开；追随只走单槽合并总线禁先撤后挂死循环
- **硬止损与雷达不抢份额**：二者合并为 **单槽 closePosition**；TP123 用 reduceOnly 限价
- **头寸微漂 ≠ TP1 成交**：伪 TP 仍拦截记账；不挡雷达启动
- **开仓禁止裸仓**（v13.64）：TV 空字段必须本地补全 TP；`expected=0` 不得假齐；硬止损强制终检
- **重启禁止无故平仓**：未达激活线且 TP1 未成交 → 只挂 VPS 宽硬止损
- 雷达交棒：**先挂保本 STOP 核实 → 再撤宽硬止损 → 再钉钉**；随后 TP2/TP3 逐级锁利
- **平仓归因**（v13.67）：以 `_radar_handoff_done` 判定雷达保本，**不以钉钉是否发出为准**；钉钉带 `exit_source`（radar_be / tp3 / vps_hard_sl）
- **TP 成交禁补挂**（v13.68/v13.69）：**价到 + 限价消失 = 成交**（微漂不算）；记账后耐心等 TP23，禁止再挂已成交档
- **三轨不抢份额**：TP123=`reduceOnly`；雷达保本+VPS宽硬止损=`closePosition` 单槽；各自独立
- **时序先平后开**（v13.76）：**带开仓 → 一律先平后开刷新**；单独平仓 → 清零等待；同秒开平同样先平后开
- **禁穿价 TP 秒平**（v13.71）：开仓挂 TP 前校验市价；穿价则 ATR 重算/推离，禁止挂出即成交导致剩蚂蚁仓
- **开仓宽限禁档位裁减**（v13.71）：刚开仓内不做 regime_cap 减仓
- **TP 成交必须价到**（v13.72）：记 TP1/2/3 前强制拉实时 mark + best；未触及该档价一律拒认（头寸变少≠TP成交）；钉钉同类标题 30s 去重、一次对账一条

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
| 雷达适度追随 | R1~R4 激活85/80/75/70% · 步进35/30/25/20% · 呼吸1.0/0.8/0.65/0.5ATR | 🟡 P1 |
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

### 雷达 5 阶段（v13.73 适度追随 · 按开仓档位）

| 阶段 | 触发条件 | 多头止损 | 空头止损 |
|------|----------|----------|----------|
| 0 | 未达档位激活线 | VPS 宽硬止损 | 同左 |
| 1 | 价触激活线交棒 | 成本 + 0.1% | 成本 − 0.1% |
| 2 | TP1→TP2 走过**档位步进%** | best − ATR×**呼吸** | best + ATR×**呼吸** |
| 3 | 达 TP2（或 TP2 已价到成交） | 同上 | 同上 |
| 4 | TP2→TP3 走过**档位步进%** | 同上 | 同上 |
| 5 | 达 TP3（或 TP3 已价到成交） | 同上 | 同上 |

**档位参数（开仓锁定 `open_regime`）：**

| 档位 | 激活（→TP1） | 步进 | 呼吸 ATR | 说明 |
|------|--------------|------|----------|------|
| R1 | 85% | 35% | 1.0 | 最松，给足空间 |
| R2 | 80% | 30% | 0.8 | 较松 |
| R3 | 75% | 25% | 0.65 | 适中 |
| R4 | 70% | 20% | 0.5 | 稍积极，仍非紧追 |

> 强趋势是「适度追随」不是「紧追」；旧统一 85% + 阶段 ATR 0.3 极限表已删除。

### 锁存原则

- 止损只向有利方向移动（多头只上移 / 空头只下移）
- 价格回调时止损保持不动，**永不回退**
- 雷达止损优先级高于 VPS 宽硬止损（合并为单槽 closePosition）
- 开仓后 **`POST_OPEN_RADAR_BLOCK_SEC=180s`** 禁止近市雷达挂单
- SHORT 保本线 **禁止抬到开仓价及以上**（防近市秒平）
- 无 TP1 时激活距离下限：`max(ATR×1.5, entry×0.5%)`，禁止激活线=成本误触交棒
- **WS mark@1s** 最快盯价：朝激活线走过 90% 即加速；达线/交棒 0.25s 紧急跑
- 追随前先 **TP 价到对账**（剩仓实时）；同价已挂跳过；25s 步进门限防撤挂死循环

### 流程

```
开仓
  → TP123（reduceOnly）+ VPS 宽硬止损（closePosition）
  → 雷达待命（阶段0）· mark@1s WS 盯价 · 开仓冷却内禁止交棒

现价达档位激活线（R1=85%…R4=70%）或 TP1 已真实成交（价到+限价消失）
  → _perform_radar_handoff()（for_handoff 修交棒死锁）
     ① 理想保本 SL = 成本 ±0.1%
     ② 须距市价足够安全（禁止贴市毛刺）
     ③ 挂雷达 STOP（closePosition 单槽合并，不抢 TP）核实 → 再撤宽硬止损
     ④ 钉钉 report_radar_activated（只发一次；失败哨兵补发）

后续向 TP2/TP3 推进（WS mark + 哨兵）
  → 按档位步进% 推进阶段 · 呼吸 ATR 追随 · 按剩余头寸挂单 · 只升不降
  → 雷达进行中若新 TV OPEN 到达 → 一律先平后开（干净换防，雷达回待命）
```

### 交棒安全

- 先挂保本 STOP → 核实成功才撤硬止损 / 发钉钉
- `_ideal_radar_sl_is_safe()` / `_clamp_radar_sl_for_market()` 保证距 mark ≥ gap
- 空间不足 → **延迟交棒**，保留 VPS 硬止损呼吸空间
- 重启：现价未达档位激活线且 TP1 未成交 → **禁止**恢复保本线（防贴成本误平）
- 追随只走 `_sync_exchange_stop` 单槽合并总线，**禁止**先 scope=radar 撤再挂（防秒挂秒撤）
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
① 雷达进行中（已交棒/已激活）→ 一律先平后开（干净换防）
② ATR 变化 (>3%)             → 先平后开
③ Regime 变化                → 先平后开
④ 价差 ≥ 0.15%               → 先平后开
⑤ 否则（雷达未启动）         → 不重复开仓，仅刷新 TP123 + VPS 硬止损
```

空仓短时重复同向信号 → 忽略 + 钉钉（无 `bar_index`/`seq` 时指纹去重约 45s）。  
**凡带开仓的 TV → 一律先平后开刷新仓位**（有仓先平；无仓净挂单再开）。  
单独平仓（无开仓 TV）→ 清仓+撤净挂单+复位，干净等待下次 TV。  
同K/同秒同时开+平 → 短停聚合后强制先平后开（终态必须有仓）。
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
| 雷达激活 | `report_radar_activated` | 首次保本；含**启动闸门**(档位激活线/TP1成交)；失败哨兵补发 |
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
| R1 | **8%** | **25/35/40** | **2.78%** | **85% · 步进35% · 呼吸1.0 ATR** |
| R2 | **14%** | **20/35/45** | **3.89%** | **80% · 步进30% · 呼吸0.8 ATR** |
| R3 | **20%** | **18/32/50** | **5.56%** | **75% · 步进25% · 呼吸0.65 ATR** |
| R4 | **26%** | **5/20/75** | **8.33%** | **70% · 步进20% · 呼吸0.5 ATR** |

- 名义 ≈ 保证金 × **25x**；双品种合计 ≤ 权益 × **13**
- 实盘以 **`open_regime`（开仓档位）** 锁定：TP 比例、硬止损%、雷达激活线；整笔单不变
- TV UPDATE 改 `regime` 可改展示，**不得**收窄本仓硬止损档

```
activation_price = entry ± |tp1 - entry| × activation_ratio(open_regime)
stage1_SL = entry ± 0.1%
trail_SL  = best ∓ ATR × breath_atr(open_regime)   # 只向有利方向；步进%门限再推升
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
# 期望: v13.77.0-open-defense-takeover-safe
grep 'DEPLOY_SCRIPT_VERSION' deploy_binance.sh

source venv/bin/activate    # 如有 venv
pip install -r requirements.txt
bash deploy_binance.sh

# 验收
curl -s http://127.0.0.1:5003/health | python3 -m json.tool
# version 含 v13.77.0 · tv_strategy 含 v6.9.108
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
| `seq` | **时序次键** — 同 bar 编号；**开平并存时动作优先于 seq**（永远先平后开） |
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
| 开仓后无 TP、无硬止损 | 升级 ≥ **v13.77**；查「开仓终检」「place_failed_keep_old」「穿价推离」；人工先挂 closePosition |
| 只有 TP23 无 TP1 | 查是否现价已达 TP1（已成交禁重挂）；`伪TP` / `trusted_initial` |
| TP1成交雷达未启 | 升级 ≥ v13.65（修交棒死锁 for_handoff）；查「雷达交棒」「开仓冷却」 |
| 微盈就全平、无雷达钉钉 | 升级 ≥ v13.67；查「交棒延迟」/「补发雷达激活钉钉」；平仓看「平仓归因」 |
| 不知是雷达还是 TP 平的 | 钉钉「🧭 平仓归因」：radar_be / tp3 / vps_hard_sl；日志 `启动闸门=` |
| 硬止损横跳 R3↔R4 | 查 `open_regime` 粘性 / TV OPEN 锁定（≥ v13.63） |
| 秒挂秒撤 TP/止损 | thrash 刹车（≥ v13.62）+ 雷达单槽合并（≥ v13.74 禁先撤后挂）；查核武退避与宽限日志 |
| 同秒开平后空仓（先开后秒平） | 升级 ≥ **v13.75/v13.76**；查「强制先平后开」「同秒开平」；钉钉先平后开链 |
| 开完立刻秒平大半 | 升级 ≥ v13.71（禁穿价 TP）；查「穿价」「无菌」「档位裁减」 |
| 头寸少了却报 TP12 / 异常减仓刷屏 | 升级 ≥ **v13.76.1**；微差锚定实盘；异常减仓 600s 一条 |
| 重启后仓位被莫名平掉 / TP1 被削光 | 升级 ≥ **v13.77**；接管禁 force 裁减；现价达 TP1 禁当漏挂重挂 |
| 雷达慢/未及时保本 | 升级 ≥ v13.74；确认 mark@1s WS；查「接近激活线」「紧急交棒」 |
| deploy 报 Gunicorn 失败但日志已 Listening | 升级 deploy ≥ **v13.76-deploy**；PID 滞后误报，轮询端口/health |
| Permission denied 日志 | 用 `tail -f logs/binance_brain.log`，不要直接执行日志文件名 |

---

## 实盘事故与优化备忘（必读）

> 以下均来自 **2026-07 币安 ETH/XAU 实盘** 踩坑记录。改逻辑、部署、排错前先读本节。  
> 当前修复版本基准：**`v13.77.0-open-defense-takeover-safe`**（含 v13.75～v13.76.1 时序/钉钉铁律）。

### 一、绝对铁律（违反必出事故）

| # | 铁律 | 说明 |
|---|------|------|
| 1 | **带开仓的 TV → 一律先平后开** | 有仓先无菌平干净再开；无仓也净挂单再开。禁止「同向只刷 TP」「空仓轻量直开绕过净场」。 |
| 2 | **同秒开+平 → 永远先平后开** | 即使 TV 把 OPEN 标 `seq=1`、CLOSE 标 `seq=2`，也**动作优先于 seq**。终态必须有仓。 |
| 3 | **单独平仓 → 只清场等待** | 无并存 OPEN 时：平仓 + 撤净挂单 + 复位账本，**耐心等下次 TV**，不要自作主张再开。 |
| 4 | **开仓必须有 TP123 + VPS 宽硬止损** | 缺任一视为裸仓；终检补挂；钉钉告警。雷达开仓后仅待命，不替代硬止损。 |
| 5 | **硬止损先挂核实，再撤孤儿** | 禁止「先撤净 STOP 再挂」；新挂失败 → **保留旧 STOP**（`place_failed_keep_old`）。 |
| 6 | **TP 成交必须价到** | 现价/best 触及该档 TP + 限价消失才记账。头寸变少 ≠ TP 成交。 |
| 7 | **现价已达 TP1 → 禁止当漏挂重挂** | 重启/补挂若把 TP1 再挂到穿价区，会在 TP1 被连续吃光，永远到不了 TP23。 |
| 8 | **重启接管禁止莫名平仓** | 不 force 档位裁减；scorch **只撤 TP、保留硬止损**；蚂蚁仓以外禁止核武全平。 |
| 9 | **钉钉同类不刷屏** | 标题去重（普通 ≥120s；系统告警/异常减仓 ≥600s）；攒批同标题折叠；事件锁防双线程双发。 |

### 二、实盘事故档案（现象 → 根因 → 修复）

#### 事故 A · 同秒「开多 + 全平」→ 先开后秒平（空仓）

- **现象**：TV 同秒发 `LONG OPEN seq=1` 与 `CLOSE_PROTECT seq=2`；实盘先开仓立刻被平掉。  
- **根因**：旧逻辑按 **seq 升序**消费 → OPEN 先于 CLOSE。  
- **修复**：≥ **v13.75** 同 bar settle 聚合 + `action_exec_rank`（CLOSE→OPEN）；≥ **v13.76** 凡 OPEN 一律先平后开。  
- **日志关键字**：`同bar强制先平后开` / `TV seq颠倒已纠正` / `先平后开链`。  
- **注意**：不要再假设「TV 永远 CLOSE.seq < OPEN.seq」。

#### 事故 B · 「异常减仓·非TP」钉钉连环刷屏

- **现象**：开仓后钉钉刷十几条 `异常减仓·非TP成交`，数量如 `5.909→5.665`，其实未到 TP。  
- **根因**：开仓基线与 REST 实盘微差被反复判定为「明显减仓」；哨兵 + WS 双线程同时对账；去重窗过短。  
- **修复**：≥ **v13.76.1** 微差/开仓宽限内 **静默锚定实盘**；异常减仓 **600s 一条**；锚定后不再触发；钉钉标题/攒批去重加固。  
- **优化建议**：开仓后以核实仓 `_open_settled_qty` 为唯一基线；勿用偏高 target 当基线。

#### 事故 C · SHORT 开仓成功但无 TP123、无 VPS 宽硬止损

- **现象**：TV SHORT OPEN（含 tv_tp1/2/3）Webhook 成功，币安有仓，盘口无线价止盈、无 closePosition。  
- **根因（叠加）**：  
  1. 硬止损「先撤净再挂」，挂失败 → 裸仓；  
  2. SHORT 触发价贴/穿市价被 **直接拒绝** 且不重挂；  
  3. 穿价 TP **整档跳过** → 可能 0 档 TP；  
  4. 开仓 `cancel_all` 与挂单竞态。  
- **修复**：≥ **v13.77** 先挂核实再撤孤儿；贴市推到安全距；穿价 TP 推离再挂；开仓只清 TP；终检无 TP/无止损再补一轮 + 钉钉。  
- **日志关键字**：`place_failed_keep_old` / `推高到安全` / `开仓终检` / `穿价.*推离`。

#### 事故 D · 重启/部署后 TP1 被反复补挂 → 仓位在 TP1 削光

- **现象**：VPS 更新部署后，接管以为「TP1 缺失」，不断重挂 TP1；现价已到/接近 TP1 时挂出即成交，仓位被吃光，到不了 TP23。  
- **根因**：补挂/核武路径把「限价不在」当成漏挂，**未先判断现价是否已达该档**；`cancel_all` 连带撤硬止损。  
- **修复**：≥ **v13.77**  
  - 现价已达 TP 档 → **记账/等待，禁止当漏挂重挂**；  
  - 重启 scorch **只撤 TP，保留硬止损**；  
  - 接管跳过 force 档位裁减（禁误减仓/误平）。  
- **正确接管姿势**：  
  1. 读实盘仓位与方向（**禁止无故平仓**）；  
  2. 看 mark 是否已达 TP1 → 决定是否启动雷达；  
  3. 钉钉/日志给出「应挂 TP 价 + 剩余切片数量」；  
  4. 只补 **未达价且确实缺失** 的档。

#### 事故 E · deploy 脚本报「Gunicorn 启动失败」但服务已起来

- **现象**：`./deploy_binance.sh` 第 3 步红字失败，但日志已 `Listening at http://0.0.0.0:5003`、WS 已连。  
- **根因**：`--daemon` 写 PID 文件滞后，旧脚本 `sleep 2` 只查 PID 就 `exit 1`。  
- **修复**：deploy ≥ **v13.76-deploy-pid-health-robust**，轮询 PID / 端口 / `/health`（约 12s）。  
- **操作**：`git reset --hard origin/main` 后再 `./deploy_binance.sh`；用 `curl -s http://127.0.0.1:5003/health` 验收。

### 三、雷达（开仓后 · 多空对称）注意点

| 项 | 正确行为 | 禁止 |
|----|----------|------|
| 开仓后 | 雷达**待命**；挂 TP123 + VPS 宽硬止损；`POST_OPEN_RADAR_BLOCK_SEC≈180s` 内禁止近市雷达挂单 | 开仓立刻用雷达保本替换宽硬止损 |
| 启动条件 | 价触档位激活线（R1=85%…R4=70%）**或** TP1 **价到+限价消失** | 仅头寸变少就当 TP1 成交并交棒 |
| 交棒 | 先挂保本 STOP 核实 → 再撤宽硬止损 → 钉钉一条 | 先撤硬止损再挂保本（裸仓窗口） |
| 追随 | 按档位步进% + 呼吸 ATR；单槽合并总线；25s 步进门限 | 先 scope=radar 撤再挂（死循环） |
| 多空 | LONG/SHORT 公式对称（止损只向有利方向） | SHORT 把保本抬过开仓价贴市秒平 |

### 四、优化清单（持续改进）

1. **开仓链路**：核实仓 → 消毒 TP → 挂 TP123 → 挂/核实硬止损 → 终检；任一步失败钉钉 **一条** 并保留已有防护。  
2. **时序**：同 bar 短停聚合（`TV_SAME_BAR_SETTLE`≈1s）；消费前 `reorder_batch_close_then_open`。  
3. **对账**：TP 只认价到；基线用 `_open_settled_qty`；微差锚定，不刷「异常减仓」。  
4. **重启**：品种隔离锁；有仓只修防线不平仓；输出「雷达是否该启 + TP 应挂价与切片」。  
5. **钉钉**：开仓/交棒/平仓归因各一条；告警类 600s 去重；禁止同标题连发。  
6. **部署**：以 `health.version` 为准，不以脚本瞬时 PID 为准；部署后立刻查盘口是否有 TP+STOP。  
7. **深币镜像**：改币安大脑逻辑必须同步深币同版本号（时序/钉钉去重/开平铁律）。

### 五、排错命令速查（实盘）

```bash
# 健康与版本
curl -s http://127.0.0.1:5003/health | python3 -m json.tool

# 开仓/裸仓/硬止损
grep -E '开仓终检|裸仓|place_failed_keep_old|推高到安全|推低到安全|强制VPS宽硬止损|穿价' \
  logs/binance_brain.log | tail -60

# 时序先平后开
grep -E '强制先平后开|同秒开平|先平后开链|seq颠倒' logs/binance_brain.log | tail -40

# TP 补挂是否误伤
grep -E '拒绝补挂|现价已达|价到\+限价消失|重建跳过|伪TP' logs/binance_brain.log | tail -40

# 钉钉去重
grep -E '钉钉去重|钉钉标题去重|仓位核实' logs/binance_brain.log | tail -30
```

### 六、版本对照（本备忘相关）

| 版本 | 解决什么 |
|------|----------|
| v13.75 | 同秒开平 seq 颠倒 → 强制先平后开 |
| v13.76 | 凡 OPEN 一律先平后开；单独 CLOSE 清零等待 |
| v13.76.1 | 异常减仓/微差钉钉不刷屏 |
| v13.77 | 开仓必挂防线；硬止损失败保留旧单；重启禁误平、禁 TP1 重挂死循环 |
| deploy v13.76+ | Gunicorn PID 滞后误报失败 |

---

## 版本演进

### 近期详细更新记录（v13.67 → v13.77）

#### v13.77.0 · `open-defense-takeover-safe`

**主题：开仓必挂 TP123+宽硬止损；重启接管禁误平、禁 TP1 重挂死循环**

- 硬止损：**先挂核实再撤孤儿**；失败保留旧 STOP（`place_failed_keep_old`），禁先撤净裸仓
- 贴/穿市硬止损：推到安全距再挂（多空对称）
- 开仓：只清 TP 残留；终检无 TP/无硬止损 → 消毒补挂 + 钉钉一条
- 接管/重启：跳过档位 force 裁减；scorch **只撤 TP 保留硬止损**；现价已达 TP1 禁当漏挂重挂
- 穿价 TP：推离后再挂，禁止跳过全档导致空防线

#### v13.76.1 · `dingtalk-dedupe-qty`

**主题：仓位微差对账不刷屏**

- 开仓基线 vs 实盘微差（未达 TP）：**静默锚定实盘**；开仓宽限内只播「仓位核实」一条
- 「异常减仓·非TP」：**同仓位 600s 只告警一次**，告警后立即锚定，杜绝哨兵/WS 连环刷
- 钉钉：标题去重默认 120s；系统告警 600s；攒批同标题折叠；双线程去重加锁

#### v13.76.0 · `always-close-then-open`

**主题：开仓铁律极简清晰 — 要么先平后开，要么单独平仓清零等待**

- **带开仓的 TV**（OPEN / LONG|SHORT 建仓）→ **一律先平现有仓再开**（刷新仓位；无仓也净挂单再开）
- **平仓+开仓同时到** → 同样先平后开（终态有仓）
- **单独平仓** → 清仓 + 撤净全部挂单 + 复位账本，干净等待下次 TV（不开仓）
- 废除：同向「仅刷新 TP」、开仓冷却禁先平后开、空仓轻量直开绕过无菌
- 钉钉：先平后开链核实；单独平仓走清场播报
- 币安 + 深币同铁律

#### v13.75.0 · `force-close-then-open`

**主题：同秒开+平永远先平后开（终态必须有仓）**

- **根因**：TV 可同秒发 LONG OPEN `seq=1` + CLOSE_PROTECT `seq=2`；旧逻辑按 seq 升序 → 先开后秒平
- **铁律**：同 bar / 同秒同时有开仓与平仓 → **短停约 1s 聚合** → **强制先平后开**（动作优先于 seq）
- 缓冲层 `action_exec_rank` + 消费侧 `reorder_batch_close_then_open` 双重保险
- 钉钉：`先平后开链 · 同秒开平·强制先平后开`（含 seq 颠倒纠正说明）
- 空仓仅 OPEN、无并存 CLOSE → 仍按档位直开（不画蛇添足）

#### v13.74.0 · `ws-radar-first`（`3ede680`）

**主题：WebSocket 最快盯价 + 雷达进行中换防铁律**

- **盯价**：`markPrice@1s` 为最快价源；朝档位激活线走过 **90%** 即加速快轮询；达线/已交棒 **0.25s** 紧急执行交棒/追随（不在 WS 线程挂单，哨兵串行）
- **TV 规则**：
  - 空仓仅 OPEN → 按档位直开 + TP123 + VPS 宽硬止损 + 雷达待命
  - **雷达进行中**（已交棒/已激活）新 TV → **一律先平后开**（净场后再挂 TP123+宽止损+雷达待命）
  - 同K CLOSE+OPEN / 有仓换防 → 无菌先平后开
- **防死循环**：雷达追随禁止「先 scope=radar 撤再挂」；只走 `_sync_exchange_stop` 单槽合并（雷达∪VPS宽底）；同价已挂跳过；25s 步进门限
- **三轨共存**：TP=`reduceOnly`；雷达+VPS宽硬止损=`closePosition` 单槽；互不抢份额
- **钉钉**：同类标题去重；激活/追随核实后各一条

#### v13.73.0 · `radar-moderate-follow`（`7f67529`）

**主题：雷达「适度追随」分档表（强趋势不是紧追）**

| 档位 | 激活（→TP1） | 步进 | 呼吸 ATR |
|------|--------------|------|----------|
| R1 | 85% | 35% | 1.0 |
| R2 | 80% | 30% | 0.8 |
| R3 | 75% | 25% | 0.65 |
| R4 | 70% | 20% | 0.5 |

- **删除旧逻辑**：全档统一 85%、段内固定 50%、阶段 ATR 紧追至 0.3（易被打掉）
- 移动前 **TP 价到对账**；按**剩余头寸**追随；开仓档位锁定参数
- 遗留 `position_supervisor.py` 对齐同表并标 DEPRECATED（生产只用 binance 大脑）

#### v13.72.0 · `tp-fill-requires-mark`（`aab145e`）

**主题：TP 成交必须价到，禁凭头寸变少瞎报 TP12**

- 记 TP1/2/3：**现价或 best 必须触及该档价 + 限价消失**
- 头寸变少但价未到 → 拒认 TP，报「异常减仓·非TP」
- 清除账本里未价到的假 TP 标记
- 钉钉：标题级 30s 去重（`dingtalk.py` 全交易所共用）；一次对账一条汇总

#### v13.71.0 · `open-only-no-instant-trim`（`08835d3`）

**主题：空仓只开仓，禁止开完秒平大半剩蚂蚁仓**

- 空仓仅 OPEN → 轻量确认空仓后按档位开满（不做重型无菌/先平后开）
- **禁穿价 TP**：挂前校验 mark；穿价则 ATR 重算/推离市价
- 开仓宽限内禁止 `regime_cap` 减仓
- 钉钉：开仓不再另发本金快照条；同行为去重

#### v13.70.0 · `close-then-open-sterile`（`7befdfc`）

**主题：同K线先平后开 + 无菌净场**

- TV 同K只可能：单独 CLOSE，或 CLOSE(seq小)+OPEN(seq大)；永无「先开后平」同时发
- `_sterile_flat_gate`：撤→平→再撤→扫孤儿→验 qty=0+orders=0 再开
- 钉钉先平后开链对账；防残留限价成交反手/超档位

#### v13.69.0 · `tp-price-gone-lanes`（`278700b`）

- TP 成交主判 = **价到 + 限价消失**（微漂忽略）
- 已成交档禁止补挂，耐心等 TP23
- 三轨钉钉说明：TP reduceOnly / 雷达+宽硬止损 closePosition 单槽

#### v13.68.0 · TP 成交对账 + 时序再开（`234d694`）

- 减仓+限价消失先记账再挂剩余档；禁把已成交 TP 当漏挂按现仓重挂（修 TP1 附近循环）
- R4 小额 TP 强制检测；WS 多档成交提示
- 幂等键含 action；CLOSE 后释放开仓键允许同 bar 再开

#### v13.67.0 · 平仓归因 + 雷达钉钉必达（`165ae02`）

- `exit_source`：radar_be / tp3 / vps_hard_sl（以 `_radar_handoff_done` 为准）
- 雷达激活钉钉必达 + 哨兵补发
- 开单钉钉含硬止损价 / 激活线 / 头寸对账

---

### 版本一览表

| 版本 | 要点 |
|------|------|
| **v13.77.0** | **开仓必挂TP123+宽硬止损；硬止损失败保留旧单；重启只撤TP不撤STOP；禁TP1价到后当漏挂重挂；接管禁误减仓** |
| **v13.76.1** | **仓位微差不刷屏：静默锚定实盘；异常减仓600s一条；钉钉标题/告警长去重+攒批折叠** |
| **v13.76.0** | **开仓铁律：凡带开仓一律先平后开刷新；单独平仓清零等待；废除同向仅刷TP/空仓轻量直开** |
| **v13.75.0** | **同秒开+平强制先平后开（终态必须有仓）：短停聚合；动作优先于seq；纠正 OPEN.seq=1+CLOSE.seq=2 先开后秒平事故** |
| **v13.74.0** | **WS mark@1s最快盯价：接近激活线90%加速、达线0.25s紧急交棒；雷达进行中新TV一律先平后开；追随禁先撤后挂，单槽合并总线防死循环** |
| **v13.73.0** | **雷达适度追随：R1~R4 激活85/80/75/70%·步进35/30/25/20%·呼吸1.0/0.8/0.65/0.5ATR；删旧统一85%+阶段紧追；移动前TP价到对账；25s步进门限防撤挂死循环** |
| **v13.72.0** | **TP成交铁律：必须现价/best触及该档TP价+限价消失；禁凭头寸变少误报TP12；异常减仓单独告警；钉钉标题级去重（全交易所）** |
| **v13.71.0** | **空仓仅OPEN按档位直开（不做先平后开）；禁挂穿价TP秒平；开仓宽限内禁止档位裁减；钉钉同行为去重（开仓一条播报）** |
| **v13.70.0** | **TV同K线铁律：只可能单独CLOSE或CLOSE(seq小)+OPEN(seq大)；无菌空仓闸（撤→平→撤→扫孤儿→qty=0+orders=0）再开；钉钉先平后开链对账；防残留限价成交反手/超档位** |
| **v13.69.0** | **TP成交主判=价到+限价消失（微漂忽略）；已成交档禁止补挂、耐心等TP23；三轨钉钉：TP reduceOnly / 雷达+宽硬止损 closePosition 单槽互不抢份额** |
| **v13.68.0** | **TP成交头寸对账：减仓+限价消失先记账再挂剩余档；禁把已成交TP当漏挂按现仓重挂（修TP1附近循环）；R4小额TP强制检测；WS多档成交提示；时序CLOSE后释放再开** |
| **v13.67.0** | **平仓归因 exit_source（雷达保本 vs TP3 vs VPS硬止损）；雷达激活钉钉必达+哨兵补发；开单钉钉含硬止损/激活线对账；归因以 handoff_done 为准不以钉钉标志** |
| **v13.66.0** | **TP成交对账增强：多档TP1+TP2识别；禁TP2误判伪TP1/人工减仓；开仓头寸↔TP123切片对账；硬止损/雷达closePosition单槽与TP reduceOnly共存** |
| **v13.65.0** | **雷达统一距TP1剩15%(85%)启动；TP1成交强制交棒；修交棒死锁(for_handoff)；WS mark脉冲；TP23锁利防回吐；硬止损/雷达单槽closePosition不抢份额**（已被 v13.73 分档表取代统一85%） |
| **v13.64.2** | **交棒/重启激活线只用现价（禁历史 best 误触保本）；硬止损撤后 3 次重挂；开仓 REST 滞后补探挂防线；接管终检裸仓** |
| **v13.64.1** | **宽限内盘口无保护 STOP 仍允许补挂硬止损（禁裸仓空转）** |
| **v13.64** | **开仓裸仓闸：TV空字段强制 ATR 补全 TP123；expected=0 不再假齐；宽限后挂单；强制 VPS 硬止损终检** |
| **v13.63** | **开仓档位以 TV OPEN 为准：禁 recover/粘性误锁 R4；钉钉对账 TV档 vs 硬止损档；雷达激活线按开仓R显示** |
| **v13.62.1** | **止损检测默认计入 VPS closePosition（禁 exclude_shield 误判缺失）+ thrash 刹车** |
| **v13.62** | **防线 thrash 刹车：纯缺失优先补挂；核武最短间隔；force 止损仍幂等；开仓后 90s 宽限** |
| **v13.61** | **雷达改价触激活线启动：R1/R2=70% R3=75% R4=80%；废除三重强制门槛；TP2/TP3 仍逐级锁利**（历史中间态，现以 v13.73 表为准） |
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

*GEMINI Quant · 双轨智慧雷达 · v13.77.0-open-defense-takeover-safe*

# GEMINI 双轨交易工厂 · 统一实盘逻辑

**当前版本：`v13.52.0-vps-sl-radar-triad-strict`**

TradingView Webhook → 交易所永续自动化引擎。**币安 ETH+XAU** 与 **深币** 两套 VPS 共用同一套「军师大脑」逻辑（`position_supervisor_*.py` 镜像实现），仅 **计量单位 / 交易所 API / 钉钉主题** 不同。

| 工厂 | GitHub | VPS 目录 | 端口 | 品种 | 杠杆 | 钉钉 |
|------|--------|----------|------|------|------|------|
| **币安** | `vivian5285/eth-webhook-server` | `~/binance-engine` | **5003** | ETH + XAU | **25x** | 黄金 |
| **深币** | `vivian5285/deepcoin-hft-server-main` | `~/deepcoin-hft-server` | **5004** | ETH + XAU 张 | **25x** | 紫金 |

**健康检查：**

```bash
curl -s http://127.0.0.1:5003/health   # 币安
curl -s http://127.0.0.1:5004/health   # 深币
# 期望 version: v13.52.0-vps-sl-radar-triad-strict
```

**Cursor / VPS 逻辑自查：**

```bash
python check_vps_logic.py          # 静态对账（无需 API Key）
# 完整清单见 docs/VPS实盘检查清单.md
```

---

## 目录

1. [统一架构](#统一架构)
2. [VPS 实盘检查清单](#vps-实盘检查清单)
3. [防线总线：TP123 + VPS硬止损 + 雷达](#防线总线tp123--vps硬止损--雷达)
4. [雷达状态机（三重验证）](#雷达状态机三重验证)
5. [信号与开仓逻辑](#信号与开仓逻辑)
6. [哨兵 + 空闲巡检](#哨兵--空闲巡检)
7. [重启 / 人工接管](#重启--人工接管)
8. [钉钉推送链条](#钉钉推送链条)
9. [Regime 矩阵（对齐 TV v6.9.93）](#regime-矩阵对齐-tv-v68993)
10. [VPS 部署与更新](#vps-部署与更新)
11. [日志与排错](#日志与排错)
12. [版本演进](#版本演进)

---

## 统一架构

```
TradingView Alert (JSON + symbol)
        ↓
app.py                          ← 网关：symbol 路由 → Secret → 异步入队 → 200
        ↓
position_supervisor_*.py        ← 每品种独立军师（ETH / XAU 互不串单）
├── VPS 自主开仓 sizing（总权益 × 档位% × 25x）
├── VPS 自主硬止损（开仓价 × 档位%，tv_sl 仅参考）
├── TP123 限价（TV 价格 + Regime 比例）
├── 雷达待命 → TP1 三重验证后交棒保本
├── 11x 总名义硬顶（双品种合计）
├── 哨兵 5~8s · 空闲巡检 12s
└── 钉钉：先核实盘口再推送
```

**设计原则（两工厂一致）：**

- **TV 只发信号**：网关不做实盘决策；`symbol` 字段区分 ETH / XAU
- **硬止损 VPS 自主**：`开仓价 × 档位%`；TV `tv_sl` 存入 `tv_sl_ref` **仅日志参考**
- **开仓基数 = 账户总权益**（marginBalance），非可用余额
- **雷达 TP1 后启动**：价格达 TP1（主）+ TP1 限价成交/消失（辅）+ 基线减仓（参考）
- **头寸微漂 ≠ TP1 成交**：R4 TP1 仅 5%，禁止因浮盈/保证金变化误判
- 雷达交棒：**先挂保本 STOP 核实 → 再撤宽硬止损 → 再钉钉**

### 生产模块 vs 遗留

| 模块 | 币安 | 深币 | 说明 |
|------|------|------|------|
| `app.py` | ✅ | ✅ | Flask 网关 |
| `position_supervisor_*.py` | ✅ | ✅ | **唯一** 实盘大脑 |
| `*_client.py` | ✅ | ✅ | 交易所 API |
| `dingtalk.py` | ✅ | ✅ | 钉钉播报 |
| `deploy_*.sh` | ✅ | ✅ | 标准部署 |
| `position_supervisor.py` 等 | ❌ | ❌ | 遗留，未接入 |

---

## VPS 实盘检查清单

完整清单：[docs/VPS实盘检查清单.md](docs/VPS实盘检查清单.md)

```bash
python check_vps_logic.py    # 7 大模块静态对账
```

| 模块 | 要点 | 优先级 |
|------|------|--------|
| 品种路由 | `symbol` / `ticker` → ETHUSDT / XAUUSDT，未知拒绝 | 🔴 P0 |
| 开单 sizing | 总权益 × R1~R4 **6/12/18/22%** × 25x | 🔴 P0 |
| VPS 硬止损 | 开仓价 × 2.78%~8.33%，忽略 TV 紧止损 | 🔴 P0 |
| 11x 名义硬顶 | ETH+XAU 合计 ≤ 权益×11 | 🔴 P0 |
| 雷达三重验证 | 价格 + 订单 + 减仓量，防 R4 5% 误判 | 🟡 P1 |
| 钉钉全链路 | 开单/TP/雷达/拦截/平仓 | 🟢 P2 |

---

## 防线总线：TP123 + VPS硬止损 + 雷达

所有「补挂 / 重启 / 人工同向 / 空闲接管」统一走 `_ensure_full_defense_stack()`：

```
_disarm_premature_radar()     ← 清除伪 TP1 / 过早保本线
  ↓
_reconcile_stale_tp_consumed() ← 账本 TP 标记 vs 实盘数量对账
  ↓
_ensure_tp123_prices_from_tv() ← 从 TV 补全 TP1/2/3 价格
  ↓
_enforce_defense_alignment()   ← TP123 比例限价 reduceOnly
  ↓
_maintain_hard_shield()        ← VPS 宽硬止损 Stop-Limit（雷达激活后合并）
  ↓
[若 TP1 三重验证通过]
  _perform_radar_handoff()     ← 原子雷达交棒
  _process_radar_trailing()    ← TP2/TP3 逐级收紧
```

### TP123

- Regime 比例拆分仓位（例 R3 → 18% / 32% / 50%）
- `reduceOnly` 限价，与全平止损 **不抢额度**
- 已成交档位写入 `tp_levels_consumed`，**不再补挂**
- 审计异常（叠单 / 缺档）→ 核武撤 TP 重挂（**不动**已齐雷达线，除非交棒）

### VPS 自主硬止损（v13.38+ · 开仓价百分比）

- **计算**：`硬止损距离 = 开仓价 × 档位%`（TV `tv_sl` 仅参考，不直接挂单）
- **等比呼吸**：ETH 任意价位，各档位亏损空间按百分比缩放
- **执行**：币安 `STOP` 限价单（触发价=VPS止损价，限价±0.15% 缓冲防跳空）
- **持仓期**：硬止损不动；仅 LONG/SHORT 开仓时重算；雷达激活后被保本线取代
- **优先级**：`CLOSE_STOPLOSS` 市价全平 > VPS 缓冲止损 > 忽略 `UPDATE_SL`

| Regime | 档位百分比 | 示例@1800 呼吸 |
|--------|------------|----------------|
| 1 | **2.8%** | ≈50.4U |
| 2 | **3.9%** | ≈70.2U |
| 3 | **5.6%** | ≈100.8U |
| 4 | **8.3%** | ≈149.4U |

### 伪 TP1 / 三重验证（v13.45+）

`_tp1_filled_verified()` 须同时满足：

1. **价格主判**：WS/现价/best 达 TP1 区（`_price_reached_tp1_zone`）
2. **订单辅判**：账本 TP1 已消费 + 盘口 **无** TP1 限价残留
3. **减仓参考**：相对 `_trusted_initial_qty` 减仓量匹配 TP1 切片，且 \> 噪声阈值（2% 开仓量）

未满足却出现保本线 → `_disarm_premature_radar()` 恢复 VPS 宽硬止损。

---

## 雷达状态机（三重验证）

**核心理念**：**TP1 限价实盘三重验证后才激活雷达**；TP1 前只有 VPS 宽硬止损。

| 阶段 | 触发条件 | 多头止损 | 空头止损 |
|------|----------|----------|----------|
| 0 | TP1 未成交 | VPS 宽硬止损 | 同左 |
| 1 | TP1 成交 | 成本 + 0.1% | 成本 − 0.1% |
| 2 | 价格达 TP1→TP2 50% | 最高价 − ATR×1.0 | 最低价 + ATR×1.0 |
| 3 | 达 TP2 | 最高价 − ATR×0.6 | 最低价 + ATR×0.6 |
| 4 | 价格达 TP2→TP3 50% | 最高价 − ATR×0.5 | 最低价 + ATR×0.5 |
| 5 | 达 TP3 | 最高价 − ATR×0.3 | 最低价 + ATR×0.3 |

### 锁存原则

- 止损只向有利方向移动（多头只上移 / 空头只下移）
- 价格回调时止损保持不动，**永不回退**
- 雷达止损优先级高于 VPS 宽硬止损
- 哨兵轮询 5~8 秒更新

### 流程

```
开仓
  → TP123 + VPS 宽硬止损
  → 雷达待命（阶段0）

TP1 实盘限价成交验证通过
  → _perform_radar_handoff()
     ① 保本 SL = 成本 ±0.1%
     ② clamp 到 mark - gap（禁止贴市价）
     ③ 挂雷达 STOP，取代硬止损
     ④ 钉钉 report_shield_disarmed + report_radar_activated

后续向 TP2/TP3 推进
  → 阶段 2~5 逐级收紧，只升不降
```

### 交棒安全

- 先挂保本 STOP → 核实成功才撤硬止损 / 发钉钉  
- `_clamp_radar_sl_for_market()` 保证 SL 距 mark ≥ gap  
- 空间不足 → **延迟交棒**，保留 VPS 硬止损呼吸空间  

---

## 信号与开仓逻辑

### 动作矩阵

| action | 行为 |
|--------|------|
| `LONG` / `SHORT` | 同向筛选 或 反向先平后开 |
| `UPDATE_SL` | 仅更新 `tv_sl` 并换挂 STOP（不单独重挂 TP123） |
| `CLOSE` / `CLOSE_PROTECT` / `CLOSE_TP3` | 撤单 → 全平 → 复位 |

### 反向信号

持多收 `SHORT`（或反之）→ **一律先平后开**，不做同向筛选。

### 同向智能筛选

```
① ATR 变化 (>3%)     → 先平后开
② Regime 变化        → 先平后开
③ 价差 ≥ 0.15%       → 先平后开
④ 否则               → 不重复开仓，仅刷新 TP123 + SL
```

空仓 5 分钟内重复同向信号 → 忽略 + 钉钉。

### 动态加仓（v6.9.93 / v13.25）

对齐 TV **gemini止损_动态加仓**：

| 类型 | sizing 规则 |
|------|-------------|
| **OPEN** | VPS 自主计算（`VPS_RISK_PCT` × 档位系数 × 25x），**不以 TV risk_pct 为准** |
| **PYRAMID** | `add_qty = base_qty × TV qty_ratio`（首仓 base 不变） |
| **PROFIT_ADD** | 同上，比例由 TV 按档位动态下发 |

**档位默认加仓比例 / 次数上限**（TV 未传 qty_ratio 时回退）：

| 档位 | 加仓比例 | 最多次数 | TP123 减仓比例 |
|------|----------|----------|----------------|
| R1 | 0%（禁止） | 1 | **25/35/40** |
| R2 | 30% | 2 | **20/35/45** |
| R3 | 50% | 2 | **18/32/50** |
| R4 | 70% | 3 | **5/20/75** |

加仓后：**撤旧 TP → 按 TV `tv_tp1/2/3` 价格 + 新总头寸重挂 TP123**（`open_regime` 比例，已成交档跳过），并同步 **tv_sl + 雷达**（TP1 后推升保本线），钉钉实盘核实。

```
_add_to_position()
  → 市价加仓核实
  → _realign_after_position_add()
     ① 刷新 TV TP 价格
     ② 撤全部旧 TP 限价单（数量已过期）
     ③ _enforce_defense_alignment() 按新仓重挂
     ④ 未齐 → 核武重挂
     ⑤ _maintain_hard_shield() + 雷达推升（TP1 后）
```

### 人工 / orphan 持仓（空闲巡检 12s）

VPS 账本空仓但交易所有仓：

- **同向** → `_perform_live_takeover()`：`_ensure_full_defense_stack()` 挂 TP123 + tv_sl + 雷达待命  
- **反向 TV** → 强制全平 + 钉钉  
- **加减仓** → 按比例重算 TP123；TV 加仓信号走 `_realign_after_position_add()`  

### 误清场防护（v13.21+）

- `_confirm_position_flat()`：多次 REST 复核才认定全平  
- 重启后 45s 哨兵宽限期  
- 全平分类：`_infer_flat_close_meta()` 区分 TP 吃完 / 交易所 STOP / 人工  

---

## 哨兵 + 空闲巡检

### 哨兵轮询

| 状态 | 间隔 |
|------|------|
| 常态 | 6s |
| 雷达预热（进度≥50%） | 3s |
| 雷达已激活 | 2s |

每 tick：持仓核实 → best_price → 人工异动 → `_process_directional_defenses()` → Guardian TP 审计。

### 空闲巡检

`IDLE_PATROL_INTERVAL_SEC = 12`：仅在 **monitoring=False 且 VPS 空仓** 时扫描 orphan 持仓并接管。

---

## 重启 / 人工接管

```
recover_state_on_startup()
  → 单例锁 logs/.recover_singleton.lock（防多 worker 重复接管）
  → 读 state + TV 日志
  → 有仓：_ensure_full_defense_stack(source="recovery")
  → _bootstrap_live_defenses_after_recover()
  → 钉钉 report_recover_takeover
  → 启动哨兵 + WS
```

---

## 钉钉推送链条

| 场景 | 函数 | 说明 |
|------|------|------|
| 开仓 | `report_supervisor_open` | 核实持仓 + TP 对齐 |
| tv_sl 已挂 | `report_adverse_shield_armed` | 仅 TP1 前 |
| 雷达交棒 | `report_shield_disarmed` | **保本 STOP 核实后** |
| 雷达激活 | `report_radar_activated` | 首次保本，含进度 |
| 雷达推升 | `report_intervention` | 后续移动 |
| TP 成交 | `report_tp_fill` | 减仓检测 |
| 人工异动 | `report_manual_position_change` | 加减仓 / 全平 |
| 重启接管 | `report_recover_takeover` | 含 TP/雷达/tv_sl 审计 |
| 雷达解除 | `report_system_alert` | 伪 TP1 / 恢复呼吸空间 |
| 反向强平 | `report_force_align` | TV 反向 |

**预期雷达交棒双推（v13.24）：** 先 `report_radar_activated`（保本已挂），交棒通知 `report_shield_disarmed` 中 **live_qty > 0**。

---

## Regime 矩阵（对齐 TV v6.9.93 · gemini止损_动态加仓）

**TP123 减仓比例按档位不同**（Pine `qty_percent`，非全档位相同）：

| 档位 | 保证金 | TP1/TP2/TP3 比例 | 雷达 activation | 追踪 ATR 倍率 |
|------|--------|------------------|-----------------|---------------|
| R1 | 15% | **25/35/40** | **92%** → TP1 | 0.20 (TP1后) |
| R2 | 25% | **20/35/45** | **92%** | 0.20 |
| R3 | 35% | **18/32/50** | **95%** | 0.30 (TP2后) |
| R4 | 50% | **5/20/75** | **95%** | 0.30 |

实盘以 **`open_regime`（开仓档位）** 锁定 TP 比例，整笔单不变；加仓后按新总头寸重算数量，比例仍用开仓档。

```
activation_price = entry ± |tp1 - entry| × activation
trail_SL_LONG = max(best - trail_offset, entry + 0.4×ATR)
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
grep 'v13.26.0-add-tp-radar-realign' position_supervisor_binance.py
grep 'DEPLOY_SCRIPT_VERSION' deploy_binance.sh

source venv/bin/activate    # 如有 venv
pip install -r requirements.txt
bash deploy_binance.sh

# 验收
curl -s http://127.0.0.1:5003/health | python3 -m json.tool
tail -f logs/binance_brain.log
```

### deploy_binance.sh 流程

1. 校验脚本完整性（防 dingtalk 误覆盖）
2. kill 端口 / gunicorn / 删 recover 锁
3. Python 语法检查
4. supervisor 版本门控
5. Gunicorn **1 worker × 10 threads**（`--daemon`）
6. 健康检查重试 6 次

### 环境变量

```env
BINANCE_API_KEY=
BINANCE_API_SECRET=
WEBHOOK_SECRET=
DINGTALK_WEBHOOK=
DINGTALK_SECRET=
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
  "entry_type": "OPEN"
}
```

| 字段 | 说明 |
|------|------|
| `symbol` | **必填** — `ETHUSDT.P` / `XAUUSDT.P`，网关按品种路由 |
| `tv_sl` | TV 紧止损，**仅参考**；实盘挂单价由 VPS 按档位% 计算 |
| `tv_tp1~3` | TP123 挂单价 + 雷达距离基准 |
| `entry_type` | OPEN / PYRAMID / PROFIT_ADD |

---

## 日志与排错

| 文件 | 说明 |
|------|------|
| `logs/binance_brain.log` | 大脑主日志（哨兵/雷达/交棒/TP） |
| `logs/binance_tv_journal.jsonl` | TV 信号流水 |
| `logs/binance_open_journal.jsonl` | 开仓/接管流水 |
| `binance_vps_state.json` | 运行时状态 |

**常用 grep：**

```bash
tail -f logs/binance_brain.log
grep -E '雷达交棒|交棒延迟|TP1未成交|解除过早雷达|核武|空闲巡检' logs/binance_brain.log | tail -50
```

| 现象 | 排查 |
|------|------|
| git pull 失败 | 见上文 `git reset --hard` |
| 只有 TP23 无 TP1 | 查 `伪TP` / `trusted_initial` / 重启 `_ensure_full_defense_stack` |
| 微盈就全平、无雷达钉钉 | v13.24 前：贴市价 STOP；升级后查「交棒延迟」日志 |
| Permission denied 日志 | 用 `tail -f logs/binance_brain.log`，不要直接执行日志文件名 |

---

## 版本演进

| 版本 | 要点 |
|------|------|
| **v13.52** | **硬止损只挂 VPS 宽价（拒 TV 紧止损污染）+ 雷达强制三重价/单/仓 + 重启不自动平仓** |
| **v13.51** | **硬止损改 closePosition（不抢 TP reduceOnly）+ 全平勿误标 TV tv_sl + 开仓禁 recover 核武撤** |
| **v13.50** | **短周期仓位权重 6/12/18/22% + 名义硬顶 11x（ETH45m/XAU50m）** |
| **v13.49** | **雷达三重验证加固：理想保本须距市价安全才交棒；禁止贴市毛刺止损；钉钉标题标注 ETH/XAU** |
| **v13.48** | **钉钉 TP/头寸单位按品种透传 XAU/ETH；缺 symbol 拒绝默念 ETH；全文扫描强化检测** |
| **v13.47** | **VPS 检查清单 + check_vps_logic.py；总权益 sizing；README 对齐** |
| **v13.46** | **双品种 ETH+XAU：保证金 sizing、VPS 硬止损、9x 名义硬顶** |
| v13.45 | TP1 三角对账 — 防 R4 5% 开仓微漂误启雷达 |
| v13.29 | 全平后核武撤净 TP123+止损 |

---

## 双工厂差异速查

| 项目 | 币安 | 深币 |
|------|------|------|
| 大脑文件 | `position_supervisor_binance.py` | `position_supervisor_deepcoin.py` |
| 止损实现 | `closePosition` STOP_MARKET 单槽合并 | tv_sl 条件单 + 雷达触发单分离 |
| 蚂蚁仓 | ≤ 0.004 ETH | ≤ 1 张 |
| WS | `ethusdt@markPrice@1s` | `market-latest` |

逻辑、Regime、雷达公式、钉钉语义 **保持一致**；改一侧必须镜像另一侧并同版本号推送。

---

*GEMINI Quant · 双轨智慧雷达 · v13.52.0-vps-sl-radar-triad-strict*

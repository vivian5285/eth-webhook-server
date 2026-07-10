# GEMINI 双轨交易工厂 · 统一实盘逻辑

**当前版本：`v13.25.0-dynamic-add`**

TradingView Webhook → 交易所永续自动化引擎。**币安**与**深币**两套 VPS 共用同一套「军师大脑」逻辑（`position_supervisor_*.py` 镜像实现），仅 **计量单位 / 交易所 API / 钉钉主题** 不同。

| 工厂 | GitHub | VPS 目录 | 端口 | 单位 | 杠杆 | 钉钉 |
|------|--------|----------|------|------|------|------|
| **币安** | `vivian5285/eth-webhook-server` | `~/binance-engine` | **5003** | ETH | **15x** | 黄金 |
| **深币** | `vivian5285/deepcoin-hft-server-main` | `~/deepcoin-hft-server` | **5004** | 张 (0.1 ETH) | **15x** | 紫金 |

**健康检查：**

```bash
curl -s http://127.0.0.1:5003/health   # 币安
curl -s http://127.0.0.1:5004/health   # 深币
# 期望 version: v13.25.0-dynamic-add
```

---

## 目录

1. [统一架构](#统一架构)
2. [防线总线：TP123 + tv_sl + 雷达](#防线总线tp123--tv_sl--雷达)
3. [雷达状态机（v13.24）](#雷达状态机v1324)
4. [信号与开仓逻辑](#信号与开仓逻辑)
5. [哨兵 + 空闲巡检](#哨兵--空闲巡检)
6. [重启 / 人工接管](#重启--人工接管)
7. [钉钉推送链条](#钉钉推送链条)
8. [Regime 矩阵（对齐 TV v6.9.86）](#regime-矩阵对齐-tv-v68986)
9. [VPS 部署与更新](#vps-部署与更新)
10. [日志与排错](#日志与排错)
11. [版本演进](#版本演进)

---

## 统一架构

```
TradingView Alert (JSON)
        ↓
app.py                          ← 网关：Secret 校验 → 异步入队 → 即时 200
        ↓
position_supervisor_*.py        ← 唯一实盘决策层（120s 互斥锁 + 信号队列）
├── 同向智能筛选（ATR → 档位 → 价差）
├── 开仓 / 加仓 / 反向先平后开
├── _ensure_full_defense_stack()  全链：TP123 + tv_sl + 雷达待命
├── 哨兵循环（2~6s 自适应）
├── 空闲巡检（12s，VPS 空仓时接管 orphan 持仓）
├── 重启闪电接管 + 单例锁
└── 钉钉：先核实盘口再推送
        ↓
*_client.py                     ← REST 交易 + 公开 WS 推价
dingtalk.py                     ← 必须与 supervisor 成对更新
```

**设计原则（两工厂一致）：**

- 网关 **不做实盘决策**
- 硬止损价 **exclusively 来自 TV `tv_sl`**（无 ±10% fallback）
- TP1 **实盘成交验证通过前**，雷达 **待命**，保留 `tv_sl` 宽止损呼吸空间
- 雷达交棒：**先挂保本 STOP 并核实 → 再撤 tv_sl → 再钉钉**（禁止先撤后裸奔）
- 每个 `closePosition` / 全平止损槽位同一方向 **只能存在一个**

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

## 防线总线：TP123 + tv_sl + 雷达

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
_maintain_hard_shield()        ← tv_sl closePosition（可与雷达合并为单槽）
  ↓
[若 TP1 已验证成交且达激活比]
  _perform_radar_handoff()     ← 原子雷达交棒
  _process_radar_trailing()    ← 后续推升保本线
```

### TP123

- Regime 比例拆分仓位（例 R3 → 18% / 32% / 50%）
- `reduceOnly` 限价，与全平止损 **不抢额度**
- 已成交档位写入 `tp_levels_consumed`，**不再补挂**
- 审计异常（叠单 / 缺档）→ 核武撤 TP 重挂（**不动**已齐雷达线，除非交棒）

### tv_sl 硬止损

- 来源：TV 信号字段 `tv_sl` 或 `UPDATE_SL`
- 类型：币安 `STOP_MARKET closePosition`；深币条件触发全平
- TP1 前：**始终维护**，给策略呼吸空间
- 与雷达合并（币安）：`effective = max(雷达, tv_sl)`（LONG）/ `min`（SHORT）

### 伪 TP1 防护（v13.22+）

`_tp1_filled_verified()` 需同时满足：

1. 账本 `tp_levels_consumed` 含 1  
2. 相对 `_trusted_initial_qty` 有对应减仓  
3. 盘口 **无** TP1 限价单残留  

不满足 → `_disarm_premature_radar()` 恢复 `tv_sl`，钉钉「雷达解除·恢复呼吸空间」。

---

## 雷达状态机（v13.24）

对齐 TradingView **v6.9.86**（`trailTight=0.62`）：

| 常量 | 值 | 含义 |
|------|-----|------|
| `TV_TRAIL_TP2_ATR` | ≈0.20 ATR | TP1 后追踪 |
| `TV_TRAIL_TP3_ATR` | ≈0.30 ATR | TP2 后追踪 |
| `TV_BOOT_SL_ATR` | 0.40 ATR | 保本底线 entry ± 0.4 ATR |
| `RADAR_STOP_MIN_GAP` | max(2.5U, 0.12%) | 止损与现价最小距离，**防刚挂就全平** |

### 阶段

```
开仓
  → TP123 + tv_sl 宽止损
  → 雷达待命（进度 0~92/95%）

TP1 实盘成交验证通过
  → 仍保留 tv_sl，直到价格达 activation 比

现价达 activation（entry ± tp1_dist × 0.92/0.95）
  → _perform_radar_handoff()
     ① 计算保本 SL（best - trail，不低于 boot 线）
     ② clamp 到 mark - gap（禁止贴市价）
     ③ _ensure_radar_sl / 原子换 STOP
     ④ 核实挂出
     ⑤ 钉钉 report_shield_disarmed + report_radar_activated

后续
  → 推升/下压保本线，守 TP2/TP3
  → 钉钉 report_intervention（120s 冷却）
```

### v13.24 交棒修复（解决「刚激活就全平」）

**旧问题：**

1. `_force_disarm_shield_before_radar` 定义了但 **先撤 tv_sl**，存在裸奔窗口  
2. 保本线 `entry + 0.4 ATR` 可能 **≥ 现价**，STOP 一挂即触发 `closePosition` 全平  
3. 钉钉显示持仓 0 ETH（全平后才推交棒通知）

**现逻辑 `_perform_radar_handoff()`：**

- 先挂保本 STOP → 核实成功才撤 tv_sl / 发钉钉  
- `_clamp_radar_sl_for_market()` 保证 SL 距 mark ≥ gap  
- 空间不足 → **延迟交棒**，日志「雷达交棒延迟…保留 tv_sl」  
- 交棒失败 → 回滚维护 tv_sl  

---

## 信号与开仓逻辑

### 动作矩阵

| action | 行为 |
|--------|------|
| `LONG` / `SHORT` | 同向筛选 或 反向先平后开 |
| `UPDATE_SL` | 仅更新 `tv_sl` 并换挂 STOP（PYRAMID/PROFIT_ADD 不重建 TP123） |
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
| **OPEN** | VPS 自主计算（`VPS_RISK_PCT` × 档位系数 × 15x），**不以 TV risk_pct 为准** |
| **PYRAMID** | `add_qty = base_qty × TV qty_ratio`（首仓 base 不变） |
| **PROFIT_ADD** | 同上，比例由 TV 按档位动态下发 |

**档位默认加仓比例 / 次数上限**（TV 未传 qty_ratio 时回退）：

| 档位 | 加仓比例 | 最多次数 |
|------|----------|----------|
| R1 | 0%（禁止） | 1 |
| R2 | 30% | 2 |
| R3 | 50% | 2 |
| R4 | 70% | 3 |

加仓后：**只更新 tv_sl + 钉钉实盘核实**，TP123 与雷达状态机不变（继续守首仓 open_regime 比例）。

### 人工 / orphan 持仓（空闲巡检 12s）

VPS 账本空仓但交易所有仓：

- **同向** → `_perform_live_takeover()`：`_ensure_full_defense_stack()` 挂 TP123 + tv_sl + 雷达待命  
- **反向 TV** → 强制全平 + 钉钉  
- **加减仓** → 按比例重算 TP123，PYRAMID 只更新 SL  

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

## Regime 矩阵（对齐 TV v6.9.86）

| 档位 | 保证金 | TP 比例 | 雷达 activation | 追踪 ATR 倍率 |
|------|--------|---------|-----------------|---------------|
| R1 | 15% | 25/35/40 | **92%** → TP1 | 0.20 (TP1后) |
| R2 | 25% | 20/35/45 | **92%** | 0.20 |
| R3 | 35% | 18/32/50 | **95%** | 0.30 (TP2后) |
| R4 | 50% | 5/20/75 | **95%** | 0.30 |

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
grep 'v13.25.0-dynamic-add' position_supervisor_binance.py
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
| `tv_sl` | **必填**（或后续 UPDATE_SL）— 唯一硬止损价 |
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
| v13.17 | TV 反向 → 强制全平 |
| v13.18 | 12s 空闲巡检 orphan 接管 |
| v13.18.1 | 修复同向人工误强平 |
| v13.19~20 | TP1 门控雷达；`_ensure_full_defense_stack` 统一全链 |
| v13.21 | 误清场复核；stale tp_consumed 重置 |
| v13.22 | `_trusted_initial_qty`；2/2 TP 审计拒绝 |
| v13.23 | `_tp1_filled_verified` 雷达门控；伪 TP 解除 |
| **v13.24** | **安全雷达交棒：先挂保本、mark gap、失败回滚 tv_sl** |
| **v13.25** | **动态加仓：首仓 VPS sizing，加仓 base×TV qty_ratio + 档位次数上限** |

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

*GEMINI Quant · 双轨智慧雷达 · v13.25.0-dynamic-add*

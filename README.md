# 币安 Binance · ETH 永续 Webhook 交易系统

**当前版本：`v13.5.2-flat-gate`**

TradingView Webhook → 币安 **ETHUSDT 永续** 自动化引擎。与深币 VPS **实盘逻辑完全对齐**（仅单位/交易所 API 不同），按 **ETH** 计量，**10 倍杠杆**，钉钉 **黄金主题**。

---

## VPS 部署信息

| 项目 | 值 |
|------|-----|
| 目录 | `~/binance-engine` |
| 端口 | **5003** |
| 杠杆 | **10x**（开仓前自动 `set-leverage`） |
| 健康检查 | `GET /health` |
| 主日志 | `logs/binance_brain.log` |
| 状态文件 | `binance_vps_state.json` |
| 部署脚本 | `bash deploy_binance.sh` |

---

## 系统架构

```
TradingView Webhook
        ↓
    app.py（网关 · 即时 200 响应）
        │  enqueue_signal → 信号队列
        ↓
position_supervisor_binance.py（智慧大脑 · 工作线程）
├── 同向智能筛选（ATR 优先 · v13.5.1）
├── TV/开仓日志持久化
├── 重启闪电接管 + TV 对账
├── TP123 比例审计 + 增量/核武补挂
├── 雷达移动保本（WS 推价 + STOP_MARKET）
├── 空仓对账 + 蚂蚁仓扫尾
└── 哨兵循环（持仓/人工异动/定期扫描）
        ↓
binance_client.py（REST 交易 + 公开 WS 行情）
dingtalk.py（黄金钉钉 · 含智能筛选播报）
```

**设计原则：** 网关不做实盘决策，只负责验签、入队、快速应答；所有开仓/平仓/止盈/风控逻辑在 `position_supervisor_binance.py` 的信号工作线程中串行执行，避免 TV 超时与竞态。

---

## 实盘需求与执行逻辑（v13.5.1）

### 1. 信号总线

| 阶段 | 行为 |
|------|------|
| TV 推送 | `POST /webhook`，JSON 含 `action/regime/atr/price/tv_tp1~3` |
| 网关 | 校验 `secret` → 写入信号队列 → **立即返回 200** |
| 大脑线程 | 逐条 `_process_signal`：更新 `regime/atr/tv_price/tv_tps` → 执行动作 |

### 2. 反向信号（一律先平后开）

持 **多** 收到 `SHORT`，或持 **空** 收到 `LONG`：

1. 撤销全部挂单  
2. 市价全平  
3. 再次撤单清场  
4. 按新 TV 信号市价开仓 → 挂 TP123 → 启动哨兵 + WS  
5. 记录 `open_regime`、`open_atr`（本次开仓时的档位与 ATR）

**不做** 同向智能筛选；反方向永远原子换防。

### 3. 同向智能筛选（核心 · ATR 第一优先级）

已有持仓且 TV 方向与实盘 **相同** 时，按以下 **严格顺序** 决策：

```
┌─────────────────────────────────────────────────────────┐
│  ① ATR 是否变化？（持仓 open_atr vs TV atr，偏差 >3%）   │
│     是 → 先平后开（刷新仓位）+ 钉钉「刷新仓位 · ATR变化」  │
├─────────────────────────────────────────────────────────┤
│  ② 档位 regime 是否变化？（open_regime vs TV regime）    │
│     是 → 先平后开（保证金比例/TP比例/雷达参数均变）       │
├─────────────────────────────────────────────────────────┤
│  ③ 理论开仓价差是否 ≥ 阈值？                              │
│     比较：实盘 entry vs TV price，相对 ETH 现价百分比      │
│     是（≥0.15%）→ 先平后开 + 钉钉「刷新仓位 · 价差达标」  │
├─────────────────────────────────────────────────────────┤
│  ④ 以上均未触发                                         │
│     → 不重复市价开仓                                     │
│     → 向交易所核实当前持仓                               │
│     → 按新 TV 价刷新 TP123（保留原仓位与雷达状态）         │
│     → 钉钉「同向持仓 · 仅刷新止盈」                      │
└─────────────────────────────────────────────────────────┘
```

**为何 ATR 优先？** ATR 反映 TV 策略当前波动率档位；变化意味着止盈距离、雷达追踪倍数、市场强弱判断均已更新，必须 **整仓刷新**（先平后开），而非在原仓上叠单。

**为何需要价差阈值？** 同方向、同 ATR、同档位下，若 TV 理论价与实盘成本几乎相同，再开一单只会增加手续费与滑点；此时 **只更新 TP123 挂单价** 即可跟踪 TV 最新目标。

### 4. 空仓短时去重（5 分钟）

盘口 **无持仓** 时，若 5 分钟内再次收到几乎相同的同向信号：

| 检查顺序 | 条件 |
|----------|------|
| ① ATR | 与上次信号 ATR 相似（偏差 ≤3%） |
| ② 档位 | regime 相同 |
| ③ 价差 | TV 理论价偏差 < 0.15% |

三者均满足 → **忽略开仓**，钉钉「短时重复同向 · 已忽略」。  
任一项不满足 → 正常开仓（例如 ATR 已变，视为新行情）。

### 5. 智能筛选参数（代码常量）

| 常量 | 值 | 含义 |
|------|-----|------|
| `SAME_DIR_MIN_SPREAD_PCT` | **0.15%** | 理论开仓价差阈值（相对 ETH 现价） |
| `SAME_DIR_DEDUP_SEC` | **300s** | 空仓重复信号去重窗口 |
| `ATR_SIMILAR_RATIO` | **3%** | ATR 相似判定（\|a−b\|/max(a,b) ≤ 3% 视为未变） |

### 6. 持久化字段（`binance_vps_state.json`）

| 字段 | 说明 |
|------|------|
| `open_regime` | 当前持仓开仓时的 TV 档位 |
| `open_atr` | 当前持仓开仓时的 TV ATR |
| `watched_entry` | 实盘成本价 |
| `tv_tps` | 最新 TV 止盈 1/2/3 目标价 |
| `last_tv_side` | TV 最新方向（LONG/SHORT） |

重启接管时若检测到实盘有仓但缺少 `open_atr`，自动回填为当前 `current_atr`。

### 7. 钉钉智能筛选播报（`dingtalk.py` 必须同步）

| 类型 | 标题 | 触发条件 |
|------|------|----------|
| 仅刷新止盈 | 🧠 同向持仓 · 仅刷新止盈 | ATR 未变 + 价差不足 |
| 刷新仓位 | 🧠 同向持仓 · 刷新仓位 | ATR/档位/价差触发先平后开 |
| 忽略重复 | 🧠 短时重复同向 · 已忽略 | 空仓 5 分钟内重复信号 |

每条推送包含：**持仓 ATR vs TV ATR**、理论价差、档位、实盘核实明细、TP123 审计（刷新止盈时）。

> ⚠️ **v13.5+ 部署必须同时更新 `position_supervisor_binance.py` 与 `dingtalk.py`**。旧版 dingtalk 使用 `atr=` 参数，新版为 `open_atr/tv_atr`，不匹配会导致 `TypeError`。

---

## 其他核心能力

### 重启闪电接管
- 读取状态文件 + TV 日志 + 开仓日志  
- TV 方向强制对齐；方向背离 / TV 已 CLOSE → 核武清场  
- 人工加减仓 → 写日志 + 钉钉 + 按比例重挂 TP  
- 雷达恢复 + WS + 哨兵  

### 限价止盈 TP123
- Regime 1~4 对应不同 TP 比例与保证金比例  
- 例：R3 → `18% / 32% / 50%`  
- `reduceOnly` 限价，tick/step 对齐；重复单核武撤净重挂  

### 雷达移动保本
- 达 TP1 距离 × activation 比例 → 激活雷达  
- ATR × 档位 `trail_offset` 推升/下压 **STOP_MARKET**  
- WS：`ethusdt@markPrice@1s`；推止损时 **只撤 STOP，保留 TP123**  
- 哨兵轮询：6s / 3s / 2s（常态/预热/激活）  

### 空仓对账与蚂蚁仓扫尾
- ≤0.004 ETH 蚂蚁仓，或 TP 吃完残量 ≤12% 且无 TP 单 → 自动扫尾  
- 宕机补发「完美胜利」钉钉；空仓每 30s 空闲巡检  

### 日志与审计

| 文件 | 说明 |
|------|------|
| `logs/binance_tv_journal.jsonl` | 每条 TV 信号 |
| `logs/binance_open_journal.jsonl` | 开仓 / 接管记录 |
| `logs/binance_brain.log` | 大脑主日志 |
| `binance_vps_state.json` | 运行时状态 |

---

## 四档 Regime 矩阵

| 档位 | 保证金占比 | TP 比例 (1/2/3) | 雷达激活 | 追踪倍数 |
|------|-----------|-----------------|----------|----------|
| R1 | 15% | 25% / 35% / 40% | 40% | 0.40×ATR |
| R2 | 25% | 20% / 35% / 45% | 50% | 0.60×ATR |
| R3 | 35% | 18% / 32% / 50% | 60% | 0.90×ATR |
| R4 | 50% | 5% / 20% / 75% | 70% | 1.30×ATR |

开仓量：`余额 × margin × 10x ÷ 价格`，步长 **0.001 ETH**。

---

## 环境变量（`.env`）

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

## TradingView Webhook

**URL：** `http://你的VPS:5003/webhook`

```json
{
  "action": "SHORT",
  "secret": "528586",
  "regime": 3,
  "atr": 30.0,
  "price": 1560.0,
  "tv_tp1": 1537.85,
  "tv_tp2": 1517.40,
  "tv_tp3": 1499.22,
  "reason": "可选说明"
}
```

| action | 说明 |
|--------|------|
| `LONG` / `SHORT` | 经 **同向智能筛选** 或 **先平后开** 后开仓/刷新 |
| `CLOSE` | 换防清场 |
| `CLOSE_PROTECT` | 保护性全平 |
| `CLOSE_TP3` | TP3 吃满收网 |

---

## VPS 部署（标准流程）

```bash
cd ~/binance-engine
git fetch origin && git reset --hard origin/main

# 版本与 dingtalk 门控（v13.5+ 必查）
grep v13.5.1-atr-priority position_supervisor_binance.py app.py
grep -E 'report_smart_same_dir_decision|open_atr|tv_atr' dingtalk.py

pip3 install -r requirements.txt
bash deploy_binance.sh

# 验收
curl -s http://127.0.0.1:5003/health
tail -60 logs/binance_brain.log
```

**健康检查响应：**

```json
{"service":"binance_webhook","status":"ok","version":"v13.5.1-atr-priority"}
```

**部署成功日志示例：**

```
🟢 Binance Client v13.4.6-flat-reconcile 已加载
🧠 币安 VPS [v13.5.1-atr-priority] 军师托管版已加载
📡 币安公开 WS 启动: ETHUSDT@markPrice@1s
🧠 同向 [LONG] ATR 28.50→32.10 变化 → 先平后开重入
🧠 同向智能处理完成: ATR未变+价差不足，未再开仓，TP123 已按新 TV 价刷新
```

---

## 与深币系统对比

| 项目 | 币安 | 深币 |
|------|------|------|
| 单位 | ETH | 张（0.1 ETH/张） |
| 杠杆 | 10x | 10x |
| 端口 | 5003 | 5004 |
| 钉钉主题 | 黄金 | 紫金 |
| 止损类型 | STOP_MARKET | 条件单 trigger |
| WS 频道 | markPrice@1s | market-latest |
| 同向智能筛选 | ✅ v13.5.1 | ✅ v13.5.1（逻辑一致） |
| 蚂蚁仓阈值 | ≤0.004 ETH | ≤1 张 |

---

## 版本演进摘要

| 版本 | 要点 |
|------|------|
| v13.3-smart-guard | TV/开仓日志、比例审计、增量补挂 |
| v13.4-nuclear-guard | 核武清场重挂 |
| v13.4.3-ws-radar | WS 推价 + REST 限频兜底 |
| v13.4.6-flat-reconcile | 空仓对账、蚂蚁仓扫尾、宕机补发钉钉 |
| v13.5.0-smart-same-dir | 同向价差/档位筛选、TP 刷新、信号队列 120s 锁 |
| **v13.5.1-atr-priority** | **ATR 第一优先级决策链、open_atr 持久化、钉钉三态播报** |
| **v13.5.2-flat-gate** | **开仓前空仓闸门、先平后开验证、walletBalance 仓位预算、TP 挂前撤净、叠仓/超标告警** |

---

## 仓位预算公式（Regime 保证金线）

```
保证金 = walletBalance × regime.margin
名义价值 = 保证金 × 10x
目标数量 = 名义价值 ÷ ETH 现价        （币安，步长 0.001 ETH）
目标张数 = 名义价值 ÷ (现价 × 0.1)    （深币，整数张）
```

| 档位 | 保证金占比 | 700U 本金示例（10x） |
|------|-------------|---------------------|
| R1 | 15% | 105U 保证金 → 1050U 名义 |
| R2 | 25% | 175U → 1750U |
| R3 | 35% | 245U → 2450U |
| R4 | 50% | 350U → 3500U |

> v13.5.2 起使用 **walletBalance（本金）** 计算，不再用含浮盈的 marginBalance 放大仓位。  
> **先平后开** 必须空仓验证通过才允许新开；平仓失败则 **拒绝叠仓** 并钉钉告警。

---

## 注意事项

1. 部署务必 `git reset --hard origin/main`，且 **supervisor + dingtalk 成对更新**。  
2. `deploy_binance.sh` 会校验 v13.4.6+ / v13.5+ 及 `dingtalk.py` 智能筛选函数。  
3. 重启后钉钉「闪电接管报告」应显示 TP **比例审计 ✅**。  
4. 仅同时持有一个方向；**反向** 永远先平后开；**同向** 走智能筛选链。  
5. 建议 one-way 持仓模式，ETHUSDT 永续。  

---

*Quant AI · 币安黄金趋势大波段引擎*

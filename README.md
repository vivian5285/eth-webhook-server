# GEMINI 双轨交易工厂 · 统一实盘逻辑

**当前版本：`v13.88.1-open-sl-failsafe`**  
**TV 策略 schema：`v6.9.108`**（`webhook_parser.TV_STRATEGY_VERSION`）  
**生产唯一大脑：`position_supervisor_binance.py`**（遗留 `position_supervisor.py` 已删除）

TradingView Webhook → 交易所永续自动化。**币安 ETH+XAU** 与 **深币** 共用同一套军师逻辑，仅计量单位 / 交易所 API / 钉钉主题不同。

| 工厂 | GitHub | VPS 目录 | 端口 | 品种 | 杠杆 | 钉钉 |
|------|--------|----------|------|------|------|------|
| **币安** | `vivian5285/eth-webhook-server` | `~/binance-engine` | **5003** | ETH + XAU | **TV `leverage`** | 黄金 |
| **深币** | `vivian5285/deepcoin-hft-server-main` | `~/deepcoin-hft-server` | **5004** | ETH + XAU 张 | **TV `leverage`** | 紫金 |

```bash
curl -s http://127.0.0.1:5003/health   # 币安
curl -s http://127.0.0.1:5004/health   # 深币
# 期望 version 含 v13.88.1-open-sl-failsafe
# 期望 leverage: "tv_webhook" · sizing: "TV_RISK_FORMULA"
# 期望 tv_strategy: v6.9.108

python check_vps_logic.py              # 静态自查，期望全部通过
# 清单：docs/VPS实盘检查清单.md
```

---

## 目录

1. [核心铁律（一句话）](#核心铁律一句话)
2. [统一架构](#统一架构)
3. [仓位公式与对照表](#仓位公式与对照表)
4. [防线总线：TP123 + TV硬止损 + 雷达](#防线总线tp123--tv硬止损--雷达)
5. [开仓裸仓闸](#开仓裸仓闸)
6. [雷达状态机](#雷达状态机)
7. [信号与执行顺序](#信号与执行顺序)
8. [哨兵与空闲巡检](#哨兵与空闲巡检)
9. [重启 / 人工接管](#重启--人工接管)
10. [钉钉推送](#钉钉推送)
11. [Regime 矩阵](#regime-矩阵对齐-tv-v69108)
12. [VPS 部署](#vps-部署)
13. [日志与排错](#日志与排错)
14. [实盘事故备忘](#实盘事故与优化备忘必读)
15. [版本演进](#版本演进)
16. [双工厂差异](#双工厂差异速查)

---

## 核心铁律（一句话）

```
TV 到达 → 先平干净 → 再开仓 → 挂 TP123 + tv_sl（各一次）→ 雷达候命(WS) → 钉钉确认一次
```

| 铁律 | 说明 |
|------|------|
| **完全按 TV 执行** | 仓位用 `risk_pct` / `qty_ratio` / `leverage`；硬止损用 `tv_sl` **原值**；TP 用 `tv_tp1/2/3` |
| **禁止旧逻辑** | 无档位保证金%×杠杆、无固定 25x、无 `tv_sl±buffer`、无 maxNotional 硬上限、无 TP 成交后补挂 |
| **先平后开** | 同向/反向/同秒开+平 → 永远先平干净再开；终态有仓且防线齐 |
| **三轨不抢份额** | TP123 = `reduceOnly`；TV硬止损 ∪ 雷达 = `closePosition` **单槽** |
| **雷达只前进** | 交棒后止损只向有利方向推升，禁止「解除雷达」回撤到 `tv_sl` |
| **硬止损失败** | 开仓路径 / 滞后核实仍无 STOP → **撤销开仓防裸奔** |
| **杠杆同源** | `set_leverage(TV.leverage)`；缺 leverage → **拒单**（例：TV=5 → API=5） |

---

## 统一架构

```
TradingView Alert (JSON + symbol + bar_index/seq)
        ↓
app.py                              ← Secret / 品种路由 / 异步入队 / 200
        ↓
position_supervisor_binance.py      ← 唯一生产大脑（ETH / XAU 独立实例）
├── tv_seq：bar_index↑ · 同 bar 动作优先 CLOSE→OPEN
├── 无菌先平后开（凡 OPEN 一律 FULL_REENTRY）
├── TV 唯一公式 sizing（无硬上限；双品种合计 ≤ 权益×13）
├── set_leverage = TV leverage
├── 挂一次 TP123（reduceOnly）+ tv_sl（closePosition）
├── 雷达候命 → mark@1s 达激活线交棒 → 适度追随（只前进）
├── 哨兵 5~8s · 开仓宽限 90s · 空闲巡检 12s
└── 钉钉：开仓核实一条 + 标题去重；失败退避 / 企微备用
```

**生产模块**

| 模块 | 说明 |
|------|------|
| `app.py` | Flask 网关 |
| `position_supervisor_binance.py` | **唯一军师大脑** |
| `webhook_parser.py` | 解析 / sizing 公式 / 雷达比例 |
| `binance_client.py` | REST + WS；`set_leverage` 拒固定回退 |
| `tv_seq.py` | 时序幂等 |
| `dingtalk.py` | 播报 |
| `symbol_config.py` | ETH/XAU 路由 |
| `check_vps_logic.py` | 静态自查 |
| ~~`position_supervisor.py`~~ | **已删除** |

---

## 仓位公式与对照表

### 唯一公式

```
止损距离 = |price − tv_sl|
风险金额 = 账户权益 × (risk_pct / 100)
理论仓位 = 风险金额 / 止损距离
杠杆限制 = 账户权益 × leverage / price
下单量   = min(理论, 杠杆限制) × qty_ratio
下单量   = floor(下单量 × 1000) / 1000   # 最小 0.001
```

- `HARD_NOTIONAL_CAP = 0`（无单笔 50000 硬上限）
- 缺 `risk_pct` / `leverage` → **拒绝下单**
- 加仓：同一公式，`qty_ratio` 按 TV（约 0.3~0.7）

### 对照表（本金 1000U · ETH=1892.43 · TV leverage=5 · qty_ratio=1）

| Regime | risk_pct | 止损距 | 下单量 | 名义约 | **等效杠杆** |
|--------|----------|--------|--------|--------|--------------|
| R1 | 0.81% | 12.08 | **0.67 ETH** | ~1268 U | **1.27×** |
| R2 | 1.35% | 14.09 | **0.96 ETH** | ~1817 U | **1.82×** |
| R3 | 2.03% | 14.02 | **1.45 ETH** | ~2744 U | **2.74×** |
| R4 | 2.70~3.38% | 15.94 | **1.69~2.12** | ~3200~4011 U | **3.2~4.0×** |

> **等效杠杆** = 名义/本金，**不是**交易所 API 杠杆。  
> 交易所 `set_leverage` = TV 下发值（常见 **5**）。理论仓常先绑定时，5× 与更高杠杆算出的 **qty 相同**。

---

## 防线总线：TP123 + TV硬止损 + 雷达

统一入口 `_ensure_full_defense_stack()`：

```
清伪TP标记（已交棒则绝不回撤止损）
  → TP123 价格对齐 TV / ATR 补全
  → 挂/核 TP123（reduceOnly；已成交档禁止补挂）
  → 挂 TV tv_sl（closePosition 原值；禁止 ±buffer / 贴市推宽）
  → 价触激活线或 TP1 成交 → 雷达交棒 → 步进追随
```

### TP123

- 比例按开仓档位（见下方 Regime 表）
- `reduceOnly` 限价；与硬止损/雷达 **不抢额度**
- **价到 + 限价消失 = 成交** → 记账后 **永不补挂**该档
- 纯缺失可增量补；严重叠单才核武（有 thrash 刹车）

### TV 硬止损（`tv_sl`）

- 触发价 = webhook **`tv_sl` 原值**（多空严格）
- `STOP_MARKET` + `closePosition=true`
- **禁止**：档位%宽止损、`tv_sl±buffer`、`gap×1.25` 推宽、加仓「取更宽」
- 穿价/贴市导致拒单 → 返回失败 → **紧急撤开仓**（禁止改价保活）
- `UPDATE_SL` → 按新 `tv_sl` 改挂

### 雷达

- 开仓后 **候命**（阶段0）；达激活线或 TP1 成交后交棒
- 交棒后 **只前进不回撤**；只更新 closePosition 单槽；**不碰 TP123**
- 激活比例：R1=50% / R2=60% / R3=70% / R4=80%（entry→TP1）

---

## 开仓裸仓闸

```
市价开仓核实
  → 补全 TP123（TV 空则 ATR×regime）
  → 账本写入 tv_sl 并 force 挂 closePosition
  → 挂 TP123
  → 终检：无 STOP → 再挂；仍无 → _emergency_flatten_naked_open
  → 滞后 REST 核实仍无硬止损 → 同样撤开仓防裸奔（v13.88.1）
  → 防线齐 → 钉钉开仓确认（雷达=候命）→ 哨兵宽限 90s
```

| 失败场景 | 行为 |
|----------|------|
| 硬止损挂失败 / 终检无 STOP | **撤销开仓**，不裸奔 |
| TP 某档挂失败 | 记日志 / 告警，**不撤**硬止损 |
| `expected=0` | 禁止假齐；必须先补全 TP 价 |

---

## 雷达状态机

| Regime | 激活（→TP1） | 步进 | 呼吸 ATR |
|--------|--------------|------|----------|
| R1 | **50%** | 35% | 1.0 |
| R2 | **60%** | 30% | 0.8 |
| R3 | **70%** | 25% | 0.65 |
| R4 | **80%** | 20% | 0.5 |

```
开仓 → TP123 + tv_sl → 雷达候命（mark@1s）
现价达激活线 或 TP1 成交
  → 交棒：先挂保本核实 → 再撤 tv_sl 单槽改保本
  → 钉钉「雷达激活」（只一次；失败哨兵补发）
后续 → 步进+呼吸追随；只升不降
价格回撤 → 止损不动（禁止解除雷达）
新 TV OPEN → 一律先平后开，雷达回候命
```

---

## 信号与执行顺序

| 顺序 | 动作 |
|------|------|
| 1 | 解析 action / price / tv_sl / tp123 / risk_pct / qty_ratio / leverage / regime |
| 2 | 有仓（含反向）→ 先全部平掉 |
| 3 | 确认仓位为 0 |
| 4 | TV 公式算量 + `set_leverage(TV)` |
| 5 | 开仓（市价核实） |
| 6 | 挂硬止损 `tv_sl`（一次） |
| 7–9 | 挂 TP1/TP2/TP3（各一次） |
| 10 | 雷达 **候命**（WS） |
| 11 | 钉钉确认一次 |

| action | 行为 |
|--------|------|
| `LONG` / `SHORT` | 先平后开 + 挂齐防线 |
| `UPDATE_SL` | 按 TV `tv_sl` 改挂硬止损 |
| `UPDATE_TP` | 更新 TP 价并按需重挂**未成交**档 |
| `CLOSE*` / `CLOSE_STOPLOSS` | 撤单全平复位；`CLOSE_STOPLOSS` 优先市价全平 |

**时序：** `bar_index` + `seq` 幂等；同 bar / 同秒开+平 → 动作优先先平后开。

---

## 哨兵与空闲巡检

| 状态 | 周期 |
|------|------|
| 常态有仓 | **8s** |
| 雷达已交棒 | **5s** |
| 空闲巡检 | **12s** |
| 开仓宽限 | **90s**（优先补缺失，抑连环核武） |

WS：`markPrice@1s` + User Data → 脉冲交棒/追随（挂单仍在哨兵线程串行）。

---

## 重启 / 人工接管

```
recover_state_on_startup()
  → 按品种锁 + REST 探仓
  → 有仓：_ensure_full_defense_stack
  → 未达激活线：只挂 tv_sl，禁止历史 best 误触保本
  → 已交棒状态：价格回撤也不撤雷达（只前进）
```

空闲发现人工同向仓 → 接管挂 TP123+tv_sl+雷达候命；**禁止**因异常自动乱平。

---

## 钉钉推送

| 场景 | 要点 |
|------|------|
| 开仓确认 | 方向 / Regime / 开仓价 / 数量 / **TV杠杆** / tv_sl✅ / TP123✅ / **雷达候命**（未到线勿写「已激活」） |
| 雷达激活 | 达激活线交棒后 **一次**；含档位比例文案 `R1=50%/…/R4=80%` |
| TP 成交 | 每档一次 |
| 全平 | `exit_source`：雷达保本 / TP3 / TV硬止损 |
| 系统告警 | 裸仓、伪TP、敞口顶等；**标题去重**（默认 300s） |

禁止：同类连环刷屏、清伪TP 时发「雷达解除」、文案再写 `R1=85%…R4=70%`。

---

## Regime 矩阵（对齐 TV v6.9.108）

| 档位 | TP1/TP2/TP3 | 雷达激活 | 步进 | 呼吸 |
|------|-------------|---------|------|------|
| R1 | **25/35/40** | **50%** | 35% | 1.0 ATR |
| R2 | **20/35/45** | **60%** | 30% | 0.8 ATR |
| R3 | **18/32/50** | **70%** | 25% | 0.65 ATR |
| R4 | **5/20/75** | **80%** | 20% | 0.5 ATR |

- 仓位 **不**再用「保证金%×杠杆」表；只用 TV `risk_pct`
- 硬止损 **不**再用档位%表；只用 TV `tv_sl`
- `open_regime` 开仓锁定：TP 比例与雷达参数整笔不变
- 双品种合计名义 ≤ 权益 × **13**

```
activation = entry ± |tp1−entry| × ratio(open_regime)
stage1_SL  = entry ± 0.1%
trail_SL   = best ∓ ATR × breath   # 只向有利方向
```

---

## VPS 部署

```bash
cd ~/binance-engine
git fetch origin && git reset --hard origin/main

grep 'BINANCE_VPS_VERSION' position_supervisor_binance.py
# 期望: v13.88.1-open-sl-failsafe

source venv/bin/activate   # 如有
pip install -r requirements.txt
bash deploy_binance.sh

curl -s http://127.0.0.1:5003/health | python3 -m json.tool
# version · leverage:"tv_webhook" · sizing:"TV_RISK_FORMULA" · tv_strategy:v6.9.108
tail -f logs/binance_brain.log
```

> 日常更新统一 `git fetch && git reset --hard origin/main`，勿裸 `git pull` 卡在 local changes。

### 环境变量（节选）

```env
BINANCE_API_KEY=
BINANCE_API_SECRET=
WEBHOOK_SECRET=
DINGTALK_WEBHOOK=
DINGTALK_SECRET=
WECHAT_WEBHOOK=              # 可选企微备用
REDIS_URL=                   # 可选时序幂等
FLASK_PORT=5003
```

### TradingView Webhook 示例

```json
{
  "action": "LONG",
  "secret": "YOUR_SECRET",
  "symbol": "ETHUSDT.P",
  "regime": 3,
  "atr": 13.42,
  "price": 1892.43,
  "tv_tp1": 1909.87,
  "tv_tp2": 1927.31,
  "tv_tp3": 1943.41,
  "tv_sl": 1878.41,
  "risk_pct": 2.03,
  "qty_ratio": 1,
  "leverage": 5,
  "entry_type": "OPEN",
  "bar_index": 200,
  "seq": 1
}
```

| 字段 | 说明 |
|------|------|
| `symbol` | 必填，品种路由 |
| `risk_pct` / `qty_ratio` / `leverage` | **仓位三件套**；缺 leverage/risk → 拒单 |
| `tv_sl` | 硬止损挂单价（原值） |
| `tv_tp1~3` | 止盈价；缺档 ATR 补全 |
| `bar_index` / `seq` | 时序幂等；开平并存先平后开 |

---

## 日志与排错

| 文件 | 说明 |
|------|------|
| `logs/binance_brain.log` | 军师主日志 |
| `logs/binance_open_journal.jsonl` | 开仓流水 |
| `logs/tv_seq_idempotency.json` | 无 Redis 时幂等落盘 |

```bash
# 杠杆 / 仓位
grep -E 'set_leverage=|TV参数|仓位预算|sizing_lev' logs/binance_brain.log | tail -30

# 硬止损原值 / 禁推宽
grep -E 'TV硬止损|禁止推宽|撤开仓防裸奔' logs/binance_brain.log | tail -30

# 雷达
grep -E '雷达交棒|只前进|激活线|R1=50%' logs/binance_brain.log | tail -30

# TP 禁补挂
grep -E '拒绝补挂|现价已达|伪TP' logs/binance_brain.log | tail -30
```

---

## 实盘事故与优化备忘（必读）

> 基准版本：**`v13.88.1-open-sl-failsafe`**

| # | 铁律 |
|---|------|
| 1 | 凡 OPEN → 先平后开，终态有仓+防线 |
| 2 | 仓位只认 TV `risk_pct/qty_ratio/leverage` |
| 3 | `set_leverage` = TV leverage（禁 25x 回退） |
| 4 | 硬止损 = `tv_sl` 原值（禁 buffer / 推宽 / 取更宽） |
| 5 | TP123 成交后不补挂 |
| 6 | 三轨：TP reduceOnly · SL∪雷达 closePosition 单槽 |
| 7 | 雷达 50/60/70/80%；交棒后只前进 |
| 8 | 硬止损失败 → 撤开仓（含滞后核实） |
| 9 | 钉钉开仓写雷达**候命**；激活后再推激活 |
| 10 | 生产只用 `position_supervisor_binance.py` |

---

## 版本演进

### 近期（必读）

#### v13.88.1 · `open-sl-failsafe`
开仓滞后核实仍无硬止损 → 撤开仓防裸奔（补齐自查 7.6）。

#### v13.88.0 · `tv-sl-raw`
废除贴市 `gap×1.25` 推宽、加仓取更宽；`tv_sl` 原值挂单；穿价失败走紧急平仓。

#### v13.87.1 · `drop-legacy-supervisor`
删除遗留 `position_supervisor.py`。

#### v13.87.0 · `radar-advance-only`
交棒后只前进；钉钉比例统一 `R1=50%/…/R4=80%`；废除「雷达解除」。

#### v13.86.0 · `tv-leverage-live`
`set_leverage` 与仓位公式同源 = TV leverage；`EXCHANGE_LEVERAGE=0`。

#### v13.85.0 · `no-hard-cap`
删除单笔 maxNotional/50000 硬上限。

#### v13.84 ~ v13.82
雷达 50/60/70/80；废除 VPS% 宽止损；禁 TP1 补挂死循环；硬止损失败撤开仓；TV risk 唯一公式；铁律先平后开链。

### 版本表（摘要）

| 版本 | 要点 |
|------|------|
| **v13.88.1** | 滞后核实无硬止损 → 撤开仓 |
| **v13.88.0** | tv_sl 原值；禁推宽/取更宽 |
| **v13.87.1** | 删遗留 supervisor |
| **v13.87.0** | 雷达只前进；比例文案最新 |
| **v13.86.0** | set_leverage=TV |
| **v13.85.0** | 无单笔硬上限 |
| **v13.84.0** | 雷达分档 + 废 VPS% SL + 禁 TP1 补挂 |
| **v13.83.0** | 先平后开铁律链 |
| **v13.82.0** | TV risk 唯一公式 |
| v13.73~v13.75 | 适度追随雷达；同秒先平后开 |
| ≤v13.65 | 历史（统一85%、VPS宽止损、固定25x 等）**已废除** |

---

## 双工厂差异速查

| 项目 | 币安 | 深币 |
|------|------|------|
| 大脑 | `position_supervisor_binance.py` | `position_supervisor_deepcoin.py`（镜像） |
| 止损 | closePosition 单槽 | 以实现为准，逻辑同铁律 |
| 数量 | ETH / XAU 合约数量 | 张 |
| WS | mark@1s + User Data | 以深币实现为准 |

改一侧必须镜像另一侧并同版本推送。

---

*GEMINI Quant · 双轨智慧雷达 · v13.88.1-open-sl-failsafe*

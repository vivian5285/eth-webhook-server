# GEMINI 双轨交易工厂 · 统一实盘逻辑

**当前版本：`v15.0.1-tv-direction-force-flat`**  
**TV 策略 schema：`v6.5.6`**（`webhook_parser.TV_STRATEGY_VERSION`）  
**生产唯一大脑：`position_supervisor_binance.py`**

TradingView Webhook → 交易所永续自动化。**币安 ETH+XAU** 与 **深币** 共用同一套军师逻辑，仅计量单位 / 交易所 API / 钉钉主题不同。

| 工厂 | GitHub | VPS 目录 | 端口 | 品种 | 杠杆 | 钉钉 |
|------|--------|----------|------|------|------|------|
| **币安** | `vivian5285/eth-webhook-server` | `~/binance-engine` | **5003** | ETH + XAU | **固定 5x** | 黄金 |
| **深币** | `vivian5285/deepcoin-hft-server-main` | `~/deepcoin-hft-server` | **5004** | ETH + XAU 张 | **固定 5x** | 紫金 |

```bash
curl -s http://127.0.0.1:5003/health   # 币安
curl -s http://127.0.0.1:5004/health   # 深币
# 期望 version 含 v15.0.1-tv-direction-force-flat
# 期望 leverage: "fixed_5" · sizing: "RISK20_NOTIONAL5"
# 期望 tv_strategy: v6.5.6
# 期望：实盘与 TV 方向不一致 → 强制平仓 + 钉钉

python check_vps_logic.py              # 静态自查，期望全部通过
# 清单：docs/VPS实盘检查清单.md
```

---

## 目录

1. [核心铁律（一句话）](#核心铁律一句话)
2. [统一架构](#统一架构)
3. [仓位公式（20%×5）](#仓位公式20×5)
4. [防线总线：TP1+TP2 + stop_loss + 阶梯雷达](#防线总线tp1tp2--stop_loss--阶梯雷达)
5. [开仓裸仓闸](#开仓裸仓闸)
6. [阶梯雷达状态机](#阶梯雷达状态机)
7. [信号与执行顺序](#信号与执行顺序)
8. [v6.5.6 动作集](#v656-动作集)
9. [哨兵与空闲巡检](#哨兵与空闲巡检)
10. [重启 / 人工接管](#重启--人工接管)
11. [钉钉推送](#钉钉推送)
12. [VPS 部署](#vps-部署)
13. [日志与排错](#日志与排错)
14. [实盘事故备忘](#实盘事故与优化备忘必读)
15. [版本演进](#版本演进)
16. [双工厂差异](#双工厂差异速查)

---

## 核心铁律（一句话）

```
TV 到达 → 先平干净 → 再开仓 → 挂 TP1+TP2 + stop_loss → 阶梯雷达候命(WS) → 钉钉确认
```

| 铁律 | 说明 |
|------|------|
| **TV方向为准** | 实盘与最新 TV LONG/SHORT **反向** → **强制市价全平** + 钉钉 `report_force_align`（哨兵/重启同逻辑） |
| **仓位公式** | 风险资金=权益×**20%** / 止损距 ∩ 名义≤权益×**5** ∩ TV.qty → `RISK20_NOTIONAL5` |
| **硬止损** | webhook `stop_loss` / `tv_sl` **原值** closePosition |
| **分腿 TP** | 固定 **30/30/40**；盘口**只挂 TP1+TP2**（leg3 交雷达） |
| **禁止旧逻辑** | 无 risk_pct 公式、无档位保证金%、无固定 25x、无 maxNotional 硬上限 |
| **先平后开** | 同向/反向/同秒开+平 → 永远先平干净再开 |
| **三轨不抢份额** | TP1+TP2 = `reduceOnly`；stop_loss ∪ 雷达 = `closePosition` **单槽** |
| **阶梯雷达** | TP1 路程 **85%** 激活保本 → 0.5 ATR 步进 / 0.3 ATR 跟进 → TP 里程碑底限 |
| **硬止损失败** | 开仓路径仍无 STOP → **撤销开仓防裸奔** |

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
├── sizing：风险20%/止损距 ∩ 名义×5 ∩ TV.qty（RISK20_NOTIONAL5）
├── set_leverage = 5（固定）
├── 挂 TP1+TP2（reduceOnly）+ stop_loss（closePosition）
├── 阶梯雷达：85% 激活 → ladder SL → TP3 后 2.0 ATR 追踪
├── 哨兵 5~8s · 开仓宽限 90s · 空闲巡检 12s
└── 钉钉：开仓核实 + TV 对账 + 标题去重
```

**生产模块**

| 模块 | 说明 |
|------|------|
| `app.py` | Flask 网关 · health 暴露 sizing/leverage |
| `position_supervisor_binance.py` | **唯一军师大脑** |
| `webhook_parser.py` | 解析 / 固定仓位 / 阶梯雷达 / v6.5.6 动作集 |
| `binance_client.py` | REST + WS |
| `tv_seq.py` | 时序幂等 |
| `dingtalk.py` | 播报含 `report_tv_reconcile` |
| `symbol_config.py` | ETH/XAU 路由 |
| `check_vps_logic.py` | 静态自查 |

---

## 仓位公式（20%×5）

### 唯一公式

```
名义 = 账户权益 × 20% × 5
qty  = floor(名义 / price × 1000) / 1000   # 最小 0.001
```

**示例**：1000U · ETH=1800 → 名义 1000U → **qty=0.555 ETH**

- `HARD_NOTIONAL_CAP = 0`（无单笔硬上限）
- 双品种合计名义 ≤ 权益 × **13**
- 加仓已废除（`compute_vps_add_qty` 恒 0）
- **无需** webhook `risk_pct` / `leverage` 字段

### 对照表（本金 1000U · ETH=1800）

| 本金 | 名义 | qty | 等效杠杆 |
|------|------|-----|----------|
| 1000U | 1000U | **0.555** | ~1.0× |
| 5000U | 5000U | **2.775** | ~1.0× |

> 交易所 `set_leverage` 固定 **5**；等效杠杆 = 名义/本金。

---

## 防线总线：TP1+TP2 + stop_loss + 阶梯雷达

```
清伪TP标记
  → 补全 TP1/2/3 价（TV 空则 ATR）
  → 挂 TP1+TP2（reduceOnly；leg3 无挂单）
  → 挂 stop_loss（closePosition 原值）
  → 价触 TP1 路程 85% → 阶梯 radar SL 交棒
```

### 分腿 TP（30/30/40）

- 盘口只挂 **TP1 + TP2**（各 30%）
- leg3（40%）无限价，由阶梯雷达追踪收网
- 价到 + 限价消失 = 成交 → **永不补挂**该档

### TV 硬止损

- 触发价 = webhook **`stop_loss` / `tv_sl` 原值**
- `STOP_MARKET` + `closePosition=true`
- 穿价拒单 → 紧急撤开仓（禁止改价保活）

### 阶梯雷达参数

| 参数 | 值 | 含义 |
|------|-----|------|
| `RADAR_ACTIVATE_TP1_FRAC` | **0.85** | entry→TP1 路程 85% 激活 |
| `RADAR_STEP_ATR` | **0.5** | 每推进 0.5×ATR 推一次 |
| `RADAR_LOCK_ATR` | **0.3** | 每次止损至少跟进 0.3×ATR |
| `RADAR_TP1_FLOOR_ATR` | **0.5** | 触 TP1 底限 entry±0.5×ATR |
| `RADAR_TP2_FLOOR_ATR` | **1.5** | 触 TP2 底限 entry±1.5×ATR |
| `RADAR_TP3_TRAIL_ATR` | **2.0** | TP3 后 best∓2.0×ATR 纯追踪 |

**激活价示例**（LONG entry=1800, tp1=1840.5）→ **1834.425**

---

## 开仓裸仓闸

```
市价开仓核实
  → 补全 TP 价
  → 账本写入 stop_loss 并挂 closePosition
  → 挂 TP1+TP2
  → 终检：无 STOP → 再挂；仍无 → 撤开仓防裸奔
  → 防线齐 → 钉钉开仓确认（雷达=候命）
```

---

## 阶梯雷达状态机

```
开仓 → TP1+TP2 + stop_loss → 雷达候命（mark@1s）
现价达 TP1 路程 85% 或 TP1 成交
  → 交棒：保本(±1 tick) → 阶梯跟进
  → 触 TP1/TP2 里程碑 → 底限抬升
  → 触 TP3 → 2.0 ATR 纯追踪
  → 只升不降
```

---

## 信号与执行顺序

| 顺序 | 动作 |
|------|------|
| 1 | 解析 action / price / stop_loss / tp123 / regime / atr |
| 2 | 有仓 → 先全部平掉 |
| 3 | 确认仓位为 0 |
| 4 | 固定 20%×5 算量 + set_leverage(5) |
| 5 | 开仓（市价核实） |
| 6 | 挂 stop_loss |
| 7–8 | 挂 TP1 / TP2 |
| 9 | 阶梯雷达候命（WS） |
| 10 | 钉钉确认 |

| action | 行为 |
|--------|------|
| `LONG` / `SHORT` | 先平后开 + 挂齐防线 |
| `CLOSE_TP` 等 | **对账**：不下单，日志/钉钉 |
| `CLOSE_QUICK_EXIT` / `CLOSE_RSI_EXIT` | **快平**：撤单全平 |
| ~~`UPDATE_SL` / `UPDATE_TP`~~ | **v6.5.6 已废除** |

---

## v6.5.6 动作集

| 集合 | 动作 | 行为 |
|------|------|------|
| **RECONCILE** | `CLOSE_TP`, `CLOSE_TRAIL`, `CLOSE_SL_INITIAL`, `CLOSE_SL_BREAKEVEN` | 对账标记，**不下单** |
| **FLATTEN** | `CLOSE_QUICK_EXIT`, `CLOSE_RSI_EXIT` | 市价全平 |

---

## 哨兵与空闲巡检

| 状态 | 周期 |
|------|------|
| 常态有仓 | **8s** |
| 雷达已交棒 | **5s** |
| 空闲巡检 | **12s** |
| 开仓宽限 | **90s** |

WS：`markPrice@1s` + User Data → 脉冲交棒/追随。

---

## 重启 / 人工接管

```
recover_state_on_startup()
  → 按品种锁 + REST 探仓
  → 有仓：_ensure_full_defense_stack
  → 未达 85% 激活线：只挂 stop_loss
  → 已交棒：阶梯 SL 只前进
```

---

## 钉钉推送

| 场景 | 要点 |
|------|------|
| 开仓确认 | 方向 / qty / **20%×5x** / stop_loss / TP1+TP2 / 雷达候命 |
| 雷达激活 | 达 85% 交棒后一次 |
| TP 成交 | 每档一次 |
| TV 对账 | `report_tv_reconcile`（CLOSE_* 对账信号） |
| 全平 | `exit_source` 归因 |
| 系统告警 | 裸仓、敞口顶等；标题去重 |

---

## VPS 部署

```bash
cd ~/binance-engine
git fetch origin && git reset --hard origin/main

grep 'BINANCE_VPS_VERSION' position_supervisor_binance.py
# 期望: v15.0.0-risk20-ladder

source venv/bin/activate
pip install -r requirements.txt
bash deploy_binance.sh

curl -s http://127.0.0.1:5003/health | python3 -m json.tool
# version · leverage:"fixed_5" · sizing:"RISK20_NOTIONAL5" · tv_strategy:v6.5.6
tail -f logs/binance_brain.log
```

### TradingView Webhook 示例

```json
{
  "action": "LONG",
  "secret": "YOUR_SECRET",
  "symbol": "ETHUSDT.P",
  "regime": 3,
  "atr": 13.42,
  "price": 1800,
  "tv_tp1": 1840.5,
  "tv_tp2": 1860,
  "tv_tp3": 1880,
  "stop_loss": 1770,
  "qty1": 0.3,
  "qty2": 0.3,
  "qty3": 0.4,
  "entry_type": "OPEN",
  "bar_index": 200,
  "seq": 1
}
```

| 字段 | 说明 |
|------|------|
| `symbol` | 必填，品种路由 |
| `stop_loss` / `tv_sl` | 硬止损挂单价 |
| `tv_tp1~3` | 止盈价；缺档 ATR 补全 |
| `qty1/2/3` | 可选；默认 30/30/40 |
| `bar_index` / `seq` | 时序幂等；开平并存先平后开 |

---

## 日志与排错

```bash
grep -E 'RISK20_NOTIONAL5|20%×5|set_leverage=5' logs/binance_brain.log | tail -30
grep -E '阶梯|85%|ladder|激活线' logs/binance_brain.log | tail -30
grep -E 'TV对账|CLOSE_TP|report_tv_reconcile' logs/binance_brain.log | tail -30
```

---

## 实盘事故与优化备忘（必读）

> 基准版本：**`v15.0.0-risk20-ladder`** · TV **`v6.5.6`**

| # | 铁律 |
|---|------|
| 1 | 凡 OPEN → 先平后开 |
| 2 | 仓位 = 风险资金权益×**20%**/止损距 ∩ 名义≤权益×**5** ∩ TV.qty（RISK20_NOTIONAL5） |
| 3 | set_leverage = **5**（固定） |
| 4 | 硬止损 = stop_loss 原值 |
| 5 | 只挂 TP1+TP2；leg3 交雷达 |
| 6 | 三轨：TP reduceOnly · SL∪雷达 closePosition 单槽 |
| 7 | 雷达 **85%** 激活 + 阶梯 ATR 跟进 |
| 8 | 硬止损失败 → 撤开仓 |
| 9 | CLOSE_* 对账信号不下单 |
| 10 | 生产只用 `position_supervisor_binance.py` |

---

## 版本演进

### v14.0.0 · `risk20-ladder`
- 固定 **20%×5** 仓位（废除 TV risk_pct）
- 阶梯雷达：85% 激活 / 0.5·0.3·2.0 ATR
- TP **30/30/40**，只挂 TP1+TP2
- v6.5.6 动作集：RECONCILE + FLATTEN
- 废除 UPDATE_SL/UPDATE_TP webhook

### ≤v13.x（已废除）
- TV risk_pct 公式、旧分档雷达、UPDATE_SL 同步、旧 risk 公式常量等 **均已 superseded**

---

## 双工厂差异速查

| 项目 | 币安 | 深币 |
|------|------|------|
| 大脑 | `position_supervisor_binance.py` | `position_supervisor_deepcoin.py`（镜像） |
| 数量 | ETH / XAU 合约数量 | 张 |
| sizing | RISK20_NOTIONAL5 | 同逻辑 |

改一侧必须镜像另一侧并同版本推送。

---

*GEMINI Quant · 双轨智慧雷达 · v15.0.0-risk20-ladder / TV v6.5.6*

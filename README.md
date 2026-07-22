# 币安单一账户系统（binance-engine）· VPS 实盘

**当前版本：`v15.5.23-breath-tv-atr`**  
**TV 策略 schema：`v6.5.6`**  
**仓位模式：`RISK20_NOTIONAL5`（本金×20% 风险；名义=本金×20%×5=本金×1 · 永远）**  
**保护引擎：呼吸止损（`breath_stop` · TV `atr`=initial_atr · 币安原生 1h ATR 呼吸系数 · markPrice WS）**  
**生产唯一大脑：`position_supervisor_binance.py`**  
**通知渠道：钉钉（`dingtalk.py`；VPS 已配置，暂不迁 Telegram）**

> 本文档为**唯一权威说明**。凡与旧文档（妈妈版阶梯雷达、TP3 挂限价、CAP_ALIGN、TV `stop_loss` 作盘口止损基准、同向跳过平仓、名义×0.85 折扣等）冲突，一律以本文为准。  
> 旧逻辑清除对照表：[`docs/DELETED_LEGACY_LOGIC_v15.5.13.md`](docs/DELETED_LEGACY_LOGIC_v15.5.13.md)  
> 天文 qty 事故：[`docs/INCIDENT_20260722_HUGE_TV_QTY.md`](docs/INCIDENT_20260722_HUGE_TV_QTY.md)

TradingView Alert → Webhook → VPS 接收/校验 → **TV.atr 锁定 initial_atr** + 1h ATR 呼吸系数 → **先平后开** → 市价开仓 → 挂 **TP1/TP2** + **呼吸止损开仓即工作** → 平仓钉钉诚实归因。

| 工厂 | VPS 目录 | 端口 | 品种 | 仓位逻辑 | 钉钉主题 |
|------|----------|------|------|----------|----------|
| **币安**（本仓库） | `~/binance-engine` | **5003** | ETHUSDT + XAUUSDT | RISK20_NOTIONAL5 | 黄金 |
| **深币**（对照） | `~/deepcoin-hft-server` | **5004** | ETH + XAU（张） | **同逻辑** | 紫金 |

```bash
curl -s http://127.0.0.1:5003/health | python3 -m json.tool
# version: v15.5.23-breath-tv-atr
# sizing: RISK20_NOTIONAL5 · notional=equity×20%×5(=1×equity) · tv_strategy: v6.5.6
# radar: breath_tv_atr_1h · trading_paused: false

python3 check_vps_logic.py
python3 test_breath_radar_upgrade.py
python3 check_deploy_events.py --live
```

---

## 零、五条不可动摇的硬性原则

1. **开仓永远先平后开**  
   不判断新旧仓位方向是否相同；收到 `LONG`/`SHORT` 一律：查实盘 → 有仓则市价全平 + 撤全部挂单 → **等待确认仓位归零** → 再算 qty 开新仓。  
   外部/人工仓位与后续 TV 同向时，**同样先平后开**，禁止「方向一致跳过平仓」。

2. **单仓位，不加仓**（pyramiding=1）  
   任意时刻一个 symbol 只允许一笔持仓。无多笔 trade 并存、无加权均价重算、无浮盈加仓。

3. **下单数量铁律（永远）**  
   - 风险资金 = **合约账户本金 × 20%**  
   - 名义上限 = **(本金 × 20%) × 5 倍杠杆 = 本金 × 1**（≈余额 1 倍，绝不是本金×5）  
   - `qty = min(风险/|价−initialStop|, 名义/价, TV.qty′)`，向下取整  
   - **绝不采信天文 TV.qty 为最终下单量**（Pine equity 膨胀时忽略该上限）  
   - 交易所保证金再裁：`availableBalance × 20% × 5 × 0.92`（防 -2019）  
   无状态纯函数，不读历史仓位/加仓次数。真实挂止损价只用 VPS `initialStop`。

4. **止损单全局唯一写入方 = 呼吸止损引擎**  
   下单 / 改单 / 触发平仓只由呼吸引擎执行。订单监控、重启恢复等模块**不得**直接调用止损类交易所 API，只能通知引擎执行。  
   保留兜底：`HARD_SL_FAIL_ABORT`、`CLOSE_THEN_OPEN_FAIL_ABORT`、`FORCE_ALIGN`。  
   **已删除：`CAP_ALIGN`（仓位上限主动减仓）**。

5. **同窗先平后开**  
   同 symbol 消息缓存 **固定 1.0s**；平仓优先于开仓；平干净确认后再开。
---

## 一、信号流与架构

```
TradingView v6.5.6 Alert (secret / token)
        │
        ▼
   app.py  /webhook   ← 解析 · 鉴权 · 品种路由 · 异步线程
        │
        ▼
position_supervisor_binance.py     ← 唯一生产大脑（每 symbol 一实例）
   ├── tv_seq.py                   缓存 1.0s · 同窗折叠 · 先平后开 · 开仓单到延长等待
   ├── webhook_parser.py           动作白名单 · RISK20 仓位纯函数
   ├── atr_1h.py                   币安原生 1h ATR(14) · 5 分钟刷新 · 呼吸系数
   ├── breath_stop.py              两阶段呼吸止损（×breathing_coefficient）
   ├── market_engine.py            90m 仅作缺 atr 降级/对比日志（非止损权威）
   ├── binance_client.py           REST + markPrice WS + 用户数据流
   └── dingtalk.py                 钉钉 / 企业微信双通道播报
```

| 环节 | 行为 |
|------|------|
| 缓存窗口 | 同 symbol 首包后 **固定 1.0s** settle，到期统一处理 |
| 同窗折叠 | 平仓只保留一条；开仓只保留**最新**一条；顺序永远 **先平后开**（乱序到达亦然） |
| 去重 | 60s 内同一 `action+symbol+price` 忽略（**含 price**；同向改价重发会放行） |
| 哨兵 | `_sentinel_loop`：**WS tick 优先**，REST 轮询兜底；对账仓位、TP 成交、呼吸改单、挂单修复 |
| 状态文件 | `binance_vps_state_{SYMBOL}.json`（ETH / XAU 独立） |
| 持仓查询失败 | 返回 `POSITION_QUERY_FAILED`，**禁止当空仓清账本**（见 §九.1） |

---

## 二、Webhook：仅 5 个有效 action

解析层 `VALID_ACTIONS` 只接受：

| action | 含义 |
|--------|------|
| `LONG` | 开多（先平后开） |
| `SHORT` | 开空（先平后开） |
| `CLOSE_QUICK_EXIT` | 反转保护 · 市价全平 |
| `CLOSE_RSI_EXIT` | RSI 反转保护 · 市价全平 |
| `PING` | 心跳（不交易） |

**别名归一**（示例）：`BUY→LONG`，`SELL→SHORT`，`CLOSE`/`CLOSE_LONG`/`CLOSE_SHORT`/`QUICK_EXIT`/`CLOSE_PROTECT→CLOSE_QUICK_EXIT`，`RSI_EXIT→CLOSE_RSI_EXIT`。

**一律拒绝 / 忽略**（不进白名单、不执行交易主路径）：  
`CLOSE_TP` · `CLOSE_TRAIL` · `CLOSE_SL_*` · `CLOSE_TP3` · `UPDATE_SL` · `UPDATE_TP` · `leg` 字段驱动平仓等。

鉴权：`secret` 必填；旧字段名 `token` 仍兼容。

### 2.1 开仓消息示例

```json
{
  "action": "LONG",
  "symbol": "ETHUSDT",
  "price": 1900.0,
  "qty": 12,
  "qty1": 3,
  "qty2": 3,
  "qty3": 6,
  "stop_loss": 1860.0,
  "tp1": 1954.0,
  "tp2": 2000.0,
  "tp3": 2044.0,
  "secret": "528586"
}
```

### 2.2 每个字段用在哪（是否参与实际止损价）

| 字段 | VPS 怎么用 | 参与实际止损价？ |
|------|------------|------------------|
| `price` | 开仓参考价；与 `stop_loss` 做差得到「TV 隐含止损距离」，只用于仓位换算；参与 60s 去重键 | **否** |
| `qty` | 三选一候选之一（先经 `sl_adj` 换算，见第四节） | **否** |
| `qty1` / `qty2` | TP1/TP2 **限价止盈**挂单数量意图；按 `实开仓/TV总量` 缩放后挂出 | **否** |
| `qty3` | **不使用**（TP3 不挂限价，余仓交阶段二） | 不适用 |
| `stop_loss` | **只**反推 TV 隐含止损距离 → 修正仓位；**绝不**作为盘口止损价 | **否** |
| `tp1` / `tp2` | TP1/TP2 限价止盈**价格** | **否** |
| `tp3` | **不使用** | 不适用 |
| `atr` / `adx` | **不读取作止损**；一律用 VPS 行情引擎。`atr` 仅可选用于调试比对 | **否** |
| `symbol` / `ticker` | 品种路由 ETH / XAU（支持 `ETHUSDT.P`） | — |
| `bar_index` / `seq` | 时序排序与幂等 | — |
| `secret` / `token` | 鉴权 | — |
| `reason` | 反转平仓文案 | — |
| `bar_time` | 可选；更早的 bar 记日志且不执行交易 | — |

**一句话：** 消息里只有 `price` 与 `stop_loss` 会做一次减法算出「TV 隐含止损距离」，**唯一用途是修正仓位数量**；不会、也不能当作 VPS 挂在交易所上的止损单价格。开仓后呼吸引擎**不再需要**任何新 TV 数据。

---

## 三、LONG / SHORT 开仓流程

1. 查询交易所**实际持仓**（查询失败不得当空仓，见 §九.1）  
2. 若非空：市价全平 → 撤销所有挂单 → **等待成交回报或仓位归零确认** → 重置该 symbol 呼吸引擎全部状态 → 钉钉「先平后开」  
3. 净场失败：重试 **3** 次（间隔 **1s / 3s / 6s**）→ 仍失败则 `CLOSE_THEN_OPEN_FAIL_ABORT`，**放弃本笔开仓**  
4. **独立**拉 30m K → 合成 90m → 算 `initialAtr`（此后锁定）→ `initialStop = entry ± 1.5×initialAtr`（与 TV.stop_loss **零交集**）  
5. 按第四节公式算最终下单数量（含 `sl_adj`）  
6. `set_leverage` 固定 **5x** → 市价开仓  
7. 挂 **TP1+TP2** 限价（价格=`tp1`/`tp2`，数量=`qty1`/`qty2` 按实开缩放）  
8. **不挂 TP3**（`qty3` 对应余仓由阶段二追踪）  
9. 呼吸止损初始化：挂 STOP（`quantity=全仓`，价格=`initialStop`）并立即接管  
10. 钉钉：开仓详情（方向、价格、数量、initialStop；算仓模式=RISK20）

开仓成功后的**下一个 tick 起**：呼吸引擎逐 tick 更新止损价，直到仓位归零。

---

## 四、仓位数量计算（开仓算一次，此后不变）

**铁律（2026-07-22 天文 qty 事故后再次钉死）：永远按合约本金 ×20% 风险资金 + 本金 ×5 名义上限独立核算，不采信天文 TV.qty。**

```
VPS实际止损距离 = |entryPrice − initialStop| = 1.5 × initialAtr
TV隐含止损距离 = |price − stop_loss|
调整系数 sl_adj = TV隐含止损距离 / VPS实际止损距离     # 缺 stop_loss → 1.0
调整后的TV数量上限 = qty × sl_adj
  # 若 TV.qty′ ≫ max(风险候选, 名义候选)×50 → 忽略该上限（Pine equity 膨胀）

风险资金 = 合约账户本金 × 20%
名义上限 = 合约账户本金 × 5          # 不做 0.85 等折扣

理论数量 = min(
    风险资金 / VPS实际止损距离,
    名义上限 / entryPrice,
    调整后的TV数量上限（若未忽略）,
)
最终下单数量 = 向下取整至交易所精度
# 下单前再裁：availableBalance × 5 × 0.92 / price（仅防交易所 -2019）
```

**为什么需要 `sl_adj`：** TV.qty 按 TV 内部止损距算出；实盘初始止损距是 **1.5×ATR**。换算后，止损在 VPS `initialStop` 触发时亏损可控在风险资金预算内。

数量在开仓算完即固定，**不**随后续价格/ADX 重算。开仓日志打印：`sl_adj`、三候选、`binding`、是否 `tv_qty_ignored_absurd` / `margin_cap`。

---

## 五、CLOSE_QUICK_EXIT / CLOSE_RSI_EXIT（反转保护）

1. 立即市价全平剩余仓位（不管浮盈）  
2. 撤销所有未成交挂单（TP1/TP2、止损）  
3. 重置该 symbol 呼吸止损全部状态（含 `watched_entry`）  
4. 停止该 symbol 订单监控  
5. 钉钉：反转保护平仓 · `reason` · 价格  

除 TV 这两种信号 + 呼吸引擎自身止损触发 + 交易所 TP 吃满外，**不存在任何第三方策略平仓判断路径**。

---

## 六、呼吸止损引擎（开仓即工作 · TV atr 基准 · 1h 呼吸系数）

实现：`breath_stop.py` + `atr_1h.py`。盘口：`STOP_MARKET` + `reduceOnly` + 明确 `quantity`。  
驱动：币安 **markPrice WebSocket** 逐 tick；REST 仅兜底。

### 6.1 初始止损（不用 TV.stop_loss 挂单）

1. **`initial_atr` = TV webhook `atr`**（开仓锁定，全程不变；缺则降级 1h→90m 并钉钉）  
2. 多：`initialStop = entry − 1.5×initial_atr`；空：`entry + 1.5×initial_atr`  
3. **盘口挂单** = `order_stop_price`：多再 −0.3 USDT / 空再 +0.3（执行缓冲）  
4. 币安原生 **1h ATR** 每 5 分钟刷新，算呼吸系数（最近 3 次 ratio 平滑）  

**禁止**：持仓期用默认 `ATR=30` 虚构止损；禁止把 ADX 当呼吸系数传参。

### 6.2 必须持久化 / 每 tick 更新

| 字段 | 开仓后 | 说明 |
|------|--------|------|
| `initialAtr` / `open_atr` | **固定** | = TV atr；不因 1h 刷新而改 |
| `initialStop` / `initial_stop` | **固定** | 阶梯基准（理论价，不含 0.3） |
| `currentStop` / `current_sl` | **每 tick 可上移** | 账本理论价；盘口 = ±0.3 |
| `highestPrice` / `lowestPrice` (`best_price`) | **每 tick** | 多只增 / 空只减 |
| `breakevenPhase` | **只可 false→true** | 进入阶段二后不可回退 |
| `breathing_coefficient` | **可刷新** | 0.7~1.5；3 次平滑 |
| `remaining_qty_pct` | TP 成交后更新 | 止损单数量收缩 |
| `tp_levels_radar_handoff` | TP 超时移交后持久化 | 禁止虚假 clear 后核武重挂 |

### 6.3 每个 markPrice tick

1. 刷新呼吸系数（1h ATR / initial_atr）  
2. 更新 `highestPrice` / `lowestPrice`  
3. `breakevenPhase == false` → 阶段一；否则 → 阶段二  
4. 新止损更优（多更高 / 空更低）才改单；否则本 tick 空操作（幂等，防撤挂抖动）  
5. 价格跌破/突破 `currentStop` → 市价全平剩余 → 重置状态 → 钉钉  

### 6.4 阶段一（保本前 · 多单示意）

```
step_trigger = 0.75 × initial_atr × breathing_coefficient
step_advance = 0.4 × initial_atr × breathing_coefficient
# TP1/TP2 强制底线：浮盈≥1.35/2.5×ATR → stop≥entry+0.5/1.5×ATR
# 浮盈≥3.0×ATR → 切入阶段二
```

### 6.5 阶段二（呼吸系数自适应追踪）

```
trail_distance = initial_atr × breathing_coefficient   # 约 0.7~1.5×ATR
current_stop = max(current_stop, highest - trail_distance)  # 多
```

档位：ratio<0.7→0.7；0.7~1.0→0.85；1.0~1.4→1.0；1.4~2.0→1.2~1.4 线性；≥2.0→1.5。

### 6.6 HARD_SL_FAIL_ABORT

改单失败重试 3 次 → 钉钉告警，保持现状，不自主平仓。

### 6.7 CLOSE_THEN_OPEN_FAIL_ABORT（先平后开净场失败）

1. 重试 **3** 次，间隔 **1s / 3s / 6s**  
2. 仍失败 → **放弃本笔开仓**  
3. 高优先级钉钉「清仓失败·需人工介入」  
4. 该 symbol 置 `trading_paused=CLOSE_THEN_OPEN_FAIL_ABORT`（空仓也不自动解除）  
5. 人工核对后：`POST /admin/resume/ETHUSDT` 恢复  

### 6.8 ATR 应急降级（仅开仓瞬间 · 高调 · 需人工恢复）

**定位：** 极端情况下临时用 TV 隐含 ATR，**不是**常态备选。  
**持仓期禁止**因「缺 TV.qty / 假 ATR」反复降级刷屏（已 hold-skip）。

触发（开仓信号，任一）：
1. VPS 拉不到足够 K 线 / ATR 空或 ≤0  
2. VPS ATR < 近50根中位数 × 30%  
3. VPS vs TV隐含偏差 ≥20%，且**连续 3 次**开仓信号  

降级时：钉钉 `ATR_DEGRADE_MANUAL_RESUME`；随后暂停该 symbol 自动开仓，复验后 `/admin/resume/{SYMBOL}`。

---

## 七、订单监控（TP1 / TP2）

| 事件 | 行为 |
|------|------|
| TP1 成交 | 剩余比例更新；引擎撤旧止损→按剩余 qty + 当前 `currentStop` 重挂；期间暂停 breath tick |
| TP2 成交 | 同上 → 剩余进一步收缩 |
| TP 限价超时未成交 | **仅当现价已进入该档触及区**仍超时未成交 → 确认撤单后记入 `tp_levels_radar_handoff`；价未到属正常等待，不撤不告警 |
| 全部平仓 | 归零 → 撤挂单 → 完整重置呼吸态（含 `watched_entry`）→ 停监控 → 钉钉 |

缺 `qty1/qty2` 时回退 30/30 比例切分；`PLACE_TP_LEVELS=2`。  
盘口残留 TP3 限价视为**孤儿**，只撤孤儿，不因账本仍有 TP3 价而误判不齐。

---

## 八、VPS 行情引擎

| 项 | 值 |
|----|-----|
| 数据源 | 交易所 **30m** 原始 K 线 |
| 合成 | 每 3 根完整 30m → 1 根 **90m**，**UTC epoch 对齐**（`open_time % (90×60×1000)==0`） |
| 锚点 | `bucket = t - (t % PERIOD_90M_MS)`；禁止从进程启动时刻随意起算 |
| 指标 | 合成 K 闭合后重算 **ATR(14)**、**ADX(14)**（Wilder / 与 TV RMA 对齐） |
| 刷新下限 | ≥60s |
| ATR 兜底 | 开仓前：ATR≤0 无条件拒单；或 ATR < 近50根中位数×30% → 拒本笔+钉钉 |
| 权威性 | 止损距离只用开仓锁定的 `open_atr`；ADX 可刷新；webhook atr/adx 无效 |

```bash
python3 check_90m_align.py          # 单元对齐
python3 check_90m_align.py --live   # 拉实盘30m，打印90m开盘时间供与TV逐根比对
```

> **注意：** 钉钉「ATR核对差异」曾误用 `|price−stop_loss|/1.5` 反推 TV ATR。现优先比对 webhook `atr`；否则按 `TV_HARD_SL_ATR_MULT=1.0` 反推。该核对**仅日志/提示，不拦截开仓**。

---

## 九、重启恢复与安全闸

1. **强制 REST 多轮**探测持仓（禁止仅信空 WS 缓存）  
2. 有持仓 → 读持久化呼吸状态  
   - **旧 schema**（缺 `initial_stop`/`open_atr`/`breakeven_phase`）→ 钉钉告警 + `trading_paused`，**禁止自动转换**  
3. **FORCE_ALIGN**：持仓方向与最新可信 TV 方向不一致 → 市价全平 + 撤单 + 重置 + 钉钉  
4. 按恢复的 `currentStop` 重挂止损（优先采纳交易所已挂止损；拒绝 ATR=30 虚构）  
5. 恢复未成交且价格仍有利的 TP1/TP2；handoff 档禁止重挂  
6. 无持仓 → 清状态待命；空仓确认阶段若 `QUERY_FAILED` → **禁止清挂单**  
7. 钉钉：重启恢复 / 空仓待命  

**CAP_ALIGN 已删除**：禁止「仓位超限 reduceOnly 减仓」。

### 9.1 持仓查询失败（fail-closed）

| 层 | 行为 |
|----|------|
| `binance_client.get_position` | REST 失败：优先 ≤60s 缓存；无缓存 → `POSITION_QUERY_FAILED`（**不是** `None`） |
| `_get_active_position` | 映射为 `"QUERY_FAILED"` |
| 哨兵 / 空闲巡检 / `_confirm_position_flat` | 跳过空仓判定，**保留账本**，钉钉限频告警 |
| 重启探测 | 全程失败 → `"AMBIGUOUS"`，禁止报空仓待命 |

### 9.2 重启窗口止损振荡（已闭环）

**根因（历史）：** 二次 recover「跳过重复接管」仍点火哨兵且未 hydrate → 默认 `open_atr=30` 虚构止损（如 1886.53），与正确接管价（如 1910.18）互相撤挂。  
**修复（v15.5.11+）：** skip-takeover 必须 hydrate；拒绝持仓期 ATR=30 发明；`_stop_write_blocked`；优先交易所 adopt。

---

## 十、人工 / 外部开仓（生产标准行为）

| 场景 | 行为 |
|------|------|
| 空闲巡检发现未登记仓位 | **立刻**纳入呼吸止损：用当前市价独立算 ATR/`initialStop` 接管；`link_historical_tv=False`（不编造历史 TV 关联） |
| 钉钉文案 | 「未登记来源仓位 · 系统接管（来源待核实）」——**不**武断写「人工开仓」 |
| 后续收到 TV LONG/SHORT | 一律先平后开，**不因**来源是外部而特例跳过 |
| 平仓归因 | **仅当现价贴近挂出止损线**才判定「止损平仓」；否则归因为「主动/脚本/异动市价平仓」 |

---

## 十一、防螺旋与自我检查

| 机制 | 行为 |
|------|------|
| 仓位一致性 | tick / webhook / 订单监控时以交易所为准修正本地 |
| TP 超时 | 价已触及才撤单 + handoff 持久化；价未到不告警 |
| 重复消息 | 60s 同 `action+symbol+price` 忽略 |
| 改单失败 | 3 次重试 → HARD_SL_FAIL_ABORT |
| 先平后开净场失败 | 3 次(1s/3s/6s) → CLOSE_THEN_OPEN_FAIL_ABORT |
| 持仓查询失败 | 保留账本，禁止当空仓 |
| 开仓偏离目标 | **只告警，不减仓**（CAP_ALIGN 已废） |
| 止损幂等 | 同价同量不重复撤挂 |

---

## 十二、钉钉事件清单

| 事件 | 内容要点 | 函数 |
|------|----------|------|
| 开仓 | 方向、价格、数量、initialStop；**算仓模式=RISK20**（不展示旧档位/「中势推升」） | `report_supervisor_open` |
| 先平后开 | 已有持仓已全平撤单，准备开新仓 | `report_close_then_open_chain` |
| 同窗开平同时到达 | 检测到平仓+开仓同秒到达，已按先平后开执行 | `report_close_then_open_chain`（phase「同秒开平·强制先平后开」） |
| 阶段切换（一→二） | 切换价、ADX、追踪距离（文案用「阶段二」，禁用「雷达激活」标题） | `report_radar_activated` |
| 止损移动 | 新止损、浮盈、所处阶段 | `report_intervention` |
| TP1/TP2 成交 | 成交价、剩余比例、当前止损 | `report_tp_fill` |
| 止损触发 | 须现价贴线；阶段一/二、盈亏 | `report_supervisor_close` |
| 反转 / 主动平仓 | reason、平仓价；非贴线不标止损 | `report_supervisor_close` |
| 未登记来源接管 | 「来源待核实」 | `report_manual_position_change` |
| 重启恢复 / 待命 | 状态、方向、数量 | `report_recover_takeover` / `report_recover_standby` |
| FORCE_ALIGN | 方向不一致、已全平重置 | `report_force_align` |
| HARD_SL_FAIL_ABORT | 改单失败、保持现状 | `report_hard_sl_fail_abort` |
| 持仓查询失败 | 保留账本提示 | `report_system_alert` |
| 异常告警 | 对账不一致、挂单超时等 | `report_system_alert` |

**禁止出现的旧文案**：雷达激活·妈妈版、保护性全平、TP3 止盈成交、加仓成交、档位限额强制对齐（CAP_ALIGN）、武断「人工开仓·系统接管」、旧档位「中势推升」算仓暗示。

---

## 十三、生产模块一览

| 模块 | 职责 |
|------|------|
| `app.py` | Flask 网关 · `/webhook` · `/health` · `/admin/resume` · 端口 5003 |
| `position_supervisor_binance.py` | 唯一大脑 · 开平仓 · 哨兵 · 恢复 · 空闲接管 |
| `breath_stop.py` | 两阶段呼吸止损纯函数 |
| `market_engine.py` | 90m 合成 · ATR/ADX · 降级判定 |
| `webhook_parser.py` | 解析归一 · RISK20 仓位 · 动作白名单 |
| `tv_seq.py` | 1.0s 缓存 · 先平后开折叠 · 幂等 |
| `binance_client.py` | REST · markPrice WS · 用户数据流 · `POSITION_QUERY_FAILED` |
| `symbol_config.py` | ETH / XAU 元数据与路由 |
| `dingtalk.py` | 钉钉 + 企业微信播报 |
| `deploy_binance.sh` | 干净重部署 · 健康检查 · 自动跑事件自检 |
| `check_vps_logic.py` | 静态逻辑自查 |
| `check_deploy_events.py` | 部署后事件函数 + smoke + 可选 `/health` |

**非生产路径**（勿当作大脑）：`order_executor.py`、`position_manager.py`、`profit_taker.py`、`state_manager.py` 等历史模块。

---

## 十四、部署与自检

```bash
cd ~/binance-engine
git fetch origin && git reset --hard origin/main

grep 'BINANCE_VPS_VERSION' position_supervisor_binance.py
# 期望: v15.5.20-checklist-final

bash deploy_binance.sh
# 或: systemctl restart binance-engine.service

curl -s http://127.0.0.1:5003/health | python3 -m json.tool

python3 check_deploy_events.py --live
python3 check_deploy_events.py --live --deep
python3 check_vps_logic.py -v
python3 check_90m_align.py
python3 check_90m_align.py --live
python3 -m unittest test_huge_tv_qty_sizing.py test_tv_seq_collapse.py
```

### 本地 / CI 回归（部署前）

```bash
export BINANCE_SKIP_BOOTSTRAP=1
python3 test_tv_seq_collapse.py
python3 test_huge_tv_qty_sizing.py
python3 test_position_query_fail_safe.py
python3 test_attribution_honest.py
python3 test_restart_stop_and_tp_handoff.py
python3 test_stop_idempotent_and_tp_levels.py
```

### `check_deploy_events.py` 覆盖

- 钉钉关键事件函数存在；CAP_ALIGN/加仓为 no-op  
- Supervisor 核心方法：`_full_reentry`、`_sync_exchange_stop`、`_breath_resize_stop_on_tp`、`recover_state_on_startup`…  
- Webhook 白名单、`PLACE_TP_LEVELS=2`  
- 呼吸止损 / RISK20 仓位数值 smoke  
- `--live`：本机 `/health` 版本与 sizing  

部署后观察窗口内**禁止**再次 rebuild 打断；日志可记入 `logs/prod_gate_observe_60m.txt`。

---

## 十五、已删除 / 禁止的旧逻辑

| 分类 | 已删除项 |
|------|----------|
| 仓位 | 曾误把名义写成「全本金×5」；正确为「本金×20%×5=本金×1」；加仓 / `opentrades` / pyramiding>1 |
| 止盈 | TP3 限价挂单主路径 |
| 旧雷达 | `activated` 0.85×TP1；步进 0.5/0.3 ATR；TP3 后固定 2.0×ATR 追踪 |
| 自主平仓 | 保护性全平、**CAP_ALIGN** 档位减仓 |
| Webhook | `CLOSE_TP` / `CLOSE_TRAIL` / `CLOSE_SL_*` / `CLOSE_TP3` / `leg`；`UPDATE_SL`/`UPDATE_TP` 改挂 |
| 特例 | 同向开仓「跳过平仓」 |
| 钉钉 | 雷达·妈妈版、TP3 止盈、加仓成交、档位限额裁减、武断「人工开仓」 |
| 死代码 | `_handle_tv_reconcile` / `_handle_tv_sl_update` 已清空壳（不可达） |

**保留**：`HARD_SL_FAIL_ABORT`、`CLOSE_THEN_OPEN_FAIL_ABORT`、`FORCE_ALIGN`。

`webhook_parser.compute_ladder_radar_sl` 调用即报错；supervisor 只走 `breath_stop`。完整清单见 [`docs/DELETED_LEGACY_LOGIC_v15.5.13.md`](docs/DELETED_LEGACY_LOGIC_v15.5.13.md)。

---

## 十六、上线前验证清单

1. 全局搜索旧参数 `0.85` / `0.5`/`0.3` ATR 步进 / `2.0` TP3 追踪，确认不在生效路径  
2. 完整生命周期：开仓 → 阶段一 → TP1（止损 qty 收缩）→ TP2 → 阶段二 → 止损或追踪平仓  
3. 重启恢复：有仓重启止损价稳定（无 ATR=30 虚构振荡）；旧 schema 告警暂停  
4. ATR/ADX：VPS 90m 与 TV 图表核对  
5. 先平后开：平仓确认完成前，新开仓计算不得提前发生  
6. 查询失败：模拟 REST 失败时账本不被清零  
7. 未登记仓位：接管文案「来源待核实」；平仓贴线才标止损  
8. `python3 check_deploy_events.py --live --deep` 全绿 + 上表单测全绿  
9. 天文 TV.qty：`test_huge_tv_qty_sizing.py` 绑定 notional（本金×20%×5/价 = 本金×1/价）

---

## 十七、Cursor 自检清单（第十四节 · 验收）

| # | 检查项 | 状态 |
|---|--------|------|
| 1 | 开仓前强制查询交易所实际持仓，非空则全平撤单，等待确认完成后才开新仓 | **已确认** |
| 2 | 平仓和撤单操作必须等交易所确认执行完成（仓位归零确认）后才能进入下一步 | **已确认** |
| 3 | 同一缓存窗口内平仓+开仓同时到达时，优先执行所有平仓，平完再开 | **已确认** |
| 4 | 不存在「先开仓再平仓」或「开平并行」的执行路径，只有「先平后开」一条路 | **已确认** |
| 5 | 全局不存在任何「加仓」相关分支、下单函数、状态字段（生效路径） | **已确认** |
| 6 | 全局不存在独立于 TV webhook 之外的 VPS 自主平仓判断（旧保护性全平已删；保留呼吸止损触发/FORCE_ALIGN） | **已确认** |
| 7 | 下单数量计算函数是无状态纯函数，不读取历史仓位/加仓次数；永远 `本金×20%` 风险 + `本金×20%×5(=本金×1)` 名义 | **已确认** |
| 8 | 止损单唯一写入方是呼吸止损引擎，其他模块只检测事件并通知引擎 | **已确认** |
| 9 | TP1/TP2 成交后不再有单独的「强制移动止损」代码分支 | **已确认** |
| 10 | TP3 限价单挂单代码已删除，TP3 完全由呼吸止损引擎阶段二接管 | **已确认** |
| 11 | Webhook 解析只接受 4 个交易 action（+PING），其余拒绝并记录日志 | **已确认** |
| 12 | 旧版 activated/stepCount/固定 2.0×ATR 追踪相关生效代码已全删 | **已确认** |
| 13 | 状态持久化 schema 已更新为新字段集，重启能识别并拒绝旧 schema 残留 | **已确认** |
| 14 | 钉钉文案按清理对照表执行，新增「缓存窗口处理」通知 | **已确认** |
| 15 | VPS 行情引擎（30m 合成 90m K 线 + ATR/ADX 计算）已实现 | **已确认** |
| 16 | 1 秒缓存窗口按优先级排序逻辑已实现（平仓 > 开仓） | **已确认** |
| 17 | 缓存窗口超时兜底已实现，不无限等待 | **已确认** |

---

## 十八、一句话总结

**VPS = 开仓执行（永远先平后开 + 确认时序 + 独立 RISK20 仓位）+ 呼吸止损引擎（唯一止损写入，WS 逐 tick）+ 订单监控（只报告 TP，handoff 防重挂）+ 反转保护 + 独立行情引擎（90m ATR/ADX）+ 查询失败 fail-closed + 未登记仓位诚实接管。**  
同时到达铁律：一律先平后开，平干净再开。  
TP1/TP2 挂限价兑现部分利润；TP3 不挂，交由阶段二追踪退出。  
除 TV 的四种交易信号（LONG/SHORT/两种 CLOSE）和引擎自身止损 / 交易所 TP 吃满外，不存在第三方策略平仓路径。

---

## 十九、相关文档

| 文件 | 说明 |
|------|------|
| [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) | 架构摘要（指向本 README） |
| [`docs/DELETED_LEGACY_LOGIC_v15.5.13.md`](docs/DELETED_LEGACY_LOGIC_v15.5.13.md) | 已删除旧逻辑清单 |
| [`docs/PROD_GATE_v15.5.13.md`](docs/PROD_GATE_v15.5.13.md) | 生产门禁交付（含提交哈希） |
| [`docs/VPS实盘检查清单.md`](docs/VPS实盘检查清单.md) | Cursor / 开发自查表 |
| [`docs/CHECKLIST_20260723_v1518_RESUME.md`](docs/CHECKLIST_20260723_v1518_RESUME.md) | 最新复验 |
| [`docs/INCIDENT_20260722_HUGE_TV_QTY.md`](docs/INCIDENT_20260722_HUGE_TV_QTY.md) | 天文 qty 事故与防护 |
| `check_vps_logic.py` / `check_deploy_events.py` | 静态与部署后审计 |
| `test_*.py` | 折叠 / 查询失败 / 归因 / 重启止损 / TP handoff / 天文 qty 回归 |

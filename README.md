# GEMINI 双轨交易工厂 · VPS 实盘

**当前版本：`v15.5.2-tv-field-spec`**  
**TV 策略 schema：`v6.5.6`**  
**仓位模式：`RISK20_NOTIONAL5`（含 TV 止损距调整系数）**  
**保护引擎：呼吸止损（`breath_stop` · 90m ATR/ADX）**  
**生产唯一大脑：`position_supervisor_binance.py`**

> 本文档为**唯一权威说明**。凡与旧文档（妈妈版阶梯雷达、TP3 挂限价、CAP_ALIGN、TV `stop_loss` 作止损基准等）冲突，一律以本文为准。

TradingView Alert → Webhook → VPS 接收/校验 → 行情引擎(90m ATR/ADX) → 先平后开 → 市价开仓 → 挂 TP1/TP2 + 呼吸止损全程接管。

| 工厂 | VPS 目录 | 端口 | 品种 | 仓位逻辑 | 钉钉主题 |
|------|----------|------|------|----------|----------|
| **币安**（本仓库） | `~/binance-engine` | **5003** | ETHUSDT + XAUUSDT | RISK20_NOTIONAL5 | 黄金 |
| **深币** | `~/deepcoin-hft-server` | **5004** | ETH + XAU（张） | **同逻辑** | 紫金 |

```bash
curl -s http://127.0.0.1:5003/health | python3 -m json.tool
# version: v15.5.2-tv-field-spec
# sizing: RISK20_NOTIONAL5 · leverage: fixed_5 · tv_strategy: v6.5.6
# radar: breath_stop_90m

python3 check_vps_logic.py
python3 check_deploy_events.py --live
```

---

## 零、四条不可动摇的硬性原则

1. **开仓永远先平后开**  
   不判断新旧仓位方向是否相同；收到 `LONG`/`SHORT` 一律：查实盘 → 有仓则市价全平 + 撤全部挂单 → **等待确认仓位归零** → 再算 qty 开新仓。

2. **单仓位，不加仓**（pyramiding=1）  
   任意时刻一个 symbol 只允许一笔持仓。无多笔 trade 并存、无加权均价重算、无浮盈加仓。

3. **下单数量每次独立计算，无状态**  
   只依赖：账户权益、开仓价、`initialStop`（VPS ATR）、TV.qty、以及 **仅用于调整系数** 的 TV.stop_loss。不读历史仓位、不加仓次数、不读上一笔结果。真实挂止损价仍只用 VPS `initialStop`。

4. **止损单全局唯一写入方 = 呼吸止损引擎**  
   下单 / 改单 / 触发平仓只由呼吸引擎执行。订单监控、重启恢复等模块**不得**直接调用止损类交易所 API，只能通知引擎执行。  
   保留兜底：`HARD_SL_FAIL_ABORT`（改单失败重试告警）、`FORCE_ALIGN`（重启方向不一致强制对齐）。  
   **已删除：`CAP_ALIGN`（仓位上限主动减仓）**。

---

## 一、信号流与架构

```
TradingView v6.5.6 Alert (token=528586)
        │
        ▼
   app.py  /webhook   ← 解析 · token · 品种路由 · 异步线程
        │
        ▼
position_supervisor_binance.py     ← 唯一生产大脑（每 symbol 一实例）
   ├── tv_seq.py                   缓存 1.0s · 同窗折叠 · 先平后开
   ├── webhook_parser.py           动作白名单 · RISK20 仓位纯函数
   ├── market_engine.py            30m×3→90m · Wilder ATR/ADX(14)
   ├── breath_stop.py              两阶段呼吸止损状态机
   ├── binance_client.py           REST + markPrice WS + 用户数据流
   └── dingtalk.py                 钉钉 / 企业微信双通道播报
```

| 环节 | 行为 |
|------|------|
| 缓存窗口 | 同 symbol 首包后 **固定 1.0s** settle，到期统一处理 |
| 同窗折叠 | 平仓只保留一条；开仓只保留最新一条；顺序永远 **先平后开** |
| 去重 | 60s 内同一 `action+symbol` 直接忽略 |
| 哨兵 | `_sentinel_loop` **0.5s** 轮询：对账仓位、TP 成交、呼吸 tick、挂单修复 |
| 状态文件 | `binance_vps_state_{SYMBOL}.json`（ETH / XAU 独立） |

---

## 二、Webhook：仅 4 个有效 action

解析层只接受：

| action | 含义 |
|--------|------|
| `LONG` | 开多（先平后开） |
| `SHORT` | 开空（先平后开） |
| `CLOSE_QUICK_EXIT` | 反转保护 · 市价全平 |
| `CLOSE_RSI_EXIT` | RSI 反转保护 · 市价全平 |
| `PING` | 心跳（不交易） |

**别名归一**（示例）：`BUY→LONG`，`SELL→SHORT`，`CLOSE`/`QUICK_EXIT`/`CLOSE_PROTECT→CLOSE_QUICK_EXIT`，`RSI_EXIT→CLOSE_RSI_EXIT`。

**一律拒绝 / 忽略**（不执行）：  
`CLOSE_TP` · `CLOSE_TRAIL` · `CLOSE_SL_*` · `CLOSE_TP3` · `UPDATE_SL` · `UPDATE_TP` · `leg` 字段驱动平仓等。

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
  "token": "528586"
}
```

### 2.2 每个字段用在哪（是否参与实际止损价）

| 字段 | VPS 怎么用 | 参与实际止损价？ |
|------|------------|------------------|
| `price` | 开仓参考价；与 `stop_loss` 做差得到「TV 隐含止损距离」，只用于仓位换算 | **否** |
| `qty` | 三选一候选之一（先经 sl_adj 换算，见第四节） | **否**（只影响仓位大小） |
| `qty1` / `qty2` | TP1/TP2 **限价止盈**挂单数量意图；按 `实开仓/TV总量` 缩放后挂出 | **否** |
| `qty3` | **不使用**（TP3 不挂限价，余仓交阶段二） | 不适用 |
| `stop_loss` | **只**反推 TV 隐含止损距离 → 修正仓位；**绝不**作为盘口止损价 | **否**（最易误解） |
| `tp1` / `tp2` | TP1/TP2 限价止盈**价格** | **否** |
| `tp3` | **不使用** | 不适用 |
| `atr` / `adx` | **不读取**；一律用 VPS 行情引擎 | **否** |
| `symbol` / `ticker` | 品种路由 ETH / XAU | — |
| `bar_index` / `seq` | 时序排序与幂等 | — |
| `token` | 必须 = `528586` | — |
| `reason` | 反转平仓文案 | — |

**一句话：** 消息里只有 `price` 与 `stop_loss` 会做一次减法算出「TV 隐含止损距离」，**唯一用途是修正仓位数量**；不会、也不能当作 VPS 挂在交易所上的止损单价格。

`price` / `stop_loss` 仅开仓瞬间用一次；可落日志，**不参与后续任何 tick 级止损计算**。

---

## 三、LONG / SHORT 开仓流程

1. 查询交易所**实际持仓**（不只信本地缓存）  
2. 若非空：市价全平 → 撤销所有挂单 → **等待成交回报或仓位归零确认** → 重置该 symbol 呼吸引擎全部状态 → 钉钉「先平后开」  
3. **独立**拉 30m K → 合成 90m → 算 `initialAtr`（此后锁定）→ `initialStop = entry ± 1.5×initialAtr`（与 TV.stop_loss **零交集**）  
4. 按第四节公式算最终下单数量（含 sl_adj）  
5. `set_leverage` 固定 **5x** → 市价开仓  
6. 挂 **TP1+TP2** 限价（价格=`tp1`/`tp2`，数量=`qty1`/`qty2` 按实开缩放）  
7. **不挂 TP3**（`qty3` 对应余仓由阶段二追踪）  
8. 呼吸止损初始化：挂 STOP（`quantity=全仓`，价格=`initialStop`）并立即接管  
9. 钉钉：开仓详情（方向、价格、数量、initialStop）

开仓成功后的**下一个 tick 起**：呼吸引擎逐 tick 更新止损价，**不再需要 TV 传任何新数据**，直到仓位归零（止损触发或反转保护全平）。

---

## 四、仓位数量计算（开仓算一次，此后不变）

```
VPS实际止损距离 = |entryPrice − initialStop| = 1.5 × initialAtr
TV隐含止损距离 = |price − stop_loss|
调整系数 sl_adj = TV隐含止损距离 / VPS实际止损距离     # 缺 stop_loss → 1.0
调整后的TV数量上限 = qty × sl_adj

风险资金 = 账户本金 × 20%
名义上限 = 账户本金 × 5

理论数量 = min(
    风险资金 / VPS实际止损距离,
    名义上限 / entryPrice,
    调整后的TV数量上限,
)
最终下单数量 = 向下取整至交易所精度
```

**为什么需要 sl_adj：** TV.qty 按 TV 内部止损距（常见约 1.0×ATR）算出；实盘初始止损距是 **1.5×ATR**。若直接 `min(..., TV.qty)`，按 VPS 止损触发时实际亏损会被放大约 50%。换算后，只要止损在 VPS `initialStop` 触发，亏损可控在风险资金预算内。

数量在开仓算完即固定，**不**随后续价格/ADX 重算。开仓日志打印：`sl_adj`、三候选、`binding`。

---

## 五、CLOSE_QUICK_EXIT / CLOSE_RSI_EXIT（反转保护）

1. 立即市价全平剩余仓位（不管浮盈）  
2. 撤销所有未成交挂单（TP1/TP2、止损）  
3. 重置该 symbol 呼吸止损全部状态  
4. 停止该 symbol 订单监控  
5. 钉钉：反转保护平仓 · `reason` · 价格  

除 TV 这两种信号 + 呼吸引擎自身止损触发外，**不存在任何第三方平仓判断路径**。

---

## 六、呼吸止损引擎（与 TV 完全无关 · 开仓即接管）

实现：`breath_stop.py`。盘口：`STOP_MARKET` + `reduceOnly` + 明确 `quantity`。

### 6.1 VPS 自算初始止损（与 TV.stop_loss 平行、互不干扰）

1. 交易所 30m K → 合成 90m  
2. `initialAtr = ATR(14)`（开仓锁定，全程不变）  
3. 多：`initialStop = entry − 1.5×initialAtr`；空：`entry + 1.5×initialAtr`  
4. 以此价挂首笔止损，并作为后续阶梯基准  

### 6.2 必须持久化 / 每 tick 更新

| 字段 | 开仓后 | 说明 |
|------|--------|------|
| `initialAtr` / `open_atr` | **固定** | 不因后续 ATR 刷新而重算止损距 |
| `initialStop` / `initial_stop` | **固定** | 阶梯基准 |
| `currentStop` / `current_sl` | **每 tick 可上移** | 只朝盈利方向 |
| `highestPrice` / `lowestPrice` (`best_price`) | **每 tick** | 多只增 / 空只减 |
| `breakevenPhase` | **只可 false→true** | 进入阶段二后不可回退 |
| `remaining_qty_pct` | TP 成交后更新 | 止损单数量收缩 |

### 6.3 每个 markPrice tick

1. 更新 `highestPrice` / `lowestPrice`  
2. `breakevenPhase == false` → 阶段一；否则 → 阶段二  
3. 新止损更优（多更高 / 空更低）才改单；否则本 tick 空操作  
4. 价格跌破/突破 `currentStop` → 市价全平剩余 → 重置状态 → 钉钉  

### 6.4 阶段一（保本前 · 多单示意）

```
step_count = max(0, floor((price − entry) / (0.75 × initialAtr)))
step_stop  = initialStop + step_count × 0.4 × initialAtr
候选 = max(currentStop, step_stop)

若 price ≥ entry + 1.35×ATR → 候选 = max(候选, entry + 0.5×ATR)   # TP1 底线
若 price ≥ entry + 2.5×ATR  → 候选 = max(候选, entry + 1.5×ATR)   # TP2 底线

currentStop = 候选

若 price ≥ entry + 3.0×ATR：
    breakevenPhase = true
    currentStop = max(currentStop, highest − trail_distance(adx)×initialAtr)
```

### 6.5 阶段二（ADX 连续追踪）

ADX 来自行情引擎，**每根 90m K 闭合更新一次**（tick 间复用上次 ADX）；**只有止损价每个 tick 重算**。

```
trail_dist = trail_distance(adx) × initialAtr   # ADX≤15→1.2；≥35→2.5；中间线性插值
候选 = highestPrice − trail_dist
currentStop = max(currentStop, 候选)   # 只上移不倒退
```

### 6.6 HARD_SL_FAIL_ABORT

改单失败重试 3 次 → 钉钉告警，保持现状，不自主平仓。

---

## 七、订单监控（TP1 / TP2）

| 事件 | 行为 |
|------|------|
| TP1 成交 | 剩余比例更新；引擎撤旧止损→按剩余 qty + 当前 `currentStop` 重挂；期间暂停 breath tick |
| TP2 成交 | 同上 → 剩余进一步收缩 |
| TP 限价 >5 分钟未成交 | 撤单，头寸移交呼吸引擎，禁止重复挂单 |
| 全部平仓 | 归零 → 撤挂单 → 重置呼吸态 → 停监控 → 钉钉 |

强制底线已由阶段一公式覆盖。缺 `qty1/qty2` 时回退 30/30 比例切分；`PLACE_TP_LEVELS=2`。

---

## 八、VPS 行情引擎

| 项 | 值 |
|----|-----|
| 数据源 | 交易所 **30m** 原始 K 线 |
| 合成 | 每 3 根 → 1 根 **90m**（开=首开，收=末收，高=三高，低=三低，量=求和） |
| 指标 | 合成 K 闭合后重算 **ATR(14)**、**ADX(14)**（Wilder / 与 TV RMA 对齐） |
| 刷新下限 | ≥60s |
| 权威性 | 止损距离只用开仓锁定的 `open_atr`；ADX 可刷新；webhook atr/adx 无效 |

上线前应用人工核对 VPS 自算 ATR/ADX 与 TV 90m 图表是否一致。

---

## 九、重启恢复

1. 查询交易所所有持仓与未成交挂单  
2. 有持仓 → 读持久化呼吸状态  
   - **旧 schema**（存在 `activated`/`stepCount`/`radar_*` 等旧字段，但缺少 `initial_stop`/`open_atr`/`breakeven_phase`）→ **视为无效**：钉钉告警 + `trading_paused`，**禁止自动转换**  
3. **FORCE_ALIGN**（保留）：持仓方向与最近开仓方向不一致 → 市价全平 + 撤单 + 重置 + 钉钉，继续等待新信号（非自主策略平仓）  
4. 按恢复的 `currentStop` 重挂止损  
5. 恢复未成交且价格仍有利的 TP1/TP2  
6. 重启行情引擎 → 恢复逐 tick 计算  
7. 无持仓 → 清状态，待命  
8. 钉钉：重启恢复 / 空仓待命  

**CAP_ALIGN 已删除**：禁止「仓位超限 reduceOnly 减仓」类自主平仓补丁。

---

## 十、防螺旋与自我检查

| 机制 | 行为 |
|------|------|
| 仓位一致性 | tick / webhook / 订单监控时以交易所为准修正本地 |
| TP 超时 | 5 分钟未成交 → 撤单移交呼吸引擎 |
| 重复消息 | 60s 同 action+symbol 忽略 |
| 改单失败 | 3 次重试 → HARD_SL_FAIL_ABORT |
| API 断线 | 自动重试，指数退避（1s, 2s, 4s…） |
| 开仓偏离目标 | **只告警，不减仓**（CAP_ALIGN 已废） |

---

## 十一、钉钉事件清单

| 事件 | 内容要点 | 函数 |
|------|----------|------|
| 开仓 | 方向、价格、数量、initialStop | `report_supervisor_open` |
| 先平后开 | 已有持仓已全平撤单，准备开新仓 | `report_close_then_open_chain` |
| 阶段切换（一→二） | 切换价、ADX、追踪距离 | `report_radar_activated` |
| 止损移动 | 新止损、浮盈、所处阶段 | `report_intervention` |
| TP1/TP2 成交 | 成交价、剩余比例、当前止损 | `report_tp_fill` |
| 止损触发 | 触发价、阶段一/二、盈亏 | `report_supervisor_close` |
| 反转保护平仓 | reason、平仓价 | `report_supervisor_close` |
| 重启恢复 / 待命 | 状态、方向、数量 | `report_recover_takeover` / `report_recover_standby` |
| FORCE_ALIGN | 方向不一致、已全平重置 | `report_force_align` |
| HARD_SL_FAIL_ABORT | 改单失败、保持现状 | `report_hard_sl_fail_abort` |
| 异常告警 | 对账不一致、挂单超时等 | `report_system_alert` |

**禁止出现的旧文案**：雷达激活、保护性全平、TP3 止盈成交、加仓成交、档位限额强制对齐（CAP_ALIGN）。

---

## 十二、生产模块一览

| 模块 | 职责 |
|------|------|
| `app.py` | Flask 网关 · `/webhook` · `/health` · 端口 5003 |
| `position_supervisor_binance.py` | 唯一大脑 · 开平仓 · 哨兵 · 恢复 |
| `breath_stop.py` | 两阶段呼吸止损纯函数 |
| `market_engine.py` | 90m 合成 · ATR/ADX |
| `webhook_parser.py` | 解析归一 · RISK20 仓位 · 动作白名单 |
| `tv_seq.py` | 1.0s 缓存 · 先平后开折叠 · 幂等 |
| `binance_client.py` | REST · markPrice WS · 用户数据流 |
| `symbol_config.py` | ETH / XAU 元数据与路由 |
| `dingtalk.py` | 钉钉 + 企业微信播报 |
| `deploy_binance.sh` | 干净重部署 · 健康检查 · 自动跑事件自检 |
| `check_vps_logic.py` | 静态逻辑自查（CI / 本地） |
| `check_deploy_events.py` | **部署后**事件函数 + smoke + 可选 `/health` |

**非生产路径**（勿当作大脑）：`order_executor.py`、`position_manager.py`、`profit_taker.py`、`state_manager.py` 等历史模块。

---

## 十三、部署与自检

```bash
cd ~/binance-engine
git fetch origin && git reset --hard origin/main

grep 'BINANCE_VPS_VERSION' position_supervisor_binance.py
# 期望: v15.5.2-tv-field-spec

bash deploy_binance.sh
# 脚本末尾会自动: python3 check_deploy_events.py --live

curl -s http://127.0.0.1:5003/health | python3 -m json.tool

# 手动全面自检
python3 check_deploy_events.py --live          # 事件函数 + smoke + health
python3 check_deploy_events.py --live --deep   # 再跑 check_vps_logic 全套
python3 check_vps_logic.py -v                  # 仅静态逻辑
```

### `check_deploy_events.py` 覆盖

- 钉钉 §十一全部关键事件函数是否存在、CAP_ALIGN/加仓是否为 no-op  
- Supervisor 核心方法：`_full_reentry`、`_sync_exchange_stop`、`_breath_resize_stop_on_tp`、`recover_state_on_startup`…  
- Webhook 白名单、PLACE_TP_LEVELS=2  
- 呼吸止损 / RISK20 仓位数值 smoke  
- 行情引擎接口、先平后开时序  
- `--live`：本机 `/health` 版本与 sizing  

---

## 十四、已删除 / 禁止的旧逻辑（对照清理表）

| 分类 | 已删除项 |
|------|----------|
| 仓位 | `(equity×0.20×5)/price` 旧公式；加仓 / `opentrades` / pyramiding>1 |
| 止盈 | TP3 限价挂单与成交监控主路径 |
| 旧雷达 | `activated` 0.85×TP1 激活；步进 0.5/0.3 ATR；TP3 后固定 2.0×ATR 追踪 |
| TP 成交 | 「止损=entry+1tick / entry+1.5ATR」独立强制分支（改由阶段一底线自动覆盖） |
| 自主平仓 | 保护性全平、**CAP_ALIGN** 档位减仓 |
| Webhook | CLOSE_TP / CLOSE_TRAIL / CLOSE_SL_* / leg；读取 msg.atr/adx 作权威 |
| 钉钉 | 雷达激活、保护性全平、TP3 止盈、加仓成交、档位限额裁减 |

**保留**：`HARD_SL_FAIL_ABORT`、`FORCE_ALIGN`。

`webhook_parser.compute_ladder_radar_sl` 等旧函数若仍在仓库中，标注为**已废除**，禁止进入实盘决策路径（supervisor 决策只走 `breath_stop`）。

---

## 十五、上线前验证清单

1. 全局搜索旧参数 `0.85` / `0.5`/`0.3` ATR 步进 / `2.0` TP3 追踪，确认不在生效路径  
2. 模拟盘完整生命周期：开仓 → 阶段一阶梯 → TP1 → TP2 → 阶段二 → 止损或追踪平仓  
3. 重启恢复：运行中重启；旧 schema 应告警暂停而非强转  
4. ATR/ADX：VPS 90m 与 TV 图表核对  
5. 先平后开时序：平仓确认完成前，新开仓计算不得提前发生  
6. `python3 check_deploy_events.py --live --deep` 全绿  

---

## 十六、一句话总结

**VPS = 开仓执行（先平后开 + 独立仓位计算）+ 呼吸止损引擎（唯一止损写入，全程一套逻辑）+ 订单监控（只报告 TP 成交，不碰止损单）+ 反转保护执行 + 独立行情引擎（90m 合成 ATR/ADX）。**  
TP1/TP2 挂限价兑现部分利润；TP3 不挂，交由阶段二追踪退出。  
除 TV 的三种交易信号（LONG/SHORT/两种 CLOSE）和引擎自身止损触发外，不存在任何第三方平仓判断路径。

---

## 十七、相关文档

| 文件 | 说明 |
|------|------|
| [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) | 架构摘要（指向本 README） |
| [`docs/VPS实盘检查清单.md`](docs/VPS实盘检查清单.md) | Cursor / 开发自查表 |
| `check_vps_logic.py` | 静态逻辑审计 |
| `check_deploy_events.py` | 部署后事件与函数审计 |

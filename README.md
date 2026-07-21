# GEMINI 双轨交易工厂 · VPS 实盘

**当前版本：`v15.5.0-final-spec`**  
**TV 策略 schema：`v6.5.6`**  
**仓位模式：`RISK20_NOTIONAL5`**  
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
# version: v15.5.0-final-spec
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
   只依赖：账户权益、开仓价、`initialStop`（VPS ATR）、TV.qty。不读历史仓位、不加仓次数、不读上一笔结果。

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

| 字段 | 用途 |
|------|------|
| `action` / `price` / `qty` | 必填（开仓）；`qty` 为上限帽 |
| `tp1` / `tp2` / `tp3` | TP 限价价格（只挂 TP1+TP2；tp3 仅日志参考） |
| `stop_loss` / `tv_sl` | **仅日志参考**，不参与止损计算 |
| `atr` / `adx` | **不读取、不采纳**；一律用 VPS 行情引擎 |
| `symbol` / `ticker` | 路由 ETH / XAU |
| `bar_index` / `seq` | 时序排序与幂等 |
| `token` | 必须 = `528586`（可用环境变量覆盖） |
| `reason` | 反转平仓原因文案 |

---

## 三、LONG / SHORT 开仓流程

1. 查询交易所**实际持仓**（不只信本地缓存）  
2. 若非空：市价全平 → 撤销所有挂单 → **等待成交回报或仓位归零确认** → 重置该 symbol 呼吸引擎全部状态 → 钉钉「先平后开」  
3. 用 VPS ATR 算 `initialStop = entry ± 1.5×ATR`，再按第二节公式算 qty（无状态）  
4. `set_leverage` 固定 **5x** → 市价开仓  
5. 挂 **TP1** 限价（数量 ≈ 30%）· 挂 **TP2** 限价（数量 ≈ 30%）  
6. **不挂 TP3**（余仓 40% 由阶段二追踪退出）  
7. 呼吸止损初始化并立即挂 STOP（`quantity=全仓`，价格=`initialStop`）  
8. 行情引擎持续提供 ATR/ADX；哨兵接管监控  
9. 钉钉：开仓详情（方向、价格、数量、initialStop）

---

## 四、仓位公式（RISK20_NOTIONAL5 · 无状态纯函数）

```
风险资金 = 账户本金(合约权益) × 20%
名义上限 = 账户本金(合约权益) × 5
initialStop = 开仓价 ± 1.5 × ATR(VPS 90m)     # 多减空加
理论数量 = min(风险资金 / |开仓价 − initialStop|, 名义上限 / 开仓价)
最终数量 = min(理论数量, TV.qty)
向下取整至交易所精度（ETH 约 0.001）
```

**输入只有**：账户余额、开仓价、`initialStop`、TV.qty。  
**禁止**：旧公式 `(equity×0.20×5)/price`、忽略止损距离、忽略 TV.qty、按历史仓位叠加。

额外：多品种合计名义仍受总敞口闸约束（实现见 `_assert_notional_cap_or_reject`）。

---

## 五、CLOSE_QUICK_EXIT / CLOSE_RSI_EXIT（反转保护）

1. 立即市价全平剩余仓位（不管浮盈）  
2. 撤销所有未成交挂单（TP1/TP2、止损）  
3. 重置该 symbol 呼吸止损全部状态  
4. 停止该 symbol 订单监控  
5. 钉钉：反转保护平仓 · `reason` · 价格  

除 TV 这两种信号 + 呼吸引擎自身止损触发外，**不存在任何第三方平仓判断路径**。

---

## 六、呼吸止损引擎（开仓即接管 · 唯一止损写入方）

实现：`breath_stop.py` · 接线：`_apply_breath_stop_tick` / `_sync_exchange_stop` / `_breath_resize_stop_on_tp`

### 6.1 必须持久化的状态

| 字段 | 含义 |
|------|------|
| `entryPrice` / `watched_entry` | 开仓均价 |
| `open_atr` / `initialAtr` | 开仓时刻 VPS ATR，全程锁定 |
| `initial_stop` | `entry ± 1.5×initialAtr`，阶梯公式基准 |
| `current_sl` / `currentStop` | 当前止损，只朝盈利方向移动 |
| `best_price` | 持仓期最高（多）/ 最低（空） |
| `breakeven_phase` | 是否已进阶段二（一旦 True 不可回退） |
| `remaining_qty_pct` | 剩余仓位比例（TP 成交后更新） |
| `last_adx` | 最近 ADX（阶段二追踪宽度） |

### 6.2 阶段一（保本前）— 多单示意，空单镜像

```
step_count = max(0, floor((price − entry) / (0.75 × initialAtr)))
step_stop  = initialStop + step_count × 0.4 × initialAtr   ← 基准是 initialStop，不是 entry
candidate  = max(currentStop, step_stop)

若 price ≥ entry + 1.35×ATR → candidate = max(candidate, entry + 0.5×ATR)   # TP1 强制底线
若 price ≥ entry + 2.5×ATR  → candidate = max(candidate, entry + 1.5×ATR)   # TP2 强制底线

currentStop = candidate

若 price ≥ entry + 3.0×ATR：
    breakevenPhase = True
    trail_dist = trail_distance(adx) × initialAtr
    currentStop = max(currentStop, highestPrice − trail_dist)
```

### 6.3 阶段二（保本后 · ADX 连续追踪）

```
trail_dist = trail_distance(adx) × initialAtr
candidate  = highestPrice − trail_dist
currentStop = max(currentStop, candidate)   # 只上移不倒退
```

### 6.4 追踪距离插值

```
trail_distance(adx):
  adx ≤ 15 → 1.2
  adx ≥ 35 → 2.5
  否则     → 1.2 + (adx−15)/20 × (2.5−1.2)
```

趋势越强（ADX 高）距离越宽，让利润奔跑；越弱越窄，加速锁利。

### 6.5 止损触发

价格跌破（多）/ 突破（空）`currentStop` → 市价全平剩余仓位 → 重置状态 → 钉钉（含阶段一/二）。

盘口形态：`STOP_MARKET` + `reduceOnly` + **明确 quantity**（跟随剩余仓位），由引擎统一改挂。

### 6.6 HARD_SL_FAIL_ABORT（保留）

改单 / 挂单失败 → 重试 **3** 次 → 仍失败则钉钉 `report_hard_sl_fail_abort`，**保持当前止损不变**，不视为 VPS 自主平仓。

---

## 七、订单监控（TP1 / TP2）

订单监控**只检测成交并通知引擎**，不直接操作止损单。

| 事件 | 行为 |
|------|------|
| TP1 成交 | `remainingQtyPct≈70%`；通知引擎：撤旧止损 → 按 70% qty + 当前 `currentStop` 重挂；期间 `_breath_tick_paused` |
| TP2 成交 | 同上 → ≈40% |
| TP 限价超时 | 挂单 **>5 分钟**未成交 → 取消该档 → 头寸移交呼吸引擎，禁止重复挂单 |
| 全部平仓 | 确认归零 → 取消挂单 → 重置呼吸态 → 停监控 → 钉钉 |

强制底线已由阶段一公式自动覆盖，**无** TP 成交后「止损=entry+1tick」等独立强制分支。

比例常量：`LEG_TP_RATIOS = [0.30, 0.30, 0.40]`，`PLACE_TP_LEVELS = 2`。

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
# 期望: v15.5.0-final-spec

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

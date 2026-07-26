# 币安单一账户系统（binance-engine）· 终极生产级

**当前版本：`v16.4.0-tv-atr-no-tp3`**  
**TV 策略 schema：`v6.5.6`**  
**仓位模式：`RISK20_NOTIONAL5`**（ETH/XAU 同一公式：`qty = 本金×20%×5 / 开仓价`；TV.qty 可选 soft-cap；20U 演练可传小 qty）  
**保护引擎：三层防线**（永久硬止损 + 独立雷达止损 + TP1/TP2 限价；**TP3 永不挂限价**，70% 交雷达）  
**TP 分腿：10% / 20% / 70%**（盘口限价 **恰好 2** 笔 LIMIT=TP1+TP2；余仓无上限）  
**硬止损：`|TV.price−TV.stop_loss|×1.15` 锚定成交价**（**统一呼吸垫，不分档**；禁止 1.5×ATR 地板；开仓以市价回执为准，禁因 REST 滞后跳过硬止损）  
**雷达（规格 §5.1）**：弱/中/强档步进；**绝对价锚定** — 首次 **(TP1+TP2)/2** · 重入 **TP2**；激活臂 **entry±0.5×TV.atr**；达线前盘口**仅硬止损**  
**ATR：只信 TV webhook `atr`**（已删除 VPS 独立拉 1h/合成 ATR、场景一二与降级切换）  
**重入：最多 1 次**；窗口 ETH 2×90m≈3h · XAU 3×45m≈2.25h；双保险再入价；成功后雷达放宽一档 + 激活门改为 TP2  
**幂等铁律（v15.9.1+）**：本地订单标签未释放 → 绝对拒挂；查单失败 fail-closed；未成交挂单硬上限 **5**  
**生产闸门（v15.9.3）**：竞态/部分成交失败/限流/`本地标签vs空盘` → **`trading_paused`**；REST 单品种 ≥100ms；档位 `config/reentry_tiers.json`；30s 状态快照；日志 `[OPS|STATE|ALERT|AUDIT]`  
**日熔断开仓闸门（v15.9.2）**：**暂时关闭**（`CIRCUIT_BREAKER_OPEN_GATE_ENABLED=False`）；`risk_manager` 仅记账，不挡真实 TV  
**TV 图表周期：ETH 90m · XAU 45m**（VPS **不再**另拉 ATR）  
**生产唯一大脑：`position_supervisor_binance.py`**（每 symbol 一实例）  
**通知：Telegram 全量 + 钉钉仅重要告警（`dingtalk.py` · 品牌前缀【币安单系统】· `TELEGRAM_*`）**

> **绝对红线（曾实盘击穿）**：查不到挂单 → **禁止**「再挂一张」。历史事故：同价 LIMIT 叠到 **50+ 笔**。现行多层铁律见下文「防叠单专章」。  
> **双 STOP 说明**：雷达未激活时盘口**只应有硬止损**；激活后才硬+雷达双挂。TV 原 `stop_loss` **不挂盘**（只作硬止损距离输入）。  
> **硬止损（唯一公式）**：`|TV价 − TV.stop_loss| × 1.15`，挂在**成交价**外侧。缺/异常 `stop_loss` → **拒开**。巡检/接管禁止用 0.5×ATR 顶替。  
> **TP（v16.4.0）**：只挂 TP1+TP2（10%/20%）；**TP3 永远不挂限价**，70% 完全交雷达（无价格天花板）。  
> **ATR（v16.4.0）**：全程只用 webhook `atr`；删除场景一/二与 `atr_1h` 拉取。  
> **雷达（规格 §5.1 · v16.3.7+）**：首次激活价=`(TP1+TP2)/2`，重入激活价=`TP2`；过中点未到 TP2 的重入单仍须休眠。  
> **叠单铁律**：挂单查询失败 → **fail-closed 禁止挂**；本地标签未清拒挂；未成交挂单总数 **≥5 熔断**。  
> **API 限流**：REST 仅下单/改撤/对账；价格与成交靠 WebSocket；空闲巡检 45s + 失败退避 120s；单品种 REST ≥100ms；触发 -1003 → 暂停品种；**禁止运维脚本狂轮询**。  
> **v16.4.0**：删 TP3 限价/互斥；ATR 只信 TV；清理历史 TP3。  

> **权威依据**：[《VPS完整系统规格_币安单账户版》](docs/VPS完整系统规格_币安单账户版.md)（第三轮修正：TP3 不挂限价 + ATR 只用 TV）+ 本文。  
> 旧逻辑清除对照：[`docs/DELETED_LEGACY_LOGIC_v15.7.0.md`](docs/DELETED_LEGACY_LOGIC_v15.7.0.md)


```bash
curl -s http://127.0.0.1:5003/health | python3 -m json.tool
# version: v16.4.0-tv-atr-no-tp3 · sizing: RISK20_NOTIONAL5 · trading_paused: false

python3 check_vps_logic.py
python3 test_defense_v1590.py
python3 test_risk_iron_v1591.py
python3 test_radar_reentry.py
python3 test_two_scenario_atr.py
python3 test_orders_dup_guard.py

# 生产级 20U 实盘矩阵（ETH/XAU × LONG/SHORT；需在 VPS 且密钥可用）
# sudo -u trading ./venv/bin/python3 live_test_20u_matrix.py
```

| 工厂 | VPS 目录 | 端口 | 品种 |
|------|----------|------|------|
| **币安**（本仓库） | `~/binance-engine` | **5003** | ETHUSDT + XAUUSDT |
| **深币**（对照） | `~/deepcoin-hft-server` | **5004** | ETH + XAU |

---

## 防叠单专章（红线 · 曾 50+ 同价 LIMIT）

### 事故回顾
VPS 在 **openOrders 查询失败/超时** 时，错误地认为「没有挂单」，于是反复 `place_limit`，同一价格可叠出 **50+ 笔** 限价单。根因是**幂等性缺失 + 查询失败当空**。

### 现行多层闸门（缺一不可）

| 层 | 机制 | 失败时行为 |
|----|------|------------|
| 1 本地标签 | 再入 `reentry_order_tag`；防御 TP `pending_order_tags` + `newClientOrderId` | 标签未释放 → **绝对拒挂**（即使交易所返回空） |
| 2 fail-closed | `ORDERS_QUERY_FAILED` 哨兵；`place_limit`/`place_stop` 查单失败 | **return None**，禁止「盲补首挂」 |
| 3 同价去重 | `_existing_same_limit` / 120s 本地缓存 | 已有同价 → 复用，不新挂 |
| 4 硬上限 5 | 未成交挂单总数 ≥5 **或** LIMIT≥5 | 熔断拒挂 + 可 `trading_paused` |
| 5 无菌开仓 | `_verify_sterile_flat`：qty=0 且 LIMIT+STOP=0 | 不净 → 拒开 |
| 6 持仓对账 | 哨兵约 30s `_held_position_reconcile` | 超上限 → 暂停该品种 |
| 7 TP3↔雷达 | 持久化 `exit_ownership` | 一腿成交锁定，禁止另一腿再挂 |

**原则：宁可错过，不要做错。** 任何「查不到单」默认**不挂不撤**，等下一周期或人工。

相关实现：`order_idempotency.py` · `binance_client.place_limit_order` · `radar_reentry_mixin._place_reentry_limit` · `PositionSupervisorBinance._place_defense_tp_limit`。

---

## 交易所 API 限流与 WebSocket 分工

| 功能 | 通道 | 频率 / 策略 |
|------|------|-------------|
| 价格监控 | **WebSocket** 行情 | 实时 |
| 订单成交 | **WebSocket** User Data | 实时推送优先 |
| 开仓 / 改撤 | REST | 仅信号触发；单品种调用间隔留余量 |
| 持仓核对 | REST | 持仓期约 30s；空闲巡检 **45s** |
| 挂单查询 | REST | 下单前必查；失败 → fail-closed |
| 5m K 线（再入） | REST | 再入时按需；失败降级 TV×系数 |
| WS 断线 | 指数退避重连 | 1s → 60s 封顶 |

**限流防护**
- 空闲巡检 `QUERY_FAILED` / `-1003` → 退避 **120s**（`IDLE_PATROL_BACKOFF_SEC`）  
- 核武对齐刹车：`NUCLEAR_REALIGN_MIN_INTERVAL_SEC`（防秒挂秒撤打爆 REST）  
- 雷达改单最短间隔 5s  
- 20U 实盘矩阵：周期前冷却 30s、周期间 22s，避免演练自身触发 ban  
- **禁止**用 REST 轮询价格/成交替代 WebSocket  

触发 API ban 时：暂停该品种激进补挂路径，等待窗口结束；**禁止**在 ban 中循环 place。

---

## 日熔断与开仓闸门（v15.9.2）

`risk_manager.py` 仍记录：日亏 5.5%、连续亏 3、日交易 8、回撤 12%。  

**开仓拒开闸门已暂时关闭**：`CIRCUIT_BREAKER_OPEN_GATE_ENABLED = False`。  
原因：日熔断状态易在演练/恢复路径误挡真实 TV；真正防击穿依赖挂单幂等硬上限，而非日亏挡信号。  

重新启用：将该常量改为 `True` 并走完整回归。监控仍可看 `risk_manager.get_status()`。

---

## 零、三层防线永久共存模型（核心·不可误解）

开仓成交瞬间**同步**做三件事（不分先后）：

1. **挂永久硬止损**  
   距离 = `|TV价 − TV.stop_loss| × 1.15`（统一呼吸垫，见 `defense_profiles.py`）  
   → 挂在**交易所成交价**外侧（closePosition）。缺/过小 `stop_loss` → **拒开**。  
   身份：**永久防线**。仓位归零前：**不改价、不撤销**（仅公式升级允许一次性重挂）。  
   实现：`atr_scenario.hard_stop_price` → `frozen_hard_sl_px` + `_ensure_frozen_hard_sl`。

2. **挂 TP1+TP2+TP3 限价止盈**  
   价格 = TV `tp1`/`tp2`/`tp3`；数量 = VPS 自算总仓位的 **10% / 20% / 70%**。  
   与硬止损同时挂出。每档带防御 `clientOrderId`。TP3 与雷达对同一余仓**互斥**：谁先成交撤另一腿并写 `exit_ownership`。

3. **启动 VPS 原生 1h ATR 拉取**（呼吸系数 / 场景决议；≠ TV 图表周期）  
   - **场景一**（成功）：雷达用真实 ATR  
   - **场景二**（失败）：雷达用 TV `atr`；可持续恢复场景一  
   - 两种场景均保留三级 TP 限价（不再因场景一撤 TP3）

### 硬止损 vs 雷达止损

| | 永久硬止损 | 雷达止损 |
|--|-----------|---------|
| 挂出时机 | 开仓瞬间 | 硬止损+TP 挂好后，引擎独立计算再挂 |
| 价格来源 | **唯一公式** `|TV−SL|×1.15` 锚定成交价 | 呼吸引擎（场景一/二 ATR） |
| 数量 | closePosition（始终覆盖剩余） | 明确 quantity=剩余仓位 |
| 改价 | **禁止**（公式升级重挂除外） | 可随呼吸上移（只收紧） |
| 撤销 | **仅仓位归零** | 仓位归零 / 互斥撤 / 被触发 |
| 关系 | 两笔**独立共存**，不是升级/替换/接管 |

**谁先被价格触及谁先平仓；任一归零 → 立即撤销另一笔及全部挂单。**  
**禁止**：先撤硬止损再挂雷达；禁止因雷达更优而撤硬止损；禁止改硬止损价去「同步」雷达。

### 部分平仓时数量同步（原子）

- TP1/TP2 部分或全部成交 → `_atomic_resize_after_partial_tp`：更新头寸 → 收缩雷达数量 → 调整剩余 TP（含 TP3）  
- 硬止损 closePosition 自动覆盖剩余；**不改硬止损价**  
- 任一步失败 → 告警，不在中间态继续狂挂

### 示例（ETH SHORT）

TV 价 1897.03，TV.SL 1912.18，成交 1900.51：  
`dist = |1897.03−1912.18|×1.15 ≈ 17.42` → **硬止损 ≈ 成交价 + 17.42**。  
雷达另挂独立 STOP。账户同时 **硬止损 + 雷达 + TP123**（总数 ≤5）。

---

## 一、五条硬性原则

1. **开仓永远先平后开**（含同向；无菌：qty=0 且 LIMIT+STOP/Algo=0）  
2. **单仓位，不加仓**（pyramiding=1）  
3. **下单数量**：`(本金×20%×5)/price`；`stop_loss`/`TV.qty` 可选收紧；不采信天文 TV.qty  
4. **双 STOP 永久共存**（见 §零）；写入方：`_ensure_frozen_hard_sl`（硬）+ `_sync_exchange_stop`（雷达）  
5. **15s 开平窗口**：同 symbol 内 OPEN 先到→丢弃窗内 CLOSE；CLOSE 先到→先平后开；超时 CLOSE 独立执行

---

## 二、信号流与架构

```
TradingView v6.5.6 Alert (secret)
        │
        ▼
   app.py  /webhook
        │
        ▼
position_supervisor_binance.py     ← 唯一生产大脑
   ├── tv_seq.py                   1.0s 缓存折叠 + 15s OPEN/CLOSE 铁律
   ├── webhook_parser.py           动作白名单 · RISK20 仓位
   ├── atr_scenario.py             硬止损价 · 场景决议 · TP 档数
   ├── atr_1h.py                   币安原生 1h ATR(14)
   ├── breath_profiles.py          ETH / XAU 呼吸参数
   ├── breath_stop.py              两阶段呼吸止损
   ├── market_engine.py            90m 仅对比/ADX 日志（非止损权威）
   ├── binance_client.py           REST + markPrice WS + 用户流
   └── dingtalk.py                 钉钉播报
```

| 环节 | 行为 |
|------|------|
| 缓存 | 同 symbol 首包后 **1.0s** settle |
| 15s 铁律 | OPEN 先到丢弃窗内 CLOSE；CLOSE 先到先平后开 |
| 去重 | 60s 同 `action+symbol+price` |
| 哨兵 | WS tick 优先；REST ≥1s 兜底 |
| 状态 | `binance_vps_state_{SYMBOL}.json` 按品种隔离 |
| 查询失败 | fail-closed，禁止当空仓/盲补 |

---

## 三、Webhook

**有效 action**：`LONG` · `SHORT` · `CLOSE_QUICK_EXIT` · `CLOSE_RSI_EXIT` · `PING`  
鉴权：`secret`（兼容 `token`）。

### 开仓示例（qty 非必须）

```json
{
  "action": "LONG",
  "symbol": "ETHUSDT",
  "price": 1930.49,
  "atr": 14.5,
  "stop_loss": 1916.75,
  "tp1": 1953.51,
  "tp2": 1971.50,
  "tp3": 1988.71,
  "secret": "****"
}
```

| 字段 | 用途 |
|------|------|
| `price` | 开仓参考 / 去重键 |
| `stop_loss` | 永久硬止损公式输入（`|price−stop_loss|×buffer`）；亦可参与 sizing 收紧 |
| `atr` | 场景一日志；场景二雷达 ATR；缺则拒开 |
| `tp1`/`tp2`/`tp3` | 限价止盈价；数量固定 **10% / 20% / 70%**（三级常挂） |
| `qty` | 可选 soft-cap；天文值忽略 |

---

## 四、开仓流程（生产路径）

1. 查实盘；非空 → 市价全平 + 撤全部挂单 → **无菌确认**  
2. `qty = (本金×20%×5)/price`（可选 sl/TV.qty 收紧）→ 杠杆 5x → 市价开仓  
3. **共同第一步**：永久硬止损 + TP1/TP2/TP3（10/20/70）  
4. **同步拉原生 1h ATR** → 场景一或场景二 → **独立挂雷达止损**  
5. 开仓后核对：盘口至少硬止损在；雷达按场景挂出；钉钉播报  

**已废除**：临时硬止损被 ATR「替换」；硬+雷达单槽合并；必须带 TV.qty。

---

## 五、仓位公式

```
风险资金 = 本金 × 20%
名义上限 = 风险资金 × 5 = 本金 × 1
qty = 名义上限 / entryPrice
# 可选：stop_loss 收紧；TV.qty soft-cap（天文忽略）
# 下单前：availableBalance × 20% × 5 × 0.92 再裁（防 -2019）
```

双币同时持仓合计名义 ≈ **2×本金**（已知设计）。

---

## 六、被动雷达 + 智能再入场（规格 §5.1 · 独立于硬止损）

- ETH / XAU 参数只读 `breath_profile` + `reentry_profiles` / `config/reentry_tiers.json`
- **设计哲学**：入场靠 TV 评分；利润兑现靠 TP1/2/3；雷达只防止趋势被过早打断，不主动判断方向
- **ADX 三档**：0 弱（<20）/ 1 中（20–30）/ 2 强（>30）；开仓锁定档位 → **仅**雷达步进/呼吸分档；硬止损 buffer **恒为 1.15**
- **启动（绝对价锚定，共用 webhook TP1/TP2）**：
  - 首次开仓：**`(TP1 + TP2) / 2`**（TP1–TP2 区间中点）
  - 重入开仓：**`TP2`**（必须真正走到 TP2 才接管；过中点未到 TP2 **不得**激活）
  - 达线前仅硬止损守护
- **激活臂**：止损上移至 **entry ± 0.5×ATR**，随后按档位 `step_trigger` / `step_advance` 被动跟进
- **分区呼吸**：TP1–TP2 宽松 → TP2–TP3 收紧 → TP3+ 动态追踪（`min_mult`~`max_mult`）
- **重入**：仅雷达扫出且微赚区间；最多 **1** 次；窗口 ETH **2** 根 90m · XAU **3** 根 45m；价须优于上次开仓；成功后雷达放宽一档 + 激活门改为 TP2；**TP1 已成交 / 非强趋势禁重入**
- **双保险再入价**：多 `min(5m低+tick, TV×0.997)`；空 `max(5m高−tick, TV×1.003)`
- **禁止重入**：硬止损 / 亏损 / TV 平仓或反向 / 窗口过期 / 已重入过 / 价格不优 / TP1 已成交 / 弱·中趋势

### 两次 TV 之间：只有三条路

1. **开仓 → TP1/2/3 兑现** → 周期结束  
2. **开仓 → 雷达微赚扫出 → 更优价再入一次 → 再冲击 TP**  
3. **开仓 → 硬止损触发 → 坚决离场，禁止再入**

| 档位 | ADX | buffer | ETH step/adv · breath12/23 · trail | XAU step/adv · breath12/23 · trail |
|------|-----|--------|-------------------------------------|-------------------------------------|
| 0 弱 | <20 | **1.15** | 0.40/0.25 · 0.8/1.0 · 1.2~1.5 | 0.35/0.20 · 0.7/0.9 · 1.0~1.3 |
| 1 中 | 20–30 | **1.15** | 0.50/0.35 · 1.2/1.6 · 2.0~2.5 | 0.40/0.30 · 1.0/1.4 · 1.8~2.2 |
| 2 强 | >30 | **1.15** | 0.60/0.40 · 1.5/2.0 · 2.5~3.5 | 0.50/0.35 · 1.3/1.8 · 2.2~3.0 |

再入微赚区：ETH ±0.5×ATR · XAU ±0.3×ATR。配置源：`config/reentry_tiers.json`。  
实现：`radar_reentry_mixin.py` + `smart_reentry_engine.py` + `reentry_profiles.py`。

### 模块地图（后期优化入口）

| 文件 | 职责 | 改这里时注意 |
|------|------|--------------|
| `app.py` | Flask webhook → `handle_signal` | 鉴权/路由；不写交易逻辑 |
| `webhook_parser.py` | TV payload 解析、VALID_ACTIONS、15s 序 | schema 变更必同步 TV |
| `position_supervisor_binance.py` | 唯一大脑：开平/硬止损/TP/哨兵 | 每 symbol 一实例；无菌开仓 |
| `radar_reentry_mixin.py` | 被动雷达休眠 + 再入闭环 + 订单标签 | **标签未清禁挂**；无菌后再入 |
| `smart_reentry_engine.py` | 再入决策纯函数 | 无 IO，易单测 |
| `reentry_profiles.py` | ETH/XAU ADX 三档、窗口、双保险 | 改档位只动配置表 |
| `breath_stop.py` / `breath_profiles.py` | 雷达呼吸价 / 品种呼吸表 | 与硬止损独立 |
| `atr_scenario.py` | 硬止损唯一公式 + 场景一/二 | 滑点按成交价外侧 |
| `binance_client.py` | REST/WS；限价/止损 fail-closed + 去重 | 查单失败禁止挂；REST≥100ms |
| `dingtalk.py` | 实盘核实通知 | 雷达激活须注明首次/重入 |
| `check_vps_logic.py` | 静态逻辑审计（部署门禁） | 新铁律加断言 |

**一句话**：硬止损是底线（呼吸垫统一 1.15）。雷达是骑士：首次走到 TP1–TP2 **中点**才接管，重入要走到 **TP2**。弱趋势收紧、强趋势放宽（仅跟踪参数）。重入最多一次，有窗口，价必须更优。

---

## 七、TP 与平仓

| 事件 | 行为 |
|------|------|
| TP1/TP2 成交 | 止损数量同步收缩；硬止损价不变 |
| TP 超时 | 仅价已触及才 handoff；价未到不撤 |
| 反转 CLOSE | 市价全平 + 撤全部挂单 + 重置 |
| 任一层止损触发 | 平仓 + 撤销其余挂单 |
| 仓位归零 | 立即撤该 symbol 全部挂单（唯一允许撤硬止损的时机） |

---

## 八、15 秒开平时序铁律

- 同 symbol **15s** 内 OPEN+CLOSE：一律保证最终有仓（先平后开语义）  
- **OPEN 先到、CLOSE 在 15s 内到**：丢弃该 CLOSE，新仓不受影响  
- **CLOSE 先到、OPEN 在 15s 内到**：先平后开  
- **超过 15s 的 CLOSE**：独立平仓  
- 已移除基于复杂时间戳比较的旧逻辑

---

## 九、重启 / 安全闸 / fail-closed

- 多轮 REST 探仓；旧 schema 缺关键字段 → 暂停，禁止自动瞎转  
- `FORCE_ALIGN`：方向与可信 TV 不一致 → 全平重置  
- 持仓/挂单查询失败 → 保留账本，禁止盲补  
- 无菌开仓：qty=0 **且** 限价+止损=0  
- **CAP_ALIGN / 加仓 / 单槽 merge 已删除**

---

## 十、部署与三端同步

```bash
# 本地
git status   # 工作区应干净（不含密钥）
git log -1 --oneline
python3 check_vps_logic.py
python3 test_orders_dup_guard.py
python3 test_risk_iron_v1591.py
python3 test_defense_v1590.py

# 推送
git push origin main

# VPS
cd /home/trading/binance-engine
git fetch origin && git reset --hard origin/main
grep BINANCE_VPS_VERSION position_supervisor_binance.py
# 期望: v16.3.0-slice-reentry
chown -R trading:trading /home/trading/binance-engine
systemctl restart binance-engine.service
curl -s http://127.0.0.1:5003/health | python3 -m json.tool

# 20U 生产级全链路（ETH/XAU × 多空；结束后应双品种 FLAT）
sudo -u trading ./venv/bin/python3 live_test_20u_matrix.py
# 或仅 ETH: LIVE20U_ONLY=ETH sudo -u trading ./venv/bin/python3 live_test_20u_matrix.py
```

**验收**：本地 HEAD = `origin/main` = VPS `git rev-parse HEAD`；health.version=`v16.3.0-slice-reentry`；`trading_paused=false`；ETH/XAU 空仓待命；矩阵 PASS 且全程无同价重复、挂单总数≤5。

### 20U 矩阵观察点

| 环节 | 期望 |
|------|------|
| PREFLAT | qty=0 且 orders=0 |
| OPEN webhook | HTTP 200；市价成交 |
| 防线就绪 | stops∈[1,2]，limits=**3**，total≤**5**，dups=[] |
| HARD_SL | `frozen_hard_sl_px` ≈ \|TV−SL\|×**1.15** 外侧 |
| HOLD | 二次扫描无叠单漂移 |
| CLOSE | 无菌 flat |
| 限流 | 周期间冷却，日志无持续 -1003 |

### 回归单测

```bash
export BINANCE_SKIP_BOOTSTRAP=1
python3 test_tv_seq_collapse.py
python3 test_two_scenario_atr.py
python3 test_huge_tv_qty_sizing.py
python3 test_position_query_fail_safe.py
python3 test_orders_dup_guard.py
python3 test_attribution_honest.py
python3 test_breath_radar_upgrade.py
```

---

## 十一、钉钉要点

开仓 / 先平后开 / 场景二降级与恢复 / TP 成交 / 止损触发（须贴线） / 反转平仓 / 重启恢复 / HARD_SL_FAIL_ABORT / 查询失败。

**禁止旧文案**：雷达激活·妈妈版、硬止损被雷达「接管/替换」、加仓、CAP_ALIGN、武断「人工开仓」。

---

## 十二、已废除旧逻辑（摘要）

| 旧逻辑 | 状态 |
|--------|------|
| 临时硬止损被场景一 ATR **替换** | 废除 |
| 硬止损+雷达 **单槽合并** | 废除（v15.7.3 对账不再「合并为单槽」） |
| TP 后 `preserve_hard=False` 清双止损再挂 | 已修（v15.7.1） |
| 查单失败「允许首挂」限价/止损 | **废除（v15.7.4）** → fail-closed |
| 空仓不扫残留挂单 | **已修（v15.7.4）** 空闲巡检强制净场 |
| 查仓失败当残留仓强平 / `float(None)` | **已修（v15.7.5）** QUERY_FAILED fail-closed 拒开 |
| 挂单不可读谎称已有硬止损 / 撤TP误 `cancel_all` | **已修（v15.7.6）** 禁谎称 + 禁盲撤 + 同价去重 |
| 硬止损仅 TV×1.2 系统性紧于雷达 | **已修（v15.7.8）** 唯一公式 max(TV×1.2,1.5×ATR×1.05)+滑点×2 |
| 硬止损新旧双路径并存 | **已清（v15.7.9）** 单一 `hard_stop_price`；README/注释对齐 |
| sizing 预览未绑 atr 误发「缺TV atr」钉钉 | **已修（v15.7.10）** 预览先绑 atr；拒开钉钉仅主路径 |
| 双持仓时后开品种按 available×20%×5 裁仓 | **已修（v15.7.11）** 仅保证金不足才裁；雷达查重排除硬腿 |
| XAU early_be 噪声易扫保本 | **v15.8.2** 递进雷达 + 幂等再入闭环；查不到单绝不狂挂 |
| 同窗仅 1s / 5s 迟到 CLOSE | 改为 **15s** |
| webhook 必须 qty | 废除 |
| CAP_ALIGN / 加仓 / 旧雷达 activated | 废除 |

详见 [`docs/DELETED_LEGACY_LOGIC_v15.7.0.md`](docs/DELETED_LEGACY_LOGIC_v15.7.0.md)。

---

## 十二-B、事故与防护：空仓幽灵限价 / 同价 TP 叠单击穿（2026-07-23）

### 现象（内测截图）
1. **仓位=0，当前委托仍有 reduceOnly 限价**（ETH 卖出 TP 残留）→ 幽灵单，可能被扫成交成反向蚂蚁仓。  
2. **一笔 ETH 多 + 一笔 XAU 多，却出现多方向多笔限价**（含多单卖出 TP + 空单买入 TP 并存）→ 反手未净场干净。  
3. 历史更严重：查单失败时哨兵以为「TP 缺失」→ **同价限价叠到 50+ 笔**，有击穿实盘风险。

### 根因
- 平仓/反手后撤单未完全确认，或空闲巡检在「账本已空」时**直接 return，不扫残留挂单**。  
- `place_limit` / `place_stop` 在挂单 REST 失败时曾 **「允许首挂」**；上层 `_has_tp_limit_at_price` 失败时返回 False，形成「查不到→再挂」循环。

### 现行防护（必须保持）
| 层 | 行为 |
|----|------|
| `place_limit` / `place_stop` | 查单失败 → **return None**（仅 120s 本地缓存可复用，不新挂） |
| LIMIT 熔断 | 同 symbol 未成交挂单总数≥5 或 LIMIT≥5 → 拒挂 |
| `_has_tp_limit_at_price` / `_has_stop_sl_near` | 查失败 → **保守 True**（禁止补挂） |
| `_place_tp_levels_only` / `_patch_missing_tp` / nuclear | `orders_unreadable` → 中止，禁止盲补 |
| 空闲巡检 | 仓=0 且挂单>0 → `_purge_all_defense_orders_on_flat` |
| 开仓前 | `_verify_sterile_flat`：qty=0 **且** LIMIT+STOP=0，否则拒开 |

### 头寸公式（ETH/XAU 同一规则，防「精度/算错导致没开单」）
```
qty = (合约本金余额 × 20% × 5) / 开仓价
```
- 使用交易所 `format_quantity` / `format_price` 精度；TV.qty 可选 soft-cap，天文值忽略。  
- 缺 `atr` 拒开；有 `stop_loss` 可再按风险距离收紧，但**不得**因收紧为 0 而静默跳过——校验失败钉钉告警。

---

## 十三、Cursor 易错三点（白皮书原文精神）

1. **禁止**「先撤硬止损，再挂雷达」——雷达是额外防线，不是升级版  
2. **禁止**改硬止损价去对齐雷达——硬止损只读  
3. **禁止**因雷达更优而撤硬止损——两笔共存直到平仓  

**一句话**：硬止损永不撤销永不修改永不替换；雷达独立挂出独立运行独立触发；两笔同时存在，谁先触发谁执行；部分平仓数量同步收缩；仓位归零两笔同撤；任何时候至少一笔止损在保护，不存在裸奔窗口。

---

## 十四、生产监管状态

系统进入 **等待真实 TV 信号** 状态后：按本 README / 白皮书自动执行，无需人工干预或额外测试脚本。

| 文件 | 说明 |
|------|------|
| 桌面《Gemini终极生产级全功能白皮书》 | 最终权威 |
| [`docs/DELETED_LEGACY_LOGIC_v15.7.0.md`](docs/DELETED_LEGACY_LOGIC_v15.7.0.md) | 旧逻辑清除表 |
| [`docs/INCIDENT_20260722_HUGE_TV_QTY.md`](docs/INCIDENT_20260722_HUGE_TV_QTY.md) | 天文 qty 事故 |
| `check_vps_logic.py` / `check_deploy_events.py` | 静态与部署审计 |

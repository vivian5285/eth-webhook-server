# 币安单一账户系统（binance-engine）· 终极生产级

**当前版本：`v16.25-v2-TP-simplify`**（TP架构简化：废除循环对账，回归简洁三流程。开仓→TP1/TP2/硬止损挂出后不动；平仓→一键平仓+撤干净；恢复→检查TP是否存在，缺失才补，不对账）
**TV 策略 schema：`v6.5.6`**
**Webhook地址：`http://187.77.130.144/binance/webhook`**
**仓位模式：`RISK20_NOTIONAL5`**（ETH/XAU/ZEC/BNB 同一公式：`qty = 本金×20%×5 / 开仓价`；TV.qty 可选 soft-cap；20U 演练可传小 qty）
**保护引擎：三层防线**（永久硬止损 + 独立雷达止损 + TP1/TP2 限价；**TP3 永不挂限价**，70% 交雷达）  
**TP 分腿：10% / 20% / 70%**（盘口限价 **恰好 2** 笔 LIMIT=TP1+TP2；余仓无上限）  
**硬止损：`｜TV.price−TV.stop_loss｜×1.15` 锚定成交价**（**统一呼吸垫，不分档**；禁止 1.5×ATR 地板；开仓以市价回执为准，禁因 REST 滞后跳过硬止损）  
**雷达（v16.24 v2.1）**：激活用 **(TP1+TP2)/2 中点激活**（首次），重入用TP2激活。TP1是否成交仅记日志不阻塞。激活瞬间**保本起步**（entry±tick±fee）；呼吸空间与步进参数大幅放宽约40-60%（市价在前面跑，雷达保持安全距离跟在后面）  
**ATR：只信 TV webhook `atr`**（已删除 VPS 独立拉 1h/合成 ATR、场景一二与降级切换）  
**重入：最多 1 次**；窗口 ETH 2×90m≈3h · XAU 3×45m≈2.25h · ZEC/BCH 3×45m≈2.25h；双保险再入价；成功后雷达放宽一档  
**提前保本检查点（v16.21）**：**已废除（v16.22+v16.24 v2.1）**——XAU/ZEC波动大，雷达太早启动易出局  
**幂等铁律（v15.9.1+）**：本地订单标签未释放 → 绝对拒挂；查单失败 fail-closed；未成交挂单硬上限 **5**  
**生产闸门（v15.9.3）**：竞态/部分成交失败/限流/`本地标签vs空盘` → **`trading_paused`**；REST 单品种 ≥100ms；档位 `config/reentry_tiers.json`；30s 状态快照；日志 `[OPS|STATE|ALERT|AUDIT]`  
**日熔断开仓闸门（v15.9.2）**：**暂时关闭**（`CIRCUIT_BREAKER_OPEN_GATE_ENABLED=False`）；`risk_manager` 仅记账，不挡真实 TV  
**TV 图表周期：ETH 90m · XAU/ZEC/BCH 45m**（VPS **不再**另拉 ATR）  
**生产唯一大脑：`position_supervisor_binance.py`**（每 symbol 一实例）  
**通知：仅 Telegram 全量事件（2026-07-31 取消钉钉，`DINGTALK_DISABLE=True`）**
**ZEC/BCH支持**：ZECUSDT（2026-08-04）和BCHUSDT（2026-08-05）均使用ETH相同的呼吸参数和雷达配置，待独立回测校准

> **绝对红线（曾实盘击穿）**：查不到挂单 → **禁止**「再挂一张」。历史事故：同价 LIMIT 叠到 **50+ 笔**。现行多层铁律见下文「防叠单专章」。  
> **双 STOP 说明**：雷达未激活时盘口**只应有硬止损**；激活后才硬+雷达双挂。TV 原 `stop_loss` **不挂盘**（只作硬止损距离输入）。  
> **硬止损（唯一公式）**：`|TV价 − TV.stop_loss| × 1.15`，挂在**成交价**外侧。缺/异常 `stop_loss` → **拒开**。巡检/接管禁止用 0.5×ATR 顶替。  
> **TP（v16.4.0）**：只挂 TP1+TP2（10%/20%）；**TP3 永远不挂限价**，70% 完全交雷达（无价格天花板）。  
> **ATR（v16.4.0）**：全程只用 webhook `atr`；删除场景一/二与 `atr_1h` 拉取。  
> **雷达（v16.24 v2.1）**：激活用 **(TP1+TP2)/2 中点激活**（首次），重入用TP2。TP1是否成交仅记日志不阻塞。激活第一步永远是保本（entry±tick±fee），再从保本位阶梯跟随市价。呼吸空间与步进参数大幅放宽约40-60%（市价在前面跑，雷达保持安全距离跟在后面）。取消提前保本检查点。  
> **v16.8.1**：雷达激活改为 TP 绝对价格锚定：首次开仓=(TP1+TP2)/2，重入开仓=TP2；移除旧的 ADX/TP1 距离百分比激活逻辑。  
> **v16.8.0**：规格 v1.0雷达——保本起步 + 取消强制底线（激活比例已被 v16.8.1 纠正）。  
> **叠单铁律**：挂单查询失败 → **fail-closed 禁止挂**；本地标签未清拒挂；未成交挂单总数 **≥5 熔断**。  
> **API 限流（v16.6.2 绝对封死）**：账号预算默认 **24/min** + 最小间隔 **1.8s**；单品种 REST≥**2.0s**、全账户≥**1.5s**；挂单缓存 **45s**；哨兵 **45/30/25s**、空闲巡检 **300s**、持仓对账 **300s**；IP 冷却 **900s** 内 **零 REST**（仅缓存/WS）；冷却期内 `/admin/resume` 默认 **429**；Deepcoin 哨兵 **0.5s→25s**；公网 K 线计入币安节流阀。**禁止运维脚本狂轮询**。  
> **v16.4.0**：删 TP3 限价/互斥；ATR 只信 TV；清理历史 TP3。  
> **v16.4.1**：`trading_paused`/限流时哨兵休眠禁 REST 雪崩；限流告警去重；TP1 成交后 TP2 按开仓 20% 绝对量挂，禁止全仓堆 TP2。  
> **v16.4.2**：禁止压扁开仓基线；IP 全局 REST 冷却 + `_GLOBAL` 双品种暂停；限价档对账不再误报 TP3 drift。事故全文见 [`docs/SYSTEM_ISSUE_FIX_LOG.md`](docs/SYSTEM_ISSUE_FIX_LOG.md)。  
> **v16.4.3**：REST 全面降速——哨兵 8s/雷达 4s、空闲巡检 90s、持仓对账 90s；单品种 REST≥350ms、全账户≥250ms；限流冷却 300s。  
> **v16.4.4**：休眠雷达假死修复——价过激活线/TP2 成交后必须武装雷达止损（不再只打日志）。  
> **v16.4.5**：本地防御标签 GC；核武撤 TP 清标签；禁止假记 TP3 consumed。  
> **v16.4.6**：IP 冷却期内硬禁 REST；挂单短缓存；限流告警去重；冷却结束自动解暂停。  
> **v16.4.7**：无 TP3 限价收尾增强——（已由 v16.8.0 废除强制利润地板）雷达 qty 贴合现仓、TP2→TP3 区加速追随。事故总览见 [`docs/SYSTEM_ISSUE_FIX_LOG.md`](docs/SYSTEM_ISSUE_FIX_LOG.md)。  
> **v16.4.8**：GEMINI 对照——TP 限价预算硬帽（禁 TP1+TP2=整仓）、挂单帽暂停去重、空仓自清可恢复 pause；深币同步绝对分片。  
> **v16.5.0**：苹果风 Console（`/console`）——多套 API 档案热切换、每档案风险%/杠杆可改即生效、Webhook secret、日志与 30 日盈亏胜率；口令 `CONSOLE_PASSWORD`。  
> **v16.6.0**：生产流水线编制——总账本+状态机+督察官+账号级 REST 节流阀；现有开平仓/雷达挂岗位边界（软闸默认开，不打断实盘）。`/health` 含 `pipeline` 阶段。Deepcoin 同步同套编制。  
> **v16.6.1**：补强——TP **开仓+补挂**双预算闸；`chief_auditor`/`tp_slice` 空仓自清；成交历史走节流阀；督察官硬止损以盘口核实为准；Deepcoin PLACE=2 硬帽+自检。  
> **v16.6.2**：API 限流绝对封死——预算/间隔/哨兵全面收紧；账户/K线/名义敞口全部走节流；冷却期零 REST + resume 门禁；Deepcoin `v13.90.2-rate-iron`。
> **v16.24 v2.1**：废除提前保本检查点；雷达改为中点激活（首次=(TP1+TP2)/2）；TP1是否成交仅记日志不阻塞；呼吸空间与步进参数大幅放宽约40-60%（市价在前面跑，雷达保持安全距离跟在后面）。
> **v16.22 v2.0**（已废弃）：首次开仓也必须TP2成交才激活，导致TP1到TP2这段完全裸奔。
> **v16.22.1**：修复TP1漏挂无法补挂问题——移除补挂逻辑中'价到就跳过'铁律。
> **v16.22.2**：修复_expected_tp_levels中同样阻止补挂的逻辑。

### Console 管理页
- 地址：`http://VPS_IP:5003/console`（无需域名）
- 默认口令：环境变量 `CONSOLE_PASSWORD`（务必修改）
- 支持 ETHUSDT、XAUUSDT、BNBUSDT、ZECUSDT 四合约独立仓位设置（苹果毛玻璃风格）
- 档案存 `data/account_profiles.json`；切换 API 默认要求无持仓
- 前端保存后下一笔 TV 信号直接按新设置下单，无需重启服务

> **权威依据**：[《VPS完整系统规格_币安单账户版》](docs/VPS完整系统规格_币安单账户版.md)（第三轮修正：TP3 不挂限价 + ATR 只用 TV）+ 本文。  
> 旧逻辑清除对照：[`docs/DELETED_LEGACY_LOGIC_v15.7.0.md`](docs/DELETED_LEGACY_LOGIC_v15.7.0.md)  
> 事故与复查：[`docs/SYSTEM_ISSUE_FIX_LOG.md`](docs/SYSTEM_ISSUE_FIX_LOG.md)


```bash
# 币安主账户
curl -s http://127.0.0.1:5003/health | python3 -m json.tool
# version: v16.24.2-v2.1-cleanup · pipeline: {ETHUSDT, XAUUSDT, BNBUSDT, ZECUSDT} · trading_paused: false
# Console: http://VPS_IP:5003/console
# TV Webhook: http://187.77.130.144/binance/webhook

# 币安B账户
curl -s http://127.0.0.1:5007/health | python3 -m json.tool
# Console: http://VPS_IP:5007/console
# TV Webhook: http://187.77.130.144/binance-b/webhook

python3 -m unittest test_pipeline_workflow.py test_ip_rate_hard_block.py
python3 test_defense_v1590.py
python3 test_risk_iron_v1591.py
python3 test_radar_reentry.py
python3 test_orders_dup_guard.py
python3 test_stop_idempotent_and_tp_levels.py

# 生产级 20U 实盘矩阵（ETH/XAU × LONG/SHORT；需在 VPS 且密钥可用）
# sudo -u trading ./venv/bin/python3 live_test_20u_matrix.py
```

|| 工厂 | VPS 目录 | 端口 | Webhook 路由 | 品种 |
|------|----------|------|--------------|------|
|| **币安**（本仓库） | `~/binance-engine` | **5003** | `/binance/webhook` | ETHUSDT + XAUUSDT + BNBUSDT + ZECUSDT |
|| **币安B**（对照） | `~/binance-engine` | **5007** | `/binance-b/webhook` | ETHUSDT + XAUUSDT + BNBUSDT |
|| **深币**（对照） | `~/deepcoin-hft-server` | **5004** | `/deepcoin/webhook` | ETH + XAU |

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
| 持仓核对 | REST | 持仓期约 **90s**；空闲巡检 **90s** |
| 挂单查询 | REST | 下单前必查；失败 → fail-closed |
| 5m K 线（再入） | REST | 再入时按需；失败降级 TV×系数 |
| WS 断线 | 指数退避重连 | 1s → 60s 封顶 |

**限流防护**
- 哨兵常态 **~8s**、雷达 **~4s**（原 1s 过密，2026-07-26 改）  
- 空闲巡检 `QUERY_FAILED` / `-1003` → 退避 **300s**  
- REST 间隔：单品种 ≥**350ms**，全账户合计 ≥**250ms**  
- **IP 全局冷却 300s**：任一品种 `-1003` → 广播 `_GLOBAL` 暂停 ETH+XAU  
- `trading_paused` 时哨兵休眠，**禁止**补挂/核武/对账雪崩  
- 核武对齐刹车：≥90s；雷达改单 ≥8s  
- **禁止**用 REST 轮询价格/成交替代 WebSocket  

触发 API ban 时：暂停该品种激进补挂路径，等待窗口结束；**禁止**在 ban 中循环 place。  
同 IP 报「XAU 限流」而 TV 是 ETH → 见 [`docs/SYSTEM_ISSUE_FIX_LOG.md`](docs/SYSTEM_ISSUE_FIX_LOG.md)（2026-07-26）。

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

2. **挂 TP1+TP2 限价止盈**（**TP3 永不挂限价**）
   价格 = TV `tp1`/`tp2`；数量 = VPS 自算总仓位的 **10% / 20%**。
   与硬止损同时挂出。每档带防御 `clientOrderId`。TP3（70%余仓）**永不挂限价**，完全交雷达管理，雷达是 TP3 余仓唯一退出路径。

3. **启动价格监控与雷达引擎**
   雷达休眠至激活价（首次 (TP1+TP2)/2 · 重入 TP2）。
   雷达全程只读 TV webhook `atr`（VPS 不拉独立 ATR）。

### 硬止损 vs 雷达止损

| | 永久硬止损 | 雷达止损 |
|--|-----------|---------|
| 挂出时机 | 开仓瞬间 | 硬止损+TP 挂好后，引擎独立计算再挂 |
| 价格来源 | **唯一公式** `|TV−SL|×1.15` 锚定成交价 | 呼引用擎（只读 TV webhook `atr`；VPS 不拉独立 ATR） |
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
雷达另挂独立 STOP。账户同时 **硬止损 + 雷达 + TP1+TP2**（TP3 永不挂限价；总数 ≤5）。

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
   app.py  /webhook  (+ /console 管理页)
        │
        ▼
position_supervisor_binance.py     ← 唯一生产大脑（岗位边界挂接）
   ├── pipeline_ledger.py          总账本 + 状态机（唯一阶段真相源）
   ├── pipeline_bridge.py          岗位交接桥（不大拆交易所调用）
   ├── chief_auditor.py            督察官 8 项复查
   ├── api_throttle.py             账号级 REST 节流阀（ETH/XAU 共用）
   ├── tv_seq.py                   1.0s 缓存折叠 + 15s OPEN/CLOSE 铁律
   ├── webhook_parser.py           动作白名单 · RISK20 · PLACE_TP_LEVELS=2
   ├── atr_scenario.py             硬止损价公式
   ├── breath_profiles.py          ETH / XAU 呼吸参数
   ├── breath_stop.py              两阶段雷达止损
   ├── market_engine.py            90m 仅对比/ADX 日志（非止损权威）
   ├── binance_client.py           REST(过节流阀) + markPrice WS + 用户流
   └── dingtalk.py                 通知（开仓播报在督察后）
```

### 流水线岗位与状态机（v16.6）

| 岗位 | 职责 | 红线 |
|------|------|------|
| 信号官 | 验 secret、解析字段、写账本 `SIGNAL_RECEIVED` | **不调交易所 API** |
| 仓位稽查员 | 先平后开 / 无菌净场 → `CLEARED` | 唯一决定是否清场 |
| 执行官 | 下单→确认成交→挂硬止损+TP1/TP2；TP 自检 30% | 不私自改仓位权重/切片 |
| 雷达值守员 | 读账本/实盘头寸跟踪；`_pipeline_radar_update` | 禁止影子仓 |
| 督察官 | 开仓首轮 8 项复查；硬失败可暂停 | 方向/切片/硬止损为硬项 |
| 通讯官 | 督察后发开仓通知 → `REPORTED`→`MONITORING` | 其他岗位勿直接刷屏通知 |

阶段：`SIGNAL_RECEIVED → PENDING_CLEAR → CLEARED → ENTRY_SUBMITTED → ENTRY_CONFIRMED → ORDERS_PLACED → VERIFIED → REPORTED → MONITORING`  
失败：`FAILED`（卡住可见；空仓后 `chief_auditor`/`tp_slice`/`api_rate_limit` 等可自动清暂停）。

| 环境变量 | 默认 | 含义 |
|----------|------|------|
| `PIPELINE_SOFT_GATES` | `1` | 非法阶段只记日志，不硬挡现有路径（保实盘） |
| `PIPELINE_AUDITOR_HARD_PAUSE` | `1` | 督察硬失败 → `trading_paused` |
| `API_BUDGET_PER_MIN` | `48` | 账号 REST 滑动窗口预算 |
| `API_SILENCE_SEC` | `600` | 撞限流后强制静默秒数 |

### 今日实盘问题 → 现行拦截（复查表）

| 今日问题 | 拦截层 | 状态 |
|----------|--------|------|
| TP1+TP2 吞整仓 | `_normalize_tp_qty_map` 硬帽 + `_assert_place_tp_budget`（开仓**与补挂**）+ 督察 `tp_slice` | **已拦** |
| 假 TP3 / drift | `PLACE_TP_LEVELS=2`；不记 TP3 consumed；对账不含 TP3 | **已拦** |
| `initial_qty` 被压扁 | 账本 `initial_qty` 只在 `ENTRY_CONFIRMED` 写一次；supervisor `_trusted_initial_qty` 只升不降 | **已拦（双层）** |
| 限流后巡检仍 REST | `AccountThrottle` 静默 + `_raise_if_ip_rate_limited` + 哨兵/空闲巡检休眠 + `_GLOBAL` | **已拦** |
| ETH 限流 XAU 不知 | 节流阀按 **账号** `binance` 共用；`-1003` 广播 `_GLOBAL` | **已拦** |
| 空仓仍暂停 | `_maybe_auto_clear_pause_when_flat`（含 `api_rate_limit`/`chief_auditor`/`tp_slice`） | **已拦** |
| 雷达余仓偏弱 | 雷达 qty 跟实盘；`_pipeline_radar_update`；TP1/TP2 利润地板 | **已加强** |

| 环节 | 行为 |
|------|------|
| 缓存 | 同 symbol 首包后 **1.0s** settle |
| 15s 铁律 | OPEN 先到丢弃窗内 CLOSE；CLOSE 先到先平后开 |
| 去重 | 60s 同 `action+symbol+price` |
| 哨兵 | WS tick 优先；REST 兜底且过节流阀 |
| 状态 | `binance_vps_state_{SYMBOL}.json`（含 `pipeline` 字段） |
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
| `atr` | TV webhook `atr`（雷达全程只用此值）；缺则拒开 |
| `tp1`/`tp2`/`tp3` | 止盈价；**只挂 TP1+TP2 限价**（10%/20%）；`tp3` 价可传入但不挂单，70% 交雷达 |
| `qty` | 可选 soft-cap；天文值忽略 |

---

## 四、开仓流程（生产路径）

1. **信号官**登记 `SIGNAL_RECEIVED`（不调交易所）  
2. **仓位稽查员**先平后开 / 无菌净场 → `CLEARED`  
3. **执行官** `qty=(本金×20%×5)/price` → 杠杆(档案) → 市价开仓 → `ENTRY_CONFIRMED`（锁定 `initial_qty`）  
4. **共同第一步**：永久硬止损 + **仅 TP1+TP2**（10%/20%，预算闸自检）→ `ORDERS_PLACED`  
5. 雷达休眠待命（激活线前盘口仅硬止损）  
6. **督察官** 8 项复查 → `VERIFIED`（硬失败可暂停）  
7. **通讯官** TG 开仓播报 → `REPORTED`→`MONITORING`  

**已废除**：TP3 限价；硬止损被 ATR「替换」；硬+雷达单槽合并；必须带 TV.qty；VPS 自拉 ATR 做止损权威；钉钉通知（已 2026-07-31 停用）。

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

## 六、被动雷达 + 智能再入场（规格 v1.0 · v16.9.1 · 独立于硬止损）

- ETH / XAU 参数只读 `breath_profile` + `reentry_profiles` / `config/reentry_tiers.json`
- **设计哲学**：激活瞬间第一步永远是**保本**，再像规格 v1.0一样跟在市价后面；弱趋势早激活防微利回吐，强趋势晚激活给深度回踩留呼吸
- **ADX 三档**：0 弱（<20）/ 1 中（20–30）/ 2 强（>30）；开仓锁定档位 → 启动比例 + 步进/呼吸；硬止损 buffer **恒为 1.15**
- **启动（规格 v1.0 · §5.1 绝对价格锚定）**：
  - 首次开仓：雷达激活价 = **(TP1 + TP2) / 2**
  - 重入开仓：雷达激活价 = **TP2**
  - 开仓冻结激活价；**独立于 TP1 是否已成交**；达线前仅硬止损守护
- **激活臂（保本起步）**：止损移到 **entry ± tick ± fee_cover**（约 0.08%），**禁止**跳到 entry±0.5ATR / TP1 底线
- **取消强制底线**：TP1/TP2 成交后**只收缩数量**（→70% / →40%），止损价格不跳变，继续阶梯推进
- **阶梯跟随**：按档位 `step_trigger` / `step_advance` 从保本位被动跟进；浮盈≥3×ATR 进入动态追踪阶段连续追踪
- **第二层 trail**：实时 ATR/initial_atr 连续插值（ETH/XAU 按档 min~max）
- **分区呼吸**：TP1–TP2 宽松 → TP2–TP3 收紧 → TP3+ 动态追踪（`min_mult`~`max_mult`）
- **重入**：仅雷达扫出且微赚区间；最多 **1** 次；窗口 ETH **2** 根 90m · XAU **3** 根 45m；价须优于上次开仓；成功后雷达放宽一档
- **双保险再入价**：多 `min(5m低+tick, TV×0.997)`；空 `max(5m高−tick, TV×1.003)`
- **禁止重入**：硬止损 / 亏损 / TV 平仓或反向 / 窗口过期 / 已重入过 / 价格不优 / TP1 已成交 / 弱·中趋势

### 两次 TV 之间：只有三条路

1. **开仓 → TP1/2/3 兑现** → 周期结束  
2. **开仓 → 雷达微赚扫出 → 更优价再入一次 → 再冲击 TP**  
3. **开仓 → 硬止损触发 → 坚决离场，禁止再入**

| 档位 | ADX | 激活价锚定 | buffer | ETH step/adv · breath12/23 · trail | XAU step/adv · breath12/23 · trail |
|------|-----|------------|--------|-------------------------------------|-------------------------------------|
| 0 弱 | <20 | **(TP1+TP2)/2** | **1.15** | 0.70/0.42 · 1.5/2.0 · 2.5~3.5 | 0.70/0.50 · 2.0/2.8 · 3.0~4.5 |
| 1 中 | 20–30 | **(TP1+TP2)/2** | **1.15** | 0.85/0.55 · 2.0/2.8 · 3.0~4.5 | 0.85/0.55 · 2.5/3.5 · 3.5~5.5 |
| 2 强 | >30 | **(TP1+TP2)/2** | **1.15** | 1.00/0.65 · 2.5/3.5 · 4.0~6.0 | 1.00/0.65 · 3.0/4.0 · 5.0~7.0 |

> **v16.24 v2.1**：步进和呼吸空间比旧版放宽约40-60%，让雷达跟踪距市价更远，给强趋势足够的呼吸空间。首次开仓中点激活，TP1是否成交仅记日志不阻塞。
> **v16.22 v2.0**（已废弃）：首次开仓也必须TP2成交才激活，导致TP1到TP2这段完全裸奔。

再入微赚区：ETH ±0.5×ATR · XAU ±0.3×ATR。配置源：`config/reentry_tiers.json`（schema `v2.1-midpoint-activation-highway`）。  
实现：`radar_reentry_mixin.py` + `smart_reentry_engine.py` + `reentry_profiles.py` + `breath_stop.py`。

**一句话**：首次开仓雷达中点激活（TP1+TP2)/2，重入激活TP2；TP1是否成交仅记日志不阻塞（市价在前面跑，雷达保持安全距离跟在后面）；激活瞬间永远保本起步，再从保本位阶梯跟随。步进和呼吸空间大幅放宽约40-60%。硬止损始终独立并存。

### 模块地图（后期优化入口）

| 文件 | 职责 | 改这里时注意 |
|------|------|--------------|
| `app.py` | Flask webhook → `handle_signal` | 鉴权/路由；不写交易逻辑 |
| `webhook_parser.py` | TV payload 解析、VALID_ACTIONS、15s 序 | schema 变更必同步 TV |
| `position_supervisor_binance.py` | 唯一大脑：开平/硬止损/TP/哨兵 | 每 symbol 一实例；无菌开仓 |
| `radar_reentry_mixin.py` | 被动雷达休眠 + 再入闭环 + 订单标签 | **标签未清禁挂**；无菌后再入 |
| `smart_reentry_engine.py` | 再入决策纯函数 | 无 IO，易单测 |
| `reentry_profiles.py` | ETH/XAU ADX 三档、窗口、双保险、**启动比例** | 改档位/启动只动配置表 |
| `breath_stop.py` / `breath_profiles.py` | 雷达呼吸价 / 品种呼吸表（第二层） | 与硬止损独立；本次不改 trail |
| `atr_scenario.py` | 硬止损唯一公式（呼吸垫 1.15） | 滑点按成交价外侧 |
| `binance_client.py` | REST/WS；限价/止损 fail-closed + 去重 | 查单失败禁止挂；REST≥100ms |
| `dingtalk.py` | 实盘核实通知 | 雷达激活须注明首次/重入 + ADX 比例 |
| `check_vps_logic.py` | 静态逻辑审计（部署门禁） | 新铁律加断言 |

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
# 期望: v16.24-tp-baseline-fix (v2.1 中点激活)
chown -R trading:trading /home/trading/binance-engine
systemctl restart binance-engine.service
# 重要：VPS 部署后等待真实 TV 信号，现有持仓由雷达守护，不干预
curl -s http://127.0.0.1:5003/health | python3 -m json.tool

# 20U 生产级全链路（ETH/XAU × 多空；结束后应双品种 FLAT）
sudo -u trading ./venv/bin/python3 live_test_20u_matrix.py
# 或仅 ETH: LIVE20U_ONLY=ETH sudo -u trading ./venv/bin/python3 live_test_20u_matrix.py
```

**验收**：本地 HEAD = `origin/main` = VPS `git rev-parse HEAD`；health.version=`v16.17-open-retry-iron`；`trading_paused=false`；ETH/XAU/BNB 空仓待命；矩阵 PASS 且全程无同价重复、挂单总数≤5。

### 20U 矩阵观察点

| 环节 | 期望 |
|------|------|
| PREFLAT | qty=0 且 orders=0 |
| OPEN webhook | HTTP 200；市价成交 |
| 防线就绪 | stops∈[1,2]，limits=**2**（仅TP1+TP2），total≤**5**，dups=[] |
| HARD_SL | `frozen_hard_sl_px` ≈ \|TV−SL\|×**1.15** 外侧 |
| HOLD | 二次扫描无叠单漂移 |
| CLOSE | 无菌 flat |
| 限流 | 周期间冷却，日志无持续 -1003 |

### 回归单测

```bash
export BINANCE_SKIP_BOOTSTRAP=1
python3 -m unittest test_pipeline_workflow.py
python3 test_tv_seq_collapse.py
python3 test_huge_tv_qty_sizing.py
python3 test_position_query_fail_safe.py
python3 test_orders_dup_guard.py
python3 test_attribution_honest.py
python3 test_breath_radar_upgrade.py
python3 test_stop_idempotent_and_tp_levels.py
```

### 流水线上线验收清单（v16.6.1）

- [x] `/health` → `version=v16.6.1-pipeline`，含 `pipeline` 字段  
- [x] Deepcoin `/health` → `v13.90.1-pipeline`  
- [x] `.env` 含 `PIPELINE_SOFT_GATES=1`、`PIPELINE_AUDITOR_HARD_PAUSE=1`、`API_BUDGET_PER_MIN=48`  
- [x] 持仓中重启：ETH LONG 保留，`pipeline=MONITORING`，硬止损账本在  
- [x] 单测：`test_pipeline_workflow` 9/9；`check_tp_slice_budget(1,0.5,0.5).ok is False`  
- [ ] **下一笔新开仓**日志确认：`📋 … → ORDERS_PLACED`、`督察官通过`、TP 预算闸未拒挂  
- [ ] 限流冷却期人工观察：哨兵不再打穿 REST（日志无连续 -1003）  
- [ ] 盘口 LIMIT 恒为 2（无 TP3）；TP1+TP2 qty ≈ initial×30%（持仓中核对）

---

## 十一、Telegram 通知（含 TV 趋势档位）

**通道**：仅 Telegram 全量事件（钉钉已于 2026-07-31 停用，`DINGTALK_DISABLE=True`）。品牌前缀【币安单系统】。

### TV 趋势档位（弱 / 中 / 强）
| 档位 | 含义 | ADX（缺 `tier` 时反推） | 影响 |
|------|------|------------------------|------|
| **T0 弱趋势** | 震荡/弱趋势 | &lt;20 | 雷达步进/呼吸**收紧** |
| **T1 中趋势** | 常态 | 20–30 | 默认档 |
| **T2 强趋势** | 强趋势 | &gt;30 | 雷达步进/呼吸**放宽**；**仅强档允许重入** |

- Webhook 可直接传 `tier`（0/1/2，或 `弱/中/强`）；有则优先用 TV，否则 ADX 反推。
- **硬止损呼吸垫恒 1.15，不分档**（档位不改变硬止损距离）。
- 开仓 / 雷达激活 推送字段：`📊 趋势档位`（例：`强趋势（T2） · ADX=32.1 · 仅影响雷达步进/呼吸 · 硬止损垫恒1.15 · 来源=TV.tier`）。

### 典型推送
| 事件 | 必含信息 |
|------|----------|
| 开仓 | 品种/方向/价量/硬止损/雷达待命价/**趋势档位**/TP1+TP2 |
| 雷达激活 | 首次或重入、**绝对价格锚定(首次=(TP1+TP2)/2·重入=TP2)**、激活价、初始雷达止损、**趋势档位** |
| 止损移动 | 新止损、浮盈、极值 |
| TP1/TP2 成交 | 成交价量、剩余仓位、当前止损 |
| 平仓 | 来源（硬止损/雷达/TV反转等）、盈亏 |
| 系统告警 | 限流静默、HARD_SL_FAIL、查仓失败、暂停 |

**禁止旧文案**：R1–R4「TV档位」、雷达激活·妈妈版、硬止损被雷达「接管/替换」、加仓、CAP_ALIGN、武断「人工开仓」。

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

系统进入 **等待真实 TV 信号** 状态后：按本 README / 白皮书 + **流水线编制（v16.6）** 自动执行。出问题先查 `/health.pipeline` 卡在哪个阶段、再查 `docs/SYSTEM_ISSUE_FIX_LOG.md`。

| 文件 | 说明 |
|------|------|
| 桌面《全域生产级工作流架构方案》 | 岗位/账本/督察编制权威 |
| [`docs/SYSTEM_ISSUE_FIX_LOG.md`](docs/SYSTEM_ISSUE_FIX_LOG.md) | **系统问题/修复日志（查历史事故优先）** |
| `pipeline_ledger.py` / `chief_auditor.py` / `api_throttle.py` | 总账本 · 督察官 · 节流阀 |
| [`docs/DELETED_LEGACY_LOGIC_v15.7.0.md`](docs/DELETED_LEGACY_LOGIC_v15.7.0.md) | 旧逻辑清除表 |
| [`docs/INCIDENT_20260722_HUGE_TV_QTY.md`](docs/INCIDENT_20260722_HUGE_TV_QTY.md) | 天文 qty 事故 |
| `test_pipeline_workflow.py` | 流水线单测门禁 |

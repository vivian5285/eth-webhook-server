# 币安单一账户系统（binance-engine）· 终极生产级

**当前版本：`v15.8.2-idempotent-loop`**  
**TV 策略 schema：`v6.5.6`**  
**仓位模式：`RISK20_NOTIONAL5`**（ETH/XAU 同一公式：`qty = 本金×20%×5 / 开仓价`；TV.qty 非必须）  
**保护引擎：三层防线永久共存**（永久硬止损 + 独立雷达止损 + TP1/TP2；场景二另挂 TP3）  
**波段滚动：五档 1.0~5.0；双保险再入价（5m极值 ∩ TV×0.997/1.003 取更优）**  
**递进雷达：休眠至 50/65/80/90/95%×TP1距；微赚归零可再入；硬止损/亏损不重入**  
**幂等铁律（v15.8.2）**：本地 `reentry_order_tag` 未释放 → 绝对拒挂第二笔；查单失败 fail-closed；无菌净场后才再入  
**TV 图表周期：ETH 90m · XAU 45m**（VPS 1h ATR 仅作呼吸系数采样，不是 XAU 图表周期）  
**生产唯一大脑：`position_supervisor_binance.py`**（每 symbol 一实例）  
**通知：钉钉（`dingtalk.py`）**

> **双 STOP 说明**：盘口两笔接近的止损 = **硬止损** + **雷达(1.5×ATR)**。TV 原 `stop_loss` **不挂盘**（只作硬止损距离输入）。  
> **硬止损（唯一公式 · v15.7.8+）**：`max(|TV价−TV.SL|×1.2, 1.5×initial_atr×1.05) + |成交−TV价|×2`，挂在**成交价**外侧。已删除单独的「|成交−TV.SL|×1.2」旧路径。  
> **叠单铁律（v15.7.4+）**：挂单查询失败 → **fail-closed 禁止挂** TP/止损；空仓必须挂单=0；LIMIT≥6 熔断拒挂。  
> **查仓铁律（v15.7.5）**：持仓 `QUERY_FAILED` → fail-closed 拒开；空闲巡检 45s + 失败退避 120s。  
> **防叠铁律（v15.7.6）**：挂单不可读 → 禁止谎称已有硬止损 / 禁止盲撤补。  
> **v15.8.1**：五档波段滚动 + 双保险再入价；仓位归零且保本/微赚触发；每档独立 trail 带宽。  
> **v15.8.2**：再入闭环 + 本地订单标签幂等；仓归零→无菌→挂限价→成交→硬@fill+TP12+雷达休眠；钉钉核实。

> **权威依据**：桌面《智能再入场与波段滚动完整方案（最终文字版）》+ 白皮书 + 本文。  
> 旧逻辑清除对照：[`docs/DELETED_LEGACY_LOGIC_v15.7.0.md`](docs/DELETED_LEGACY_LOGIC_v15.7.0.md)

```bash
curl -s http://127.0.0.1:5003/health | python3 -m json.tool
# version: v15.8.2-idempotent-loop · sizing: RISK20_NOTIONAL5 · trading_paused: false

python3 check_vps_logic.py
python3 test_radar_reentry.py
python3 test_breath_radar_upgrade.py
python3 test_two_scenario_atr.py
python3 test_orders_dup_guard.py
```

| 工厂 | VPS 目录 | 端口 | 品种 |
|------|----------|------|------|
| **币安**（本仓库） | `~/binance-engine` | **5003** | ETHUSDT + XAUUSDT |
| **深币**（对照） | `~/deepcoin-hft-server` | **5004** | ETH + XAU |

---

## 零、三层防线永久共存模型（核心·不可误解）

开仓成交瞬间**同步**做三件事（不分先后）：

1. **挂永久硬止损**  
   距离 = `max(|TV价 − TV.stop_loss| × 1.2, 1.5 × initial_atr × 1.05) + |成交价 − TV价| × 2`  
   → 挂在**交易所成交价**外侧（closePosition）。  
   身份：**永久防线**。仓位归零前：**不改价、不撤销**（仅公式升级允许一次性重挂）。  
   实现：`atr_scenario.hard_stop_price` → `frozen_hard_sl_px` + `_ensure_frozen_hard_sl`。

2. **挂 TP1+TP2 限价止盈**  
   价格 = TV `tp1`/`tp2`；数量 = VPS 自算总仓位的 **30% / 30%**。  
   与硬止损同时挂出。场景一**不挂 TP3**（余仓交雷达收网）；场景二挂 TP3 兜底。

3. **启动 VPS 原生 1h ATR 拉取**（呼吸系数 / 场景决议；≠ TV 图表周期）  
   - **场景一**（成功）：雷达用真实 ATR；**不挂 TP3**  
   - **场景二**（失败）：雷达用 TV `atr`；**挂 TP3**；可持续恢复场景一

### 硬止损 vs 雷达止损

| | 永久硬止损 | 雷达止损 |
|--|-----------|---------|
| 挂出时机 | 开仓瞬间 | 硬止损+TP 挂好后，引擎独立计算再挂 |
| 价格来源 | **唯一公式**（TV×1.2 与 1.5×ATR×1.05 取大 + 滑点×2） | 呼吸引擎（场景一/二 ATR） |
| 数量 | closePosition（始终覆盖剩余） | 明确 quantity=剩余仓位 |
| 改价 | **禁止**（公式升级重挂除外） | 可随呼吸上移（只收紧） |
| 撤销 | **仅仓位归零** | 仓位归零 / 被另一笔止损触发后撤销 |
| 关系 | 两笔**独立共存**，不是升级/替换/接管 |

**谁先被价格触及谁先平仓；任一归零 → 立即撤销另一笔及全部挂单。**  
**禁止**：先撤硬止损再挂雷达；禁止因雷达更优而撤硬止损；禁止改硬止损价去「同步」雷达。

### 部分平仓时数量同步

- TP1 成交 → 仓≈70%：硬止损（closePosition 自动）+ 雷达（独立改量）同步到剩余  
- TP2 成交 → 仓≈40%：再次同步  
- 收缩**只改数量、不改硬止损价格**；实现禁止 `preserve_hard=False` 清场（裸奔窗口）

### 示例（ETH SHORT）

TV 价 1897.03，TV.SL 1912.18，成交 1900.51，initial_atr 12.69：  
`base=max(18.18, 19.99)=19.99`，`slip=6.96`，`final≈26.95` → **硬止损≈1927.46**（盘口再加执行缓冲）。  
雷达 initialStop≈1919.54 → 账户同时挂 **硬止损(更宽) + 雷达** 两笔 STOP。

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
| `stop_loss` | 永久硬止损公式输入（与 `atr`/`price`/成交价一并计算）；亦可参与 sizing 收紧 |
| `atr` | 场景一日志；场景二雷达 ATR；缺则拒开 |
| `tp1`/`tp2` | 限价止盈价；数量固定 30%/30% |
| `tp3` | 仅场景二挂出（40%）；场景一不挂 |
| `qty` | 可选 soft-cap；天文值忽略 |

---

## 四、开仓流程（生产路径）

1. 查实盘；非空 → 市价全平 + 撤全部挂单 → **无菌确认**  
2. `qty = (本金×20%×5)/price`（可选 sl/TV.qty 收紧）→ 杠杆 5x → 市价开仓  
3. **共同第一步**：永久硬止损 + TP1/TP2（不挂 TP3）  
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

## 六、呼吸雷达 + 波段滚动再入场（独立于硬止损）

- ETH / XAU 参数只读 `breath_profile` + `reentry_profiles`（`enabled` 可关）  
- **生产现状（v15.8.2）**：开仓挂硬+TP；雷达休眠至档位激活线；仓位归零且保本/微赚 → **无菌净场** → 双保险限价再入 → 成交后硬止损按 **新成交价+滑点** 重挂 + TP12 + 雷达休眠候命  
- 启动阈值档位 **1.0~5.0**：50% / 65% / 80% / **90%** / 95% × TP1距，只增不减  
- 每档独立 `early_be` / `step_*` / **phase2 trail 带宽**（随档位放宽）  
- **双保险再入价**：多 `min(5m低+tick, TV×0.997)`；空 `max(5m高−tick, TV×1.003)`（无 K 线则 3m→仅 TV 折扣）  
- 必须优于 TV；TTL 5min；最多 4 次重入；未成交刷新≤5；硬止损/亏损不重入；新 TV 彻底清场  

### 两次 TV 之间：只有三条路（无第四种）

1. **开仓 → TP1/2/3 兑现** → 周期结束，等待下一 TV  
2. **开仓 → 雷达保本/微赚扫出 → 更优价再入 → 再冲击 TP**（可多次波段滚动）  
3. **开仓 → 硬止损触发 → 坚决离场，禁止再入**  

雷达扫出不是失败，是「等一下再上一次车」；唯一主动认输 = 硬止损。

### 闭环检查点（v15.8.2）

| 阶段 | 必须通过 |
|------|----------|
| 仓归零 | 撤该品种全部限价/止损；`_verify_sterile_flat`（qty=0 且 orders=0） |
| 再入判断 | 非硬止损、非亏损、未超 `max_reentries`、TV 方向仍有效 |
| 挂限价前 | 本地 `reentry_order_tag` **为空**；交易所查单可读；无同向同价 LIMIT |
| 挂限价 | 生成唯一 `newClientOrderId` 写入本地状态后再下单；TTL 5min |
| TTL 刷新 | **先撤旧单并释放旧标签** → 再生成新标签挂单（每周期仅一笔） |
| 成交后 | 释放标签；硬止损按 fill 重算（含 \|fill−TV\|×2）；TP 方向无效则按 fill 重算；雷达休眠 |
| 钉钉 | 挂限价 / 成交防线核实（hard hung + TP12 + 滑点） |

### 红色警告：查不到单绝不可狂挂

本地状态表存在该品种 `reentry_order_tag` 时，**即使交易所 openOrders 返回空，也绝对不允许再挂**。  
查单失败 → fail-closed 拒挂。标签仅在：成交确认 / 确认撤销 / TTL 刷新前释放。  
这是防止「查不到 TP/雷达/止损就循环补挂 50+ 单击穿实盘」的最后一道防线。

| 档位 | 激活% | ETH early/trig/adv · trail | XAU early/trig/adv · trail |
|------|-------|----------------------------|----------------------------|
| 1.0 | 50% | 0.50/0.75/0.40 · 1.2~2.5 | 0.65/0.70/0.45 · 1.2~2.5 |
| 2.0 | 65% | 0.65/0.90/0.46 · 1.4~2.8 | 0.85/0.85/0.52 · 1.4~2.8 |
| 3.0 | 80% | 0.85/1.10/0.52 · 1.6~3.0 | 1.10/1.00/0.58 · 1.6~3.0 |
| 4.0 | 90% | 1.05/1.25/0.58 · 1.8~3.2 | 1.30/1.15/0.64 · 1.8~3.2 |
| 5.0 | 95% | 1.30/1.40/0.64 · 2.0~3.5 | 1.55/1.30/0.70 · 2.0~3.5 |

再入微赚区：ETH ±0.5×ATR · XAU ±0.3×ATR。配置源：`reentry_profiles.py`。  
实现：`radar_reentry_mixin.py` + `smart_reentry_engine.py` + `place_limit_order(..., client_order_id=)`。

### 模块地图（后期优化入口）

| 文件 | 职责 | 改这里时注意 |
|------|------|--------------|
| `app.py` | Flask webhook → `handle_signal` | 鉴权/路由；不写交易逻辑 |
| `webhook_parser.py` | TV payload 解析、VALID_ACTIONS、15s 序 | schema 变更必同步 TV |
| `position_supervisor_binance.py` | 唯一大脑：开平/硬止损/TP/哨兵 | 每 symbol 一实例；无菌开仓 |
| `radar_reentry_mixin.py` | 递进雷达休眠 + 再入闭环 + 订单标签 | **标签未清禁挂**；无菌后再入 |
| `smart_reentry_engine.py` | 再入决策纯函数（可否再入/计划价） | 无 IO，易单测 |
| `reentry_profiles.py` | ETH/XAU 五档系数、TTL、双保险公式 | 改档位只动配置表 |
| `breath_stop.py` / `breath_profiles.py` | 雷达呼吸价 / 品种呼吸表 | 与硬止损独立 |
| `atr_scenario.py` | 硬止损唯一公式 + 场景一/二 | 滑点按成交价外侧 |
| `binance_client.py` | REST/WS；限价/止损 fail-closed + 去重 | 查单失败禁止挂 |
| `dingtalk.py` | 实盘核实通知 | 成交/防线 hung 必报 |
| `check_vps_logic.py` | 静态逻辑审计（部署门禁） | 新铁律加断言 |

TV 全链条（空仓待命 → 新信号）：  
`webhook → handle_signal → _ensure_flat_before_open → 市价开 → _arm_temp_stop_and_tp12 → 雷达休眠 → 哨兵`；  
平仓归零后若保本：`_maybe_start_smart_limit_reentry → 无菌 → 标签限价 → 成交 → 硬@fill+TP+休眠`。

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
python3 test_two_scenario_atr.py
python3 test_tv_seq_collapse.py

# 推送
git push origin main

# VPS
cd /home/trading/binance-engine
git fetch origin && git reset --hard origin/main
grep BINANCE_VPS_VERSION position_supervisor_binance.py
# 期望: v15.7.1-triple-defense
chown -R trading:trading /home/trading/binance-engine
systemctl restart binance-engine.service
curl -s http://127.0.0.1:5003/health | python3 -m json.tool
```

**验收**：本地 HEAD = `origin/main` = VPS `git rev-parse HEAD`；health.version 一致；`trading_paused=false`；ETH/XAU 空仓待命。

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
| LIMIT 熔断 | 同 symbol 可读 LIMIT≥6 → 拒挂 |
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

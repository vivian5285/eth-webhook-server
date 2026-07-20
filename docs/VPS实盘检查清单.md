# 🛡️ 万亿战神 VPS 实盘检查清单（Cursor 开发自查专用）

> **币安** `eth-webhook-server` · **深币** `deepcoin-hft-server-main` 共用逻辑；运行 `python check_vps_logic.py` 做静态对账。

## 📌 核心原则（必须刻进代码）

| # | 原则 | 代码落点 |
|---|------|----------|
| 1 | **TV 只发信号**；仓位 sizing 由 VPS；**硬止损严格按 TV `tv_sl` 挂单** | `app.py` 网关不入队决策；`position_supervisor_*.py` |
| 2 | TV `tv_sl` **即实盘硬止损挂单价**（多空）；禁止 VPS% 覆盖 | `_tv_hard_sl_target()` · `_sync_exchange_stop()` |
| 3 | **雷达适度追随**按档激活（R1=85%…R4=70%）或 TP1 成交后启动 | `_radar_ready_to_handoff()` · `_process_radar_trailing` |
| 4 | **ETH / XAU** 独立状态，互不串单 | `symbol_config.py` · `SUPERVISORS` 按品种 |
| 5 | 计算基于 **账户总权益**（marginBalance），非可用余额 | `get_total_equity()` · `_snapshot_sizing_principal()` |

---

## 模块一：Webhook 解析与币种路由

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 1.1 | 正确解析 JSON `symbol` / `ticker` | ✅ | `extract_symbol_from_payload()` |
| 1.2 | ETH 信号 → 只操作 ETHUSDT | ✅ | `get_supervisor_for_payload()` |
| 1.3 | XAU 信号 → 只操作 XAUUSDT | ✅ | 同上 |
| 1.4 | 未知 symbol → 拒绝并记录 | ✅ | `app.py` 返回 400 + `allowed` 列表 |
| 1.5 | 同 action+price 去重（45s） | ✅ | 无时序旧信号：`SIGNAL_DEDUP_SEC`；有 `bar_index`+`seq`：幂等键 |
| 1.5b | TV 时序有序消费 | ✅ | `tv_seq.py`：先 `bar_index` 再 `seq`；乱序暂存 `TV_SEQ_PENDING_WAIT` |
| 1.5d | 先平后开 | ✅ | 同 bar settle + 动作优先 CLOSE→OPEN；同秒开平终态有仓 |
| 1.5g | 无菌空仓再开 | ✅ | `_sterile_flat_gate`：凡 OPEN 一律先平后开净场再开 |
| 1.5h | 禁开完秒平 | ✅ | 穿价 TP 推离；开仓宽限禁 regime_cap；钉钉去重 |
| 1.5i | TP成交必须价到 | ✅ | 每档验 mark/best；拒认仅凭减仓；假TP记账可清除 |
| 1.5c | 钉钉攒批防限流 | ✅ | `DINGTALK_BATCH_*` + 1/2/4s 重试 + `WECHAT_WEBHOOK` 备用 |

### 实盘场景

| 场景 | VPS 预期 |
|------|----------|
| `"symbol":"ETHUSDT.P"` | ETH 档位/仓位/止损 |
| `"symbol":"XAUUSDT.P"` | XAU 档位/仓位/止损 |
| 无 symbol（且 URL 无路径） | 默认 ETHUSDT（建议 TV 始终带 symbol） |
| 恶意未知品种 | 拒绝 `unknown_symbol` |
| 同 K 线重复 Webhook | 第二条去重忽略 |
| 同 K 线先平后开（CLOSE→OPEN） | seq 升序执行；CLOSE 释放开仓幂等；开仓前无菌净场 |

---

## 模块二：开单计算（TV 唯一公式）

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 2.1 | 总权益实时获取 | ✅ | `get_total_equity()` |
| 2.2 | TV `risk_pct` / `qty_ratio` / `leverage` | ✅ | 直接用，不重算 |
| 2.3 | 止损距离 = \|price − tv_sl\| | ✅ | `_normalize_stop_dist` |
| 2.4 | API 杠杆 25x | ✅ | `EXCHANGE_LEVERAGE`（仅 set_leverage） |
| 2.5 | 最终量 = min(理论, 杠杆限制, 硬上限)×qty_ratio | ✅ | `compute_tv_order_qty()` |
| 2.6 | 精度 floor×1000/1000（最小 0.001） | ✅ | `_floor_qty_3dp` |
| 2.7 | 单笔硬上限 50000U / price | ✅ | `HARD_NOTIONAL_CAP` |
| 2.8 | Σ名义 ≤ 总权益 × 13 | ✅ | `check_total_notional_cap()` |

### 唯一公式

```
止损距离 = |price - tv_sl|
风险金额 = 账户权益 × (risk_pct / 100)
理论仓位 = 风险金额 / 止损距离
杠杆限制 = 账户权益 × leverage / price
硬上限   = 50000 / price
最终下单量 = min(理论, 杠杆限制, 硬上限) × qty_ratio
精度     = floor(最终 × 1000) / 1000
```

**禁止**旧「档位保证金% × 25x」路径；缺 `risk_pct`/`leverage` 时拒绝下单。

### 参考表（本金 1000U · ETH≈1892 · qty_ratio=1.0）

| 档位 | risk_pct | 止损距离 | 下单量 | 名义约 |
|------|----------|----------|--------|--------|
| R1 | 0.81% | 12.08 | 0.67 ETH | 1268U |
| R2 | 1.35% | 14.09 | 0.96 ETH | 1817U |
| R3 | 2.03% | 14.02 | 1.45 ETH | 2744U |
| R4 | 2.70~3.38% | 15.94 | 1.69~2.12 ETH | 3200~4011U |

本金线性：下单量(任意) = 下单量(1000U) × (本金/1000)。加仓同公式，`qty_ratio` 约 0.3~0.5。

---

## 模块三：硬止损（严格按 TV `tv_sl` 挂盘）

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 3.1 | 硬止损 = TV `tv_sl` | ✅ | `_tv_hard_sl_target()` |
| 3.2 | 旧 VPS% 仅对照不挂盘 | ✅ | `VPS_HARD_SL_PCT` 不用于挂单 |
| 3.3~3.4 | 多/空方向公式 | ✅ | TV `tv_sl` 多空严格 |
| 3.5 | 开仓成交后立即挂 **closePosition STOP**（不占 reduceOnly，避免撤掉 TP123） | ✅ | `_sync_exchange_stop()` · `use_stop_limit=False` |
| 3.5b | TV 空 TP/价 → ATR 强制补全 TP123；`expected=0` 不假齐；终检无止损钉钉 | ✅ | v13.64 `_protect_and_monitor` |
| 3.6 | 硬止损与雷达单槽合并 | ✅ | 雷达激活后保本取代；止损只向有利方向 |
| 3.7 | TV `tv_sl` 挂盘 | ✅ | `tv_sl`=`tv_sl_ref`=TV价，盘口 STOP 对齐 |

| 档位 | 旧VPS%对照（不挂盘） | @1800 示例 |
|------|---------------------|------------|
| R1 | 2.78% | 1750 |
| R2 | 3.89% | 1730 |
| R3 | 5.56% | 1700 |
| R4 | 8.33% | 1650 |

---

## 模块四：雷达移动保本（价触激活线启动）

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 4.1 | **主判**：现价达档位激活线或 TP1 真实成交 | ✅ | `_radar_ready_to_handoff()` |
| 4.2 | 分档激活 **85/80/75/70%** · 步进35/30/25/20% · 呼吸1.0/0.8/0.65/0.5ATR | ✅ | `get_radar_*` |
| 4.3 | TP1 成交强制交棒（防回吐） | ✅ | `_tp1_fill_allows_radar` |
| 4.4 | 废除三重强制门槛 | ✅ | 限价成交/减仓仅作伪TP记账 |
| 4.5 | 微漂 <2% 开仓量不作伪TP依据 | ✅ | `TP_FILL_NOISE_VS_OPEN_PCT = 0.02` |
| 4.6 | 雷达启动 → 成本 ±0.1% | ✅ | `RADAR_STAGE_COST_BUFFER_PCT` |
| 4.7 | TP2/TP3 逐级收紧 ATR 追踪 | ✅ | `_radar_stage()` 5 阶段 |
| 4.8 | 交棒死锁修复 for_handoff | ✅ | `_ensure_radar_sl(for_handoff=True)` |
| 4.9 | WS mark@1s 最快盯价 | ✅ | `_on_mark_price_tick` · 接近90%加速 · 达线紧急交棒 |
| 4.9b | 雷达进行中新TV | ✅ | 一律先平后开；空仓OPEN直开+TP123+宽止损+雷达待命 |
| 4.10 | 硬止损/雷达单槽不抢 TP | ✅ | closePosition 合并 |

### 启动伪代码（v13.65）

```
主判：现价达 entry→TP1 × 档位激活比（R1=85%…R4=70%）
     或 TP1 已成交（账本消费 / WS 提示）
理想保本线距现价足够安全 → for_handoff 挂保本 STOP 核实 → 交棒
交棒成功 → _radar_handoff_done=True → 钉钉 [ETHUSDT]/[XAUUSDT]
否则 → 保留 TV 硬止损，雷达继续待命
WS mark 达激活线 / TP1成交 → 脉冲哨兵 1.5s 快轮询锁利
随后 TP1→TP2→TP3 路程推进 → 阶段2~5 逐级锁利（只升不降）
硬止损与雷达 = closePosition 单槽；TP123 = reduceOnly（不抢份额）
UPDATE_SL → 按 TV tv_sl 强制改挂盘口硬止损（雷达激活时可合并保本）
```

### 防误判场景

| 场景 | 雷达 |
|------|------|
| 价达激活线且保本距市价足够 | ✅ 启动 |
| 价达激活线但保本过近 | ⏳ 延迟交棒，保留宽硬止损 |
| 价未达激活线（即便伪减仓） | ❌ 不启动 |
| 达 TP2/TP3 | ✅ 已激活后逐级收紧 |

---

## 模块五：全局风控（13 倍名义硬顶）

| # | 检查项 | 状态 |
|---|--------|------|
| 5.1 | ETH+XAU 名义 ≤ 权益×13 | ✅ |
| 5.2 | 超标拒绝 + 钉钉 | ✅ `report_system_alert` |
| 5.3 | 日亏 -5.5% 熔断 | ✅ `risk_manager` |
| 5.4 | 双品种盈亏叠加 | ✅ |

---

## 模块六：头寸监控与误判防范

| # | 检查项 | 状态 |
|---|--------|------|
| 6.1 | 区分数量变化 vs 价值变化 | ✅ |
| 6.2 | 雷达基于数量减仓非价值 | ✅ |
| 6.3 | 快照 entry / initial_qty / TP1 单 | ✅ |
| 6.4 | WS 实时仓位 | ✅ User Data Stream |
| 6.5 | 微漂 <0.01 忽略 | ✅ `QTY_DRIFT_TOLERANCE_PCT` |

---

## 模块七：钉钉通知

| 场景 | 函数 | 状态 |
|------|------|------|
| 开单成功 | `report_supervisor_open` | ✅ 含硬止损价/雷达激活线/头寸对账 |
| 硬止损/VPS盾 | `report_adverse_shield_armed` | ✅ |
| 雷达启动 | `report_radar_activated` | ✅ 含启动闸门；失败哨兵补发 |
| 雷达推升 | `report_intervention` | ✅ |
| TP 成交 | `report_tp_fill` | ✅ |
| 全平收网 | `report_supervisor_close` | ✅ **平仓归因** radar_be/tp3/vps_hard_sl |
| 敞口超标拒绝 | `report_system_alert` | ✅ |
| UPDATE_SL | 按 TV 改挂盘口硬止损 | ✅ 钉钉同步播报 |

> **归因铁律**：是否雷达平仓看 `_radar_handoff_done`，**不以** `_radar_activation_notified`（钉钉是否发出）为准。

---

## 实盘完整模拟（测试用例）

### 场景 1：正常 TP1 → 雷达 → TP2/TP3

1. ETH 1800 R3 → 保证金 180U，名义 4500U，qty 2.50
2. 硬止损 1700（5.56%）
3. 价格到 TP1，限价成交，减仓 18%
4. 三重验证 → 雷达保本 1800+0.1%
5. 继续 TP2/TP3 追踪收紧

### 场景 2：插针未成交

1. 价格刺穿 TP1 后回落
2. TP1 限价仍 open，数量不变
3. 三重验证失败 → 雷达不启动

### 场景 3：双品种 R4 踩线

1. 1000U 本金，ETH R4 6500U + XAU R4 6500U = 13000U ✅（13x）
2. 若已有 13500U 名义 → 拒绝新开仓

### 场景 4：TV 紧止损被忽略

1. TV `tv_sl` 1910 → 写入账本并挂盘口硬止损
2. VPS 挂 1700（宽）→ 实盘以 VPS 为准

---

## Cursor 自查命令

```bash
# 静态逻辑对账（无需 API Key）
python check_vps_logic.py

# 健康检查
curl -s http://127.0.0.1:5003/health | python -m json.tool

# 日志关键词
grep -E '雷达交棒|激活线|补发雷达|平仓归因|exit_source|闸门=|敞口硬顶|tv_sl_ref' logs/binance_brain.log | tail -30
```

### 优先级

| 模块 | 优先级 |
|------|--------|
| 品种路由 + 开单 + VPS硬止损 + 13x硬顶 | 🔴 P0 |
| 雷达价触激活线 + TP2/TP3 锁利 | 🟡 P1 |
| 钉钉 + 日志 | 🟢 P2 |

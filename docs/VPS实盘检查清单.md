# 🛡️ 万亿战神 VPS 实盘检查清单（Cursor 开发自查专用）

> **币安** `eth-webhook-server` · **深币** `deepcoin-hft-server-main` 共用逻辑；运行 `python check_vps_logic.py` 做静态对账。

## 📌 核心原则（必须刻进代码）

| # | 原则 | 代码落点 |
|---|------|----------|
| 1 | **TV 只发信号**，开仓/硬止损/仓位计算均由 VPS 自主 | `app.py` 网关不入队决策；`position_supervisor_*.py` |
| 2 | TV `tv_sl` **仅供日志参考**，绝不作为实盘硬止损挂单 | `_refresh_vps_hard_sl()` · `tv_sl_ref` 字段 |
| 3 | **雷达移动保本**在价触档位激活线后启动（弱70%/强75~80%） | `_price_reached_radar_activation()` · `_perform_radar_handoff()` |
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
| 1.5c | 钉钉攒批防限流 | ✅ | `DINGTALK_BATCH_*` + 1/2/4s 重试 + `WECHAT_WEBHOOK` 备用 |

### 实盘场景

| 场景 | VPS 预期 |
|------|----------|
| `"symbol":"ETHUSDT.P"` | ETH 档位/仓位/止损 |
| `"symbol":"XAUUSDT.P"` | XAU 档位/仓位/止损 |
| 无 symbol（且 URL 无路径） | 默认 ETHUSDT（建议 TV 始终带 symbol） |
| 恶意未知品种 | 拒绝 `unknown_symbol` |
| 同 K 线重复 Webhook | 第二条去重忽略 |

---

## 模块二：开单计算（档位权重 + 杠杆）

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 2.1 | 总权益实时获取 | ✅ | `get_total_equity()` |
| 2.2 | R1~R4 保证金系数 | ✅ | `VPS_MARGIN_PCT_BY_REGIME` |
| 2.3 | 品种独立系数 | ✅ | 各 supervisor 独立 `sizing_principal` |
| 2.4 | 杠杆 25x | ✅ | `EXCHANGE_LEVERAGE = 25` |
| 2.5 | 名义 = 保证金 × 杠杆 | ✅ | `compute_vps_open_qty()` |
| 2.6 | qty = 名义 ÷ 开仓价（步进取整） | ✅ | `symbol_config` qty_step |
| 2.7 | Σ名义 ≤ 总权益 × 13 | ✅ | `check_total_notional_cap()` |

### 档位保证金系数（占总权益 · 短周期 ETH45m / XAU50m）

| 档位 | 系数 | 1000U 示例名义 |
|------|------|----------------|
| R1 | 8% | 80×25 = **2000U**（2.0x） |
| R2 | 14% | 140×25 = **3500U**（3.5x） |
| R3 | 20% | 200×25 = **5000U**（5.0x） |
| R4 | 26% | 260×25 = **6500U**（6.5x） |

双品种 R4+R4 = 13000U = 13×本金（踩线允许）。

---

## 模块三：硬止损（VPS 自主，忽略 TV 紧止损）

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 3.1 | 硬止损 = 开仓价 × 档位% | ✅ | `compute_vps_hard_sl()` |
| 3.2 | R1~R4 宽止损比例 | ✅ | `VPS_HARD_SL_PCT` |
| 3.3~3.4 | 多/空方向公式 | ✅ | 开多减 / 开空加 |
| 3.5 | 开仓成交后立即挂 **closePosition STOP**（不占 reduceOnly，避免撤掉 TP123） | ✅ | `_sync_exchange_stop()` · `use_stop_limit=False` |
| 3.6 | 硬止损只收紧不放松 | ✅ | 雷达阶段前不动；雷达后只升不降 |
| 3.7 | TV `tv_sl` 仅日志 | ✅ | 存入 `tv_sl_ref`，挂单用 `tv_sl`(VPS值) |

| 档位 | 硬止损% | @1800 示例 |
|------|---------|------------|
| R1 | 2.78% | 1750 |
| R2 | 3.89% | 1730 |
| R3 | 5.56% | 1700 |
| R4 | 8.33% | 1650 |

---

## 模块四：雷达移动保本（价触激活线启动）

| # | 检查项 | 状态 | 说明 |
|---|--------|------|------|
| 4.1 | **主判**：现价/best 达档位激活线 | ✅ | `_price_reached_radar_activation()` |
| 4.2 | R1/R2 = **70%** TP1 路程 | ✅ | `RADAR_ACTIVATION_RATIO_BY_REGIME` |
| 4.3 | R3 = **75%** · R4 = **80%** | ✅ | 强势给趋势空间 |
| 4.4 | 废除三重强制门槛 | ✅ | 限价成交/减仓仅作伪TP记账 |
| 4.5 | 微漂 <2% 开仓量不作伪TP依据 | ✅ | `TP_FILL_NOISE_VS_OPEN_PCT = 0.02` |
| 4.6 | 雷达启动 → 成本 ±0.1% | ✅ | `RADAR_STAGE_COST_BUFFER_PCT` |
| 4.7 | TP2/TP3 逐级收紧 ATR 追踪 | ✅ | `_radar_stage()` 5 阶段 |

### 启动伪代码（v13.61）

```
主判：现价或 best ≥ entry ± |TP1-entry| × 档位比例
  R1/R2 → 70% · R3 → 75% · R4 → 80%
理想保本线距现价足够安全 → 挂保本 STOP 核实 → 交棒
交棒成功 → _radar_handoff_done=True → 钉钉 [ETHUSDT]/[XAUUSDT]
否则 → 保留 VPS 宽硬止损，雷达继续待命
随后 TP1→TP2→TP3 路程推进 → 阶段2~5 逐级锁利
硬止损盘口价一律 = VPS 开仓×档位%；TV tv_sl 只写 tv_sl_ref，永不挂单
UPDATE_SL → 仅更新 VPS 止损追踪参考，不用 TV tv_sl 挂单
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
| 开单成功 | `report_supervisor_open` | ✅ |
| 硬止损/VPS盾 | `report_adverse_shield_armed` | ✅ |
| 雷达启动 | `report_radar_activated` | ✅ |
| 雷达推升 | `report_intervention` | ✅ |
| TP 成交 | `report_tp_fill` | ✅ |
| 敞口超标拒绝 | `report_system_alert` | ✅ |
| TV 紧止损忽略 | 日志 `tv_sl_ref` 对比 | ✅ 不单独钉钉 |

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

1. TV `tv_sl` 1910（紧）→ 记入 `tv_sl_ref`
2. VPS 挂 1700（宽）→ 实盘以 VPS 为准

---

## Cursor 自查命令

```bash
# 静态逻辑对账（无需 API Key）
python check_vps_logic.py

# 健康检查
curl -s http://127.0.0.1:5003/health | python -m json.tool

# 日志关键词
grep -E '雷达交棒|激活线|解除过早雷达|敞口硬顶|tv_sl_ref' logs/binance_brain.log | tail -30
```

### 优先级

| 模块 | 优先级 |
|------|--------|
| 品种路由 + 开单 + VPS硬止损 + 13x硬顶 | 🔴 P0 |
| 雷达价触激活线 + TP2/TP3 锁利 | 🟡 P1 |
| 钉钉 + 日志 | 🟢 P2 |

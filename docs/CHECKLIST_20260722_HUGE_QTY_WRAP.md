# 币安单一账户：2026-07-22 天文 qty 事件收尾 + 全域复盘

版本：`v15.5.17-incident-sticky`（含 v15.5.16 同步修复 + 事故暂停粘性）

---

## 一、本次事件收尾确认

### 1. GitHub 推送 + VPS 部署 / health 版本
- **结论**：本地在本清单完成时交付 `v15.5.17`；**须对照** `GET /health` 的 `version` 与 VPS 代码一致后勾选。
- v15.5.16 曾部署但未稳定入 GitHub main（此前 HEAD 仍为 docs/`v15.5.15`）。本轮一并推送。

### 2. ETH 暂停状态
- 暂停原因：`INCIDENT_20260722_HUGE_QTY_PENDING_RESUME`
- **发现并已修**：原闸门仅把 `CLOSE_THEN_OPEN_FAIL*` / `restart_*` / `ATR_DEGRADE*` 视为人工粘性；`INCIDENT_*` / `PENDING_RESUME` 在空仓时会被下一笔 LONG/SHORT **自动解除**。
- **修复**：`_process_signal` 与 `_open_position` 均将 `INCIDENT_*` 与含 `PENDING_RESUME` 视为 sticky，禁止自动恢复。
- **恢复条件**：仅人工 `POST /admin/resume/ETHUSDT`，且需负责人明确确认。

### 3. `availableBalance×5×0.92` 裁剪位置与覆盖面
- **位置**：`position_supervisor_binance.py` → `_calc_vps_open_qty`（保证金裁剪块，约 L3255–3284）
- **调用链（唯一开仓市价路径）**：
  - `_open_position` → `_calc_target_open_qty` → `_calc_vps_open_qty`
  - `_calc_regime_margin_qty` → `_calc_vps_open_qty`（辅助）
  - 信号预览：`_record_tv_signal` →（先 `_apply_tv_sizing_params`）→ `_calc_vps_open_qty`
- **LONG/SHORT**：同一 `_handle_smart_entry` → `_full_reentry` → `_open_position`，无分叉绕过。
- **加仓**：`PYRAMID` / `PROFIT_ADD` 已废除，不进入下单。
- **平仓市价** `place_market_order(..., reduce_only=True)` 不走 sizing，合理。

### 4. `test_huge_tv_qty_sizing.py` 覆盖面
| 用例 | 场景 |
|------|------|
| `test_huge_tv_qty_binds_notional_not_tv` | 天文 qty → absurd 忽略，绑定 notional（含 0.85 haircut） |
| `test_preview_and_order_use_same_payload_qty` | 残留 0.02 vs 真实巨大 qty 结果必须不同；证明未绑定时会脏读 |
| `test_margin_cap_clips_notional_to_available` | `avail×5×0.92` 数学裁剪 |
| `test_qty_zero_rejected` / `test_qty_negative_rejected` | qty≤0 |
| `test_price_zero_rejected` | price=0 |
| `test_stop_equals_price_zero_dist` | stop=price 除零保护 |
| `test_string_qty_normalized` | qty/price 字符串可解析 |
| `test_tp_direction_inverted_still_parses` | TP 方向颠倒仍可 normalize（方向校验在 supervisor） |
| `test_invalid_json_body_raises` / `test_missing_action_not_ok` | 坏 JSON / 缺 action |

### 5. 钉钉「脏读」是否更广泛时序问题？
**是时序/状态残留问题，根因不是交易所异步。**

- **本质**：实例字段 `tv_suggested_qty`（以及历史上可能的 `tv_sl_ref`）在「播报预览」与「真实下单」两次 `_calc_vps_open_qty` 之间，若未先对本笔 payload 调用 `_apply_tv_sizing_params`，会读到**上笔残留**。
- **不只 qty**：任何依赖 `self.tv_*` 共享状态、且在 apply 之前读取的字段都有同类风险。
- **已修**：
  1. `_record_tv_signal` 预览前强制 `_apply_tv_sl_from_payload` + `_apply_tv_sizing_params`
  2. `_apply_tv_sizing_params` **每次覆盖** qty/sl_ref（含 ≤0 清零），禁止残留
  3. 钉钉 `tv_sl` 同时认 `tv_sl` / `stop_loss` / 已绑定 `tv_sl_ref`
- **price / tp**：在 `_record_tv_signal` 之前已由 `_process_signal` 写入 `self.tv_price` / `self.tv_tps`，播报与本笔一致。
- **atr / regime**：播报用当前实例值；若本笔 webhook 未带 atr，可能仍是行情引擎刷新值（非「上笔 TV 残留 qty」同类 bug）。开仓 ATR 有独立决议路径。
- **开仓成功/失败钉钉**：使用 `_open_position` 当次 `sizing_meta` / `qty`，与下单变量同源。

---

## 二、全域功能复盘

| # | 项 | 判断 | 依据 |
|---|----|------|------|
| 1.1 | 永远先平后开 | **仍然符合** | `_full_reentry` → `_ensure_flat_before_open`；无同向直接加仓旁路 |
| 1.2 | 不采信 TV.qty 为最终下单量 | **仍然符合** | `compute_fixed_order_qty` 三选一 + absurd 忽略 + margin_cap；无其它 `place_market_order` 开仓入口直接用 TV.qty |
| 1.3 | 单仓位不加仓 | **仍然符合** | PYRAMID/PROFIT_ADD no-op |
| 2.1 | 计算值与使用值同源 | **已修复·仍须守** | 预览与下单均经 apply→`_calc_vps_open_qty`；共享字段每次覆盖 |
| 2.2 | sizing 纯函数 | **仍然符合（核心）** | `compute_fixed_order_qty` 无状态；supervisor 包装读账户 avail 做裁剪（有意副作用仅写 meta/日志） |
| 3.1 | 仅 5 action | **仍然符合** | `VALID_ACTIONS`；废弃 CLOSE_* 忽略 |
| 3.2 | secret 鉴权 | **仍然符合** | webhook 路径校验（部署侧） |
| 3.3 | tv_seq 折叠 | **要求复跑** | `test_tv_seq_collapse.py` |
| 3.4 | 去重 | **仍然符合** | 既有幂等签名逻辑未改 |
| 4.1 | 先平后开失败中止 | **仍然符合** | `CLOSE_THEN_OPEN_FAIL_ABORT` sticky |
| 4.2 | 裁剪与三选一组合 | **仍然符合** | 先三选一（含 absurd），再 margin_cap；binding 追加 `+margin_cap` |
| 5.* | 呼吸止损四项 | **要求复跑既有单测** | `test_stop_*` / `test_restart_*` / `test_copy_and_tp_timeout` |
| 6.* | 全平清零 / get_position fail-closed | **仍然符合** | 既有逻辑 + `test_position_query_fail_safe` |
| 7.* | 钉钉 R3/超时归因 + 时序 | **已加固** | 见第一节第 5 条 |
| 8.* | 极端输入 | **已扩测** | 见 `test_huge_tv_qty_sizing.py` 新增用例清单 |

---

## 三、恢复交易前确认（负责人）

在勾选前必须同时满足：
1. GitHub 含本版本；VPS `health.version == v15.5.17-incident-sticky`
2. ETH `trading_paused=true` 且 reason 仍为 INCIDENT…（未自动解开）
3. 本清单第一、二部分无未关闭的「需要修复」项
4. 负责人明确口头/书面确认 resume

**在未获确认前不得调用 `/admin/resume/ETHUSDT`。**

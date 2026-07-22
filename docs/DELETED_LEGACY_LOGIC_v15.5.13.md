# 已删除 / 已废除旧逻辑清单（v15.5.13-prod-gate）

交付日期: 2026-07-22  
范围: binance-engine 生产主路径（webhook → 开仓 → TP → 呼吸止损 → 平仓 → 钉钉）

## 1. 已从实盘决策路径清除 / 禁止

| 旧逻辑 | 状态 | 说明 |
|--------|------|------|
| `webhook_parser.compute_ladder_radar_sl` | 兼容残留，**不进入实盘决策** | 仍可 import；哨兵/开仓止损仅用 `breath_stop.calculate_breath_stop` + 自建 WS |
| 档位雷达 / 旧 ladder SL | 废除 | 钉钉不再展示「中势推升」等档位文案；展示「算仓模式=RISK20」 |
| 加仓 / pyramiding>1 | 废除 | `_max_add_times_*=0`；`_handle_add_entry` 等为 no-op；收到 PYRAMID/PROFIT_ADD 忽略 |
| `opentrades` 驱动加仓 | 废除 | 单仓位 pyramiding=1 |
| CAP_ALIGN 主动减仓 | 废除 | `_trim_*` 仅告警，禁止 reduceOnly 自主减仓 |
| TP3 限价挂单主路径 | 废除 | `_tp_slices_for_initial` 仅返回 TP1+TP2；余仓不挂限价，交呼吸止损 |
| webhook `CLOSE_TP` / `CLOSE_TRAIL` / `CLOSE_SL_*` / `CLOSE_TP3` | 不进 VALID_ACTIONS | 仅 5 action：LONG/SHORT/CLOSE_QUICK_EXIT/CLOSE_RSI_EXIT/PING |
| `UPDATE_SL` / `UPDATE_TP` | 废除改挂 | 兼容入口仅记 TV 参考，盘口维持呼吸止损单槽 |
| `leg` 字段驱动分腿 webhook | 废除主路径 | 平仓只认 QUICK/RSI；分腿由交易所 TP 成交 + 哨兵驱动 |
| 同向开仓「跳过平仓」特例 | 废除 | `_same_direction_entry_mode` 一律先平后开 FULL_REENTRY |

## 2. 仍保留但明确非生产主路径

- `UPDATE_SL` / 旧 CLOSE_* 字符串：钉钉标签映射、历史归因兼容、防御性分支（不会由 webhook VALID_ACTIONS 进入）
- `compute_ladder_radar_sl`：函数体留存供对照/静态检查，**禁止**再接回 `_sync_exchange_stop`
- CAP_ALIGN / report_radar_regime_cap_trim：空实现防旧调用崩

## 3. 本轮新增安全闭环

| 项 | 结论 |
|----|------|
| `get_position()` REST 失败 | 无缓存时返回 `POSITION_QUERY_FAILED`，禁止当空仓 |
| 哨兵 / 空闲巡检 / `_confirm_position_flat` / 重启探测 | QUERY_FAILED → 保留账本、跳过空仓判定、钉钉限频告警 |
| 重启窗口止损振荡 (1886.53↔1910.18) | 根因=二次 recover 跳过接管仍点火哨兵 + ATR=30 虚构；已 v15.5.11 修复并实盘 accept |
| TP 超时/核武重挂 | Gemini 同类风险曾存在；`tp_levels_radar_handoff` 持久化 + 禁止虚假 clear；见 `test_restart_stop_and_tp_handoff.py` |

## 4. Webhook 鉴权与去重（产品确认）

- secret 鉴权；token 字段仍兼容
- 60s 同 action+symbol+price 去重：**维持含 price**（若 TV 合法场景为 60s 内同向改价重发，当前会放行；同价重复拦截）
- `tv_seq` 1.0s 折叠：先 CLOSE 后 OPEN；重复 OPEN 只留最新 — 见 `test_tv_seq_collapse.py`

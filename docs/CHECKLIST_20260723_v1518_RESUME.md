# v15.5.20 最终复验 · 第十四节全部「已确认」

版本：`v15.5.20-checklist-final`  
仓位铁律：永远 `合约本金 × 20%` 风险资金 + `合约本金 × 5` 名义上限（`RISK20_NOTIONAL5`）  
通知：钉钉（已配置；不迁 Telegram）

| # | 状态 | 说明 |
|---|------|------|
| 1 | **已确认** | 开仓前查实盘；非空 → 全平+撤单 → 等归零确认 → 再开 |
| 2 | **已确认** | `_ensure_flat_before_open` / `_verify_sterile_flat` 确认后才算仓 |
| 3 | **已确认** | `tv_seq` 1.0s 同窗：先全部平仓，再开仓 |
| 4 | **已确认** | 无先开后平 / 开平并行路径 |
| 5 | **已确认** | 加仓生效路径删除；`compute_vps_add_qty` 恒 0 |
| 6 | **已确认** | 无旧「保护性全平」；保留呼吸止损触发 / FORCE_ALIGN / 裸仓中止 |
| 7 | **已确认** | `compute_fixed_order_qty` 无状态纯函数；天文 TV.qty 忽略 |
| 8 | **已确认** | 止损唯一写入 = `_sync_exchange_stop` / breath 引擎 |
| 9 | **已确认** | TP 成交只 `_breath_resize_stop_on_tp` 缩量；旧 entry±1tick 对账已清空壳 |
| 10 | **已确认** | `PLACE_TP_LEVELS=2`；不挂 TP3 |
| 11 | **已确认** | `VALID_ACTIONS` 仅 LONG/SHORT/CLOSE_QUICK_EXIT/CLOSE_RSI_EXIT(+PING) |
| 12 | **已确认** | 旧 ladder / 固定 2.0×ATR 追踪生效路径已删 |
| 13 | **已确认** | 旧 schema（activated/stepCount）重启检测 → 暂停告警 |
| 14 | **已确认** | 禁旧文案；同窗缓存钉钉「平仓+开仓同时到达，已按先平后开」 |
| 15 | **已确认** | `market_engine` 30m→90m ATR/ADX |
| 16 | **已确认** | 1s 窗口平仓优先折叠 |
| 17 | **已确认** | 窗口固定 1.0s 超时兜底，不无限等待 |

本地：`check_vps_logic` 161/161 · `test_huge_tv_qty_sizing` · `test_tv_seq_collapse`  
部署后：空仓等待真实 TV；VPS / 本地 / GitHub 同 commit。

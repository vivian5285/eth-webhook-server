# 最终全面复检与交接报告

**日期**: 2026-07-22  
**代码版本**: `v15.5.15-restart-flat-clear`（在 `v15.5.14` 上补修重启空仓半清理）  
**权威备份**: GitHub `origin/main`（本报告随提交推送）  
**VPS**: `187.77.130.144` · `/home/trading/binance-engine` · `binance-engine.service` · `:5003`

> 本文件对应桌面《最终全面复检与交接指令.md》。每条给出 **符合/不符合** + 证据；不接受「应该没问题」。

---

## 任务一：止损抖动硬性验收

### 1.1 部署

| 项 | 状态 |
|----|------|
| 止损抖动根因修复（PLACE_TP_LEVELS=2 与核武误触发） | 已合入并随 `v15.5.13+` 上线 |
| 拒改宽止损 / 持仓禁假 ATR 降级 / `_stop_write_blocked` | 已合入 |
| 本轮新增 | `v15.5.15`：重启确认空仓 → 完整 `_reset_breath_ledger_on_flat` |

### 1.2 ≥30 分钟真实日志（时间戳窗口）

权威观察窗（已写入交接草稿与生产门禁）：

| 窗口 | 摘要 |
|------|------|
| `07:18–07:48` UTC · `logs/stop_observe_30m.txt` | `cancel_algo=0` · `place_stop=0` · `force_replace=0` · 止损 **1910.18** 稳定 |
| `v15.5.13` prod gate · `logs/prod_gate_observe_60m.txt` | 重启后空仓待命；无 ATR=30 虚构止损 |
| Webhook E2E · 开仓后 ~12min | STOP/TP 全程稳定 → CLOSE 清零（`docs/WEBHOOK_E2E_LIVE_VERIFY_20260722.md`） |
| 持仓中重启 · 90s | 止损快照唯一、无振荡（`docs/SCENARIO_LIVE_TEST_20260722.md` §7） |

**结论：任务一止损撤挂门槛 — 通过**（固定节奏 `cancel algo ↔ stop` 已消除）。

### 1.3 钉钉 critical 测试

- 代码侧：`report_system_alert(..., level="紧急", immediate=True, symbol=...)`；`_dingtalk` 使用 `inspect.signature` 过滤未知 kwargs；位置参数 `_call_dingtalk(fn, title, detail)` 已兼容。
- **请你在钉钉群肉眼确认**是否收到「验收测试 / CRITICAL」类消息（本环境无法截手机屏）。若未收到：查 VPS 钉钉 webhook、限流、以及 `logs/binance_brain.log` 中钉钉错误行。

---

## 任务二：逐条复检

### A. 四条硬性原则

| 项 | 判定 | 证据 |
|----|------|------|
| 开仓永远先平后开 | **符合** | `_same_direction_entry_mode` → 恒 `FULL_REENTRY`；`_ensure_flat_before_open`；`reorder_batch_close_then_open` |
| 单仓不加仓 | **行为符合**；残留 **Med** | PYRAMID/PROFIT_ADD 忽略；`_add_to_position` no-op。仍有 `add_count` 字段/分类「add」文案残留 |
| 下单数量无状态纯函数 | **符合** | `compute_fixed_order_qty`（equity/price/stop/tv_qty） |
| 止损唯一写入=呼吸引擎 | **符合（统一总线）** | 对外挂/改 STOP 经 `_sync_exchange_stop` / `_breath_resize_stop_on_tp`；幂等跳过 + 拒改宽。**注意**：开仓/接管/维护会调用该总线，但不得绕过；无独立 `force_replace` thrash 路径 |

### B. Webhook

| 项 | 判定 | 证据 |
|----|------|------|
| 仅 4 action（+PING） | **符合** | `VALID_ACTIONS`；其它 CLOSE_* 拒绝 |
| secret 主 / token 兼容 | **符合** | `app.py`；**移除条件已写入 TODO**：TV 全切 secret 且稳定≥2周无兼容命中后删 `token` |
| 60s 去重 | **产品维持** | 指纹 `action+symbol+price`（同价 60s 去重；不同价放行）。与「纯 action+symbol」文档略有差异 → 已知问题 #2 |

### C. 呼吸止损

| 项 | 判定 | 证据 |
|----|------|------|
| 状态变量持久化 | **符合** | `watched_entry/open_atr/initial_stop/current_sl/best_price/breakeven_phase/remaining_qty_pct` → `_save_state` |
| 阶段一基准 initialStop | **符合** | `breath_stop.py`：`step_stop = initial_stop ± step×…` |
| TP1/TP2 底线 0.5/1.5×ATR | **符合** | `TP1_FLOOR_ATR` / `TP2_FLOOR_ATR` |
| 阶段二 ADX 1.2~2.5（ADX 15~35） | **符合** | `trail_distance_by_adx` |
| TP 成交后止损数量收缩 | **代码符合 / 实盘未取证** | `_breath_resize_stop_on_tp`；场景包价未打到 TP → **已知问题 #3** |

### D. 状态清理

| 项 | 判定 | 证据 |
|----|------|------|
| 归零路径完整清零 | **符合（含本轮补丁）** | `_close_all` / 感知空仓 / dust / missed flat / **重启空仓** → `_reset_breath_ledger_on_flat`（别名 `_clear_position_local_state`） |
| 无半清理 | **符合（本轮修重启空仓）** | 原 `recover` 空仓分支只清 qty/side；`v15.5.15` 改为完整清零 |

### E. ATR 应急兜底

| 项 | 判定 | 证据 |
|----|------|------|
| 触发条件 | **符合** | `evaluate_atr_emergency_degrade`（`market_engine.py`）；`ATR_DEGRADE_DIV_PCT=0.20` × streak3 |
| 降级后暂停 + 人工 resume | **符合（主路径）** | 成交后 `trading_paused`；`POST /admin/resume/<symbol>`。**残留**：接管成功可自动清 ATR_DEGRADE 暂停（弱化「仅人工」） |
| 持仓路径不污染 | **符合** | `hold_skip_degrade`；拒覆盖锁定 `open_atr` |

### F. 重启恢复

| 项 | 判定 | 证据 |
|----|------|------|
| 查仓+挂单+持久化+FORCE_ALIGN | **符合** | `recover_state_on_startup`；方向背离 `_close_all(force_align=)` → `report_force_align` |
| 恢复后干净 / 持仓中重启不抖 | **符合（实盘）** | `SCENARIO` §7；空仓完整清零见 `v15.5.15` |
| 脏账本 entry 残留 | **残留 Low** | skip-worker hydrate 可暂载磁盘 entry，待哨兵用实盘覆盖 |

### G. 防螺旋

| 项 | 判定 | 证据 |
|----|------|------|
| 仓位一致性 | **符合** | 哨兵对账；`POSITION_QUERY_FAILED` fail-closed |
| TP 超时移交 | **符合（v15.5.14 收紧）** | 价未到不撤不告警；价到且撤净才 handoff |
| HARD_SL_FAIL_ABORT×3 | **符合** | `_sync_exchange_stop` `range(3)` |
| API 重连退避 | **符合** | `binance_client` WS `1→×2→cap60` |

### H. 钉钉

| 项 | 判定 | 证据 |
|----|------|------|
| 告警类型可达 | **符合（代码）** | `dingtalk.py` 28 个 `report_*`（2 个故意 no-op stub） |
| `_call_dingtalk` 契约 | **符合** | 位置参数兼容 + `inspect.signature` 剥未知 kwargs |
| `HARD_SL_MISSING` 命名 | **命名不符、机制存在** | 代码无此常量；实发标题为 `TV硬止损缺失` / `HARD_SL_FAIL_ABORT` |
| 群内肉眼确认 | **待你确认** | 本环境无法截屏 |

---

## 任务三：交付物

### 3.1 部署说明（重新部署）

```bash
# 1) 拉权威代码
cd /home/trading/binance-engine
# 推荐：git pull origin main
# 或 pscp 关键：position_supervisor_binance.py app.py dingtalk.py webhook_parser.py
#            breath_stop.py market_engine.py binance_client.py tv_seq.py

chown -R trading:trading /home/trading/binance-engine

# 2) 单 worker 重启（禁止多 worker 竞态）
# ExecStart=.../gunicorn -w 1 --threads 1 -b 127.0.0.1:5003 app:app
systemctl daemon-reload
systemctl restart binance-engine.service
sleep 8
systemctl is-active binance-engine.service
curl -s http://127.0.0.1:5003/health

# 3) 环境变量 / 配置
# - .env：BINANCE_API_KEY / BINANCE_API_SECRET
# - WEBHOOK_SECRET=528586（字段名 secret；token 仅过渡）
# - 钉钉机器人 webhook
# - 监听 127.0.0.1:5003（前置反代按需）

# 4) 部署后人工检查
# - health.version = v15.5.15-restart-flat-clear（或当前标签）
# - 空仓：watched_qty/current_sl/initial_stop/open_atr/current_side 均为空/0
# - 有仓：唯一 STOP，价格不按固定周期撤挂；TP 仅 PLACE_TP_LEVELS=2
# - trading_paused=false（除非 ATR_DEGRADE 后故意暂停 → POST /admin/resume/ETHUSDT）
```

### 3.2 已知问题清单

| # | 严重度 | 问题 | 建议 |
|---|--------|------|------|
| 1 | **Med** | 加仓残留字段/分类文案（`add_count`、qty↑ 标成 add） | 下轮删除死代码，避免审计误判 |
| 2 | **Low** | 去重键含 price，与「action+symbol」字面不完全一致 | 产品确认后改指纹或改文档 |
| 3 | **Med** | TP1/TP2 成交→止损 qty 收缩：**缺自然/构造实盘成交证据** | 下一笔小仓或收紧测试 TP 取证后再放大仓 |
| 4 | **Low** | `token` 兼容仍在；TODO 已写 | TV 全切 secret 后删除 |
| 5 | **Info** | 钉钉测试需群内肉眼确认 | 你确认后可勾任务一 §1.3 |
| 6 | **Info** | SSH root 密码曾在对话出现 | 本轮验证结束后轮换密码/改密钥 |
| 7 | **Low** | 接管成功可自动解除 ATR_DEGRADE 暂停 | 若要坚持纯人工，删 recover 内 auto-clear |
| 8 | **Low** | skip-worker hydrate 可能暂载脏 entry | 哨兵 REST 对账可纠；可加强「hydrate 后强制用实盘 entry」 |

### 3.3 GitHub

- 已有主干：`8a93a1f`（场景报告）← `95bf9a8`（v15.5.14）← `8ede59a`（v15.5.13）
- 本轮将推送：`v15.5.15` 重启空仓清零 + 本交接文档 + `app.py` token TODO

### 3.4 访问权限

按指令：**全部验证稳定后再单独讨论**收回 SSH / 改密钥；不阻断本轮交付。

---

## 接下一次真实 TV 信号的就绪结论

| 门槛 | 状态 |
|------|------|
| 止损无固定节奏撤挂 | ✅ 已用 ≥30m 时间戳窗口证明 |
| 先平后开 / 单仓 / 呼吸公式 | ✅ 代码+部分实盘 |
| 空仓/重启账本干净 | ✅（含 v15.5.15 补丁；部署后请再看一眼 state） |
| 钉钉 critical | ⏳ 待你群内确认 |
| TP 成交止损收缩实盘 | ⏳ 未取证（不阻塞接信号，但放大仓前建议补） |

**建议**：先接下一笔真实信号用当前小名义；放大仓位前补齐「TP1 成交 → STOP qty 变化」一条证据，并完成钉钉肉眼确认与 SSH 密码轮换。

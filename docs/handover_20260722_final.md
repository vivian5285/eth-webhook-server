# 最终全面复检与交接报告

**日期**: 2026-07-22（含当日首次真实 TV LONG 事故复盘）  
**代码版本（本地权威）**: `v15.5.17-incident-sticky`  
**演进**: `v15.5.13` 门禁 → `v15.5.14` TP 超时 → `v15.5.15` 重启空仓清零 → `v15.5.16` 天文 qty 同步 → `v15.5.17` 事故暂停粘性  
**权威备份**: GitHub `origin/main`  
**VPS**: `187.77.130.144` · `/home/trading/binance-engine` · `binance-engine.service` · `:5003`

> 对应桌面《最终全面复检与交接指令》（该文件当前已不在 Desktop；以本报告为准）。每条 **符合/不符合** + 证据。

---

## ⚠ 接信号前门闩（必读）

| 项 | 状态 |
|----|------|
| ETH `trading_paused` | **应为 true**（`INCIDENT_20260722_HUGE_QTY_PENDING_RESUME`） |
| 自动开仓 | **阻断中**，直至你明确 `POST /admin/resume/ETHUSDT` |
| 首次真实 TV LONG（15:00 UTC） | **拒单无成交**（`-2019`）；钉钉 0.02 为预览脏读，真值 notional≈4.445 → 见 `docs/INCIDENT_20260722_HUGE_TV_QTY.md` |
| 部署核对 | VPS `/health.version` 必须 = `v15.5.17-incident-sticky`（或你确认的更新标签）后再 resume |

**结论：止损抖动门槛此前已过；但因事故暂停，现在还不能说「已可接下一笔真实 TV」——需你确认修复已部署 + 主动 resume。**

---

## 任务一：止损抖动硬性验收

### 1.1 修复部署（代码）

| 项 | 状态 |
|----|------|
| PLACE_TP_LEVELS=2 vs 核武误触发 | ✅ `v15.5.13+` |
| 拒改宽止损 / 持仓禁假 ATR / `_stop_write_blocked` | ✅ |
| 重启空仓完整清零 | ✅ `v15.5.15` → `_reset_breath_ledger_on_flat(source="重启确认空仓")` |
| 天文 TV.qty + 预览脏读 + 保证金 haircut | ✅ `v15.5.16/17` + `webhook_parser.ABSURD_TV_QTY_VS_CAPS` |
| 事故暂停不被空仓自动清掉 | ✅ `v15.5.17` sticky `INCIDENT_*` / `PENDING_RESUME` |

### 1.2 ≥30 分钟真实日志（时间戳窗口）

| 窗口 | 摘要 |
|------|------|
| `07:18–07:48` UTC · `logs/stop_observe_30m.txt` | `cancel_algo=0` · `place_stop=0` · `force_replace=0` · 止损 **1910.18** 稳定 |
| `v15.5.13` · `logs/prod_gate_observe_60m.txt` | 空仓待命；无 ATR=30 虚构止损 |
| Webhook E2E | STOP/TP 稳定 → CLOSE 清零（`docs/WEBHOOK_E2E_LIVE_VERIFY_20260722.md`） |
| 持仓中重启 90s | 止损唯一、无振荡（`docs/SCENARIO_LIVE_TEST_20260722.md` §7） |

**止损撤挂门槛：通过**（固定节奏 `cancel algo ↔ stop` 已消除）。

### 1.3 钉钉 critical

- 代码：`report_system_alert(..., level="紧急", immediate=True)`；`_call_dingtalk` 位置参数兼容 + `inspect.signature` 过滤 kwargs。
- **须你在群内肉眼确认**（本环境无法截手机屏）。

---

## 任务二：逐条复检

### A. 四条硬性原则

| 项 | 判定 | 证据 |
|----|------|------|
| 开仓永远先平后开 | **符合** | `_same_direction_entry_mode`→`FULL_REENTRY`；`_ensure_flat_before_open`；`reorder_batch_close_then_open` |
| 单仓不加仓 | **行为符合**；残留 Med | PYRAMID/PROFIT_ADD 忽略；`_add_to_position` no-op；`add_count` 字段残留 |
| 下单数量无状态纯函数 | **符合（加固）** | `compute_fixed_order_qty`；天文 qty 忽略；名义×0.85；开仓再裁 `avail×5×0.92` |
| 止损唯一写入=呼吸引擎 | **符合（统一总线）** | 经 `_sync_exchange_stop` / `_breath_resize_stop_on_tp`；开仓/接管调用总线但不得绕过 |

### B. Webhook

| 项 | 判定 | 证据 |
|----|------|------|
| 仅 4 action（+PING） | **符合** | `VALID_ACTIONS` |
| secret 主 / token 兼容 | **符合** | `app.py`；`TODO(remove-token)` 已写 |
| 60s 去重 | **产品维持** | 指纹含 price（≠纯 action+symbol）→ 已知 #2 |

### C. 呼吸止损

| 项 | 判定 | 证据 |
|----|------|------|
| 状态持久化 | **符合** | entry/atr/initial_stop/current_sl/best/phase/remaining → `_save_state` |
| 阶段一 / TP 底线 / 阶段二 ADX | **符合** | `breath_stop.py` |
| TP 成交后止损 qty 收缩 | **代码符合 / 实盘未取证** | `_breath_resize_stop_on_tp` → 已知 #3 |

### D. 状态清理

| 项 | 判定 | 证据 |
|----|------|------|
| 归零完整清零 | **符合** | 各 flat 路径 + **重启空仓** → `_reset_breath_ledger_on_flat` |
| 无半清理 | **符合（v15.5.15 起）** | 原重启空仓只清 qty/side 已修 |

### E. ATR 应急兜底

| 项 | 判定 | 证据 |
|----|------|------|
| 触发 / pause / resume | **符合（主路径）** | `evaluate_atr_emergency_degrade`；`/admin/resume` |
| 持仓不污染 open_atr | **符合** | `hold_skip_degrade` |
| 残留 | Low | 接管成功可自动清 ATR_DEGRADE 暂停 |

### F. 重启恢复

| 项 | 判定 | 证据 |
|----|------|------|
| recover + FORCE_ALIGN | **符合** | `recover_state_on_startup`；`report_force_align` |
| 持仓中重启不抖 | **符合（实盘）** | SCENARIO §7 |
| 残留 | Low | skip-worker hydrate 可暂载脏 entry |

### G. 防螺旋

| 项 | 判定 | 证据 |
|----|------|------|
| 仓位一致性 / fail-closed | **符合** | `POSITION_QUERY_FAILED` |
| TP 超时移交 | **符合** | 价未到不撤（v15.5.14） |
| HARD_SL_FAIL_ABORT×3 | **符合** | `_sync_exchange_stop` |
| API 重连退避 | **符合** | WS `1→×2→cap60` |

### H. 钉钉

| 项 | 判定 | 证据 |
|----|------|------|
| 告警可达 | **符合（代码）** | `dingtalk.py` 28 个 `report_*`（2 no-op stub） |
| `_call_dingtalk` | **符合** | 位置参数 + signature 过滤 |
| `HARD_SL_MISSING` 命名 | **命名不符、机制在** | 实发 `TV硬止损缺失` / `HARD_SL_FAIL_ABORT` |
| 群内确认 | **待你** | — |

---

## 任务三：交付物

### 3.1 部署 runbook

```bash
cd /home/trading/binance-engine
# 推荐：git pull origin main
# 或同步：position_supervisor_binance.py app.py dingtalk.py webhook_parser.py
#          breath_stop.py market_engine.py binance_client.py tv_seq.py

chown -R trading:trading /home/trading/binance-engine
# gunicorn 必须 -w 1 --threads 1 -b 127.0.0.1:5003
systemctl daemon-reload
systemctl restart binance-engine.service
sleep 8
curl -s http://127.0.0.1:5003/health   # version 须 = v15.5.17-incident-sticky

# 事故后恢复（仅当你确认后）：
# curl -s -X POST http://127.0.0.1:5003/admin/resume/ETHUSDT
```

### 3.2 已知问题

| # | 严重度 | 问题 | 建议 |
|---|--------|------|------|
| 0 | **P0 运营** | ETH 事故暂停；resume 前勿接 TV | 确认 health + 钉钉后再 resume |
| 1 | Med | `add_count`/加仓文案残留 | 下轮删死代码 |
| 2 | Low | 去重含 price | 改指纹或改文档 |
| 3 | Med | TP 成交→止损 qty 收缩未实盘取证 | 小仓或收紧 TP 后取证再放大 |
| 4 | Low | `token` 兼容仍在 | TV 全切 secret 后删 |
| 5 | Info | 钉钉需肉眼确认 | — |
| 6 | Info | SSH 密码曾出现在对话 | 验证后轮换 |
| 7 | Low | 接管可自动清 ATR_DEGRADE | 可改为纯人工 |
| 8 | Low | skip-worker hydrate 脏 entry | hydrate 后强制 REST entry |
| 9 | **Closed** | 天文 TV.qty → `-2019` + 钉钉脏预览 | `v15.5.16/17` + 事故报告 |

### 3.3 相关文档

- 本报告：`docs/handover_20260722_final.md`
- 事故：`docs/INCIDENT_20260722_HUGE_TV_QTY.md`
- 收尾清单：`docs/CHECKLIST_20260722_HUGE_QTY_WRAP.md`
- 场景/门禁：`docs/SCENARIO_LIVE_TEST_20260722.md` · `docs/PROD_GATE_v15.5.13.md` · `docs/WEBHOOK_E2E_LIVE_VERIFY_20260722.md`

### 3.4 访问权限

全部验证并 resume 决策完成后再单独讨论收回 SSH / 改密钥。

---

## 就绪总表

| 门槛 | 状态 |
|------|------|
| 止损无固定节奏撤挂 | ✅ 时间戳窗口已证 |
| 先平后开 / 单仓 / 呼吸 | ✅ |
| 空仓/重启账本干净 | ✅（含 v15.5.15） |
| 天文 qty / 预览同源 / 保证金裁剪 | ✅ 代码+单测；VPS 部署请再核对 |
| 钉钉 critical | ⏳ 待你确认 |
| TP 止损收缩实盘 | ⏳ 未取证 |
| **接下一笔真实 TV** | **❌ 暂停中 → 你确认后 resume** |

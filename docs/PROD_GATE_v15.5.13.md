# 生产门禁交付报告 · v15.5.13-prod-gate

日期: 2026-07-22  
版本: `BINANCE_VPS_VERSION = v15.5.13-prod-gate`

## 本地测试

| 套件 | 结果 |
|------|------|
| `test_tv_seq_collapse.py` | 5/5 OK（含乱序 CLOSE+OPEN 折叠） |
| `test_position_query_fail_safe.py` | 2/2 OK |
| `test_attribution_honest.py` | 4/4 OK |
| `test_restart_stop_and_tp_handoff.py` | 5/5 OK |
| `test_stop_idempotent_and_tp_levels.py` | 7/7 OK |

## 交付结论摘要

### 1. 重启窗口止损价异常（1886.53 ↔ 1910.18）— 已闭环

- **根因**: 二次 recover「跳过重复接管」仍点火哨兵，未 hydrate 状态 → 默认 `open_atr=30`、`current_sl=0` → 虚构止损 `entry−1.5×30=1886.53`，与正确接管止损 1910.18 互相撤挂。
- **修复**: v15.5.11（拒绝持仓期 ATR=30 虚构；skip-takeover 必须 hydrate；`_stop_write_blocked`；优先采纳交易所止损等），本轮随 v15.5.13 一并部署。
- **证据**: 此前 VPS 开仓 0.012 LONG → restart → 75s 单止损快照 `[1902.48]`，`ATR30_INVENT_HITS=[]`。

### 2. TP 重复挂单（Gemini 同类）— 曾存在风险，已修复

- **风险**: 超时撤单 → 误标 consumed → 无减仓证据却 clear → patch/nuclear 重挂。
- **修复**: `tp_levels_radar_handoff` 持久化；reconcile/spurious 路径禁止清 handoff；`_tp_level_consumed` 视 handoff 为已消费。
- **证据**: `test_restart_stop_and_tp_handoff.py`；主路径仅挂 TP1+TP2（`PLACE_TP_LEVELS=2`）。

### 3. `get_position()` 查询失败 — 本轮修复（原有 Gemini 同类隐患）

- **旧行为**: REST 失败且无缓存时返回 `None` → 上层当空仓清账本。
- **新行为**: 返回 `POSITION_QUERY_FAILED`；哨兵/空闲巡检/`_confirm_position_flat`/重启探测 fail-closed；钉钉限频告警。
- **附带**: 平仓清零补清 `watched_entry`，防旧 entry 污染下一笔。

### 4. tv_seq 1.0s 折叠

- 实测：`collapse_batch_for_execution` 对「OPEN 先到、CLOSE 后到」仍输出先平后开；重复 OPEN 只留最新。

### 5. 60s 去重含 price

- 产品维持现状。TV 若在 60s 内对同 symbol+action 发**不同 price**，当前会放行（不拦截）；同价重复拦截。

### 6. 旧逻辑清单

- 见 `docs/DELETED_LEGACY_LOGIC_v15.5.13.md`

## 部署检查清单

- [ ] GitHub `origin/main` 提交哈希: （部署后填）
- [ ] VPS `/health` version = `v15.5.13-prod-gate`
- [ ] 重启后空仓待命或正确接管；无 ATR=30 虚构止损
- [ ] 观察窗口 30–60min，禁止中途 rebuild

# 🛡️ 万亿战神 VPS 实盘检查清单（Cursor 开发自查专用）

> **币安** `eth-webhook-server` · **深币** `deepcoin-hft-server` 共用逻辑  
> **当前**：TV **v6.5.6** · VPS **`v16.3.1-dual-notify`** · sizing **RISK20_NOTIONAL5** · 规格 **币安单账户整合版(1)** · 通知 **TG全量+钉钉告警**  
> 运行 `python check_vps_logic.py` 做静态对账。

## 📌 核心原则（必须刻进代码）

| # | 原则 | 代码落点 |
|---|------|----------|
| 1 | **风险仓位**：名义=`权益×20%×5`；`qty=名义/价`（可选 SL/TV.qty 收紧） | `compute_fixed_order_qty()` · `_calc_vps_open_qty()` |
| 2 | **永久硬止损**：`\|TV.price−TV.stop_loss\|×1.15` 锚定成交价（**不分档**）；**禁止** 1.5×ATR 地板 | `atr_scenario.hard_stop_price` · `_ensure_frozen_hard_sl` |
| 3 | **独立雷达止损**：首次 0.85×TP1距 / 重入 1.00×TP1距 启动；激活臂 entry±0.5ATR；步进见 ADX 档 | `breath_stop.py` · `reentry_profiles` · `_sync_exchange_stop()` |
| 4 | TP **10/20/70**；盘口仅挂 **TP1+TP2**；TP3 **永不挂限价** | `LEG_TP_RATIOS` · `PLACE_TP_LEVELS=2` |
| 5 | **部分成交动态头寸**：任意 TP 切片成交 → REST/WS 实时总头寸 → 硬/雷达/剩余TP数量同步；失败 → **`trading_paused`** | `_schedule_partial_fill_resize` · `_atomic_resize_after_partial_tp` |
| 6 | 反转保护仅 `CLOSE_QUICK_EXIT` / `CLOSE_RSI_EXIT` → 市价全平 | `FLATTEN_ACTIONS` |
| 7 | 去重 60s · 挂单超时 5min · 90m ATR/ADX（对比用） | `SIGNAL_DEDUP_SEC` · `ORDER_TIMEOUT_SEC` |
| 8 | 实盘/重启与方向背离 → **FORCE_ALIGN** 先全平 | `_close_all(..., force_align=)` |
| 9 | secret 必须匹配环境配置（兼容旧字段 `token`） | `app.py` webhook |
| 10 | **ETH / XAU** 独立状态 | `symbol_config.py` · `SUPERVISORS` |
| 11 | TV 消息缓存 **1.0s** → 同窗**先平后开**；15s OPEN/CLOSE 铁律 | `collapse_batch_for_execution` |
| 12 | TP/硬/雷达 **订单标签** 持久化（SHA-256 `clientOrderId`） | `order_idempotency` · `_pending_order_tags` |
| 13 | 查单失败 **fail-closed**；未成交挂单硬上限 **5** | `ORDERS_QUERY_FAILED` · `MAX_OPEN_ORDERS_HARD_CAP` |
| 14 | **CAP_ALIGN 已废除**；改单失败 → **HARD_SL_FAIL_ABORT** | `report_hard_sl_fail_abort` |
| 15 | REST 单品种间隔 ≥**100ms**；持仓核对 ≈**30s**（WS优先）；`-1003` → 暂停该品种 | `REST_MIN_INTERVAL_SEC` · `_all_pos_ttl=30` · rate-limit hook |
| 16 | ATR≤0 或异常 → **拒本笔开仓** + 钉钉 | `check_atr_anomaly` · `_calc_vps_open_qty` |
| 17 | 档位配置外置 JSON；状态快照 30s；日志 `[OPS\|STATE\|ALERT\|AUDIT]` | `config/reentry_tiers.json` · `ops_log.py` |
| 18 | **重入双闸门**：TP1 从未成交 + `tier==2`（强趋势）；硬止/TV平/已重入1次一律禁 | `can_smart_reenter` · `evaluate_flat_for_reentry` |
| 19 | **平仓无菌盘**：撤光全部挂单（含蚂蚁单）→ REST 真实 qty 平仓 → 翻转则最高级暂停 | `_purge_all_defense_orders_on_flat` · `_close_all` · `_sweep_orphan_reverse_after_flat` |

---

## 严谨性三项（上线前）

| # | 项 | 阻塞? | 验证 |
|---|----|-------|------|
| 1 | 90m 边界与 TV 对齐 | **是** | `python check_90m_align.py --live` |
| 2 | Webhook `bar_time` | 否 | JSON 带 `bar_time`；乱序旧消息只记日志 |
| 3 | 防叠单铁律 | **是** | `python test_orders_dup_guard.py` · `test_risk_iron_v1591.py` |

---

## 模块二：开单计算（RISK20_NOTIONAL5）

```
风险资金 = 账户权益 × 20%
名义上限 = 账户权益 × 20% × 5 = 权益 × 1
qty = 名义上限 / entryPrice
# 可选：stop_loss 收紧；TV.qty soft-cap（天文忽略）
```

TV.stop_loss **只**作硬止损距离输入（×buffer）及 sizing 收紧，**不单独挂 TV 原价止损**。

---

## 模块四：三层防线（硬 + 雷达 + TP123）

| # | 检查项 | 值 |
|---|--------|-----|
| 4.1 | 永久硬止损 | `\|TV−SL\|×1.15` @ 成交价外侧（不分档） |
| 4.2 | 雷达激活臂 | entry ± **0.5** ×ATR（达启动线后上移） |
| 4.3 | 雷达步进 / 跟进 | 档位表 `config/reentry_tiers.json`（ETH/XAU ADX 三档） |
| 4.4 | TP 比例 / 档数 | **10/20/70** · `PLACE_TP_LEVELS=3` |
| 4.5 | 互斥 | `exit_ownership`：NONE / TP3_LIMIT / RADAR_STOP |
| 4.6 | 启动阈值 | 首次 **0.85×TP1距** · 重入 **1.00×TP1距**（按距离，非绝对价） |
| 4.7 | 切片成交 | 任意 `PARTIALLY_FILLED` → 按**当时实时总头寸**缩硬/雷达/剩余TP |
| 4.8 | 旧 85%/0.5/0.3/2.0 阶梯雷达 | **已删除生效路径** |

---

## 防死亡螺旋

| 规则 | 处理 |
|------|------|
| 查单失败 | fail-closed，禁止「没挂单就再挂」 |
| 本地标签未释放 | 绝对拒挂（即使交易所返回空） |
| 未成交挂单 ≥5 | 熔断拒挂 + `trading_paused` |
| 竞态双腿成交 | 强制对账 + **暂停品种** + 钉钉 |
| 部分成交同步失败 | 告警 + **暂停品种** |
| API 限流 -1003 | 退避 + **暂停品种** |
| 重复消息 | 60s 同 action+symbol+price 忽略 |
| 开仓前 | 强制清仓（先平后开）；失败 → CLOSE_THEN_OPEN_FAIL_ABORT |
| 平仓后盘口残留 | 多轮 purge；仍残留 → **暂停品种** |
| 超卖变反向 | 扫尾 + **最高级暂停** + 钉钉 |
| CAP_ALIGN | **已删除** |

---

## 生产就绪（三端对齐 + 等真 TV）

| # | 项 | 标准 |
|---|----|------|
| P1 | 本地 = GitHub = VPS | `git rev-parse HEAD` 三端一致 |
| P2 | health.version | `v16.3.1-dual-notify` · `notify.telegram_configured=true` |
| P3 | ETH/XAU 空仓无菌 | 持仓=0 · 挂单=0 · `trading_paused=false` · `api_monitor_only=false` |
| P4 | 钉钉 | 开仓/雷达/TP/平仓/重入/异常均可达 |
| P5 | 单元测试 | `test_radar_reentry`（含 TP1/tier 闸门）· dup_guard · risk_iron · api_monitor |
| P6 | 等真 TV | **禁止**再发测试 webhook 开仓；双品种待命收真实信号 |

---

## Cursor 自查命令

```bash
python check_vps_logic.py
python -m unittest test_risk_iron_v1591 test_orders_dup_guard test_defense_v1590 test_radar_reentry test_api_monitor_v1621
curl -s http://127.0.0.1:5003/health | python -m json.tool
# 期望 version: v16.3.1-dual-notify · trading_paused=false · notify.telegram_configured=true
# 双通道自检：
# curl -s -X POST http://127.0.0.1:5003/admin/notify_test -H 'Content-Type: application/json' -d '{"level":1}'
# curl -s -X POST http://127.0.0.1:5003/admin/notify_test -H 'Content-Type: application/json' -d '{"level":2}'
```

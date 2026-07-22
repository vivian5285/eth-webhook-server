# 🛡️ 万亿战神 VPS 实盘检查清单（Cursor 开发自查专用）

> **币安** `eth-webhook-server` · **深币** `deepcoin-hft-server` 共用逻辑  
> **当前**：TV **v6.5.6** · VPS **v15.5.3-rigor-checks** · sizing **RISK20_NOTIONAL5**  
> 运行 `python check_vps_logic.py` 做静态对账。

## 📌 核心原则（必须刻进代码）

| # | 原则 | 代码落点 |
|---|------|----------|
| 1 | **风险仓位**：风险=权益×20%；名义=权益×5；`min(风险/VPS止损距, 名义/价, TV.qty×(TV距/VPS距))` | `compute_fixed_order_qty()` · `_calc_vps_open_qty()` |
| 2 | **呼吸止损唯一写入**：initialStop=entry±1.5×ATR；不使用 TV `stop_loss` 作基准 | `breath_stop.py` · `_sync_exchange_stop()` |
| 3 | **呼吸阶段一**：0.75 ATR 步进 / 0.4 ATR 跟进（基准=initialStop） | `calculate_breath_stop()` |
| 4 | TP **30/30/40**；盘口只挂 **TP1+TP2**；余仓交阶段二 | `LEG_TP_RATIOS` · `PLACE_TP_LEVELS=2` |
| 5 | 订单监控只报告 TP 成交，**不直接改止损单**（通知引擎收缩） | `_breath_resize_stop_on_tp` |
| 6 | 反转保护仅 `CLOSE_QUICK_EXIT` / `CLOSE_RSI_EXIT` → 市价全平 | `FLATTEN_ACTIONS` |
| 7 | 去重 60s · 挂单超时 5min · 90m ATR/ADX | `SIGNAL_DEDUP_SEC` · `ORDER_TIMEOUT_SEC` · `market_engine` |
| 8 | 实盘/重启与方向背离 → **FORCE_ALIGN** 先全平 | `_close_all(..., force_align=)` |
| 9 | secret 必须 = `528586`（兼容旧字段 `token`） | `app.py` webhook |
| 10 | **ETH / XAU** 独立状态 | `symbol_config.py` · `SUPERVISORS` |
| 11 | TV 消息缓存 **1.0s** → 同窗**先平后开** | `collapse_batch_for_execution` |
| 12 | TP/止损 **订单 ID 持久化** | `_defense_order_ids` |
| 13 | 哨兵 **0.5s** | `SENTINEL_POLL_*=0.5` |
| 14 | **CAP_ALIGN 已废除**；改单失败 → **HARD_SL_FAIL_ABORT**；先平后开净场失败 → **CLOSE_THEN_OPEN_FAIL_ABORT** | `_trim` no-op · `report_hard_sl_fail_abort` · `report_close_then_open_fail_abort` |
| 15 | 90m **UTC epoch** 对齐；上线前 `check_90m_align.py --live` 与 TV 逐根对时 | `bucket_90m_open_ms` · `merge_30m_to_90m` |
| 16 | ATR≤0 或 < 近50根中位数×30% → **拒本笔开仓** + 钉钉 | `check_atr_anomaly` · `_calc_vps_open_qty` |
| 17 | 可选 `bar_time`：早于已处理 → 忽略不交易 | `enqueue_signal` · `webhook_parser` |

---

## 严谨性三项（上线前）

| # | 项 | 阻塞? | 验证 |
|---|----|-------|------|
| 1 | 90m 边界与 TV 对齐 | **是** | `python check_90m_align.py --live` → TV 90m 开盘时间完全重合；ATR/ADX 抽样误差 <5% |
| 2 | Webhook `bar_time` | 否 | JSON 带 `bar_time`；乱序旧消息只记日志 |
| 3 | ATR 异常兜底 | **是** | atr=0 / 过低中位数 → 拒单钉钉，非永久暂停 |

---

## 模块二：开单计算（RISK20_NOTIONAL5）

```
风险资金 = 账户权益 × 20%
名义上限 = 账户权益 × 5
VPS止损距 = |entry − initialStop|          # initialStop = entry ± 1.5×ATR
TV隐含距 = |TV.price − TV.stop_loss|
sl_adj = TV隐含距 / VPS止损距               # 缺 TV.stop_loss → 1.0
qty = floor( min(风险/VPS止损距, 名义/entry, TV.qty×sl_adj) )
```

TV.stop_loss **只**调 sl_adj，不挂盘。

---

## 模块四：呼吸止损（替代旧阶梯雷达）

| # | 检查项 | 值 |
|---|--------|-----|
| 4.1 | 初始止损 | **1.5** ×ATR |
| 4.2 | 步进 / 跟进 | **0.75 / 0.4** ATR（基准 initialStop） |
| 4.3 | TP1/TP2 底线 | 触及 1.35/2.5 ATR → 底线 **0.5 / 1.5** ATR |
| 4.4 | 阶段二 | 触及 **3.0** ATR → ADX 追踪 **1.2~2.5** ATR |
| 4.5 | 旧 85%/0.5/0.3/2.0 | **已删除生效路径** |

---

## 防死亡螺旋

| 规则 | 处理 |
|------|------|
| TP 限价未成交 | 取消 + 移交呼吸引擎 + 禁止重挂 |
| 重复消息 | 60s 同 action+symbol 忽略 |
| 开仓前 | 强制清仓（先平后开）；失败重试 1s/3s/6s → CLOSE_THEN_OPEN_FAIL_ABORT |
| 改单失败 | 重试 3 次 → HARD_SL_FAIL_ABORT 告警保持现状 |
| 方向不一致 | FORCE_ALIGN 先全平 + 钉钉 |
| TP3 | **不挂限价**；余仓由阶段二追踪退出 |
| CAP_ALIGN | **已删除**（禁止 VPS 自主减仓） |

---

## Cursor 自查命令

```bash
python check_vps_logic.py
python check_90m_align.py --live
curl -s http://127.0.0.1:5003/health | python -m json.tool
```

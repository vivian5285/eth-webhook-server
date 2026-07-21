# 🛡️ 万亿战神 VPS 实盘检查清单（Cursor 开发自查专用）

> **币安** `eth-webhook-server` · **深币** `deepcoin-hft-server-main` 共用逻辑  
> **当前**：TV **v6.5.6** · VPS **v15.0.5-order-persist** · sizing **RISK20_NOTIONAL5**  
> 运行 `python check_vps_logic.py` 做静态对账。

## 📌 核心原则（必须刻进代码）

| # | 原则 | 代码落点 |
|---|------|----------|
| 1 | **风险仓位**：风险资金=权益×20%；名义上限=权益×5；取 min(风险/止损距, 名义/价, TV.qty) | `compute_fixed_order_qty()` · `_calc_vps_open_qty()` |
| 2 | 硬止损 = webhook **`stop_loss` / `tv_sl` 原值** closePosition | `_tv_hard_sl_target()` · `_sync_exchange_stop()` |
| 3 | **阶梯雷达**：TP1 路程 **85%** 激活 → entry±n×0.5/0.3 ATR 跟进 | `compute_ladder_radar_sl()` · `_compute_ladder_sl()` |
| 4 | TP **30/30/40**，盘口**只挂 TP1+TP2**；TP3 永不挂限价 | `LEG_TP_RATIOS` · `PLACE_TP_LEVELS=2` |
| 5 | 平仓确认只对账+调止损，**不主动市价平仓** | `_handle_tv_reconcile()` |
| 6 | 反转保护 `CLOSE_QUICK_EXIT` / `CLOSE_RSI_EXIT` → 市价全平 | `FLATTEN_ACTIONS` |
| 7 | 去重 60s · 挂单超时 5min · ATR 5min 刷新 | `SIGNAL_DEDUP_SEC` · `ORDER_TIMEOUT_SEC` · `ATR_UPDATE_SEC` |
| 8 | 实盘/重启与 TV 方向背离 → **先全平** 对齐最新 TV + 钉钉 | `_enforce_tv_direction_or_flat` |
| 9 | token 必须 = `528586` | `app.py` webhook |
| 10 | **ETH / XAU** 独立状态 | `symbol_config.py` · `SUPERVISORS` |
| 11 | TV 消息缓存 **1.0s** → 同窗**先平后开**（平一次+最新开） | `collapse_batch_for_execution` |
| 12 | TP/止损 **订单 ID 持久化** | `_defense_order_ids` |

---

## 模块二：开单计算（RISK20_NOTIONAL5）

```
风险资金 = 账户权益 × 20%
名义上限 = 账户权益 × 5
理论数量 = min(风险资金/|开仓价-stop_loss|, 名义上限/开仓价, TV.qty)
qty      = floor(理论数量 × 1000) / 1000
```

示例：权益 1000U · 价 3300.5 · SL 3200.5 · TV.qty=12  
→ 风险仓 2.0 · 名义仓≈1.515 · **qty=1.514**

---

## 模块四：阶梯雷达

| # | 检查项 | 值 |
|---|--------|-----|
| 4.1 | 激活 | **85%** TP1 路程（非 tp1 绝对值×0.85） |
| 4.2 | 步进 / 跟进 | 从 entry 起 **0.5 / 0.3** ATR |
| 4.3 | TP1/TP2 底限 | **0.5 / 1.5** ATR |
| 4.4 | TP3 追踪 | **2.0** ATR |
| 4.5 | 激活价示例 | LONG 1800→tp1 1840.5 ≈ **1834.425** |

---

## 防死亡螺旋

| 规则 | 处理 |
|------|------|
| TP 限价未成交 | 取消 + 移交雷达 + 禁止重挂 |
| 平仓确认消息 | 只对账/调止损，不主动下平仓单 |
| 重复消息 | 60s 同 action+symbol+price 忽略 |
| 开仓前 | 强制清仓 |
| 改单失败 | 重试 3 次 → 告警保持现状 |
| 实盘/重启与 TV 方向不一致 | 先全平对齐 TV + 钉钉 |
| TP3 | 永不挂限价 |

---

## Cursor 自查命令

```bash
python check_vps_logic.py
curl -s http://127.0.0.1:5003/health | python -m json.tool
```

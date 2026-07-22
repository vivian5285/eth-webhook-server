# 真实TV首次LONG失败根因报告（币安单一账户）

时间：2026-07-22 **15:00:08–15:00:18 UTC**（钉钉约对应此时段）  
版本事故时：`v15.5.14-copy-tp-timeout`  
修复版：`v15.5.16-huge-qty-sync`（已部署；**ETH 仍人工暂停，未自动 resume**）

> Gemini 侧不在本仓库，需另行排查。以下仅币安。

---

## 一句话结论

**不是「sizing 算出 0.02 却下单 4.445」的执行脱节。**  
钉钉 0.02 是**上笔测试残留的 `tv_suggested_qty`** 在信号预览里被误用；真实下单路径按本笔巨大 TV.qty 正确算出 **notional 绑定 4.445**，随后被币安 **`-2019 Margin is insufficient`** 拒单。交易所**没有成交**。

---

## 原始时间线（VPS `logs/binance_brain.log`）

| UTC | 事件 |
|-----|------|
| 15:00:08.722 | Webhook LONG `price=1932.40` `qty=865680123`（天文数字） |
| 15:00:10.415 | **信号预览 sizing（bug）**：仍用旧 `TV.qty=0.02` → `bind=adjusted_tv_qty` → **qty=0.02**（钉钉播报来源） |
| 15:00:10.418 | `_apply_tv_sizing_params`：写入本笔 `TV.qty=865680123` |
| 15:00:17.954 | **真实开仓 sizing**：`risk=14.6883` `notional=4.4456` `tv′=5.78e8` → **`bind=notional` → qty=4.445** |
| 15:00:18.408 | `极速开仓: BUY 4.445 ETH` |
| 15:00:18.605 | **`[市价开仓失败] APIError(code=-2019): Margin is insufficient.`** |
| 15:00:18.606 | `开仓失败：市价单未成交` |

关键原始行：

```
开仓qty核算 | ... tv′=0.0200 | 生效=adjusted_tv_qty → qty=0.0200 | TV.qty=0.02   # 预览(脏)
开仓qty核算 | ... tv′=578599877 | 生效=notional → qty=4.4450 | TV.qty=865680123  # 下单(真)
[市价开仓失败] LONG 4.445 ETHUSDT: APIError(code=-2019): Margin is insufficient.
```

---

## 阶段一：0.02 vs 4.445

### 4.445 怎么来的？

权益≈1719U，名义上限=权益×5：

`qty_by_notional = 1719×5 / 1932.4 ≈ 4.445`

巨大 TV.qty 使 `adjusted_tv_qty` 失效为上限 → `min(risk≈14.7, notional≈4.445, 巨大) = 4.445`。  
**不是** qty1/qty2/qty3 拼出来的。

### 「脱节」根因（代码）

`handle_signal` 顺序：

1. `_record_tv_signal` → **先** `_calc_vps_open_qty`（此时还没 apply 本笔 qty）→ 钉钉用脏值  
2. 稍后才 `_apply_tv_sizing_params(payload)` → 写入 865680123  
3. `_open_position` → 再算一次 → 下单用 4.445  

位置：`position_supervisor_binance.py` `_record_tv_signal`（约原 1279 行）。

### 下单失败类型

**类型 1：下单请求本身被交易所拒绝**（`-2019`），并非「查询持仓失败当空仓」。无成交。

---

## 阶段二：ATR 降级

**本笔未触发 ATR 应急降级。**

同窗日志：

- `atr_source=vps atr=15.6005`
- `ATR核对 VPS=15.6005 TV=15.6005 差=0.0%`
- 当前 `trading_paused=false`（事故当时也未因本笔进入 ATR_DEGRADE 暂停）

你看到的 `atr_mismatch_streak_3 / TV隐含=23.45` **不是本笔币安开仓路径日志**（或与 Gemini/其它时段混淆）。币安本笔与 qty 异常**无因果关系**。

---

## 修复（v15.5.16-huge-qty-sync）

1. 信号预览 sizing **先** `_apply_tv_sizing_params` / `_apply_tv_sl_from_payload`，再算 qty → 钉钉与下单同源  
2. 检测天文 TV.qty 并打 `tv_qty_absurd` 警告  
3. 用 `availableBalance×杠杆×0.92` 再裁剪，降低 `-2019`  
4. 回归测试 `test_huge_tv_qty_sizing.py`（3/3 OK）

**ETH 已设 `trading_paused=true`**（`INCIDENT_20260722_HUGE_QTY_PENDING_RESUME`），修复部署后**不会自动恢复**；确认后需 `POST /admin/resume/ETHUSDT`。

---

## 当前状态

| 项 | 状态 |
|----|------|
| 交易所持仓 | 空（无成交） |
| ETH trading_paused | **true（人工暂停中）** |
| health.version | 应为 `v15.5.16-huge-qty-sync` |
| 自动开仓 | **已阻断，待你确认后再 resume** |

---

## Gemini

请在 Gemini 仓库按同类清单查：sizing 预览是否脏读、下单 raw request/response、「未检测到持仓」是拒单还是查询失败。本仓库无法代查。

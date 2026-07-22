# 遗留问题闭环：R3 文案 + TP超时告警（v15.5.14）

## 一、平仓 reason 仍带 R3 — 根因与修复

### 根因
`regime`（1–4）**仍保留在系统内部**，用于兼容旧字段、TP 分腿比例回退等逻辑判断，**算仓本身走 RISK20，不依赖档位名**。

用户可见泄露路径（已修）：
1. `_format_close_extra()` 拼接 `TV档位 R{regime}` → 进入 `_close_all(reason=...)`  
2. `_report_flat_close` 的 `verify_note` 拼接 `TV档位 R{...}`  
3. `dingtalk.get_regime_name()` 返回 `内部兼容编号 R{n}`  
4. 平仓/接管/智能筛选钉钉字段「TV内部编号」「内部编号」等  

### 修复原则
- **内部**：继续保存/使用 `regime` 数值  
- **用户可见**：一律展示 RISK20 算仓模式；禁止 `R1`–`R4` /「TV档位」进入 reason、标题、钉钉字段  

### 文案证据（模拟平仓主题，修复后）

```
输入 reason 故意带旧残留: "WEBHOOK_E2E_LIVE_TEST_CLOSE | TV档位 R3"
→ _classify_close(close_type=quick_exit) 标题:
  「反转保护平仓：WEBHOOK_E2E_LIVE_TEST_CLOSE」
→ 断言: 标题/主题中无 R1-R4、无「TV档位」
```

单测：`test_copy_and_tp_timeout.py` → `TestNoRegimeInUserCopy` 3/3 OK  

`_format_close_extra(..., regime=3)` 输出示例：  
` | TV方向 LONG | ATR 14.00 | TV价 1928.01 | TV盈亏 -0.12%`（**无 R3**）

---

## 二、「TP未成交转雷达」— 真实逻辑说明

### 结论（对应你的二选一）

**不是「去重拦住了撤单」。**  
钉钉 `title dedup(300s)` **只抑制重复播报**，不阻止撤单代码执行。

**也不是纯文案空喊。** 旧逻辑在挂单满 `ORDER_TIMEOUT_SEC`（默认 300s）后会：
1. 调用 `_cancel_tp_level_if_still_open`  
2. **无论撤单是否真正净场**，都标记 `tp_levels_consumed` + `tp_levels_radar_handoff`  
3. 若 `cancel_order` 返回真（含「旧 orderId 已不存在」假阳性）→ 弹出「TP未成交转雷达」  

E2E 观察窗矛盾解释：
- 开仓约 10:58，约 5 分钟后（11:03）超时路径触发  
- 当时 **现价远未到 TP1（1953）**，属正常等待  
- 告警文案却说「已取消移交」；同时盘口采样仍见 TP —— 符合「假阳性撤单成功 / 或撤后被其它路径补挂 / 或告警与盘口不同步」的旧缺陷组合  
- 11:03 / 11:08 的 `DingTalk title dedup` = **第二次告警被去重**，不是撤单被去重  

### 修复后策略（更合理、去噪音）

| 条件 | 行为 |
|------|------|
| 挂满 timeout，但现价**未进** TP 触及区 | **不撤、不告警**（正常等待） |
| 现价已触及仍超时未成交 | 撤单 → **复查盘口** → 确认已无该档才 handoff + 告警 |
| 撤单后盘口仍在 | 告警「TP超时撤单未净」，**不谎称已转雷达** |

新告警标题：
- 成功：`TP超时已撤单·改由呼吸止损`  
- 失败：`TP超时撤单未净`  
- **删除**易误解的「TP未成交转雷达」成功文案  

单测：`TestTpTimeoutGate` — 价未到不撤 / 价已到才撤 — 2/2 OK  

---

## 三、验收勾选

- [x] R3/档位用户可见路径清理 + 模拟平仓文案证据  
- [x] TP 超时真实逻辑说清 + 价未到不撤 + 文案改准  
- [x] 版本：`v15.5.14-copy-tp-timeout`

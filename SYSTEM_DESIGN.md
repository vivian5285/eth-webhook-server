# ETH Webhook Trading System - 系统设计文档

> 本文档用于长期维护和跨对话上下文对齐，记录当前系统的目标、架构、分层职责、核心链条和实现状态。

---

## 1. 项目目标

**核心目标**：构建一套**稳定、可维护、风控优先**的 ETH 永续合约自动化交易系统，适合个人资金 + 客户资管使用。

**核心理念**：
- 风控 > 进攻（每日回撤熔断 + 防回吐优先）
- 清晰分层架构（避免逻辑混乱）
- 混合止盈模式（TP1/TP2 落袋为安 + TP3 留仓博取）
- 强状态一致性（内存状态 + Binance 实际持仓自动对账）
- 支持人工干预检测（实盘中手动操作后系统能自动同步）

---

## 2. 整体架构（分层设计）
┌─────────────────────────────────────────────────────────────┐
│                        入口层 (Entry)                        │
│                        app.py                               │
│   - Webhook 接收信号                                        │
│   - 快速响应 + 后台处理                                     │
└───────────────────────────────┬─────────────────────────────┘
│
┌───────────────────────────────▼─────────────────────────────┐
│                    编排层 (Orchestrator)                     │
│              position_supervisor.py                         │
│   - 信号分发 (Long/Short/Close)                             │
│   - 每日回撤熔断前置检查                                    │
│   - 强制对账 (Force Reconcile)                              │
│   - 通知聚合                                                │
└───────────────────────────────┬─────────────────────────────┘
│
┌───────────────────────┼───────────────────────┐
│                       │                       │
┌───────▼────────┐    ┌─────────▼─────────┐    ┌───────▼────────┐
│  执行层         │    │   监控层          │    │   风控层        │
│ order_executor │    │  tp_monitor       │    │ risk_manager   │
│ - 开仓/全平     │    │ - 人工变化检测    │    │ - 每日回撤熔断  │
│ - TP1/TP2/TP3   │    │ - TP1 命中触发    │    │                │
│ - 移动止损      │    │   move_to_breakeven│    │                │
└───────┬────────┘    └─────────┬─────────┘    └────────────────┘
│                       │
┌───────▼───────────────────────▼─────────────────────────────┐
│                     状态管理层                               │
│                  position_manager.py                        │
│   - 内存持仓状态 (side, qty, entry_price, TP levels)        │
│   - TP3 限价单状态管理                                      │
│   - 提供统一查询接口                                        │
└─────────────────────────────────────────────────────────────┘
│
┌───────────────────────────────▼─────────────────────────────┐
│                     API 封装层                               │
│                  binance_client.py                          │
│   - open_market_order / close_position / place_limit_order  │
│   - get_usdt_balance / get_position / get_current_price     │
└─────────────────────────────────────────────────────────────┘

---

## 3. 各模块详细职责

### 3.1 app.py（入口层）
- 负责接收 TradingView Webhook 请求
- 快速返回 202 Accepted
- 将信号丢到后台线程处理（`handle_signal_in_background`）
- 启动时执行 `startup_tasks()`（强制对账 + 启动 TPMonitor）
- 提供 `/status`、`/reconcile` 等管理接口

### 3.2 position_supervisor.py（编排层 - 核心大脑）
**职责**：
- 接收来自 `app.py` 的信号
- **前置风控检查**（调用 `risk_manager` 判断是否允许开新仓）
- 调用 `order_executor` 执行实际交易
- 提供 `force_reconcile()` 方法，统一处理内存与 Binance 状态对齐
- 聚合通知逻辑

**关键方法**：
- `handle_long_signal()`
- `handle_short_signal()`
- `handle_close_signal()`
- `force_reconcile()`
- `is_new_entry_allowed()`
- `notify_open_success()`

### 3.3 order_executor.py（执行层）
**职责**：
- 真正执行下单、撤单、平仓操作
- 实现混合止盈逻辑（TP1/TP2 市价平 + TP3 限价单）
- 实现 `move_to_breakeven()`
- 调用 `binance_client` 完成实际交易
- 更新 `position_manager` 状态

**当前核心流程**：
1. 撤销旧 TP3 限价单
2. 全平旧仓位（如有）
3. 计算风险仓位（使用真实 USDT 余额）
4. 市价开新仓
5. 计算 TP/SL 价格
6. 挂出 TP3 限价单
7. 更新内存状态

### 3.4 tp_monitor.py（后台监控层）
**职责**：
- 独立线程运行（每 3 秒检查一次）
- 检测人工仓位变化（节流 8 秒）
- 检测 TP1 是否命中 → 调用 `order_executor.move_to_breakeven()`
- 触发 `position_supervisor.force_reconcile()`

### 3.5 position_manager.py（状态管理层）
**职责**：
- 作为**唯一真相来源**维护内存持仓状态
- 管理 TP3 限价单标记
- 提供 `original_qty`、`current_qty` 等辅助查询
- 使用线程锁保证状态一致性

### 3.6 risk_manager.py（风控层）
**职责**：
- 维护每日峰值权益
- 判断是否触发每日回撤熔断
- 为 `position_supervisor` 提供风控决策依据

### 3.7 binance_client.py（API 封装层）
**职责**：
- 封装 Binance Futures API
- 提供统一方法：开仓、平仓、挂限价单、查询余额、查询持仓等
- 目前已支持 `get_usdt_balance()` 用于真实风险计算

---

## 4. 核心链条（数据流）说明

### 4.1 开新仓链条
TradingView Alert
→ app.py /webhook
→ position_supervisor.handle_xxx_signal()
→ risk_manager 检查每日回撤
→ order_executor.open_position()
→ binance_client.open_market_order()
→ position_manager.set_position()
→ 通知

### 4.2 TP1 命中 + 移动止损链条
tp_monitor (后台线程)
→ 检测到持仓数量明显减少（接近 TP1 比例）
→ order_executor.move_to_breakeven()
→ position_manager 更新止损价格（当前为通知，未来可扩展真实改单）
text

### 4.3 人工操作检测链条
tp_monitor (后台线程)
→ 对比 memory vs Binance 实际持仓
→ 发现差异 → 更新 position_manager
→ 调用 position_supervisor.force_reconcile()

### 4.4 强制对账链条
position_supervisor.force_reconcile()
→ 对比内存持仓与 Binance 实际持仓
→ 不一致时以 Binance 为准更新内存
→ 清理无效 TP3 状态

---

## 5. 当前实现状态（2026-06-14）

| 模块 | 状态 | 备注 |
|------|------|------|
| 分层架构 | ✅ 已完成 | 职责清晰 |
| 每日回撤熔断 | ✅ 已完成 | 前置检查生效 |
| 强制对账 | ✅ 已完成 | 启动 + 人工操作后自动触发 |
| 混合止盈框架 | ✅ 已完成 | TP3 限价单可挂出 |
| TP1 命中检测 | 基本可用 | 基于数量减少的启发式判断 |
| move_to_breakeven | 框架完成 | 目前为通知，真实改单待扩展 |
| 真实权益风控 | ✅ 已完成 | 已接入 `get_usdt_balance()` |
| TP3 成交检测 | 待完善 | 目前仅标记状态，未跟踪 order_id |
| 状态持久化 | 未做 | 目前仅内存，重启后需对账恢复 |

---

## 6. 后续优化优先级建议

1. **高优先**：完善 TP1/TP2 真实命中后的市价平仓逻辑
2. **高优先**：`move_to_breakeven()` 实现真实止损单修改
3. **中优先**：TP3 限价单 order_id 跟踪与成交检测
4. **中优先**：参数外部化（config.py）
5. **低优先**：增加更多风控维度

---

**文档维护说明**：  
本文件应随系统演进持续更新。每次重大架构调整后，请同步修改本文件关键章节。

最后更新时间：2026-06-14

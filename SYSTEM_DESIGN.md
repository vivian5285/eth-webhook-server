# ETH Webhook Trading System - 系统设计文档

> **⚠️ 历史文档**：下文 2026-06-14 的 profit_taker / 40-40-20 分层设计 **已被 superseded**。  
> **当前生产架构**见 [`README.md`](README.md)：**TV v6.5.6** · **VPS v14.0.0-fixed20-ladder** · 固定 **20%×5** 仓位 · 阶梯雷达 · `position_supervisor_binance.py` 唯一大脑。

---

## 当前有效架构（2026-07 · v14.0.0-fixed20-ladder）

```
TradingView v6.5.6 Alert
        ↓
app.py (网关 + health: EQUITY_20PCT_X5 / fixed_5)
        ↓
position_supervisor_binance.py   ← 唯一生产大脑
├── 固定仓位：权益 20% × 5x
├── TP 30/30/40，只挂 TP1+TP2
├── stop_loss closePosition + 阶梯 radar (85% 激活)
├── RECONCILE 对账 / FLATTEN 快平
└── dingtalk.report_tv_reconcile
```

静态自查：`python check_vps_logic.py` · 清单：`docs/VPS实盘检查清单.md`

---

## 以下为历史设计存档（仅供参考，勿按此部署）

> 本文档记录 **早期** 系统的目标、架构、分层职责和实现状态（2026-06-14 更新）。

## 1. 项目目标

构建一套**稳定、可维护、风控优先**的 ETH 永续合约自动化交易系统，适合个人 + 客户资管使用。

**核心理念**：
- 风控 > 进攻（每日回撤熔断 + 防回吐优先）
- **VPS 完全接管 40/40/20 自主 scale-out**
- 监督层主动对齐最新 TV 信号方向
- 清晰分层架构 + 强状态一致性
- 支持人工干预智能处理

## 2. 整体架构（当前最终架构）

```
TradingView Alert
        ↓
app.py (入口层 + Secret 校验)
        ↓
# 编排 + 监督层（生产唯一）：position_supervisor_binance.py
# （遗留 position_supervisor.py 已删除）
    ├── 记录最新 TV 信号
    ├── 先平后开（同/反向一致）
    ├── 主动检查并强制对齐最新 TV 方向
    ├── 增强版 force_reconcile（真实对比 + 修复）
    └── 统一详细决策推送
        ↓
    ┌───────────────────────┐
    │ profit_taker.py       │  ← 核心执行层（VPS完全接管）
    │ - 40/40/20 自主减仓    │
    │ - TP1 后移保本         │
    │ - 显著加仓重算 TP      │
    │ - TP 距离监控 (18-50USD)│
    │ - 定期调用对齐检查     │
    └───────────────────────┘
        ↓
order_executor.py（仅负责开仓 + 全平 + 初始 SL）
        ↓
binance_client.py（带简单重试）
```

## 3. 关键改进点（相比原始设计）

- **从“混合 TP3 限价”演进为 “VPS 完全接管 40/40/20 市价 scale-out”**
- **监督层从被动编排 → 主动对齐最新 TV 信号**
- **profit_taker 成为价格监控 + 执行的核心**
- **所有关键决策都有美观、参数完整的钉钉推送**
- **增强了对账真实对比 + 自动修复能力**

## 4. 当前实现状态

| 模块 | 状态 | 备注 |
|------|------|------|
| VPS 完全接管 40/40/20 | ✅ 已完成 | profit_taker 完全负责 |
| 监督层主动方向对齐 | ✅ 已完成 | check_and_align_with_latest_signal |
| 增强版强制对账 | ✅ 已完成 | 真实对比 + 自动修复 |
| 统一详细决策推送 | ✅ 已完成 | emoji + 参数完整格式 |
| TP 距离监控 (18-50 USD) | ✅ 已完成 | 异常告警 |
| Webhook Secret 校验 | ✅ 已完成 | app.py |
| Binance API 简单重试 | ✅ 已完成 | binance_client |
| 异常补偿机制 | 部分 | 建议继续加强 |

**文档最后更新**：2026-06-14

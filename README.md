# ETH Webhook Trading Server（VPS 完全接管版）

基于 TradingView Webhook + Binance 的 ETH 永续合约自动化交易系统。

**当前架构核心**：**VPS 完全接管 40/40/20 自主 scale-out** + **监督层主动对齐最新 TV 信号**。

## 项目简介

本系统采用清晰分层架构，强调**风控优先 + 防回吐 + 状态一致性**，适合个人资金与客户资管使用。

主要特性：
- VPS 完全接管 40/40/20 自主市价减仓（不再依赖策略内部 TP）
- 监督层主动检查并强制对齐最新 TV 信号方向
- 显著人工加仓自动重算 TP123 并收紧
- 增强版强制对账（真实对比 + 自动修复）
- 统一美观、参数完整的钉钉详细决策推送
- TP 距离监控（目标 18-50 USD 小波段）
- Webhook Secret 校验 + Binance API 简单重试

## 系统架构（最终版）

```
TradingView Alert
        ↓
app.py（入口 + Secret 校验）
        ↓
position_supervisor.py（编排 + 监督层）
    ├── 记录最新 TV 信号
    ├── 永远先平后开
    ├── 主动检查并强制对齐最新 TV 方向
    ├── 增强版 force_reconcile
    └── 统一详细决策推送
        ↓
profit_taker.py（核心执行层 - VPS完全接管）
    ├── 40/40/20 自主市价减仓
    ├── TP1 命中后自动移保本
    ├── 显著加仓重算 TP + 收紧
    ├── TP 距离监控（18-50 USD）
    └── 定期触发对齐检查
        ↓
order_executor.py（仅负责开仓、全平、初始 SL）
        ↓
binance_client.py（带重试）
```

## 快速开始

### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置环境变量
创建 `.env` 文件：
```env
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
WEBHOOK_SECRET=your_webhook_secret
DINGTALK_WEBHOOK=your_dingtalk_webhook
DINGTALK_SECRET=your_dingtalk_secret
```

### 3. 启动服务
```bash
python3 app.py
```

### 4. TradingView Webhook 信号格式
```json
{
  "action": "LONG",
  "atr": 45.6,
  "secret": "your_webhook_secret"
}
```

支持的 `action`：
- `LONG` / `SHORT`：入场（无论同反向都会先平后开）
- `CLOSE`：保护性全平（reason 可选）

## 核心机制说明

### 1. VPS 完全接管 40/40/20
- 开仓后只挂初始止损单
- `profit_taker` 后台负责：
  - TP1 达到 → 市价平 40% + 自动移保本
  - TP2 达到 → 市价平 40%
  - 剩余 20% 作为 runner

### 2. 监督层主动对齐
- 定期检查当前持仓方向是否与**最新 TV 信号**一致
- 不一致时自动先平后开，强制对齐最新信号方向

### 3. 人工干预智能处理
- 检测到显著加仓（>15%）→ 自动重算 TP123 + 收紧 + 重新挂 SL + 详细推送
- 普通减仓 → 保持原 TP 计划，仅更新数量

### 4. 详细决策推送
所有关键行为（开仓、减仓、重算、对账、距离异常等）都会推送**带 emoji + 参数完整**的钉钉消息。

## 项目结构

```
eth-webhook-server/
├── app.py                    # 入口服务（含 Secret 校验）
├── position_supervisor.py    # 编排 + 监督层（主动对齐）
├── profit_taker.py           # 核心执行层（VPS完全接管）
├── order_executor.py         # 执行层（开仓/全平/初始 SL）
├── position_manager.py       # 状态管理
├── risk_manager.py           # 风控（每日回撤熔断）
├── binance_client.py         # Binance API 封装（带重试）
├── dingtalk.py
├── config.py
└── README.md
```

## 注意事项

- 建议配合 **one-way 持仓模式** 使用
- 实盘前请先在 testnet 充分验证（尤其是显著加仓重算和方向对齐场景）
- 客户资管账户建议使用更保守参数
- TP 距离监控目前为告警模式（非硬限制）

## 后续优化方向

- 完善异常重试与补偿机制
- Runner 增加轻量 trailing
- 参数外部化与热更新
- 更完善的结构化日志

---

**最后更新时间**：2026-06-14  
**当前版本**：VPS 完全接管 40/40/20 + 监督层主动对齐模式

# ETH Webhook Trading Server

基于 TradingView Webhook + Binance 的 ETH 永续合约自动化交易系统（混合止盈模式）。

## 项目简介

本系统采用**清晰分层架构**设计，将信号接收、风控编排、交易执行、后台监控、状态管理完全解耦，便于维护和扩展。适合个人 + 客户资管使用，强调**风控优先 + 防回吐**。

## 系统架构（分层设计）
app.py (入口层)
↓
position_supervisor.py (编排层)
├── 信号分发 (handle_long/short/close)
├── 每日回撤熔断检查
└── 强制对账
↓
order_executor.py (执行层)
├── 开仓 / 全平
├── TP1/TP2/TP3 混合止盈管理
└── 移动止损
↓
binance_client.py (API 封装层)

**后台监控层**：`tp_monitor.py`（独立线程运行）
**状态管理层**：`position_manager.py`
**风控层**：`risk_manager.py`

## 各模块职责

| 模块 | 职责 | 说明 |
|------|------|------|
| `app.py` | 入口 + 路由 | 接收 TradingView Webhook，快速响应 |
| `position_supervisor.py` | 编排 + 风控 | 信号分发、每日回撤熔断、强制对账、通知 |
| `order_executor.py` | 交易执行 | 市价开仓、设置 TP1/TP2/TP3、移动止损、全平 |
| `tp_monitor.py` | 后台监控 | 人工仓位变化检测、TP1 命中触发移动止损 |
| `position_manager.py` | 状态管理 | 内存持仓 + TP3 限价单状态管理 |
| `risk_manager.py` | 风控 | 每日回撤熔断 |
| `binance_client.py` | Binance API 封装 | 开仓、平仓、挂单、余额查询等 |
| `dingtalk.py` | 通知 | 钉钉机器人消息发送 |

## 核心特性

- **混合止盈模式**：TP1/TP2 市价分批止盈 + TP3 限价单留仓
- **每日回撤熔断**：达到阈值自动拒绝新开仓
- **强制对账机制**：启动时 + 人工操作后自动对账
- **人工操作检测**：后台自动识别手动开平仓并同步状态
- **移动止损**：TP1 命中后自动移动至保本
- **风险控制**：基于真实账户权益计算仓位

## 快速开始

### 1. 安装依赖
2. 配置环境变量
创建 .env 文件：
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
WEBHOOK_SECRET=your_webhook_secret
DINGTALK_WEBHOOK=your_dingtalk_webhook
3. 启动服务
python3 app.py
# 或使用 systemd
sudo systemctl start eth-webhook.service
4. 系统检查
python3 check_system.py
接口方法说明/webhookPOSTTradingView 信号入口/reconcilePOST手动触发强制对账/statusGET查询系统当前状态
TradingView Webhook 信号格式示例
{
  "action": "LONG",
  "atr": 45.6,
  "secret": "your_webhook_secret"
}
支持的 action：LONG、SHORT、CLOSE
注意事项

系统默认使用 ETHUSDT 永续合约
建议配合 one-way 持仓模式使用
实盘前请先在测试网充分验证
客户资管账户建议使用更保守的参数
项目结构
eth-webhook-server/
├── app.py                    # 入口服务
├── position_supervisor.py    # 编排 + 风控层
├── order_executor.py         # 交易执行层
├── tp_monitor.py             # 后台监控
├── position_manager.py       # 状态管理
├── risk_manager.py           # 风控
├── binance_client.py         # Binance API 封装
├── dingtalk.py
├── config.py
├── check_system.py           # 系统检查脚本
└── README.md
后续优化方向

完善 TP1/TP2 真实命中后的市价平仓逻辑
move_to_breakeven() 真实止损单修改
TP3 限价单 order_id 跟踪与成交检测
参数外部化配置

---

### 保存后执行：

```bash
chmod +x README.md

```bash
pip install -r requirements.txt

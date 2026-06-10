# ETH Webhook 量化交易系统（VPS 智慧大脑版）

本项目是基于 TradingView + Binance + VPS 的 ETH 永续合约自动化交易系统。  
核心目标是让 **VPS 成为智慧大脑**，实现方向信号接收 + TP123 自主监控 + 分批止盈（30%/30%/40%）。

---

## 一、项目结构
eth-webhook-server/
├── app.py                    # 主服务（Flask Webhook + 启动 TP 监控）
├── binance_client.py         # 所有币安 API 操作（开仓、平仓、部分平仓、账户快照）
├── tp_monitor.py             # TP123 后台监控线程（智慧大脑核心）
├── position_manager.py       # 持仓持久化 + TP 触发状态记录
├── tp_manager.py             # TP 价格计算（返回真实出场价格）
├── dingtalk.py               # 钉钉美化推送
├── config.py                 # 配置加载
├── .env                      # 环境变量（API Key、钉钉密钥等）
├── requirements.txt
├── start.sh                  # 传统启动脚本
├── eth-webhook.service       # systemd 服务配置（推荐）
└── README.md

---

## 二、核心文件职责

| 文件                  | 职责                                                                 | 是否必须 |
|-----------------------|----------------------------------------------------------------------|----------|
| `app.py`              | 接收 TradingView Webhook、处理信号、启动 TP 监控线程                  | 是       |
| `binance_client.py`   | 封装所有币安操作（开多/开空、全平、部分平仓、账户快照）               | 是       |
| `tp_monitor.py`       | 后台持续监控价格，达到 TP1/TP2/TP3 时执行分批止盈                     | 是       |
| `position_manager.py` | 持久化当前持仓信息 + TP 价格 + 已触发档位                             | 是       |
| `tp_manager.py`       | 计算真实的 TP 出场价格（非倍数）                                      | 是       |
| `dingtalk.py`         | 发送美化版钉钉通知                                                    | 是       |

---

## 三、启动顺序（推荐使用 systemd）

### 方式一：使用 systemd（推荐，生产环境）

```bash
# 1. 确保已创建 systemd 服务
sudo systemctl daemon-reload
sudo systemctl enable eth-webhook.service
sudo systemctl start eth-webhook.service

# 2. 查看状态
sudo systemctl status eth-webhook.service

# 3. 查看实时日志
sudo journalctl -u eth-webhook -f

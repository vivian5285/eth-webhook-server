# 影子策略引擎（Strategy Engine）

2026-08-17 新增。探索"VPS 本地拉K线自算指标出信号"这条路线的第一步——**先照抄
TradingView 上跑的 Pine 策略，纯影子模式跑一段时间，跟真实 TV 信号做对比**，
不是要现在就替换 TV 下单。这个服务本身没有下单能力，也不需要有。

## 设计原则：完全独立、零凭证

- 不 import 任何账户代码（`position_supervisor_binance.py`/`binance_client.py` 等一概不碰）
- 不需要任何币安 API Key——K线是公开数据（`https://fapi.binance.com/fapi/v1/klines`），
  自己用 stdlib `urllib` 直接打公开端点，不认证
- 只写自己的 sqlite（`data/shadow.db`），不触碰任何账户的状态文件/日志
- 一个实例服务全部品种，不用像 B/C/D 那样按账户各跑一份——同一品种同一周期
  同一策略，信号跟哪个账户执行无关

即便这个服务写出脏数据、逻辑跑飞、进程挂掉，交易主链路（B/C/D 三账户）**不受
任何影响**——这是架构上刻意做到的隔离，不是靠"记得别碰"这种约定。

## 模块

| 文件 | 职责 |
|---|---|
| `klines.py` | 公开K线拉取（含分页突破单次1500根上限）+ 任意周期合成（币安没有原生90m/45m/50m/150m，从5m或1m源周期按UTC桶对齐合成，规则跟 `market_engine.py` 的90m合成一致：桶内数据不齐全就不产出，避免用未收盘K线算信号） |
| `indicators.py` | Wilder ATR/ADX（照搬 `market_engine.py` 公式，口径跟系统里已有概念一致）+ SMA/EMA/RSI |
| `strategies/` | 策略注册表，每个策略一个模块，统一接口 `generate_signal(bars, params) -> dict | None`，返回字段跟 TV webhook payload 同构 |
| `symbol_registry.py` | 每个品种：策略名 + 图表周期 + 参数——**目前全部品种都是占位的 `_template`（双EMA交叉），等用户提供每个品种真实的 Pine 脚本资料后逐个替换** |
| `shadow_log.py` | SQLite 存储：`shadow_signals`（每次算出的信号）+ `shadow_positions`（模拟持仓生命周期，配对出pnl） |
| `backtest_stats.py` | 纯函数：胜率/盈亏比/最大回撤/权益曲线，回测和实时影子共用同一套口径 |
| `live_runner.py` | 常驻循环，每分钟检查各品种是否有新收盘K线，产出信号写入 `shadow_log`（`run_type='live'`） |
| `backtest_runner.py` | 按需触发的历史回测，逐bar喂策略（无lookahead），结果写入 `shadow_log`（`run_type='backtest'`） |

## 回测口径的简化说明（诚实告知，不是精确盈亏模拟）

- 止损/止盈用K线最高/最低价粗略判断是否命中，无法得知一根K线内的先后顺序，
  保守假设"止损先触发"
- 只用 TP1 作为止盈退出，不模拟真实执行链路的 TP1/TP2/TP3 分批止盈 + 雷达
  追踪止损那一整套
- 权益曲线是逐笔 pnl% 累加，不模拟仓位大小/复利/滑点/手续费

这是"策略方向对不对、赢面好不好"的快速评估工具。真实策略数据接进来后，如果
需要更贴近实盘的精确回测，再针对性升级（比如接入前面 ETH 反手实测出来的
真实滑点数据）。

## 部署

- VPS 路径：`/root/strategy-engine/`（独立 venv，`requirements.txt` 目前只需要
  Python 标准库，无第三方依赖）
- systemd：`strategy-engine.service`，`ExecStart` 跑 `live_runner.py`
- Dashboard（统一面板）新增的"策略/回测"区块只读 `data/shadow.db`（路径见
  `dashboard/server.py` 里的 `SHADOW_DB_PATH`），"跑回测"这个唯一的写操作走
  跟 `close_position` 一样的 subprocess 调用模式——不需要两个服务共享代码/网络接口

## 接入真实策略的步骤

用户提供某个品种的真实 Pine 脚本资料（策略逻辑 + 图表周期 + 实际生效参数）后：

1. 在 `strategies/` 下新增一个模块，照抄真实策略逻辑，实现 `generate_signal(bars, params)`
2. 在 `strategies/__init__.py` 的 `STRATEGIES` 里登记
3. 把 `symbol_registry.py` 里这个品种的 `strategy`/`timeframe`/`params` 改成真实值

不需要改 `live_runner.py`/`backtest_runner.py`/`shadow_log.py`/dashboard 任何一行。

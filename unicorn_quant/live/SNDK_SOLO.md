# SNDK单票方案（代号 sndk_solo）

宝贝自己的 C 账户只跑这一个方案，和方案F、趋势A、chan_resid、擂台等以前的方案完全分开。
聊天里说"SNDK单票"/"单票方案"就是指它。

## 命名规则

`SNDK单票方案 <版本>·<模式>·<账户>`，例如 **SNDK单票方案 v1·最高收益模式·C账户**（当前实盘）。

- **版本号**：交易规则（进出场、止损、周期、方向）变了才升，v1 → v2 …；每次升版本先回测，写进 research.json，再改 `VERSION`。
- **模式**：同一版本规则下的仓位方式，用开关文件切换，不升版本号。
- **账户**：目前只有 C（binanceC）。

## v1 规则（2026-10-31 上线）

| 项目 | 内容 |
|---|---|
| 品种 | 币安 USDT 永续 SNDKUSDT（合约，不做现货） |
| 周期 | 1h K线收盘判断，下一根开盘执行 |
| 进场 | EMA7 > EMA25 持多 |
| 出场 | EMA7 < EMA25 平多（止盈就是这个） |
| 方向 | 只做多，不做空 |
| 止损 | 开仓价 − 4×ATR(14，1h)，交易所 STOP_MARKET（按最新成交价触发），不移动 |
| 止损后 | 等 EMA7 先跌回 EMA25 下方一次才能再进 |
| 不用 | 雷达跟踪、保本锁、多周期确认、做空、日线过滤（都回测过，更差） |

## 模式（开关文件都在 VPS `/home/binanceC/sndk_solo/`）

| 文件 | 作用 |
|---|---|
| （默认） | **最高收益模式**：仓位 = 开仓时净值 × 1 倍，不满一手往上取到下一手 |
| `VOLSIZE` | **波动调仓模式**：仓位 × min(1, 2% ÷ 1h ATR%)，财报等剧烈波动时自动减仓 |
| `ARMED` | 有它才真实下单（没有 = 影子模式，只算不下单） |
| `STOP` | 一律不下单（紧急停） |
| `JOIN` | 立即跟上回测当前已有的持仓（默认只跟 ARMED 之后的新开仓信号） |

交易所设置：SNDKUSDT 全仓、杠杆 75x（只为少占保证金，实际仓位仍≈1倍）、双向持仓模式（执行器带 positionSide=LONG）。

## 执行

- 下单：只做 Maker 的限价单（GTX，挂单费 0），每分钟按最优价重挂，15 分钟没成交完用市价补。
- 信号：每轮从 SNDKUSDT 上线（2026-04-07）起把全部 1h K线按回测逻辑重放一遍，实盘 = 回测本身。
- 自己的订单 clientOrderId 前缀 `ss_`；账户里别的持仓不碰，只钉钉告警。
- 钉钉：看门狗机器人，消息开头【SNDK单票方案 v1·C账户】。

## 位置

| 东西 | 路径 |
|---|---|
| 执行器代码 | `unicorn_quant/live/sndk_solo_executor.py` → VPS `/home/binanceC/sndk_solo/` |
| 服务 | `sndk-solo-C.service`（User=binanceC） |
| 状态 / 操作日志 | `/home/binanceC/sndk_solo/status.json`、`actions.jsonl` |
| 手动跑一轮看状态 | `sudo -u binanceC SNDK_SOLO_QUIET=1 /home/binanceC/binance-engine/venv/bin/python sndk_solo_executor.py once` |
| 回测脚本 | `research_scripts/sndk_ema_grid.py`、`sndk_tf_mtf.py`、`sndk_exit_study.py`、`sndk_dd.py`、`sndk_venue.py` |
| 研究结论 | 复盘页 research.json 第 110~117 条 |

## v1 回测（最高收益模式）

| 数据 | 区间 | 收益 | 最大回撤 |
|---|---|---|---|
| 币安永续（含资金费，挂单进出） | 2026-04-21 起约 6 个月 | +115% | 22% |
| 正股 1h 常规时段 | 上市以来 1.6 年 | +1154% | 43% |
| 对照：一直拿着 | 上市以来 | +2087% | 56% |

## 版本记录

- **v1**（2026-10-31）：上线，C 账户 43.8U，ARMED。同日把交易所杠杆调到 75x、仓位改为往上取整。
- 同日停用了占用 C 账户第二把密钥的旧服务 vwap-live（它开过两笔 SOL，已平），C 账户现在只跑本方案。

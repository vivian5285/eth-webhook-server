# ADX 趋势 + 震荡双引擎：Claude 接手说明

日期：2026-10-05（北京时间）。项目：`/home/stratroster/eth-webhook-server`。
页面：http://187.77.130.144:8878/ 。本批是新增纸面擂台实验，收益尚未验证。

## 先看哪些文件

- `strategies/adx_dual_engine.py`：完整、中文注释的纯策略函数。
- `comparison_roster.py` 文件末尾：品种池和四个实验身份。
- `strategies/__init__.py` 文件末尾：函数注册和网页说明。
- `multi_strategy_runner.py::_open_from_signal`：保存 `adx`、`entry_stage`，沿用已有仓位/费用/滑点/风控。
- `klines.py`：Gate行情、合约名映射、1000倍率、闭合K线缓存。
- `dashboard/roster_server.py::_fetch_live_prices`：网页标记价格改为Gate，以免用Gate信号却按币安价格算浮盈亏。
- `research/test_adx_dual_engine.py`：离线规则测试。

## 为什么加这一批

已有 `adx_regime_switch`（4h）也是ADX切换，但趋势腿仅在EMA10/30交叉时触发；ADX与交叉错峰容易错过。
它的退出每次按最新ADX重新选规则。保留旧版独立统计，不覆盖它的历史成绩。
已有 `vwap_ema_regime`、`adx_efficiency_zscore`、`crypto_combo_*` 的部分方案被 `pruned_strategies.py` 归档；本次不解除归档，四个新名字测试新的规则，不把旧策略换名复活。

## 实验矩阵

| 身份 | 周期 | engine_mode | 用途 |
|---|---|---|---|
| adx_dual_engine_4h | 4h | both | 主双引擎 |
| adx_dual_trend_only_4h | 4h | trend | 去掉震荡腿的消融对照 |
| adx_dual_range_only_4h | 4h | range | 去掉趋势腿的消融对照 |
| adx_dual_engine_1h | 1h | both | 更快周期的实验 |

四组均为现有 `_CRYPTO_ONLY_SYMBOLS`，排除黄金和代币化美股；只用Gate原生1h/4h，550根共享行情缓存。
每个身份独立起始虚拟资金1000，单个品种在同一身份下最多一仓。不同身份可以持同一品种，属于独立对照账户。
4h三组参数完全相同，只改变允许哪条腿入场。1h的K线根数参数相同，日历时间尺度不同。
组合风险上限会造成仓位/交易机会不同，因此双引擎净值不等于两条腿对照净值相加。

## 状态判定

ADX14连续2根收盘>=25：trend；连续2根<=18：range；其余：neutral。
中间隔离区不开新仓。已有仓位保留硬止损和所属引擎的退出规则。
这是“连续确认+隔离区”，并非依赖上一轮内存状态的滞回；相同历史输入结果可重放。
ADX只衡量趋势强度，不能作为多空方向。方向另看EMA7/30、慢线斜率和DI。
25是常见趋势参考线，18、连续2根及其余组合参数是本次实验设定，不声称通用最优。

## 两条腿的细则

趋势入场：EMA7/30排列、慢线斜率、DI方向一致；且发生快线回收、20根通道收盘突破、或ADX刚完成趋势确认之一。
突破边界不包含当前K线。价格离快线超过2ATR不追。
初始硬止损2ATR，不设固定TP。EMA排列失效、确认转range或从持仓前序极值回撤3倍入场ATR时退出。
3ATR是收盘条件：只使用入场之后、当前K线之前的极值；它不是盘中交易所挂单，也不动态写回硬止损。

震荡入场：前一根收盘在BB20/2之外，RSI2<=10或>=90；当前收回带内且尚未越过中轨。
EMA30近3根累计变化<=0.5ATR，排除明显斜向漂移。到中轨预期距离至少为1.2ATR初始止损风险的1倍。
初始硬止损1.2ATR；回到当时动态SMA20中轨、确认转trend或超过12根仍未回归时退出。
不把入场时中轨冻结为TP，避免均值漂移后目标失真。
上线前触发漏斗检查：RSI14极值在低ADX中几乎不出现，6个币550根4h回放零回归入场。
改用短RSI2衡量局部极值后，两条腿都可触发；不是按收益挑参数。回归样本仍较少，需继续模拟积累。

## 重启与退出

入场信号携带 `entry_stage=adx_trend/adx_range`，现有SQLite字段持久化并由 `_hydrate_keys_from_db` 恢复。
退出逻辑永远读取入场阶段，确认变档先平仓，下一轮再按新状态寻找新信号。
不同时反手，不把亏损的回归仓变成趋势仓续命。缺阶段时保护性退出，不猜。
新策略不启用 `early_entry`，不接通用趋势保本/雷达名单，避免干扰回归腿。
入场K线不可判退出；runner的同K线重入防护继续生效。

## Gate和两个明确保留的例外

K线主端点：`https://api.gateio.ws/api/v4/futures/usdt/candlesticks`。
网页标价端点：`https://api.gateio.ws/api/v4/futures/usdt/tickers`，字段 `mark_price`。
ETHUSDT映射ETH_USDT，1000PEPEUSDT映射PEPE_USDT并将OHLC/标价乘1000。
Gate失败返回空行情/保留自身网页报价缓存，不能自动切币安代替新策略行情。
用户已明确选择保留 `OPENAIUSDT`、`ANTHROPICUSDT` 的币安旧仓例外，理由是存量入场/止损价存在交易所基差；这两只不在新实验品种池。
资金费率/OI等旧模块还有自己的数据源，不能把“新策略全用Gate K线”写成“整个系统所有数据都来自Gate”。
`/api/roster/meta` 已明确返回主行情源与旧品种例外列表。

## 验证与评价

离线：`venv/bin/python -m unittest strategy_engine.research.test_adx_dual_engine -v`。
规则测试覆盖连续确认/边界、ADX迟到、DI方向、低ADX漂移、布林回收、收益风险过滤、重启阶段、变档退出、超时和入场K线防未来函数。
集成脚本 `research/validate_adx_integration.py` 只允许在 `codex_staging` 路径运行，验证runner写SQLite后重启恢复、网页Gate标价/1000倍率/旧品种例外和Gate失败不切币安。
历史回放是独立进程，不写生产DB、不制造擂台历史收益。回放只作为规则触发和时序检查，不等同组合级完整收益回测。
下一步评估：按入场阶段分桶查看扣费收益、最大回撤、平均净收益/笔、盈亏比、换手、止损率；比较4h三组是否真的互补。
先固定参数采样，不随短期榜单频繁改阈值。历史回放/少量模拟成交不能证明盈利。
模拟器仍有5分钟轮询、OHLC路径未知、过去收盘价成交等既有近似；同K线止损/止盈按runner既有规则。

## 服务与回滚

仅更新模拟项目文件，重启 `strategy-roster.service` 与 `roster-dashboard.service`。
不修改实盘服务、账户配置或任何真实下单路径。
部署前保留修改文件和SQLite在线备份，备份目录在项目 `codex_backups/adx_dual_*`。
回滚应先停止策略服务，恢复备份源码，再重启。若新策略已有模拟持仓，不能简单移除roster后遗弃仓位；先按模拟盘规则处理新仓或保留管理入口。
SQLite备份用于审计/事故恢复，不应日常全库回滚，否则会丢失同时运行的其他模拟系统的新数据。

## 指标与API依据

- Gate API官方文档：https://www.gate.com/docs/developers/apiv4/en/futures/
- TradingView ADX官方说明：https://www.tradingview.com/support/solutions/43000589099-average-directional-index-adx/
  ADX是强度指标；常用25/20阈值不保证某资产、某周期盈利，本次range=18更保守。


## 本次部署验证记录

20项离线规则测试通过；VPS隔离集成验证覆盖实际SQLite恢复、Gate标价、1000倍率和旧品种例外。
四组共15币种、60条配置已部署到模拟擂台，策略/网页服务启动正常，网页接口已返回四组新策略。
六个代表币种550根闭合K线回放：4h双引擎67次趋势/8次回归；1h双引擎66次趋势/12次回归。这里只统计触发，不计算组合收益。
备份目录：/home/stratroster/eth-webhook-server/codex_backups/adx_dual_20261005_073521。
完整回放明细见adx_dual_replay_validation.json，上线接口快照见ADX_DUAL_VERIFIED.json。

首轮巡检确认：四组均产生strategy_risk_state，1h双引擎已在真实Gate行情上产生模拟仓位（含SOL多仓），没有向生产库灌入回放交易。
实际旧策略问题：启动日志记录SNDKUSDT 1m Gate HTTP 400，新实验15币种不含SNDK、行情检查通过；此处仅记录，不推测400原因。

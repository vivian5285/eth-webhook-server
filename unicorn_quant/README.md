# 独角兽量化 Unicorn Quant

Python 后端（行情/指标/复盘/模拟盘/交易所适配）+ Lightweight Charts 前端，Uniswap 风格 UI。

- 线上：http://187.53.133.188:8890 （systemd: `unicorn-quant`，配置 `/etc/unicorn-quant.env`，端口 8890）
- 复盘复用仓库 `strategy_engine/strategies` 全部策略 + 内置双均线带(EMA7/25)等
- 默认模拟盘；实盘仅"验证单"(`/fapi/v1/order/test`)。真实下单需 `UQ_LIVE_ENABLED=1` + `UQ_BINANCE_KEY/SECRET` + 请求 confirm=true
- 本地：`UQ_PASSWORD=xxx python unicorn_quant/server.py`

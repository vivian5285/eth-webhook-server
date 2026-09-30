"""
2026-09-05：新增品种DELLUSDT(3小时周期)、GEVUSDT(4小时周期)。3h/4h都是
币安原生K线间隔，不用合成，直接拉原生K线测真实摆动点回调分布——同
08-18/08-25那批OPENAI/ANTHROPIC/ASML/SKHYNIX/GS/MU/LITE一致的方法
(fractal pivot，±3根确认)。

只读：只调用 futures_klines 拉历史K线，不下单不查持仓。复用
scratch_calibrate_xau_skhynix.py的run_symbol()。

跑法：cd /home/binanceB/binance-engine && venv/bin/python /path/to/this.py
"""
import sys

sys.path.insert(0, "/home/binanceB/binance-engine")
sys.path.insert(0, "/tmp")
from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    # 币安期货K线原生间隔没有"3h"(1h/2h/4h/6h/8h/12h)，180分钟能被30整除，
    # 用30m原始K线合成，跟BNB(150min←30m)/ETH(旧90min←30m)同一套合成手法。
    run_symbol("DELL", "DELLUSDT", "30m", 30 * 60 * 1000, 3 * 60 * 60 * 1000, 200)
    run_symbol("GEV", "GEVUSDT", "4h", 4 * 60 * 60 * 1000, 4 * 60 * 60 * 1000, 200)

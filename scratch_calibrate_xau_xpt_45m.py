"""
2026-09-13：币安B系统(智能硬止损体系，跟CoinW同一套体系)拟对XAU/XPT改用
45分钟周期(比照CoinW的BNB/XPD 45分钟先例)，以及XPT作为新增品种(铂金，
跟XAU/XPD同属贵金属TradFi商品永续)首次校准。

只读：只调用 futures_klines 拉历史K线，不下单不查持仓。复用
scratch_calibrate_xau_skhynix.py的run_symbol()同一套方法(真实摆动点
识别fractal pivot，±3根确认)。

45分钟能被15分钟整除(45/15=3)，用15m原始K线合成，跟BNB/XPD 2026-09-12
校准同样的合成比例。

跑法：cd /home/binanceB/binance-engine && venv/bin/python /tmp/scratch_calibrate_xau_xpt_45m.py
"""
import sys

sys.path.insert(0, "/home/binanceB/binance-engine")
sys.path.insert(0, "/tmp")
from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    run_symbol("XAU", "XAUUSDT", "15m", 15 * 60 * 1000, 45 * 60 * 1000, 90)
    run_symbol("XPT", "XPTUSDT", "15m", 15 * 60 * 1000, 45 * 60 * 1000, 90)

"""
2026-09-08：宝贝截图核实TV面板真实周期——发现ETH配置里的59分钟已经
过期，TV面板实际显示75分钟。75能被15整除，用15m原始K线合成（比上次
59分钟那版用1分钟合成轻得多）。

只读：只调用 futures_klines 拉历史K线，不下单不查持仓。
跑法：cd /home/binanceB/binance-engine && venv/bin/python /path/to/this.py
"""
import sys

sys.path.insert(0, "/home/binanceB/binance-engine")
sys.path.insert(0, "/tmp")
from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    run_symbol("ETH", "ETHUSDT", "15m", 15 * 60 * 1000, 75 * 60 * 1000, 90)

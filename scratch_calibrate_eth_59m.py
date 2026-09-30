"""
2026-09-06：宝贝把ETH的TV周期改成59分钟。59是质数，不能被5/15/30整除，
只能用1分钟原始K线×59合成（同SKHYNIX 101分钟那次的手法）。

只读：只调用 futures_klines 拉历史K线，不下单不查持仓。复用
scratch_calibrate_xau_skhynix.py的run_symbol()。

跑法：cd /home/binanceB/binance-engine && venv/bin/python /path/to/this.py
"""
import sys

sys.path.insert(0, "/home/binanceB/binance-engine")
sys.path.insert(0, "/tmp")
from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    run_symbol("ETH", "ETHUSDT", "1m", 60 * 1000, 59 * 60 * 1000, 60)

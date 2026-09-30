"""
2026-09-08：宝贝截图核实TV面板真实周期——发现ANTHROPIC配置里的105分钟
已经过期，TV面板实际显示101分钟(跟SKHYNIX同周期)。101是质数，不能被
5/15/30整除，只能用1分钟原始K线合成(同SKHYNIX 101分钟手法)。

只读：只调用 futures_klines 拉历史K线，不下单不查持仓。
跑法：cd /home/binanceB/binance-engine && venv/bin/python /path/to/this.py
"""
import sys

sys.path.insert(0, "/home/binanceB/binance-engine")
sys.path.insert(0, "/tmp")
from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    run_symbol("ANTHROPIC", "ANTHROPICUSDT", "1m", 60 * 1000, 101 * 60 * 1000, 45)

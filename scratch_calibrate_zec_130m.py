"""
2026-09-06：ZEC TV策略周期从150分钟改成130分钟，重新校准呼吸空间。
只读：只调用 futures_klines 拉历史K线，不下单不查持仓。跟08-18/08-25那批
"真实摆动点识别(fractal pivot, ±3根确认)"方法保持一致，用真实原始K线
合成目标周期后测回调分布。

ZEC: 130分钟能被5分钟整除(130/5=26)，用5分钟原始K线×26合成。

跑法：cd /home/binanceB/binance-engine && venv/bin/python /path/to/this.py
"""
import sys

sys.path.insert(0, "/home/binanceB/binance-engine")

from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    run_symbol("ZEC", "ZECUSDT", "5m", 5 * 60 * 1000, 130 * 60 * 1000, 80)

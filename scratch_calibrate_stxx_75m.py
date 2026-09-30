"""
2026-09-08：新增品种STXXUSDT.P，75分钟周期。75不是币安原生间隔，能被15
整除，用15m原始K线合成，跟08-18/08-25那批品种同一套fractal pivot回调
分布校准方法(复用scratch_calibrate_xau_skhynix.py的run_symbol())。

STXXUSDT于交易所onboardDate=2026-06-11上线(TRADIFI_PERPETUAL/
underlyingType=EQUITY，跟GS/MU/LITE/TSLA/META/DELL/GEV同族)，只有约89
天历史，用85天覆盖已上线以来几乎全部真实K线。

只读：只调用 futures_klines 拉历史K线，不下单不查持仓。
跑法：cd /home/binanceB/binance-engine && venv/bin/python /path/to/this.py
"""
import sys

sys.path.insert(0, "/home/binanceB/binance-engine")
sys.path.insert(0, "/tmp")
from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    run_symbol("STXX", "STXXUSDT", "15m", 15 * 60 * 1000, 75 * 60 * 1000, 85)

"""
2026-08-29：宝贝把ETH的TV策略周期从90分钟改成150分钟，重新校准呼吸空间。
跟08-25那批XAU/SKHYNIX同一套方法(真实摆动点识别fractal pivot，±3根确认)，
只读——只调用futures_klines拉历史K线，不下单不查持仓。

150分钟能被30分钟整除(150/30=5)，用30m原始K线合成，跟XAU 90m(30m×3)同一个
合成基准单位。ETH是本仓库的"基准品种"，其它很多品种的step_trigger/advance
都是按"自身回调/ETH当前回调"这个比例缩放出来的——这次只重新测ETH自己在
新的150分钟周期下的真实回调分布，不去动其它品种已经算好的比例(那些是
各自独立测出来的，不会因为ETH数字变了就跟着变)。

跑法：cd /home/binanceB/binance-engine && venv/bin/python scratch_calibrate_eth_150.py
"""
import sys
sys.path.insert(0, "/home/binanceB/binance-engine")
sys.path.insert(0, "/tmp")
from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    run_symbol("ETH-150min", "ETHUSDT", "30m", 30 * 60 * 1000, 150 * 60 * 1000, 120)

"""
2026-09-05：ETH的TV策略周期改成55分钟(宝贝原话"erh改成55分周期"=ETH)，
原09-01那版是按150分钟合成K线测的，周期变了必须用真实55分钟K线重新测
回调分布。ETH是其它多个品种三档步进缩放比例的参照基准本身，只更新ETH
自己这份基线，不用因此回头重算其它品种的历史缩放比例(见
breath_profiles.py::BREATH_ETH顶部注释)。

只读：只调用 futures_klines 拉历史K线，不下单不查持仓。复用
scratch_calibrate_xau_skhynix.py的run_symbol()同一套方法(真实摆动点
识别fractal pivot，±3根确认)。

55分钟能被5分钟整除(55/5=11)，用5m原始K线合成。

跑法：cd /home/binanceB/binance-engine && venv/bin/python /path/to/this.py
"""
import sys

sys.path.insert(0, "/home/binanceB/binance-engine")
sys.path.insert(0, "/tmp")
from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    run_symbol("ETH", "ETHUSDT", "5m", 5 * 60 * 1000, 55 * 60 * 1000, 80)

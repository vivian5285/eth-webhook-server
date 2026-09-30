"""
2026-09-05：SNDK的TV策略周期改成75分钟（宝贝原话"sndk变成75分钟周期"），
原08-14那版是按90分钟合成K线测的，周期变了必须用真实75分钟K线重新测
回调分布，不能只改tv_tf_sec不改呼吸系数。

只读：只调用 futures_klines 拉历史K线，不下单不查持仓。复用
scratch_calibrate_xau_skhynix.py的run_symbol()同一套方法(真实摆动点
识别fractal pivot，±3根确认)。

75分钟能被15分钟整除(75/15=5)，用15m原始K线合成。

跑法：cd /home/binanceB/binance-engine && venv/bin/python /path/to/this.py
"""
import sys

sys.path.insert(0, "/home/binanceB/binance-engine")
sys.path.insert(0, "/tmp")
from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    run_symbol("SNDK", "SNDKUSDT", "15m", 15 * 60 * 1000, 75 * 60 * 1000, 90)

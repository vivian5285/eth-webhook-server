"""
2026-09-05：MU的TV策略周期改成55分钟(宝贝原话"mu的tv挂载55分钟周期了")，
原BREATH_MU(2026-08-25校准)是按90分钟合成K线测的，周期变了必须用真实
55分钟K线重新测回调分布，不能只改tv_tf_sec不改呼吸系数。

只读：只调用 futures_klines 拉历史K线，不下单不查持仓。复用
scratch_calibrate_xau_skhynix.py的run_symbol()同一套方法(真实摆动点
识别fractal pivot，±3根确认)。

55分钟能被5分钟整除(55/5=11)，用5m原始K线合成，比SKHYNIX 101分钟那种
被迫用1分钟合成效率高得多。

跑法：cd /home/binanceB/binance-engine && venv/bin/python /path/to/this.py
"""
import sys

sys.path.insert(0, "/home/binanceB/binance-engine")
sys.path.insert(0, "/tmp")
from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    run_symbol("MU", "MUUSDT", "5m", 5 * 60 * 1000, 55 * 60 * 1000, 80)

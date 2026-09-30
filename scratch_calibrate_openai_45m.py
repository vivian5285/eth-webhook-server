"""
2026-09-13：宝贝把TV那边OPENAI的alert周期从120分钟改成了45分钟(跟其余
品种统一，只剩SNDK是75分钟)，需要重新校准币安B系统和CoinW的雷达系数+
智能硬止损。复用scratch_calibrate_xau_skhynix.py的run_symbol()同一套
方法(真实摆动点识别fractal pivot，±3根确认)。

45分钟能被15分钟整除(45/15=3)，用15m原始K线合成，跟BNB/XPD/XAU/XPT/
XRP/SOL同一套方法/合成比例。

跑法：cd /home/binanceB/binance-engine && venv/bin/python /tmp/scratch_calibrate_openai_45m.py
"""
import sys

sys.path.insert(0, "/home/binanceB/binance-engine")
sys.path.insert(0, "/tmp")
from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    run_symbol("OPENAI", "OPENAIUSDT", "15m", 15 * 60 * 1000, 45 * 60 * 1000, 90)

import sys
sys.path.insert(0, "/home/binanceB/binance-engine")
sys.path.insert(0, "/tmp")
from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    # 105分钟不能被30整除，能被15整除(15×7=105)，用15m原始K线合成
    run_symbol("ANTHROPIC-105m", "ANTHROPICUSDT", "15m", 15 * 60 * 1000, 105 * 60 * 1000, 90)

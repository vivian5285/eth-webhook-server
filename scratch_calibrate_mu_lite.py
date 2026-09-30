import sys
sys.path.insert(0, "/home/binanceB/binance-engine")
sys.path.insert(0, "/tmp")
from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    run_symbol("MU", "MUUSDT", "30m", 30 * 60 * 1000, 90 * 60 * 1000, 80)
    run_symbol("LITE", "LITEUSDT", "30m", 30 * 60 * 1000, 90 * 60 * 1000, 80)

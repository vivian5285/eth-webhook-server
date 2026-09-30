import sys
sys.path.insert(0, "/home/binanceB/binance-engine")
sys.path.insert(0, "/tmp")
from scratch_calibrate_xau_skhynix import run_symbol  # noqa: E402

if __name__ == "__main__":
    # 6h是币安原生间隔，不用合成，raw_interval=90min本身就是目标周期
    run_symbol("ANTHROPIC-6h", "ANTHROPICUSDT", "6h", 6 * 60 * 60 * 1000, 6 * 60 * 60 * 1000, 200)

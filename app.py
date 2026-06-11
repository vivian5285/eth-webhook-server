# app.py（最终加强版 - 含每日日报调度器 + 方向验证加强）
from flask import Flask, request, jsonify
import os
import re
import json
import threading
import logging
from datetime import datetime
from dotenv import load_dotenv
from binance_client import BinanceClient
from tp_monitor import TPMonitor
from position_manager import PositionManager
from daily_report_scheduler import DailyReportScheduler   # 新增

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient()
position_manager = PositionManager()
tp_monitor = TPMonitor()
tp_monitor.start()

# ==================== 每日日报调度器 ====================
daily_scheduler = DailyReportScheduler(binance_client, report_time="00:05")
daily_scheduler.start()

# ==================== 配置区 ====================
TIMEFRAME = "45m"
RISK_PERCENT = float(os.getenv("RISK_PERCENT", 0.01))
STOP_DISTANCE_PERCENT = float(os.getenv("STOP_DISTANCE_PERCENT", 0.008))
CONFIRMATION_ENABLED = True

def extract_json_from_text(text: str):
    try:
        match = re.search(r'\{.*\}', text)
        if match:
            return json.loads(match.group())
    except:
        pass
    return None

def calculate_position_size(symbol: str = "ETHUSDT") -> float:
    try:
        balance_info = binance_client.get_account_balance()
        if not balance_info:
            return 0.05
        equity = balance_info.get("totalWalletBalance", 200)
        risk_amount = equity * RISK_PERCENT
        ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
        current_price = float(ticker["price"])
        stop_distance = current_price * STOP_DISTANCE_PERCENT
        if stop_distance <= 0:
            return 0.05
        return round(risk_amount / stop_distance, 3)
    except Exception as e:
        logging.error(f"[仓位计算异常] {e}")
        return 0.05

# ==================== 加强版方向二次验证 ====================
def confirm_direction(symbol: str, side: str) -> bool:
    if not CONFIRMATION_ENABLED:
        return True
    try:
        klines = binance_client.client.get_klines(symbol=symbol, interval=TIMEFRAME, limit=60)
        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]

        if len(closes) < 40:
            return True

        # EMA趋势
        ema20 = sum(closes[-20:]) / 20
        ema50 = sum(closes[-50:]) / 50
        ema_trend_ok = (closes[-1] > ema20 > ema50) if side == "long" else (closes[-1] < ema20 < ema50)

        # MACD
        def ema(data, period):
            m = 2 / (period + 1)
            res = [data[0]]
            for p in data[1:]:
                res.append(p * m + res[-1] * (1 - m))
            return res

        ema_fast = ema(closes, 12)
        ema_slow = ema(closes, 26)
        macd = [ema_fast[i] - ema_slow[i] for i in range(len(ema_slow))]
        signal_line = ema(macd, 9)
        macd_ok = (macd[-1] > signal_line[-1]) if side == "long" else (macd[-1] < signal_line[-1])

        # 成交量
        vol_ma = sum(volumes[-20:]) / 20
        vol_ok = volumes[-1] > vol_ma * 1.1

        confirmed = ema_trend_ok and macd_ok and vol_ok
        logging.info(f"[方向验证] {symbol} {side} | EMA={ema_trend_ok} | MACD={macd_ok} | 放量={vol_ok} | 结果={confirmed}")
        return confirmed

    except Exception as e:
        logging.error(f"[方向验证异常] {e}")
        return True

# ==================== 其他原有函数（开仓、平仓、美化报告等） ====================
# ...（保留你之前已有的 send_beautiful_open_report、send_beautiful_close_report 等函数）

def place_market_order(signal: str, symbol: str):
    # ...（保留原有逻辑，调用 confirm_direction 进行二次验证）
    pass

@app.route('/webhook', methods=['POST'])
def webhook():
    # ...（保留原有 webhook 逻辑）
    pass

if __name__ == "__main__":
    logging.info("ETH Webhook Server 启动中...")
    app.run(host="0.0.0.0", port=5000)

# app.py（修改后版本 - 报告权限已收归监督层）
from flask import Flask, request, jsonify
import os
import re
import json
import time
import logging
import threading
from datetime import datetime
from dotenv import load_dotenv

from binance_client import BinanceClient
from tp_monitor import TPMonitor
from position_manager import PositionManager
from position_supervisor import supervisor   # ← 引入监督层

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient()
position_manager = PositionManager()
tp_monitor = TPMonitor()
tp_monitor.start()

TIMEFRAME = "30m"
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

def confirm_direction(symbol: str, side: str) -> bool:
    if not CONFIRMATION_ENABLED:
        return True
    try:
        klines = binance_client.client.get_klines(symbol=symbol, interval=TIMEFRAME, limit=60)
        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        if len(closes) < 40:
            return True

        ema20 = sum(closes[-20:]) / 20
        ema50 = sum(closes[-50:]) / 50
        ema_trend_ok = (closes[-1] > ema20 > ema50) if side == "long" else (closes[-1] < ema20 < ema50)

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

        vol_ma = sum(volumes[-20:]) / 20
        vol_ok = volumes[-1] > vol_ma * 1.1

        confirmed = ema_trend_ok and macd_ok and vol_ok
        return confirmed
    except Exception as e:
        logging.error(f"[方向验证异常] {e}")
        return True

def send_beautiful_open_report(signal: str, symbol: str, qty: float, entry_price: float, tp1, tp2, tp3):
    try:
        balance = binance_client.get_account_balance() or {}
        equity = balance.get("totalWalletBalance", 0)
        available = balance.get("availableBalance", 0)

        title = "✅ 开仓成功"
        content = (
            f"**信号类型**：{signal}\n"
            f"**币种**：{symbol}\n"
            f"**下单数量**：{qty}\n"
            f"**开仓均价**：{entry_price}\n\n"
            f"**🎯 止盈目标（预估）**\n"
            f"- TP1：{tp1}\n"
            f"- TP2：{tp2}\n"
            f"- TP3：{tp3}\n\n"
            f"**💰 账户快照**\n"
            f"- 账户权益：{equity:.2f} USDT\n"
            f"- 可用余额：{available:.2f} USDT\n\n"
            f"**⏰ 时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        binance_client._send_dingtalk(title, content)
    except Exception as e:
        logging.error(f"[开仓报告发送失败] {e}")

def send_beautiful_close_report(reason: str, symbol: str):
    try:
        balance = binance_client.get_account_balance() or {}
        equity = balance.get("totalWalletBalance", 0)
        title = "📉 平仓成功"
        content = (
            f"**平仓原因**：{reason}\n"
            f"**币种**：{symbol}\n\n"
            f"**💰 账户快照**\n"
            f"- 账户权益：{equity:.2f} USDT\n\n"
            f"**⏰ 时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        binance_client._send_dingtalk(title, content)
    except Exception as e:
        logging.error(f"[平仓报告发送失败] {e}")

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(silent=True) or extract_json_from_text(request.get_data(as_text=True))
        if not data:
            return jsonify({"status": "error", "message": "无法解析信号"}), 400

        signal = data.get("signal")
        logging.info(f"[Webhook] 收到信号 → {signal}")

        # 只转发给监督层
        result = supervisor.handle_new_signal(signal)
        return jsonify(result), 200

    except Exception as e:
        logging.error(f"[Webhook异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    logging.info("=== ETH Webhook Server 启动（监督层已接管） ===")
    app.run(host="0.0.0.0", port=5000)

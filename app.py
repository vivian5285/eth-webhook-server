# app.py（最终加强解析版 - 兼容 TradingView 脏数据）
from flask import Flask, request, jsonify
import os
import re
import json
import logging
from dotenv import load_dotenv
from binance_client import BinanceClient
from tp_monitor import TPMonitor
from position_manager import PositionManager

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient()
position_manager = PositionManager()
tp_monitor = TPMonitor()
tp_monitor.start()

# 从 .env 读取风险参数
RISK_PERCENT = float(os.getenv("RISK_PERCENT", 0.01))
STOP_DISTANCE_PERCENT = float(os.getenv("STOP_DISTANCE_PERCENT", 0.008))

def extract_json_from_text(text: str):
    """尝试从脏文本中提取 JSON"""
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

        qty = round(risk_amount / stop_distance, 3)
        logging.info(f"[仓位计算] 权益={equity:.2f}U | 风险={RISK_PERCENT*100}% | 计算数量={qty}")
        return qty
    except Exception as e:
        logging.error(f"[仓位计算异常] {e}")
        return 0.05

def place_market_order(signal: str, symbol: str):
    try:
        current_pos = binance_client.get_current_position(symbol)
        if current_pos:
            logging.warning(f"[风控] 已存在持仓，拒绝重复开 {signal}")
            return {"status": "skipped", "reason": "已有持仓"}

        qty = calculate_position_size(symbol)
        if qty <= 0:
            return {"status": "error", "message": "仓位计算无效"}

        side = "BUY" if signal == "OPEN_LONG" else "SELL"
        order = binance_client.client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=qty
        )
        logging.info(f"[开仓成功] {signal} {symbol} | Qty: {qty}")
        return {"status": "success", "side": signal, "qty": qty, "order": order}

    except Exception as e:
        logging.error(f"[下单失败] {signal} {symbol} | {e}")
        return {"status": "error", "message": str(e)}

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        # 先尝试标准 JSON
        data = request.get_json(silent=True)

        # 如果失败，尝试从文本中提取
        if not data:
            raw_text = request.get_data(as_text=True)
            data = extract_json_from_text(raw_text)

        if not data:
            logging.warning("[Webhook] 无法解析信号内容")
            return jsonify({"status": "error", "message": "无法解析信号"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")

        logging.info(f"[Webhook] 收到信号 → {signal} | {symbol}")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            result = place_market_order(signal, symbol)
            return jsonify(result), 200

        elif signal == "CLOSE_ALL":
            result = binance_client.close_all_positions(symbol)
            if result.get("status") == "success":
                position_manager.clear_position()
            return jsonify(result), 200

        elif signal == "TP_PARTIAL":
            logging.info(f"[TP_PARTIAL] {data.get('reason', '')}")
            return jsonify({"status": "ignored"}), 200

        else:
            return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logging.error(f"[Webhook 异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

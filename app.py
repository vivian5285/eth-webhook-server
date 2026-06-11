# app.py（最终版 - 动态风险仓位计算）
from flask import Flask, request, jsonify
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

# ==================== 动态仓位计算（按风险百分比） ====================
def calculate_position_size(
    symbol: str = "ETHUSDT",
    risk_percent: float = 0.01,        # 默认风险 1%
    stop_distance_percent: float = 0.008  # 默认止损距离 0.8%
) -> float:
    """
    按风险百分比计算仓位大小
    risk_percent: 每笔交易愿意承受的最大亏损占账户权益的比例（例如 0.01 = 1%）
    stop_distance_percent: 止损距离（当前价格的百分比）
    """
    try:
        balance_info = binance_client.get_account_balance()
        if not balance_info:
            logging.warning("[仓位计算] 获取余额失败，使用默认数量 0.05")
            return 0.05

        equity = balance_info.get("totalWalletBalance", 200)
        risk_amount = equity * risk_percent

        # 获取当前价格
        ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
        current_price = float(ticker["price"])

        # 计算止损距离（USDT）
        stop_distance = current_price * stop_distance_percent

        if stop_distance <= 0:
            return 0.05

        # 计算仓位数量
        qty = risk_amount / stop_distance
        qty = round(qty, 3)

        logging.info(f"[仓位计算] 权益: {equity:.2f}U | 风险比例: {risk_percent*100}% | "
                     f"止损距离: {stop_distance_percent*100}% | 计算数量: {qty}")
        return qty

    except Exception as e:
        logging.error(f"[仓位计算异常] {e}")
        return 0.05  # 异常时使用保守默认值


def place_market_order(signal: str, symbol: str):
    """真实下单 + 动态仓位"""
    try:
        # 风控：检查是否已有持仓
        current_pos = binance_client.get_current_position(symbol)
        if current_pos:
            logging.warning(f"[风控拦截] 已存在 {symbol} 持仓，拒绝重复开 {signal}")
            return {"status": "skipped", "reason": "已有持仓"}

        # 动态计算仓位
        qty = calculate_position_size(symbol)

        if qty <= 0:
            return {"status": "error", "message": "计算出的仓位数量无效"}

        if signal == "OPEN_LONG":
            order = binance_client.client.futures_create_order(
                symbol=symbol,
                side="BUY",
                type="MARKET",
                quantity=qty
            )
            logging.info(f"[开仓成功] LONG {symbol} | Qty: {qty}")
            return {"status": "success", "side": "LONG", "qty": qty, "order": order}

        elif signal == "OPEN_SHORT":
            order = binance_client.client.futures_create_order(
                symbol=symbol,
                side="SELL",
                type="MARKET",
                quantity=qty
            )
            logging.info(f"[开仓成功] SHORT {symbol} | Qty: {qty}")
            return {"status": "success", "side": "SHORT", "qty": qty, "order": order}

    except Exception as e:
        logging.error(f"[下单失败] {signal} {symbol} | 错误: {e}")
        return {"status": "error", "message": str(e)}


@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No JSON"}), 400

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
            reason = data.get("reason", "")
            logging.info(f"[TP_PARTIAL] {reason}（由 TPMonitor 内部处理）")
            return jsonify({"status": "ignored"}), 200

        else:
            logging.warning(f"[未知信号] {signal}")
            return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logging.error(f"[Webhook 异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

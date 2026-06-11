# app.py（完整最终版 - 已集成每日报告调度器）
from flask import Flask, request, jsonify
import os
import re
import json
import logging
from datetime import datetime
from dotenv import load_dotenv

from binance_client import BinanceClient
from position_supervisor import supervisor
from daily_report_scheduler import daily_report_scheduler   # 新增

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient()

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
        risk_percent = float(os.getenv("RISK_PERCENT", 0.01))
        stop_distance_percent = float(os.getenv("STOP_DISTANCE_PERCENT", 0.008))

        risk_amount = equity * risk_percent
        ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
        current_price = float(ticker["price"])
        stop_distance = current_price * stop_distance_percent

        if stop_distance <= 0:
            return 0.05
        return round(risk_amount / stop_distance, 3)
    except Exception as e:
        logging.error(f"[仓位计算异常] {e}")
        return 0.05

# ==================== 报告函数（供监督层和调度器调用） ====================

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

# ==================== Webhook 入口 ====================

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(silent=True) or extract_json_from_text(request.get_data(as_text=True))
        if not data:
            return jsonify({"status": "error", "message": "无法解析信号"}), 400

        signal = data.get("signal")
        if not signal:
            return jsonify({"status": "error", "message": "缺少 signal 字段"}), 400

        logging.info(f"[Webhook] 收到信号 → {signal}")

        result = supervisor.handle_new_signal(signal)

        if result.get("status") == "ready_to_open":
            try:
                qty = calculate_position_size()
                if qty <= 0:
                    return jsonify({"status": "error", "message": "仓位计算无效"}), 400

                order_side = "BUY" if signal == "OPEN_LONG" else "SELL"
                order = binance_client.client.futures_create_order(
                    symbol="ETHUSDT",
                    side=order_side,
                    type="MARKET",
                    quantity=qty
                )

                entry_price = float(order.get('avgPrice', 0)) or float(
                    binance_client.client.futures_symbol_ticker(symbol="ETHUSDT")["price"]
                )

                logging.info(f"[下单成功] {signal} {qty} 张 @ {entry_price}")

                supervisor.notify_open_success(signal, qty, entry_price, 0, 0, 0)
                return jsonify({"status": "success", "signal": signal, "qty": qty}), 200

            except Exception as order_err:
                logging.error(f"[下单执行失败] {order_err}")
                return jsonify({"status": "error", "message": str(order_err)}), 500

        return jsonify(result), 200

    except Exception as e:
        logging.error(f"[Webhook 异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    logging.info("=== ETH Webhook Server 已启动（User Data Stream + 每日报告模式） ===")
    
    # 启动每日报告调度器
    daily_report_scheduler.start()
    
    app.run(host="0.0.0.0", port=5000)

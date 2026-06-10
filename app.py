# app.py（最终完整强壮版 - 可直接复制使用）
from flask import Flask, request, jsonify
import time
import traceback
import logging
from binance_client import BinanceClient
from position_manager import PositionManager
from tp_manager import get_actual_tp_prices
from dingtalk import send_dingtalk
from config import Config

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

app = Flask(__name__)
client = BinanceClient()
position_manager = PositionManager()


@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON"}), 400

    try:
        process_webhook(data)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logging.error(f"[CRITICAL] webhook处理异常:\n{traceback.format_exc()}")
        return jsonify({"status": "success"}), 200


def process_webhook(data: dict):
    signal = data.get("signal")
    symbol = data.get("symbol", "ETHUSDT")
    atr = data.get("atr")
    reason = data.get("reason", "")

    logging.info(f"[收到信号] {signal} | {symbol}")

    if signal in ["OPEN_LONG", "OPEN_SHORT"]:
        try:
            # 清理反向持仓
            current_pos = client.get_current_position(symbol)
            if current_pos and float(current_pos.get("positionAmt", 0)) != 0:
                logging.info("[风控] 检测到持仓，先全平")
                client.close_all_positions(symbol)
                time.sleep(1.5)

            qty = client.calculate_position_size(atr)
            if qty <= 0:
                send_dingtalk("风控拦截", f"计算仓位为 {qty}，已拒绝开仓", is_warning=True)
                return

            side_str = "long" if signal == "OPEN_LONG" else "short"

            # 执行开仓
            if signal == "OPEN_LONG":
                order = client.open_long(symbol, qty)
            else:
                order = client.open_short(symbol, qty)

            if order:
                # === 可靠获取入场价 ===
                entry_price = float(order.get("avgPrice") or 0)
                if entry_price == 0:
                    ticker = client.client.futures_symbol_ticker(symbol=symbol)
                    entry_price = float(ticker["price"])

                tp_prices = get_actual_tp_prices(entry_price, atr, side_str)
                position_manager.save_position(symbol, entry_price, atr, tp_prices, side_str)

                report = client.get_detailed_report()
                _send_open_notification(signal.replace("OPEN_", ""), qty, entry_price, tp_prices, report)

        except Exception as e:
            logging.error(f"[开仓异常] {e}")
            send_dingtalk("开仓异常", str(e), is_warning=True)

    elif signal == "CLOSE_ALL":
        try:
            logging.info(f"[保护性全平] 原因: {reason}")
            client.close_all_positions(symbol)
            position_manager.clear_position(symbol)
            report = client.get_detailed_report()
            send_dingtalk(
                "保护性全平",
                f"原因: {reason}\n当前已空仓\n"
                f"总权益: {report.get('total_equity')} USDT | "
                f"浮盈: {report.get('unrealized_pnl')} USDT"
            )
        except Exception as e:
            logging.error(f"[全平异常] {e}")
            send_dingtalk("全平异常", str(e), is_warning=True)


def _send_open_notification(direction: str, qty: float, entry_price: float, tp_prices: dict, report: dict):
    """开仓成功美化推送"""
    msg = (
        f"**下单数量**: {qty}\n"
        f"**入场价**: {entry_price}\n"
        f"**TP1**: {tp_prices['tp1']} | **TP2**: {tp_prices['tp2']} | **TP3**: {tp_prices['tp3']}\n\n"
        f"**账户快照**\n"
        f"总权益: {report.get('total_equity', 'N/A')} USDT\n"
        f"钱包余额: {report.get('wallet_balance', 'N/A')} USDT\n"
        f"可用保证金: {report.get('available_margin', 'N/A')} USDT\n"
        f"维持保证金: {report.get('maintenance_margin', 'N/A')} USDT\n"
        f"当前持仓: {report.get('position', 'N/A')}\n"
        f"浮盈: {report.get('unrealized_pnl', 'N/A')} USDT\n"
        f"杠杆: {report.get('leverage', 'N/A')}x"
    )
    send_dingtalk(f"{direction} 开仓成功", msg)


def send_tp_hit_report(level: str, close_price: float, report: dict = None):
    """TP触发后发送详细报表"""
    if report is None:
        report = client.get_detailed_report()

    msg = (
        f"**{level.upper()} 被触发**\n"
        f"成交价格: {close_price}\n\n"
        f"**账户快照（平仓后）**\n"
        f"总权益: {report.get('total_equity', 'N/A')} USDT\n"
        f"钱包余额: {report.get('wallet_balance', 'N/A')} USDT\n"
        f"可用保证金: {report.get('available_margin', 'N/A')} USDT\n"
        f"维持保证金: {report.get('maintenance_margin', 'N/A')} USDT\n"
        f"当前持仓: {report.get('position', 'N/A')}\n"
        f"浮盈: {report.get('unrealized_pnl', 'N/A')} USDT\n"
        f"今日已实现盈亏: {report.get('today_realized_pnl', 'N/A')} USDT"
    )
    send_dingtalk(f"{level.upper()} 止盈触发", msg)


if __name__ == "__main__":
    from tp_monitor import TPMonitor

    # 启动 TP 监控线程（智慧大脑核心）
    monitor = TPMonitor(check_interval=8)
    monitor.start()

    # 启动 Flask 服务
    app.run(host="0.0.0.0", port=5000)

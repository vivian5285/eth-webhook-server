# app.py（已全面修复 TP 计算 + 反向开仓逻辑）
from flask import Flask, request, jsonify
import os
import re
import json
import time
import logging
from datetime import datetime
from dotenv import load_dotenv

from binance_client import BinanceClient
from tp_monitor import TPMonitor
from position_manager import PositionManager
from daily_report_scheduler import DailyReportScheduler

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient()
position_manager = PositionManager()
tp_monitor = TPMonitor()
tp_monitor.start()

daily_scheduler = DailyReportScheduler(binance_client, report_time="00:05")
daily_scheduler.start()

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
        logging.info(f"[方向验证] {symbol} {side} | 结果={confirmed}")
        return confirmed
    except Exception as e:
        logging.error(f"[方向验证异常] {e}")
        return True

# ==================== 加强版开仓报告（接收已计算好的 TP 价格） ====================
def send_beautiful_open_report(signal: str, symbol: str, qty: float, entry_price: float, tp1: float, tp2: float, tp3: float):
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

# ==================== 核心下单逻辑（已全面修复） ====================
def place_market_order(signal: str, symbol: str):
    try:
        current_pos = binance_client.get_current_position(symbol)
        side = "long" if signal == "OPEN_LONG" else "short"

        # 1. 有持仓就先全平
        if current_pos:
            logging.info(f"[持仓处理] 当前持有 {current_pos['side']}，收到 {signal}，执行先全平再开新仓")
            close_result = binance_client.close_all_positions(symbol)
            if close_result.get("status") != "success":
                return {"status": "error", "message": "全平失败"}
            position_manager.clear_position()
            send_beautiful_close_report("先平后开（新信号触发）", symbol)
            time.sleep(1.5)

        # 2. 方向二次验证
        if not confirm_direction(symbol, side):
            logging.warning(f"[方向验证未通过] {signal}")
            binance_client._send_dingtalk("🔴 方向二次验证未通过", f"**信号**：{signal}\n**币种**：{symbol}", is_warning=True)
            return {"status": "blocked", "reason": "方向验证未通过"}

        # 3. 开新仓
        qty = calculate_position_size(symbol)
        if qty <= 0:
            return {"status": "error", "message": "仓位计算无效"}

        order_side = "BUY" if signal == "OPEN_LONG" else "SELL"
        order = binance_client.client.futures_create_order(
            symbol=symbol,
            side=order_side,
            type="MARKET",
            quantity=qty
        )

        # 获取真实开仓均价
        entry_price = float(order.get('avgPrice', 0)) or float(binance_client.client.futures_symbol_ticker(symbol=symbol)["price"])

        # 4. 获取 ATR 并计算 TP 价格
        try:
            klines = binance_client.client.get_klines(symbol=symbol, interval="45m", limit=20)
            highs = [float(k[2]) for k in klines]
            lows = [float(k[3]) for k in klines]
            closes = [float(k[4]) for k in klines]
            tr_list = [max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])) for i in range(1, len(klines))]
            atr = sum(tr_list) / len(tr_list)
        except:
            atr = 8.0  # 兜底

        if signal == "OPEN_LONG":
            tp1 = round(entry_price + atr * 1.28, 2)
            tp2 = round(entry_price + atr * 2.5, 2)
            tp3 = round(entry_price + atr * 3.6, 2)
        else:
            tp1 = round(entry_price - atr * 1.28, 2)
            tp2 = round(entry_price - atr * 2.5, 2)
            tp3 = round(entry_price - atr * 3.6, 2)

        logging.info(f"[开仓成功] {signal} {symbol} | Qty={qty} | Entry={entry_price} | TP1={tp1}")

        # 5. 更新 PositionManager（关键！）
        position_manager.update_position(signal.replace("OPEN_", ""), symbol, qty, entry_price, tp1, tp2, tp3)

        # 6. 发送美化报告（传入正确 TP 价格）
        send_beautiful_open_report(signal, symbol, qty, entry_price, tp1, tp2, tp3)

        return {"status": "success", "side": signal, "qty": qty, "order": order}

    except Exception as e:
        logging.error(f"[下单失败] {signal} {symbol} | {e}")
        return {"status": "error", "message": str(e)}

# ==================== Webhook ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(silent=True) or extract_json_from_text(request.get_data(as_text=True))
        if not data:
            return jsonify({"status": "error", "message": "无法解析信号"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        logging.info(f"[Webhook] 收到信号 → {signal} | {symbol}")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            return jsonify(place_market_order(signal, symbol)), 200
        elif signal == "CLOSE_ALL":
            result = binance_client.close_all_positions(symbol)
            if result.get("status") == "success":
                position_manager.clear_position()
                send_beautiful_close_report("手动全平 / CLOSE_ALL", symbol)
            return jsonify(result), 200
        else:
            return jsonify({"status": "ignored"}), 200
    except Exception as e:
        logging.error(f"[Webhook异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    logging.info("=== ETH Webhook Server 启动 ===")
    app.run(host="0.0.0.0", port=5000)

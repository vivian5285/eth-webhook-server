# app.py（最终完整优美版 - 开仓/平仓/TP分批止盈全美化）
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

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient()
position_manager = PositionManager()
tp_monitor = TPMonitor()
tp_monitor.start()

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

def confirm_direction(symbol: str, side: str) -> bool:
    if not CONFIRMATION_ENABLED:
        return True
    try:
        klines = binance_client.client.get_klines(symbol=symbol, interval=TIMEFRAME, limit=50)
        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        if len(closes) < 30:
            return True

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
        vol_ok = volumes[-1] > vol_ma * 0.85
        return macd_ok and vol_ok
    except Exception as e:
        logging.error(f"[二次验证异常] {e}")
        return True

# ==================== 美化开仓成功钉钉日报 ====================
def send_beautiful_open_report(signal: str, symbol: str, qty: float):
    try:
        balance_info = binance_client.get_account_balance() or {}
        equity = balance_info.get("totalWalletBalance", 0)
        available = balance_info.get("availableBalance", 0)
        risk_amount = equity * RISK_PERCENT

        title = "✅ 开仓成功"
        content = (
            f"**信号类型**：{signal}\n"
            f"**币种**：{symbol}\n"
            f"**时间周期**：{TIMEFRAME}\n"
            f"**下单数量**：{qty}\n\n"
            f"**📊 风控信息**\n"
            f"- 风险比例：{RISK_PERCENT * 100}%\n"
            f"- 预计止损距离：{STOP_DISTANCE_PERCENT * 100}%\n"
            f"- 预估最大亏损：{risk_amount:.2f} USDT\n\n"
            f"**💰 账户快照**\n"
            f"- 账户权益：{equity:.2f} USDT\n"
            f"- 可用余额：{available:.2f} USDT\n\n"
            f"**⏰ 执行时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        binance_client._send_dingtalk(title, content)
    except Exception as e:
        logging.error(f"[开仓日报发送失败] {e}")

# ==================== 美化平仓成功钉钉日报 ====================
def send_beautiful_close_report(reason: str, symbol: str, qty: float = 0):
    try:
        balance_info = binance_client.get_account_balance() or {}
        equity = balance_info.get("totalWalletBalance", 0)
        available = balance_info.get("availableBalance", 0)

        title = "📉 平仓成功"
        content = (
            f"**平仓原因**：{reason}\n"
            f"**币种**：{symbol}\n"
            f"**平仓数量**：{qty if qty > 0 else '全部'}\n\n"
            f"**💰 账户快照**\n"
            f"- 账户权益：{equity:.2f} USDT\n"
            f"- 可用余额：{available:.2f} USDT\n\n"
            f"**⏰ 执行时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        binance_client._send_dingtalk(title, content)
    except Exception as e:
        logging.error(f"[平仓日报发送失败] {e}")

# ==================== 美化 TP 分批止盈触发钉钉日报 ====================
def send_beautiful_tp_report(reason: str, symbol: str):
    try:
        balance_info = binance_client.get_account_balance() or {}
        equity = balance_info.get("totalWalletBalance", 0)
        available = balance_info.get("availableBalance", 0)

        # 根据 reason 决定标题和描述
        if "tp1" in reason.lower():
            level = "TP1（第一止盈）"
            percent = "30%"
        elif "tp2" in reason.lower():
            level = "TP2（第二止盈）"
            percent = "30%"
        elif "tp3" in reason.lower():
            level = "TP3（最终止盈）"
            percent = "40%"
        else:
            level = reason
            percent = "部分"

        title = "💰 分批止盈触发"
        content = (
            f"**触发级别**：{level}\n"
            f"**币种**：{symbol}\n"
            f"**平仓比例**：{percent}\n\n"
            f"**💰 账户快照**\n"
            f"- 账户权益：{equity:.2f} USDT\n"
            f"- 可用余额：{available:.2f} USDT\n\n"
            f"**⏰ 执行时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        binance_client._send_dingtalk(title, content)
    except Exception as e:
        logging.error(f"[TP日报发送失败] {e}")

# ==================== 后台二次验证 ====================
def background_direction_check(symbol: str, signal: str):
    side = "long" if signal == "OPEN_LONG" else "short"
    confirmed = confirm_direction(symbol, side)

    if not confirmed:
        try:
            title = "🔴 方向二次验证未通过"
            content = (
                f"**交易信号**：{signal}\n"
                f"**币种**：{symbol}\n"
                f"**时间周期**：{TIMEFRAME}\n\n"
                f"**验证结果**：MACD + 成交量方向与信号不一致\n\n"
                f"⚠️ 建议立即人工复核当前行情方向！"
            )
            binance_client._send_dingtalk(title, content, is_warning=True)
        except Exception as e:
            logging.error(f"[方向告警发送失败] {e}")

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
        logging.info(f"[开仓成功] {signal} {symbol} | Qty={qty}")

        send_beautiful_open_report(signal, symbol, qty)

        threading.Thread(
            target=background_direction_check,
            args=(symbol, signal),
            daemon=True
        ).start()

        return {"status": "success", "side": signal, "qty": qty, "order": order}

    except Exception as e:
        logging.error(f"[下单失败] {signal} {symbol} | {e}")
        return {"status": "error", "message": str(e)}

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(silent=True)
        if not data:
            raw_text = request.get_data(as_text=True)
            data = extract_json_from_text(raw_text)

        if not data:
            logging.warning("[Webhook] 无法解析信号")
            return jsonify({"status": "error", "message": "无法解析信号"}), 400

        signal = data.get("signal")
        symbol = data.get("symbol", "ETHUSDT")
        reason = data.get("reason", "")

        logging.info(f"[Webhook] 收到信号 → {signal} | {symbol} | Timeframe={TIMEFRAME}")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            result = place_market_order(signal, symbol)
            return jsonify(result), 200

        elif signal == "CLOSE_ALL":
            result = binance_client.close_all_positions(symbol)
            if result.get("status") == "success":
                position_manager.clear_position()
                send_beautiful_close_report("手动全平 / CLOSE_ALL", symbol)
            return jsonify(result), 200

        elif signal == "TP_PARTIAL":
            send_beautiful_tp_report(reason, symbol)
            return jsonify({"status": "success"}), 200

        else:
            return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logging.error(f"[Webhook 异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

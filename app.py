# app.py（最终版 - 立即下单 + 异步二次验证 + 智能钉钉提醒）
from flask import Flask, request, jsonify
import os
import re
import json
import threading
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

# ==================== 简单方向二次验证 ====================
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

        confirmed = macd_ok and vol_ok
        logging.info(f"[二次验证] {symbol} {side} | MACD确认={macd_ok} | 成交量确认={vol_ok} | 结果={confirmed}")
        return confirmed

    except Exception as e:
        logging.error(f"[二次验证异常] {e}")
        return True

# ==================== 后台二次验证 + 智能钉钉提醒 ====================
def background_direction_check(symbol: str, signal: str):
    side = "long" if signal == "OPEN_LONG" else "short"
    confirmed = confirm_direction(symbol, side)

    if not confirmed:
        # 方向不一致时才推送钉钉提醒
        try:
            msg = f"⚠️ 方向二次验证未通过\n信号: {signal}\n币种: {symbol}\n时间周期: {TIMEFRAME}\n建议人工复核交易方向"
            # 这里调用你已有的钉钉发送函数（如果没有可先用 print 替代）
            logging.warning(f"[方向不一致告警] {msg}")
            # binance_client.send_dingtalk_warning(msg)   # 如有此方法可取消注释
        except Exception as e:
            logging.error(f"[钉钉方向告警发送失败] {e}")
    else:
        logging.info(f"[二次验证通过] {signal} {symbol} 方向一致，无需额外提醒")

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

        # 下单成功后，异步启动二次验证（不阻塞）
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

        logging.info(f"[Webhook] 收到信号 → {signal} | {symbol} | Timeframe={TIMEFRAME}")

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            # 立即下单（不等待二次验证）
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

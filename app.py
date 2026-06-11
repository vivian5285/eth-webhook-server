# app.py（最终完整加强版）
from flask import Flask, request, jsonify
import os
import re
import json
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

# ==================== 初始化 ====================
binance_client = BinanceClient()
position_manager = PositionManager()
tp_monitor = TPMonitor()
tp_monitor.start()

# 启动每日日报调度器（每天 00:05 推送）
daily_scheduler = DailyReportScheduler(binance_client, report_time="00:05")
daily_scheduler.start()

# ==================== 配置 ====================
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

        # EMA 趋势
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

        # 成交量确认
        vol_ma = sum(volumes[-20:]) / 20
        vol_ok = volumes[-1] > vol_ma * 1.1

        confirmed = ema_trend_ok and macd_ok and vol_ok
        logging.info(f"[方向验证] {symbol} {side} | EMA趋势={ema_trend_ok} | MACD={macd_ok} | 放量={vol_ok} | 结果={confirmed}")
        return confirmed

    except Exception as e:
        logging.error(f"[方向验证异常] {e}")
        return True

# ==================== 美化报告函数 ====================
def send_beautiful_open_report(signal: str, symbol: str, qty: float):
    try:
        balance = binance_client.get_account_balance() or {}
        equity = balance.get("totalWalletBalance", 0)
        available = balance.get("availableBalance", 0)

        title = "✅ 开仓成功"
        content = (
            f"**信号类型**：{signal}\n"
            f"**币种**：{symbol}\n"
            f"**下单数量**：{qty}\n\n"
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

# ==================== 下单逻辑 ====================
def place_market_order(signal: str, symbol: str):
    try:
        current_pos = binance_client.get_current_position(symbol)
        if current_pos:
            logging.warning(f"[风控] 已存在持仓，拒绝重复开 {signal}")
            return {"status": "skipped", "reason": "已有持仓"}

        # 方向二次验证
        side = "long" if signal == "OPEN_LONG" else "short"
        if not confirm_direction(symbol, side):
            logging.warning(f"[方向验证未通过] {signal}，已拦截")
            binance_client._send_dingtalk(
                "🔴 方向二次验证未通过",
                f"**信号**：{signal}\n**币种**：{symbol}\n建议人工复核！",
                is_warning=True
            )
            return {"status": "blocked", "reason": "方向验证未通过"}

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

        logging.info(f"[开仓成功] {signal} {symbol} | Qty={qty}")
        send_beautiful_open_report(signal, symbol, qty)
        return {"status": "success", "side": signal, "qty": qty, "order": order}

    except Exception as e:
        logging.error(f"[下单失败] {signal} {symbol} | {e}")
        return {"status": "error", "message": str(e)}

# ==================== Webhook 接口 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(silent=True)
        if not data:
            raw_text = request.get_data(as_text=True)
            data = extract_json_from_text(raw_text)

        if not data:
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
                send_beautiful_close_report("手动全平 / CLOSE_ALL", symbol)
            return jsonify(result), 200

        else:
            return jsonify({"status": "ignored"}), 200

    except Exception as e:
        logging.error(f"[Webhook异常] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# ==================== 启动 ====================
if __name__ == "__main__":
    logging.info("=== ETH Webhook Server 启动 ===")
    app.run(host="0.0.0.0", port=5000)

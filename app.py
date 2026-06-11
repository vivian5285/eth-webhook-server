# app.py（最终完整强壮版）
from flask import Flask, request, jsonify
import time
import traceback
import threading
import logging
import pandas as pd
from binance_client import BinanceClient
from position_manager import PositionManager
from tp_manager import get_actual_tp_prices
from dingtalk import send_dingtalk
from config import Config

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s [%(levelname)s] %(message)s'
)

app = Flask(__name__)
client = BinanceClient()
position_manager = PositionManager()

# 从持久化文件加载最后信号方向
last_signal_direction = position_manager.get_last_signal_direction()


# ==================== 仓位一致性自动检查 + 自动纠正 ====================
def position_consistency_check():
    global last_signal_direction
    while True:
        try:
            time.sleep(40)
            current_last_dir = position_manager.get_last_signal_direction() or last_signal_direction
            if not current_last_dir:
                continue

            pos = client.get_current_position(Config.SYMBOL)
            if not pos or float(pos.get("positionAmt", 0)) == 0:
                continue

            actual_side = "long" if float(pos["positionAmt"]) > 0 else "short"

            if actual_side != current_last_dir:
                logging.warning(f"[仓位不一致] 实际: {actual_side}，TV最新: {current_last_dir}，准备自动纠正")
                send_dingtalk(
                    "🔄 仓位不一致自动纠正",
                    f"实际持仓方向: {actual_side}\nTV最新信号: {current_last_dir}\n系统将先全平再按TV信号重新开仓",
                    is_warning=True
                )

                client.close_all_positions(Config.SYMBOL)
                time.sleep(1.5)

                atr = 30
                qty = client.calculate_position_size(atr)
                if current_last_dir == "long":
                    client.open_long(Config.SYMBOL, qty)
                else:
                    client.open_short(Config.SYMBOL, qty)

                logging.info(f"[自动纠正完成] 已按 {current_last_dir} 重开仓位")
        except Exception as e:
            logging.error(f"[一致性检查异常] {e}")


threading.Thread(target=position_consistency_check, daemon=True).start()


# ==================== 加强版二次验证 ====================
def secondary_verification(signal: str, timeframe: str, symbol: str = "ETHUSDT"):
    try:
        klines = client.client.futures_klines(symbol=symbol, interval=timeframe, limit=60)
        df = pd.DataFrame(klines, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])

        df['close'] = pd.to_numeric(df['close'])
        df['high'] = pd.to_numeric(df['high'])
        df['low'] = pd.to_numeric(df['low'])
        df['volume'] = pd.to_numeric(df['volume'])

        df['ema12'] = df['close'].ewm(span=12, adjust=False).mean()
        df['ema26'] = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = df['ema12'] - df['ema26']
        df['signal_line'] = df['macd'].ewm(span=9, adjust=False).mean()

        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        low_min = df['low'].rolling(14).min()
        high_max = df['high'].rolling(14).max()
        df['k'] = 100 * ((df['close'] - low_min) / (high_max - low_min))
        df['d'] = df['k'].rolling(3).mean()

        df['vol_ma'] = df['volume'].rolling(20).mean()
        volume_ok = df['volume'].iloc[-1] > df['vol_ma'].iloc[-1] * 1.1

        latest = df.iloc[-1]
        score = 0
        reasons = []

        if latest['close'] > latest['ema26']:
            score += 1; reasons.append("EMA多头")
        else:
            score -= 1; reasons.append("EMA空头")

        if latest['macd'] > latest['signal_line']:
            score += 1; reasons.append("MACD金叉")
        else:
            score -= 1; reasons.append("MACD死叉")

        if latest['rsi'] > 55:
            score += 1; reasons.append("RSI偏强")
        elif latest['rsi'] < 45:
            score -= 1; reasons.append("RSI偏弱")

        if latest['k'] > latest['d'] and latest['k'] > 50:
            score += 1; reasons.append("KDJ向上")
        elif latest['k'] < latest['d'] and latest['k'] < 50:
            score -= 1; reasons.append("KDJ向下")

        if volume_ok:
            score += 1; reasons.append("量能放大")

        trend = "long" if score >= 2 else ("short" if score <= -2 else "neutral")

        return {"trend": trend, "score": round(score, 1), "reason": " | ".join(reasons)}
    except Exception as e:
        logging.error(f"[二次验证异常] {e}")
        return {"trend": "neutral", "score": 0, "reason": "验证失败"}


# ==================== Webhook 主逻辑 ====================
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON"}), 400
    try:
        process_webhook(data)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logging.error(f"[CRITICAL] {traceback.format_exc()}")
        return jsonify({"status": "success"}), 200


def process_webhook(data: dict):
    global last_signal_direction

    signal = data.get("signal")
    symbol = data.get("symbol", Config.SYMBOL)
    atr = data.get("atr")
    reason = data.get("reason", "")
    timeframe = data.get("timeframe", "5m")

    logging.info(f"[收到信号] {signal} | timeframe: {timeframe}")

    if signal not in ["OPEN_LONG", "OPEN_SHORT", "CLOSE_ALL"]:
        return

    if signal == "OPEN_LONG":
        last_signal_direction = "long"
        position_manager.save_last_signal_direction("long")
    elif signal == "OPEN_SHORT":
        last_signal_direction = "short"
        position_manager.save_last_signal_direction("short")

    if signal in ["OPEN_LONG", "OPEN_SHORT"]:
        try:
            current_pos = client.get_current_position(symbol)
            if current_pos and float(current_pos.get("positionAmt", 0)) != 0:
                logging.info("[风控] 检测到已有持仓，先全平再开")
                client.close_all_positions(symbol)
                time.sleep(1.8)

            # 二次验证
            verification = secondary_verification(signal, timeframe, symbol)
            if verification["trend"] not in ["neutral", None]:
                expected = "long" if signal == "OPEN_LONG" else "short"
                if verification["trend"] != expected:
                    send_dingtalk(
                        "⚠️ 二次验证告警 - 方向可能不一致",
                        f"TV信号: {signal}\n多指标判断: {verification['trend']} (得分: {verification['score']})\n依据: {verification['reason']}\n已执行TV信号，建议人工复核 {timeframe} 图表",
                        is_warning=True
                    )

            qty = client.calculate_position_size(atr)
            if qty <= 0:
                send_dingtalk("风控拦截", f"计算仓位为 {qty}，已拒绝开仓", is_warning=True)
                return

            order = client.open_long(symbol, qty) if signal == "OPEN_LONG" else client.open_short(symbol, qty)

            if order:
                entry_price = float(order.get("avgPrice") or 0)
                if entry_price == 0:
                    entry_price = client.get_current_price(symbol)

                tp_prices = get_actual_tp_prices(entry_price, atr, "long" if signal == "OPEN_LONG" else "short")
                position_manager.save_position(symbol, entry_price, atr, tp_prices, "long" if signal == "OPEN_LONG" else "short")

                report = client.get_detailed_report()
                _send_open_notification(signal.replace("OPEN_", ""), qty, entry_price, tp_prices, report)
            else:
                send_dingtalk("开仓失败", f"{signal} 下单失败", is_warning=True)

        except Exception as e:
            logging.error(f"[开仓异常] {e}")
            send_dingtalk("开仓严重异常", str(e), is_warning=True)

    elif signal == "CLOSE_ALL":
        try:
            client.close_all_positions(symbol)
            position_manager.clear_position(symbol)
            report = client.get_detailed_report()
            send_dingtalk("保护性全平", f"原因: {reason}\n当前已空仓")
        except Exception as e:
            logging.error(f"[全平异常] {e}")
            send_dingtalk("全平异常", str(e), is_warning=True)


# ==================== 加强版开仓通知（含风险比例） ====================
def _send_open_notification(direction: str, qty: float, entry_price: float, tp_prices: dict, report: dict):
    dir_cn = "多" if direction.upper() == "LONG" else "空"
    risk_percent = client.get_risk_percent() * 100

    msg = (
        f"**🚀 {dir_cn} 单开仓成功**\n\n"
        f"**下单数量**：{qty}\n"
        f"**入场价格**：{entry_price}\n"
        f"**当前风险比例**：{risk_percent:.1f}%\n\n"
        f"**止盈目标**\n"
        f"• TP1：{tp_prices['tp1']}\n"
        f"• TP2：{tp_prices['tp2']}\n"
        f"• TP3：{tp_prices['tp3']}\n\n"
        f"**📊 账户快照**\n"
        f"总权益：{report.get('total_equity', 'N/A')} USDT\n"
        f"钱包余额：{report.get('wallet_balance', 'N/A')} USDT\n"
        f"可用保证金：{report.get('available_margin', 'N/A')} USDT\n"
        f"维持保证金：{report.get('maintenance_margin', 'N/A')} USDT\n"
        f"当前持仓：{report.get('position', 'N/A')}\n"
        f"未实现盈亏：{report.get('unrealized_pnl', 'N/A')} USDT\n"
        f"杠杆倍数：{report.get('leverage', 'N/A')}x"
    )
    send_dingtalk(f"{dir_cn} 单开仓成功", msg)


# ==================== 加强版 TP 通知（支持传入真实止盈金额） ====================
def send_tp_hit_report(level: str, close_price: float, profit_amount: float = None, report: dict = None):
    if report is None:
        report = client.get_detailed_report()

    level_map = {
        "tp1": "TP1（第一止盈 30%）",
        "tp2": "TP2（第二止盈 30%）",
        "tp3": "TP3（最终止盈 全平）"
    }
    level_cn = level_map.get(level.lower(), level.upper())

    profit_str = f"{profit_amount:.2f} USDT" if profit_amount is not None else "N/A（计算失败）"

    msg = (
        f"**🎯 {level_cn} 已触发**\n\n"
        f"**成交价格**：{close_price}\n"
        f"**本次止盈金额**：{profit_str}\n\n"
        f"**📊 平仓后账户快照**\n"
        f"总权益：{report.get('total_equity', 'N/A')} USDT\n"
        f"钱包余额：{report.get('wallet_balance', 'N/A')} USDT\n"
        f"可用保证金：{report.get('available_margin', 'N/A')} USDT\n"
        f"维持保证金：{report.get('maintenance_margin', 'N/A')} USDT\n"
        f"当前持仓：{report.get('position', 'N/A')}\n"
        f"未实现盈亏：{report.get('unrealized_pnl', 'N/A')} USDT\n"
        f"今日已实现盈亏：{report.get('today_realized_pnl', 'N/A')} USDT"
    )
    send_dingtalk(f"{level_cn} 触发", msg)


# ==================== 启动 TP 监控 ====================
try:
    from tp_monitor import TPMonitor
    monitor = TPMonitor(symbol=Config.SYMBOL, check_interval=Config.TP_CHECK_INTERVAL)
    monitor.start()
    logging.info("[系统启动] TP监控已成功启动（ATR动态追踪 + 早期保本移动模式）")
except Exception as e:
    logging.error(f"[TP监控启动失败] {e}")


if __name__ == "__main__":
    logging.info("[系统启动] Webhook服务已启动（最终强壮版）")
    app.run(host="0.0.0.0", port=Config.PORT, debug=Config.DEBUG)

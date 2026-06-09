from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


class BinanceClient:
    def __init__(self, api_key, api_secret, risk_percent=0.90, max_leverage=3.0,
                 client_name="主账户"):
        self.client = Client(api_key, api_secret)
        self.risk_percent = risk_percent
        self.max_leverage = max_leverage
        self.client_name = client_name

    # ==================== 获取当前持仓 ====================
    def get_current_position(self, symbol: str = "ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            if not positions:
                return {"positionAmt": 0, "entryPrice": 0, "unrealizedProfit": 0, "leverage": 0}
            pos = positions[0]
            return {
                "positionAmt": float(pos.get("positionAmt", 0)),
                "entryPrice": float(pos.get("entryPrice", 0)),
                "unrealizedProfit": float(pos.get("unRealizedProfit", 0)),
                "leverage": float(pos.get("leverage", 0)),
            }
        except Exception as e:
            logging.error(f"[获取持仓异常] {symbol} - {e}")
            return {"positionAmt": 0, "entryPrice": 0, "unrealizedProfit": 0, "leverage": 0}

    # ==================== 获取账户权益 ====================
    def get_account_equity(self):
        try:
            account = self.client.futures_account()
            return float(account.get("totalWalletBalance", 0)) + float(account.get("totalUnrealizedProfit", 0))
        except Exception as e:
            logging.error(f"[获取账户权益失败] {e}")
            return 0.0

    # ==================== 智能仓位计算（分层风控） ====================
    def calculate_position_size(self, stop_distance: float, symbol: str = "ETHUSDT"):
        """
        根据账户权益自动分层计算仓位
        小资金(<3000)：更保守
        中资金(3000~10000)：均衡
        大资金(>10000)：适度激进但有上限
        """
        equity = self.get_account_equity()
        if equity <= 0 or stop_distance <= 0:
            return 0

        # 分层风险系数
        if equity < 3000:
            risk_mult = 0.6          # 小资金更保守
            max_position_value = 3000
        elif equity < 10000:
            risk_mult = 0.85         # 中等资金
            max_position_value = 8000
        else:
            risk_mult = 1.0          # 大资金
            max_position_value = 15000

        risk_amount = equity * (self.risk_percent * risk_mult) / 100
        raw_qty = risk_amount / stop_distance

        # 按最大仓位价值限制
        price = self.client.get_symbol_ticker(symbol=symbol)["price"]
        max_qty_by_value = max_position_value / float(price)

        final_qty = min(raw_qty, max_qty_by_value)

        # 币安精度处理（ETHUSDT 通常保留3位）
        final_qty = max(0.001, round(final_qty, 3))
        return final_qty

    # ==================== 部分平仓（TP1/TP2/TP3 智能计算） ====================
    def close_partial_position(self, symbol: str, percent: float):
        """
        按当前剩余仓位百分比平仓（智慧大脑核心）
        percent: 0.3 = 平当前仓位的30%
        """
        try:
            position = self.get_current_position(symbol)
            current_amt = float(position.get("positionAmt", 0))

            if current_amt == 0:
                logging.info(f"[部分平仓跳过] {symbol} 当前无持仓")
                return {"status": "skipped", "reason": "无持仓"}

            close_qty = abs(current_amt) * percent
            close_qty = max(0.001, round(close_qty, 3))

            side = "SELL" if current_amt > 0 else "BUY"

            logging.info(f"[部分平仓] {symbol} | 当前持仓: {current_amt} | 平仓比例: {percent*100}% | 本次平: {close_qty}")

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=close_qty,
                reduceOnly=True
            )
            return {"status": "success", "closed_qty": close_qty, "order": order}

        except BinanceAPIException as e:
            logging.error(f"[部分平仓失败] {symbol} - {e}")
            return {"status": "error", "message": str(e)}
        except Exception as e:
            logging.error(f"[部分平仓异常] {symbol} - {e}")
            return {"status": "error", "message": str(e)}

    # ==================== 全平仓位 ====================
    def close_all_positions(self, symbol: str = "ETHUSDT"):
        try:
            position = self.get_current_position(symbol)
            amt = float(position.get("positionAmt", 0))

            if amt == 0:
                logging.info(f"[全平跳过] {symbol} 当前无持仓")
                return {"status": "skipped", "reason": "无持仓"}

            side = "SELL" if amt > 0 else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=abs(amt),
                reduceOnly=True
            )
            logging.info(f"[全平成功] {symbol} | 平仓数量: {abs(amt)}")
            return {"status": "success", "order": order}

        except Exception as e:
            logging.error(f"[全平异常] {symbol} - {e}")
            return {"status": "error", "message": str(e)}

    # ==================== 获取账户报表（用于钉钉） ====================
    def get_account_report(self):
        try:
            equity = self.get_account_equity()
            position = self.get_current_position("ETHUSDT")
            pos_amt = float(position.get("positionAmt", 0))

            if pos_amt == 0:
                pos_info = "无持仓"
            else:
                direction = "多" if pos_amt > 0 else "空"
                pos_info = f"{direction} {abs(pos_amt)} @ {position.get('entryPrice')} (杠杆 {position.get('leverage')}x)"

            return (
                f"**权益**：{equity:.2f} USDT\n"
                f"**当前持仓**：{pos_info}\n"
                f"**更新时间**：{datetime.now().strftime('%H:%M:%S')}"
            )
        except Exception as e:
            logging.error(f"获取账户报表失败: {e}")
            return "账户信息获取失败"

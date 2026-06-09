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

    # ==================== 智能仓位计算（小资金更激进版） ====================
    def calculate_position_size(self, stop_distance: float, symbol: str = "ETHUSDT"):
        """
        根据账户权益分层风控（激进版）：
        - < 3000U     : 单笔风险约 4.5%（帮助小资金快速滚雪球）
        - 3000~10000U : 单笔风险约 2.0%
        - > 10000U    : 单笔风险约 1.0%（保守）
        """
        equity = self.get_account_equity()
        if equity <= 0 or stop_distance <= 0:
            return 0.0

        # ==================== 分层风险参数 ====================
        if equity < 3000:
            # 小资金：激进
            effective_risk_percent = 4.5
            max_position_value = equity * 7.0     # 允许较高杠杆

        elif equity < 10000:
            # 中等资金
            effective_risk_percent = 2.0
            max_position_value = equity * 4.0

        else:
            # 大资金：保守
            effective_risk_percent = 1.0
            max_position_value = equity * 2.5

        # 计算风险金额
        risk_amount = equity * effective_risk_percent / 100
        raw_qty = risk_amount / stop_distance

        # 按最大仓位价值限制
        try:
            price = float(self.client.get_symbol_ticker(symbol=symbol)["price"])
            max_qty_by_value = max_position_value / price
        except:
            max_qty_by_value = raw_qty

        final_qty = min(raw_qty, max_qty_by_value)

        # 币安精度处理
        final_qty = max(0.001, round(final_qty, 3))

        logging.info(f"[仓位计算] 权益: {equity:.2f}U | 有效风险: {effective_risk_percent}% | 最终仓位: {final_qty}")
        return final_qty

    # ==================== 部分平仓 ====================
    def close_partial_position(self, symbol: str, percent: float):
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

    # ==================== 获取账户报表 ====================
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

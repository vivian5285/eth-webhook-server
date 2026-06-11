# binance_client.py（最终完整强壮版）
import logging
import math
from binance.client import Client
from binance.exceptions import BinanceAPIException

class BinanceClient:
    def __init__(self, 
                 api_key: str = None, 
                 api_secret: str = None,
                 risk_percent: float = 0.03,
                 max_leverage: float = 3.0,
                 atr_multiplier_sl: float = 0.92,
                 max_position_value_usdt: float = 50000):
        
        self.client = Client(api_key, api_secret)
        self.risk_percent = risk_percent
        self.max_leverage = max_leverage
        self.atr_multiplier_sl = atr_multiplier_sl
        self.max_position_value_usdt = max_position_value_usdt
        
        logging.info("[BinanceClient] 初始化完成")

    # ==================== 资金规模差异化风险管理 ====================
    def get_risk_percent(self) -> float:
        """
        根据当前账户权益自动判断资金规模，返回对应风险比例
        - 小资金（< 3000U）：7%
        - 中资金（3000~10000U）：4.5%
        - 大资金（> 10000U）：2.5%
        """
        try:
            account = self.client.futures_account()
            total_equity = float(account.get("totalWalletBalance", 0)) + float(account.get("totalUnrealizedProfit", 0))

            if total_equity < 3000:
                risk = 0.07          # 小资金：快速滚仓
            elif total_equity < 10000:
                risk = 0.045         # 中资金
            else:
                risk = 0.025         # 大资金：保守

            logging.info(f"[资金规模判断] 当前权益 ≈ {total_equity:.2f}U → 风险比例: {risk*100}%")
            return risk

        except Exception as e:
            logging.error(f"[获取资金规模失败] 使用默认 3% | 错误: {e}")
            return 0.03

    # ==================== 动态仓位计算 ====================
    def calculate_position_size(self, atr: float = None) -> float:
        """根据当前资金规模动态计算下单数量"""
        try:
            if atr is None or atr <= 0:
                atr = 30

            risk_percent = self.get_risk_percent()
            account = self.client.futures_account()
            equity = float(account.get("totalWalletBalance", 0)) + float(account.get("totalUnrealizedProfit", 0))

            risk_amount = equity * risk_percent
            stop_distance = atr * self.atr_multiplier_sl or (atr * 0.92)

            raw_qty = risk_amount / stop_distance
            current_price = self.get_current_price()
            max_allowed_qty = (equity * self.max_leverage) / current_price

            final_qty = max(math.floor(min(raw_qty, max_allowed_qty)), 1)

            logging.info(f"[动态仓位计算] 权益: {equity:.2f}U | 风险: {risk_percent*100}% | 下单数量: {final_qty}")
            return final_qty

        except Exception as e:
            logging.error(f"[仓位计算异常] {e}")
            return 1

    # ==================== 交易方法 ====================
    def get_current_price(self, symbol: str = "ETHUSDT") -> float:
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as e:
            logging.error(f"[获取价格失败] {e}")
            return 0.0

    def get_current_position(self, symbol: str = "ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for pos in positions:
                if float(pos["positionAmt"]) != 0:
                    return pos
            return None
        except Exception as e:
            logging.error(f"[获取持仓失败] {e}")
            return None

    def open_long(self, symbol: str, quantity: float):
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side="BUY",
                type="MARKET",
                quantity=quantity,
                positionSide="BOTH"
            )
            logging.info(f"[开多成功] {symbol} | Qty: {quantity}")
            return order
        except BinanceAPIException as e:
            logging.error(f"[开多失败] {e}")
            return None

    def open_short(self, symbol: str, quantity: float):
        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side="SELL",
                type="MARKET",
                quantity=quantity,
                positionSide="BOTH"
            )
            logging.info(f"[开空成功] {symbol} | Qty: {quantity}")
            return order
        except BinanceAPIException as e:
            logging.error(f"[开空失败] {e}")
            return None

    def close_all_positions(self, symbol: str):
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "skipped", "reason": "无持仓"}

            qty = abs(float(position["positionAmt"]))
            side = "SELL" if float(position["positionAmt"]) > 0 else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty,
                reduceOnly=True,
                positionSide="BOTH"
            )
            logging.info(f"[全平成功] {symbol}")
            return {"status": "success", "order": order}
        except Exception as e:
            logging.error(f"[全平失败] {e}")
            return {"status": "error", "message": str(e)}

    def get_detailed_report(self, symbol: str = "ETHUSDT"):
        """获取详细账户快照（用于钉钉推送）"""
        try:
            account = self.client.futures_account()
            position = self.get_current_position(symbol)

            report = {
                "total_equity": round(float(account.get("totalWalletBalance", 0)) + float(account.get("totalUnrealizedProfit", 0)), 2),
                "wallet_balance": round(float(account.get("totalWalletBalance", 0)), 2),
                "available_margin": round(float(account.get("availableBalance", 0)), 2),
                "maintenance_margin": round(float(account.get("totalMaintenanceMargin", 0)), 2),
                "unrealized_pnl": round(float(account.get("totalUnrealizedProfit", 0)), 2),
                "position": "无持仓",
                "leverage": "N/A"
            }

            if position:
                report.update({
                    "position": f"{position['positionSide']} {abs(float(position['positionAmt']))}",
                    "leverage": position.get("leverage", "N/A"),
                    "entry_price": position.get("entryPrice", "N/A"),
                    "unrealized_pnl": round(float(position.get("unRealizedProfit", 0)), 2)
                })

            return report
        except Exception as e:
            logging.error(f"[获取账户报表失败] {e}")
            return {"error": str(e)}

    def get_account_balance(self):
        try:
            account = self.client.futures_account()
            return float(account.get("totalWalletBalance", 0))
        except Exception as e:
            logging.error(f"[获取余额失败] {e}")
            return 0.0

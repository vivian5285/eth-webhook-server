from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)

class BinanceClient:
    def __init__(self, api_key, api_secret, 
                 risk_percent=0.85, 
                 max_leverage=3.0,
                 atr_multiplier_sl=0.92,
                 max_position_value_usdt=5000,
                 client_name="未知账户"):
        self.client = Client(api_key, api_secret)
        self.risk_percent = risk_percent
        self.max_leverage = max_leverage
        self.atr_multiplier_sl = atr_multiplier_sl
        self.max_position_value_usdt = max_position_value_usdt
        self.client_name = client_name

    # ==================== 获取当前持仓 ====================
    def get_current_position(self, symbol: str):
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
                "liquidationPrice": float(pos.get("liquidationPrice", 0)),
            }
        except BinanceAPIException as e:
            logging.error(f"[获取持仓异常] {symbol} - {e}")
            return {"positionAmt": 0, "entryPrice": 0, "unrealizedProfit": 0}
        except Exception as e:
            logging.error(f"[获取持仓未知异常] {symbol} - {e}")
            return {"positionAmt": 0, "entryPrice": 0, "unrealizedProfit": 0}

    # ==================== 全平仓位 ====================
    def close_all_positions(self, symbol: str):
        try:
            position = self.get_current_position(symbol)
            position_amt = position.get("positionAmt", 0)

            if position_amt == 0:
                return {"status": "skipped", "reason": "当前无持仓"}

            side = "SELL" if position_amt > 0 else "BUY"
            qty = abs(position_amt)

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty,
                reduceOnly=True
            )
            logging.info(f"[全平成功] {symbol} | Qty: {qty}")
            return {"status": "success", "order": order}
        except BinanceAPIException as e:
            logging.error(f"[全平异常] {symbol} - {e}")
            return {"status": "error", "message": str(e)}
        except Exception as e:
            logging.error(f"[全平未知异常] {symbol} - {e}")
            return {"status": "error", "message": str(e)}

    # ==================== 开仓（核心方法） ====================
    def smart_open_position(self, symbol: str, side: str, atr_value: float = None):
        try:
            # 获取当前持仓
            position = self.get_current_position(symbol)
            current_amt = float(position.get('positionAmt', 0))

            # 如果有反向持仓，先全平
            if (side == "LONG" and current_amt < 0) or (side == "SHORT" and current_amt > 0):
                self.close_all_positions(symbol)

            # 计算下单数量
            qty = self._calculate_position_size(symbol)

            order_side = "BUY" if side == "LONG" else "SELL"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type="MARKET",
                quantity=qty,
                # 单向持仓模式不要加 positionSide
            )

            logging.info(f"[开仓成功] {symbol} {side} | Qty: {qty}")
            return {"status": "success", "order": order}

        except BinanceAPIException as e:
            logging.error(f"[开仓异常] {symbol} {side} - {e}")
            return {"status": "error", "message": str(e)}
        except Exception as e:
            logging.error(f"[开仓未知异常] {symbol} {side} - {e}")
            return {"status": "error", "message": str(e)}

    # ==================== 计算仓位大小 ====================
    def _calculate_position_size(self, symbol: str):
        try:
            account = self.client.futures_account()
            equity = float(account.get('totalWalletBalance', 0)) + float(account.get('totalUnrealizedProfit', 0))
            risk_amount = equity * (self.risk_percent / 100)

            # 简化处理：用固定风险金额 / 预估每点风险
            # 你可以根据需要改成用 ATR 计算更精确的仓位
            estimated_risk_per_unit = 50   # 临时值，你可以改成 ATR * 2
            qty = max(1, int(risk_amount / estimated_risk_per_unit))

            # 限制最大仓位价值
            current_price = float(self.client.futures_symbol_ticker(symbol=symbol)['price'])
            max_qty_by_value = int(self.max_position_value_usdt / current_price)
            return min(qty, max_qty_by_value)

        except Exception as e:
            logging.error(f"计算仓位失败: {e}")
            return 1000   # 兜底值

    # ==================== 获取账户报表（用于钉钉） ====================
    def get_account_report(self):
        try:
            account = self.client.futures_account()
            positions = self.client.futures_position_information()

            equity = float(account.get("totalWalletBalance", 0)) + float(account.get("totalUnrealizedProfit", 0))
            wallet_balance = float(account.get("totalWalletBalance", 0))
            available_margin = float(account.get("availableBalance", 0))
            unrealized_pnl = float(account.get("totalUnrealizedProfit", 0))

            current_position = "无持仓"
            for pos in positions:
                if float(pos.get("positionAmt", 0)) != 0:
                    current_position = f"{pos['symbol']} {pos['positionAmt']}"

            report = (
                f"权益: {equity:.2f} USDT\n"
                f"钱包余额: {wallet_balance:.2f} USDT\n"
                f"可用保证金: {available_margin:.2f} USDT\n"
                f"未实现盈亏: {unrealized_pnl:.2f} USDT\n"
                f"当前持仓: {current_position}\n"
                f"更新时间: {datetime.now().strftime('%H:%M:%S')}"
            )
            return report
        except Exception as e:
            logging.error(f"获取账户报表失败: {e}")
            return "账户信息获取失败"

    # ==================== 发送钉钉通知 ====================
    def _send_dingtalk(self, message: str):
        # 这里保留你原来的钉钉发送逻辑
        # 如果需要我帮你补全具体的钉钉发送代码，可以告诉我你的 webhook 地址
        pass

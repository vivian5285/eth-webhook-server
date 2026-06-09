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
        self.risk_percent = risk_percent          # 单笔风险占比
        self.max_leverage = max_leverage
        self.atr_multiplier_sl = atr_multiplier_sl
        self.max_position_value_usdt = max_position_value_usdt
        self.client_name = client_name

    # ==================== 获取当前持仓 ====================
    def get_current_position(self, symbol: str):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            if not positions:
                return {"positionAmt": 0, "entryPrice": 0, "unrealizedProfit": 0}
            pos = positions[0]
            return {
                "positionAmt": float(pos.get("positionAmt", 0)),
                "entryPrice": float(pos.get("entryPrice", 0)),
                "unrealizedProfit": float(pos.get("unRealizedProfit", 0)),
                "leverage": float(pos.get("leverage", 0)),
            }
        except Exception as e:
            logging.error(f"[获取持仓异常] {symbol} - {e}")
            return {"positionAmt": 0, "entryPrice": 0, "unrealizedProfit": 0}

    # ==================== 全平仓位 ====================
    def close_all_positions(self, symbol: str):
        try:
            position = self.get_current_position(symbol)
            amt = position.get("positionAmt", 0)
            if amt == 0:
                return {"status": "skipped", "reason": "无持仓"}

            side = "SELL" if amt > 0 else "BUY"
            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=abs(amt),
                reduceOnly=True
            )
            logging.info(f"[全平成功] {symbol}")
            return {"status": "success", "order": order}
        except Exception as e:
            logging.error(f"[全平失败] {symbol} - {e}")
            return {"status": "error", "message": str(e)}

    # ==================== 智能开仓（智慧大脑核心） ====================
    def smart_open_position(self, symbol: str, side: str, atr_value: float = None):
        try:
            position = self.get_current_position(symbol)
            current_amt = float(position.get('positionAmt', 0))

            # 1. 如果有反向持仓，先平掉
            if (side == "LONG" and current_amt < 0) or (side == "SHORT" and current_amt > 0):
                self.close_all_positions(symbol)

            # 2. 计算安全仓位大小（ATR动态 + 风险控制）
            qty = self._calculate_safe_position_size(symbol, atr_value, side)

            if qty <= 0:
                logging.warning(f"[拒绝开仓] {symbol} {side} | 计算仓位为0，风控拦截")
                return {"status": "rejected", "reason": "风控拦截（仓位为0）"}

            order_side = "BUY" if side == "LONG" else "SELL"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type="MARKET",
                quantity=qty,
            )

            logging.info(f"[开仓成功] {symbol} {side} | Qty: {qty}")
            return {"status": "success", "order": order, "qty": qty}

        except BinanceAPIException as e:
            logging.error(f"[开仓异常] {symbol} {side} - {e}")
            return {"status": "error", "message": str(e)}
        except Exception as e:
            logging.error(f"[开仓未知异常] {symbol} {side} - {e}")
            return {"status": "error", "message": str(e)}

    # ==================== ATR 动态仓位计算（带风控） ====================
    def _calculate_safe_position_size(self, symbol: str, atr_value: float = None, side: str = None):
        try:
            account = self.client.futures_account()
            equity = float(account.get('totalWalletBalance', 0)) + float(account.get('totalUnrealizedProfit', 0))

            # 使用 ATR 计算止损距离
            if atr_value and atr_value > 0:
                stop_distance = atr_value * self.atr_multiplier_sl
            else:
                # 没有 ATR 时用固定比例
                current_price = float(self.client.futures_symbol_ticker(symbol=symbol)['price'])
                stop_distance = current_price * 0.008   # 默认 0.8%

            # 单笔风险金额
            risk_amount = equity * (self.risk_percent / 100)

            # 理论仓位数量
            raw_qty = risk_amount / stop_distance

            # 限制最大仓位价值（防止极端情况重仓）
            current_price = float(self.client.futures_symbol_ticker(symbol=symbol)['price'])
            max_qty_by_value = self.max_position_value_usdt / current_price

            final_qty = min(raw_qty, max_qty_by_value)
            final_qty = max(1, int(final_qty))   # 至少1个单位

            # 日志记录风险情况
            position_value = final_qty * current_price
            margin_ratio = (position_value / equity) * 100 if equity > 0 else 0
            logging.info(f"[仓位计算] {symbol} | 权益: {equity:.2f} | 风险金额: {risk_amount:.2f} | "
                         f"仓位价值: {position_value:.2f} | 保证金占比≈{margin_ratio:.2f}%")

            return final_qty

        except Exception as e:
            logging.error(f"计算安全仓位失败: {e}")
            return 0

    # ==================== 获取账户报表 ====================
    def get_account_report(self):
        try:
            account = self.client.futures_account()
            equity = float(account.get("totalWalletBalance", 0)) + float(account.get("totalUnrealizedProfit", 0))
            wallet = float(account.get("totalWalletBalance", 0))
            available = float(account.get("availableBalance", 0))
            unrealized = float(account.get("totalUnrealizedProfit", 0))

            return (f"权益: {equity:.2f} USDT | 钱包: {wallet:.2f} | "
                    f"可用: {available:.2f} | 未实现: {unrealized:.2f}")
        except Exception as e:
            return f"报表获取失败: {e}"

    def _send_dingtalk(self, message: str):
        # 保留你原来的钉钉发送逻辑
        pass

import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


class BinanceClient:
    def __init__(self, api_key: str, api_secret: str,
                 risk_percent: float = 0.85,
                 max_leverage: float = 3.0,
                 atr_multiplier_sl: float = 0.92,
                 max_position_value_usdt: float = 5000):

        self.client = Client(api_key, api_secret)
        self.RISK_CONFIG = {
            "risk_percent": risk_percent,
            "max_leverage": max_leverage,
            "atr_multiplier_sl": atr_multiplier_sl,
            "max_position_value_usdt": max_position_value_usdt
        }
        logging.info(f"BinanceClient 初始化成功 | Risk={risk_percent}% | MaxLev={max_leverage}x | ATR倍数={atr_multiplier_sl}")

    # ====================== 查询账户余额 ======================
    def get_account_balance(self) -> float:
        try:
            account = self.client.futures_account()
            balance = float(account['totalWalletBalance'])
            return balance
        except Exception as e:
            logging.error(f"获取账户余额失败: {e}")
            return 0.0

    # ====================== 查询当前持仓 ======================
    def get_current_position(self, symbol: str):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for pos in positions:
                if float(pos['positionAmt']) != 0:
                    return {
                        "symbol": pos['symbol'],
                        "positionAmt": float(pos['positionAmt']),
                        "entryPrice": float(pos['entryPrice']),
                        "unRealizedProfit": float(pos['unRealizedProfit']),
                        "leverage": float(pos.get('leverage', 1))
                    }
            return None
        except BinanceAPIException as e:
            logging.error(f"[持仓查询错误] {e}")
            return None
        except Exception as e:
            logging.error(f"[持仓查询异常] {e}")
            return None

    def _get_current_price(self, symbol: str) -> float:
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            logging.error(f"获取当前价格失败: {e}")
            return 0.0

    # ====================== 交易前风控检查 ======================
    def _pre_trade_risk_check(self, symbol: str, side: str) -> tuple[bool, str]:
        try:
            position = self.get_current_position(symbol)

            # 检查是否已有持仓
            if position:
                current_qty = position['positionAmt']
                if (side == "LONG" and current_qty > 0) or (side == "SHORT" and current_qty < 0):
                    return False, "已持同方向仓位，跳过开仓"
                if (side == "LONG" and current_qty < 0) or (side == "SHORT" and current_qty > 0):
                    return False, "已有反方向持仓，建议先平仓"

            # 检查余额
            balance = self.get_account_balance()
            if balance < 30:
                return False, f"余额过低 (${balance:.2f})，暂停交易"

            # 检查最大持仓价值
            max_value = self.RISK_CONFIG["max_position_value_usdt"]
            if position:
                current_value = abs(position['positionAmt']) * position['entryPrice']
                if current_value > max_value * 0.85:
                    return False, "当前持仓价值已接近上限"

            return True, "风控检查通过"

        except Exception as e:
            logging.error(f"风控检查异常: {e}")
            return False, f"风控检查失败: {str(e)}"

    # ====================== 智能仓位计算（支持 ATR） ======================
    def calculate_position_size(self, symbol: str, side: str, atr_value: float = None) -> float:
        try:
            balance = self.get_account_balance()
            if balance <= 0:
                return 0

            current_price = self._get_current_price(symbol)
            if current_price <= 0:
                return 0

            # 计算止损距离
            if atr_value and atr_value > 0:
                stop_distance = atr_value * self.RISK_CONFIG["atr_multiplier_sl"]
            else:
                stop_distance = current_price * 0.008   # 兜底逻辑

            stop_distance = max(stop_distance, current_price * 0.003)

            risk_amount = balance * (self.RISK_CONFIG["risk_percent"] / 100)
            raw_qty = risk_amount / stop_distance

            # 最大持仓价值限制
            max_value = min(
                balance * self.RISK_CONFIG["max_leverage"],
                self.RISK_CONFIG["max_position_value_usdt"]
            )
            max_qty_by_value = max_value / current_price

            final_qty = min(raw_qty, max_qty_by_value)
            return round(max(final_qty, 0.001), 3)

        except Exception as e:
            logging.error(f"[仓位计算错误] {e}")
            return 0

    # ====================== 开仓 ======================
    def open_position(self, symbol: str, side: str, atr_value: float = None):
        can_trade, reason = self._pre_trade_risk_check(symbol, side)
        if not can_trade:
            logging.warning(f"[风控拦截] {symbol} {side} → {reason}")
            return {"status": "skipped", "reason": reason}

        qty = self.calculate_position_size(symbol, side, atr_value)
        if qty <= 0:
            return {"status": "error", "message": "计算出的仓位数量为0"}

        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side="BUY" if side == "LONG" else "SELL",
                type="MARKET",
                quantity=qty,
                positionSide="BOTH"
            )
            logging.info(f"[开{side}成功] {symbol} | 数量: {qty}")
            return {"status": "success", "order": order, "qty": qty}

        except BinanceAPIException as e:
            logging.error(f"[开{side}失败] Binance错误: {e}")
            return {"status": "error", "message": str(e)}
        except Exception as e:
            logging.error(f"[开{side}异常] {e}")
            return {"status": "error", "message": str(e)}

    # ====================== 全平 ======================
    def close_all_positions(self, symbol: str):
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "skipped", "reason": "当前无持仓"}

            qty = abs(position['positionAmt'])
            side = "SELL" if position['positionAmt'] > 0 else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty,
                reduceOnly=True,
                positionSide="BOTH"
            )
            logging.info(f"[全平成功] {symbol} | 数量: {qty}")
            return {"status": "success", "order": order}

        except BinanceAPIException as e:
            logging.error(f"[全平失败] Binance错误: {e}")
            return {"status": "error", "message": str(e)}
        except Exception as e:
            logging.error(f"[全平异常] {e}")
            return {"status": "error", "message": str(e)}

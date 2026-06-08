import logging
from binance.client import Client
from binance.exceptions import BinanceAPIException
from datetime import datetime, date

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class BinanceClient:
    def __init__(self, api_key: str, api_secret: str,
                 risk_percent: float = 0.85,
                 max_leverage: float = 3.0,
                 atr_multiplier_sl: float = 0.92,
                 max_position_value_usdt: float = 5000,
                 max_daily_loss_percent: float = 5.0,
                 max_consecutive_loss: int = 3):

        self.client = Client(api_key, api_secret)
        self.RISK_CONFIG = {
            "risk_percent": risk_percent,
            "max_leverage": max_leverage,
            "atr_multiplier_sl": atr_multiplier_sl,
            "max_position_value_usdt": max_position_value_usdt,
            "max_daily_loss_percent": max_daily_loss_percent,
            "max_consecutive_loss": max_consecutive_loss
        }

        self.consecutive_loss_count = 0
        self.last_trade_date = date.today()
        logging.info(f"BinanceClient 初始化成功 | Risk={risk_percent}% | MaxLev={max_leverage}x")

    # ==================== 基础方法 ====================
    def get_account_balance(self) -> float:
        try:
            account = self.client.futures_account()
            return float(account['totalWalletBalance'])
        except Exception as e:
            logging.error(f"获取余额失败: {e}")
            return 0.0

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
        except Exception as e:
            logging.error(f"查询持仓失败: {e}")
            return None

    def _get_current_price(self, symbol: str) -> float:
        try:
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            logging.error(f"获取当前价格失败: {e}")
            return 0.0

    # ==================== 增强风控 ====================
    def _check_daily_loss(self) -> tuple[bool, str]:
        """每日最大亏损保护"""
        try:
            account = self.client.futures_account()
            unrealized_pnl = float(account.get('totalUnrealizedProfit', 0))
            wallet_balance = float(account['totalWalletBalance'])
            total_pnl = float(account.get('totalMaintMargin', 0)) + unrealized_pnl  # 简化计算

            daily_loss_pct = abs(total_pnl) / wallet_balance * 100 if wallet_balance > 0 else 0

            if daily_loss_pct > self.RISK_CONFIG["max_daily_loss_percent"]:
                return False, f"当日亏损已达 {daily_loss_pct:.2f}%，超过限制"
            return True, "每日亏损检查通过"
        except Exception as e:
            return False, f"每日亏损检查异常: {str(e)}"

    def _check_consecutive_loss(self) -> tuple[bool, str]:
        """连续亏损保护"""
        if self.consecutive_loss_count >= self.RISK_CONFIG["max_consecutive_loss"]:
            return False, f"连续亏损已达 {self.consecutive_loss_count} 次，暂停交易"
        return True, "连续亏损检查通过"

    def _update_consecutive_loss(self, is_profit: bool):
        """更新连续亏损计数"""
        if is_profit:
            self.consecutive_loss_count = 0
        else:
            self.consecutive_loss_count += 1

    # ==================== 智能开仓（支持单向持仓反转） ====================
    def smart_open_position(self, symbol: str, side: str, atr_value: float = None):
        # 风控检查
        can_trade, reason = self._check_daily_loss()
        if not can_trade:
            return {"status": "skipped", "reason": reason}

        can_trade, reason = self._check_consecutive_loss()
        if not can_trade:
            return {"status": "skipped", "reason": reason}

        position = self.get_current_position(symbol)

        # 如果有反向持仓，先平仓
        if position:
            current_side = "LONG" if position['positionAmt'] > 0 else "SHORT"
            if current_side != side:
                logging.info(f"[反转处理] 检测到 {current_side} → 先平仓再开 {side}")
                close_result = self.close_all_positions(symbol)
                if close_result.get("status") != "success":
                    return {"status": "error", "message": f"平仓失败: {close_result}"}

        # 执行开仓
        return self.open_position(symbol, side, atr_value=atr_value)

    # ==================== 普通开仓 ====================
    def open_position(self, symbol: str, side: str, atr_value: float = None):
        qty = self.calculate_position_size(symbol, side, atr_value)
        if qty <= 0:
            return {"status": "error", "message": "计算仓位为0"}

        try:
            order = self.client.futures_create_order(
                symbol=symbol,
                side="BUY" if side == "LONG" else "SELL",
                type="MARKET",
                quantity=qty,
                positionSide="BOTH"
            )
            logging.info(f"[开{side}成功] {symbol} | Qty: {qty}")
            return {"status": "success", "order": order, "qty": qty}
        except BinanceAPIException as e:
            logging.error(f"[开{side}失败] {e}")
            return {"status": "error", "message": str(e)}

    def close_all_positions(self, symbol: str):
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "skipped", "reason": "无持仓"}

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
            logging.info(f"[全平成功] {symbol}")
            return {"status": "success", "order": order}
        except Exception as e:
            logging.error(f"[全平失败] {e}")
            return {"status": "error", "message": str(e)}

    def calculate_position_size(self, symbol: str, side: str, atr_value: float = None) -> float:
        try:
            balance = self.get_account_balance()
            if balance <= 0:
                return 0
            current_price = self._get_current_price(symbol)
            if current_price <= 0:
                return 0

            if atr_value and atr_value > 0:
                stop_distance = atr_value * self.RISK_CONFIG["atr_multiplier_sl"]
            else:
                stop_distance = current_price * 0.008

            stop_distance = max(stop_distance, current_price * 0.003)
            risk_amount = balance * (self.RISK_CONFIG["risk_percent"] / 100)
            raw_qty = risk_amount / stop_distance
            max_value = min(balance * self.RISK_CONFIG["max_leverage"],
                            self.RISK_CONFIG["max_position_value_usdt"])
            max_qty = max_value / current_price
            final_qty = min(raw_qty, max_qty)
            return round(max(final_qty, 0.001), 3)
        except Exception as e:
            logging.error(f"仓位计算失败: {e}")
            return 0

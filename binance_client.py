from binance.client import Client
from binance.enums import *
import math

class BinanceClient:
    def __init__(self, api_key, api_secret):
        self.client = Client(api_key, api_secret)

    # ====================== 风控参数 ======================
    RISK_CONFIG = {
        "base_risk_percent": 0.90,        # 每笔基础风险比例
        "max_position_percent": 30,       # 单笔最大持仓占账户权益比例
        "max_leverage": 3.0,
        "max_pyramiding": 1,              # 最多允许加仓1次
    }

    def get_account_balance(self):
        """查询账户 USDT 余额"""
        try:
            account = self.client.futures_account()
            balance = float(account['totalWalletBalance'])
            return balance
        except Exception as e:
            print(f"[余额查询错误] {str(e)}")
            return 0.0

    def get_current_position(self, symbol):
        """查询当前持仓"""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for pos in positions:
                if float(pos['positionAmt']) != 0:
                    return {
                        "symbol": pos['symbol'],
                        "positionAmt": float(pos['positionAmt']),
                        "entryPrice": float(pos['entryPrice']),
                        "unRealizedProfit": float(pos['unRealizedProfit']),
                        "leverage": float(pos['leverage'])
                    }
            return None
        except Exception as e:
            print(f"[持仓查询错误] {str(e)}")
            return None

    def calculate_position_size(self, symbol, side, atr_value=None):
        """
        根据账户权益动态计算仓位
        atr_value: 可选，如果传入ATR则使用，否则用固定止损比例
        """
        try:
            balance = self.get_account_balance()
            if balance <= 0:
                return 0

            # 风险金额
            risk_amount = balance * (self.RISK_CONFIG["base_risk_percent"] / 100)

            # 止损距离（这里简化处理，实际可用ATR）
            # 假设止损距离为当前价格的 1.5%
            current_price = float(self.client.futures_symbol_ticker(symbol=symbol)['price'])
            stop_distance = current_price * 0.015   # 可根据需要改成 ATR

            if stop_distance <= 0:
                return 0

            # 计算原始仓位
            raw_qty = risk_amount / stop_distance

            # 最大允许仓位（按 max_position_percent 控制）
            max_allowed_value = balance * (self.RISK_CONFIG["max_position_percent"] / 100)
            max_qty = max_allowed_value / current_price

            final_qty = min(raw_qty, max_qty)

            # 保留合理精度（ETH 一般保留3位）
            return round(final_qty, 3)

        except Exception as e:
            print(f"[仓位计算错误] {str(e)}")
            return 0

    def check_can_open(self, symbol, side):
        """
        判断是否允许开仓（包含简单加仓控制）
        """
        position = self.get_current_position(symbol)
        if not position:
            return True, "首次开仓"

        current_qty = position['positionAmt']
        is_long = current_qty > 0

        # 同方向判断是否允许加仓
        if (side == "LONG" and is_long) or (side == "SHORT" and not is_long):
            # 已持仓同方向，检查是否允许加仓
            if self.RISK_CONFIG["max_pyramiding"] >= 1:
                # 这里可以再加条件：是否盈利 + 趋势强势
                return True, "允许加仓（第2次）"
            else:
                return False, "已持仓且不允许加仓"

        # 反方向持仓 → 不允许直接开反向（建议先平再开）
        return False, "已有反方向持仓，建议先平仓"

    def open_position(self, symbol, side):
        """
        带风控的开仓方法
        side: "LONG" 或 "SHORT"
        """
        # 1. 风控检查
        can_open, reason = self.check_can_open(symbol, side)
        if not can_open:
            print(f"[风控拦截] {symbol} {side} 开仓被拒绝: {reason}")
            return {"status": "blocked", "reason": reason}

        # 2. 计算仓位
        qty = self.calculate_position_size(symbol, side)
        if qty <= 0:
            print(f"[风控拦截] 计算出的仓位为0，无法下单")
            return {"status": "blocked", "reason": "仓位计算为0"}

        # 3. 下单
        try:
            order_side = SIDE_BUY if side == "LONG" else SIDE_SELL
            order = self.client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type=ORDER_TYPE_MARKET,
                quantity=qty
            )
            print(f"[开仓成功] {symbol} {side} | 数量: {qty}")
            return {"status": "success", "order": order, "qty": qty}
        except Exception as e:
            print(f"[开仓失败] {str(e)}")
            return {"status": "error", "message": str(e)}

from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class BinanceClient:
    def __init__(self, api_key, api_secret, risk_percent=0.85, max_leverage=3.0,
                 atr_multiplier_sl=0.92, max_position_value_usdt=5000):
        self.client = Client(api_key, api_secret)
        self.risk_percent = risk_percent
        self.max_leverage = max_leverage
        self.atr_multiplier_sl = atr_multiplier_sl
        self.max_position_value_usdt = max_position_value_usdt

    # ==================== 新增方法 ====================
    def get_account_equity(self):
        """获取账户当前总权益（USDT）"""
        try:
            account = self.client.futures_account()
            return float(account['totalWalletBalance'])
        except Exception as e:
            logging.error(f"[获取账户权益失败] {e}")
            return 0.0

    def get_current_position(self, symbol: str):
        """获取当前持仓"""
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            for p in positions:
                amt = float(p['positionAmt'])
                if amt != 0:
                    return {
                        "side": "LONG" if amt > 0 else "SHORT",
                        "qty": abs(amt),
                        "entry_price": float(p['entryPrice'])
                    }
            return None
        except Exception as e:
            logging.error(f"[获取持仓失败] {e}")
            return None

    def check_total_risk(self, symbol: str, new_side: str, new_qty: float):
        """
        简单总风险检查（后续可继续加强）
        这里先做一个基础版本：限制单币种持仓价值
        """
        equity = self.get_account_equity()
        if equity <= 0:
            return False

        current = self.get_current_position(symbol)
        current_value = 0
        if current:
            # 简单估算持仓价值
            current_value = current["qty"] * current["entry_price"]

        # 新开仓位预估价值
        new_value = new_qty * 1680   # 这里用当前 ETH 价格估算，可后续优化为实时价格

        total_value = current_value + new_value
        max_allowed = equity * 0.30   # 示例：单个币种最大持仓价值占权益 30%

        if total_value > max_allowed:
            logging.warning(f"[风控拒绝] 总持仓价值过高: {total_value:.2f} > {max_allowed:.2f}")
            return False
        return True

    def smart_open_position(self, symbol: str, side: str, requested_qty: float = None):
        """
        加强版智能开仓（严格风控）
        - 同方向已持仓 → 拒绝
        - 反向持仓 → 先平再开
        - 增加总风险检查
        """
        equity = self.get_account_equity()
        if equity <= 0:
            return {"status": "error", "message": "无法获取账户权益"}

        current = self.get_current_position(symbol)

        # 同方向持仓检查
        if current and current["side"] == side:
            logging.warning(f"[风控拒绝] 已持有 {side}，禁止同方向加仓")
            return {"status": "rejected", "reason": f"已有{side}持仓，禁止加仓"}

        # 反向持仓处理
        if current and current["side"] != side:
            logging.info(f"[反向信号] 先平掉 {current['side']}")
            self.close_all_positions(symbol)

        # 动态仓位风控（可根据需要调整逻辑）
        safe_qty = requested_qty or (equity * 0.02 / 50)   # 示例风控逻辑
        final_qty = round(safe_qty, 3)

        # 总风险检查
        if not self.check_total_risk(symbol, side, final_qty):
            return {"status": "rejected", "reason": "总持仓风险超限"}

        # 执行开仓
        try:
            order_side = "BUY" if side == "LONG" else "SELL"
            order = self.client.futures_create_order(
                symbol=symbol,
                side=order_side,
                type="MARKET",
                quantity=final_qty,
                positionSide="BOTH"
            )
            logging.info(f"[风控通过][开{side}成功] {symbol} | Qty: {final_qty} | 权益: {equity:.2f}")
            return {"status": "success", "order": order, "qty": final_qty}
        except BinanceAPIException as e:
            logging.error(f"[开{side}失败] {e}")
            return {"status": "error", "message": str(e)}

    def close_all_positions(self, symbol: str):
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "skipped", "reason": "无持仓"}

            qty = position['qty']
            side = "SELL" if position['side'] == "LONG" else "BUY"

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

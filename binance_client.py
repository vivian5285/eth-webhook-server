    def close_partial_position(self, symbol: str, percent: float):
        """按当前剩余仓位百分比平仓"""
        try:
            position = self.get_current_position(symbol)
            current_amt = float(position.get("positionAmt", 0))

            if current_amt == 0:
                return {"status": "skipped", "reason": "无持仓"}

            close_qty = abs(current_amt) * percent
            close_qty = max(0.001, round(close_qty, 3))

            side = "SELL" if current_amt > 0 else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET",
                quantity=close_qty, reduceOnly=True
            )
            return {"status": "success", "closed_qty": close_qty, "order": order}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def close_all_positions(self, symbol: str):
        """全平（带前置检查）"""
        try:
            position = self.get_current_position(symbol)
            amt = float(position.get("positionAmt", 0))

            if amt == 0:
                return {"status": "skipped", "reason": "无持仓"}

            side = "SELL" if amt > 0 else "BUY"
            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET",
                quantity=abs(amt), reduceOnly=True
            )
            return {"status": "success", "order": order}
        except Exception as e:
            return {"status": "error", "message": str(e)}

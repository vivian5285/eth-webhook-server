    def close_all_positions(self, symbol: str):
        """最终加强版全平方法"""
        try:
            position = self.get_current_position(symbol)
            if not position or position.get("positionAmt", 0) == 0:
                logging.info("[全平] 当前无持仓，跳过")
                return {"status": "skipped", "reason": "无持仓"}

            qty = abs(position["positionAmt"])
            side = "SELL" if position["positionAmt"] > 0 else "BUY"

            logging.info(f"[全平] 开始执行 → Symbol: {symbol}, Side: {side}, Qty: {qty}")

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=qty,
                reduceOnly=True
            )

            logging.info(f"[全平成功] {symbol} 已平仓 {qty}")
            return {"status": "success", "order": order}

        except BinanceAPIException as e:
            logging.error(f"[全平失败 - Binance API错误] Code: {e.code}, Msg: {e.message}")
            return {"status": "error", "message": str(e)}
        except Exception as e:
            logging.error(f"[全平失败 - 未知异常] {type(e).__name__}: {e}")
            return {"status": "error", "message": str(e)}

from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)

class BinanceClient:
    def __init__(self, api_key, api_secret, risk_percent=0.85, max_leverage=3.0,
                 atr_multiplier_sl=0.92, max_position_value_usdt=5000, client_name="未知账户"):
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
                return {"positionAmt": 0, "entryPrice": 0, "unrealizedProfit": 0}

            pos = positions[0]
            return {
                "positionAmt": float(pos.get("positionAmt", 0)),
                "entryPrice": float(pos.get("entryPrice", 0)),
                "unrealizedProfit": float(pos.get("unRealizedProfit", 0)),
                "leverage": float(pos.get("leverage", 0)),
                "liquidationPrice": float(pos.get("liquidationPrice", 0)),
                "marginType": pos.get("marginType", ""),
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

    # ==================== 获取账户报表（用于钉钉） ====================
    def get_account_report(self):
        try:
            account = self.client.futures_account()
            positions = self.client.futures_position_information()

            equity = float(account.get("totalWalletBalance", 0)) + float(account.get("totalUnrealizedProfit", 0))
            wallet_balance = float(account.get("totalWalletBalance", 0))
            available_margin = float(account.get("availableBalance", 0))
            total_unrealized_pnl = float(account.get("totalUnrealizedProfit", 0))
            today_realized_pnl = float(account.get("totalRealizedProfit", 0))  # 近似值

            # 当前持仓汇总
            current_position = "无持仓"
            for pos in positions:
                if float(pos.get("positionAmt", 0)) != 0:
                    current_position = f"{pos['symbol']} {pos['positionAmt']}"

            report = (
                f"权益: {equity:.2f} USDT\n"
                f"钱包余额: {wallet_balance:.2f} USDT\n"
                f"可用保证金: {available_margin:.2f} USDT\n"
                f"未实现盈亏: {total_unrealized_pnl:.2f} USDT\n"
                f"今日已实现盈亏: {today_realized_pnl:.2f} USDT\n"
                f"当前持仓: {current_position}\n"
                f"更新时间: {datetime.now().strftime('%H:%M:%S')}"
            )
            return report

        except Exception as e:
            logging.error(f"[获取账户报表异常] {e}")
            return "账户信息获取失败"

    # ==================== 发送钉钉通知（内部方法） ====================
    def _send_dingtalk(self, message: str):
        # 这里保留你原来的钉钉发送逻辑即可
        # 如果需要我帮你把钉钉发送逻辑也优化，可以再告诉我
        pass

    # ==================== 开仓方法（保留，供后续使用） ====================
    def smart_open_position(self, symbol: str, side: str, atr_value: float = None):
        # 可根据需要扩展，目前保留空实现或简单版本
        logging.info(f"[smart_open_position] 收到开仓请求: {symbol} {side}")
        return {"status": "not_implemented", "message": "开仓逻辑待扩展"}

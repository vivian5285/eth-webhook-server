from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')


class BinanceClient:
    def __init__(self, api_key, api_secret, risk_percent=0.90, max_leverage=3.0,
                 client_name="主账户"):
        self.client = Client(api_key, api_secret)
        self.risk_percent = risk_percent
        self.max_leverage = max_leverage
        self.client_name = client_name
        self.MIN_POSITION_VALUE_FOR_PARTIAL = 50   # 小仓位阈值（低于此直接全平）

    # ==================== 获取当前持仓 ====================
    def get_current_position(self, symbol: str = "ETHUSDT"):
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
            }
        except Exception as e:
            logging.error(f"[获取持仓异常] {symbol} - {e}")
            return {"positionAmt": 0, "entryPrice": 0, "unrealizedProfit": 0, "leverage": 0}

    # ==================== 获取账户权益 ====================
    def get_account_equity(self):
        try:
            account = self.client.futures_account()
            return float(account.get("totalWalletBalance", 0)) + float(account.get("totalUnrealizedProfit", 0))
        except Exception as e:
            logging.error(f"[获取账户权益失败] {e}")
            return 0.0

    # ==================== 智能仓位计算（小资金激进版） ====================
    def calculate_position_size(self, stop_distance: float, symbol: str = "ETHUSDT"):
        equity = self.get_account_equity()
        if equity <= 0 or stop_distance <= 0:
            return 0.0

        if equity < 3000:
            effective_risk_percent = 7.0
            max_position_value = equity * 8.0
        elif equity < 10000:
            effective_risk_percent = 2.0
            max_position_value = equity * 4.0
        else:
            effective_risk_percent = 1.0
            max_position_value = equity * 2.5

        risk_amount = equity * effective_risk_percent / 100
        raw_qty = risk_amount / stop_distance

        try:
            price = float(self.client.get_symbol_ticker(symbol=symbol)["price"])
            max_qty_by_value = max_position_value / price
        except:
            max_qty_by_value = raw_qty

        final_qty = min(raw_qty, max_qty_by_value)
        final_qty = max(0.001, round(final_qty, 3))
        return final_qty

    # ==================== 部分平仓（带智能容错） ====================
    def close_partial_position(self, symbol: str, percent: float):
        """
        按当前剩余仓位百分比平仓。
        如果当前仓位已经很小（< 50U），则直接全平，避免留下灰尘仓位。
        """
        try:
            position = self.get_current_position(symbol)
            current_amt = float(position.get("positionAmt", 0))

            if current_amt == 0:
                logging.info(f"[部分平仓跳过] {symbol} 当前无持仓")
                return {"status": "skipped", "reason": "无持仓"}

            # 计算当前持仓价值
            try:
                price = float(self.client.get_symbol_ticker(symbol=symbol)["price"])
                position_value = abs(current_amt) * price
            except:
                position_value = 99999

            # 智能判断：极低仓位直接全平
            if position_value < self.MIN_POSITION_VALUE_FOR_PARTIAL:
                logging.info(f"[智能全平] {symbol} 当前仓位仅 {position_value:.2f}U，收到部分平仓请求，直接全平")
                return self.close_all_positions(symbol)

            # 正常部分平仓
            close_qty = abs(current_amt) * percent
            close_qty = max(0.001, round(close_qty, 3))
            side = "SELL" if current_amt > 0 else "BUY"

            logging.info(f"[部分平仓] {symbol} | 当前持仓: {current_amt} | 平仓比例: {percent*100}% | 本次平: {close_qty}")

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=close_qty,
                reduceOnly=True
            )
            return {"status": "success", "closed_qty": close_qty, "order": order}

        except BinanceAPIException as e:
            logging.error(f"[部分平仓失败] {symbol} - {e}")
            return {"status": "error", "message": str(e)}
        except Exception as e:
            logging.error(f"[部分平仓异常] {symbol} - {e}")
            return {"status": "error", "message": str(e)}

    # ==================== 全平仓位 ====================
    def close_all_positions(self, symbol: str = "ETHUSDT"):
        try:
            position = self.get_current_position(symbol)
            amt = float(position.get("positionAmt", 0))

            if amt == 0:
                logging.info(f"[全平跳过] {symbol} 当前无持仓")
                return {"status": "skipped", "reason": "无持仓"}

            side = "SELL" if amt > 0 else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type="MARKET",
                quantity=abs(amt),
                reduceOnly=True
            )
            logging.info(f"[全平成功] {symbol} | 平仓数量: {abs(amt)}")
            return {"status": "success", "order": order}

        except Exception as e:
            logging.error(f"[全平异常] {symbol} - {e}")
            return {"status": "error", "message": str(e)}

    # ==================== 美化账户报表（用于钉钉推送） ====================
    def get_detailed_report(self):
        """返回结构化、美观的账户报表"""
        try:
            equity = self.get_account_equity()
            position = self.get_current_position("ETHUSDT")
            pos_amt = float(position.get("positionAmt", 0))
            entry_price = float(position.get("entryPrice", 0))
            unrealized_pnl = float(position.get("unrealizedProfit", 0))
            leverage = float(position.get("leverage", 0))

            account_info = self.client.futures_account()
            today_pnl = float(account_info.get("totalRealizedProfit", 0))

            if pos_amt == 0:
                position_text = "📭 **当前无持仓**"
            else:
                direction = "🟢 多" if pos_amt > 0 else "🔴 空"
                position_text = (
                    f"{direction} **{abs(pos_amt):.4f}** @ **{entry_price:.2f}**\n"
                    f"杠杆：**{leverage}x** | 未实现盈亏：**{unrealized_pnl:+.2f} USDT**"
                )

            report = f"""
**📊 账户状态快照**  
**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

**总权益**：**{equity:.2f} USDT**  
**钱包余额**：**{float(account_info.get('totalWalletBalance', 0)):.2f} USDT**  
**可用保证金**：**{float(account_info.get('availableBalance', 0)):.2f} USDT**

**当前持仓**：  
{position_text}

**今日表现**：  
今日已实现盈亏：**{today_pnl:+.2f} USDT**
"""
            return report.strip()

        except Exception as e:
            logging.error(f"获取详细报表失败: {e}")
            return "⚠️ 账户报表获取失败"

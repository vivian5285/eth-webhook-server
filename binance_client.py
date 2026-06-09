from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging
from datetime import datetime, date

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class BinanceClient:
    def __init__(self, api_key, api_secret, risk_percent=0.85, max_leverage=3.0,
                 atr_multiplier_sl=0.92, max_position_value_usdt=5000,
                 daily_loss_limit_percent=5.0, max_consecutive_losses=3):
        self.client = Client(api_key, api_secret)
        self.risk_percent = risk_percent
        self.max_leverage = max_leverage
        self.atr_multiplier_sl = atr_multiplier_sl
        self.max_position_value_usdt = max_position_value_usdt
        self.daily_loss_limit_percent = daily_loss_limit_percent
        self.max_consecutive_losses = max_consecutive_losses
        self.consecutive_losses = 0
        self.last_trade_date = None

    # ==================== 工具方法 ====================
    def get_mark_price(self, symbol: str):
        try:
            ticker = self.client.futures_mark_price(symbol=symbol)
            return float(ticker['markPrice'])
        except Exception as e:
            logging.error(f"[获取标记价格失败] {e}")
            return 1680.0

    def get_account_equity(self):
        try:
            account = self.client.futures_account()
            return float(account['totalWalletBalance'])
        except Exception as e:
            logging.error(f"[获取账户权益失败] {e}")
            return 0.0

    def get_current_position(self, symbol: str):
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

    # ==================== 风控方法 ====================
    def get_today_realized_pnl(self, symbol: str = None):
        try:
            now = datetime.utcnow()
            start_time = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            income = self.client.futures_income_history(
                symbol=symbol, incomeType="REALIZED_PNL", startTime=start_time, limit=1000
            )
            return sum(float(i['income']) for i in income)
        except Exception as e:
            logging.error(f"[获取今日已实现盈亏失败] {e}")
            return 0.0

    def check_daily_loss_limit(self):
        today_pnl = self.get_today_realized_pnl()
        equity = self.get_account_equity()
        if equity <= 0:
            return False
        loss_ratio = abs(today_pnl) / equity * 100 if today_pnl < 0 else 0
        if loss_ratio >= self.daily_loss_limit_percent:
            logging.warning(f"[每日熔断触发] 今日亏损比例: {loss_ratio:.2f}%")
            return False
        return True

    def check_consecutive_losses(self):
        if self.consecutive_losses >= self.max_consecutive_losses:
            logging.warning(f"[连续亏损保护触发] 已连续亏损 {self.consecutive_losses} 次")
            return False
        return True

    def update_consecutive_losses(self, pnl: float):
        today = date.today()
        if self.last_trade_date != today:
            self.consecutive_losses = 0
            self.last_trade_date = today
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

    def check_total_risk(self, symbol: str, side: str, new_qty: float) -> bool:
        try:
            equity = self.get_account_equity()
            if equity <= 0:
                logging.warning("[总风险检查] 无法获取权益")
                return False

            current = self.get_current_position(symbol)
            current_value = 0
            if current:
                mark_price = self.get_mark_price(symbol)
                current_value = current['qty'] * mark_price

            mark_price = self.get_mark_price(symbol)
            new_value = new_qty * mark_price
            total_value = current_value + new_value

            MAX_POSITION_RATIO = 0.30
            MAX_LEVERAGE = 8.0

            position_ratio = total_value / equity if equity > 0 else 0
            total_leverage = total_value / equity if equity > 0 else 0

            logging.info(f"[总风险检查] 总持仓价值={total_value:.2f}, 权益={equity:.2f}, "
                         f"占比={position_ratio*100:.2f}%, 杠杆={total_leverage:.2f}x")

            if position_ratio > MAX_POSITION_RATIO:
                logging.warning(f"[总风险拒绝] 持仓价值占比过高: {position_ratio*100:.2f}%")
                return False

            if total_leverage > MAX_LEVERAGE:
                logging.warning(f"[总风险拒绝] 总杠杆过高: {total_leverage:.2f}x")
                return False

            return True
        except Exception as e:
            logging.error(f"[总风险检查异常] {e}")
            return False

    # ==================== 账户报表（完整版） ====================
    def get_account_report(self, symbol: str = "ETHUSDT"):
        try:
            equity = self.get_account_equity()
            position = self.get_current_position(symbol)
            today_realized = self.get_today_realized_pnl(symbol)
            mark_price = self.get_mark_price(symbol)

            report = {
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "equity": round(equity, 2),
                "wallet_balance": 0.0,
                "available_margin": 0.0,
                "margin_ratio": 0.0,
                "position": None,
                "unrealized_pnl": 0.0,
                "today_realized_pnl": round(today_realized, 2),
                "today_total_pnl": round(today_realized, 2),
                "leverage": 0,
                "liquidation_price": 0.0,
                "mark_price": round(mark_price, 2)
            }

            try:
                account = self.client.futures_account()
                report["wallet_balance"] = round(float(account.get('totalWalletBalance', 0)), 2)
                report["available_margin"] = round(float(account.get('availableBalance', 0)), 2)
            except:
                pass

            if position:
                pos_info = self.client.futures_position_information(symbol=symbol)[0]
                entry_price = float(pos_info['entryPrice'])
                liquidation_price = float(pos_info['liquidationPrice'])
                leverage = int(float(pos_info.get('leverage', 0)))
                unrealized_pnl = float(pos_info['unRealizedProfit'])
                margin = float(pos_info.get('isolatedMargin', 0)) or 0

                position_value = round(position['qty'] * mark_price, 2)
                margin_ratio = round((margin / equity * 100), 2) if equity > 0 else 0

                report.update({
                    "position": {
                        "side": position['side'],
                        "qty": position['qty'],
                        "entry_price": round(entry_price, 2),
                        "mark_price": round(mark_price, 2),
                        "position_value": position_value
                    },
                    "unrealized_pnl": round(unrealized_pnl, 2),
                    "today_total_pnl": round(today_realized + unrealized_pnl, 2),
                    "margin_ratio": margin_ratio,
                    "leverage": leverage,
                    "liquidation_price": round(liquidation_price, 2)
                })

            return report

        except Exception as e:
            logging.error(f"[生成账户报表失败] {e}")
            return None

    def send_account_report_to_dingtalk(self, symbol: str = "ETHUSDT", extra_msg: str = ""):
        try:
            report = self.get_account_report(symbol)
            if not report:
                logging.warning("[账户报表] get_account_report 返回为空，未发送钉钉")
                return

            lines = [f"【账户状态报表】{report['time']}"]

            lines.append("\n一、账户概况")
            lines.append(f"总权益: {report['equity']} USDT")
            lines.append(f"钱包余额: {report['wallet_balance']} USDT")
            lines.append(f"可用保证金: {report['available_margin']} USDT")
            lines.append(f"保证金使用率: {report['margin_ratio']}%")

            lines.append("\n二、当前持仓")
            if report.get("position"):
                p = report["position"]
                lines.append(f"方向: {p['side']}")
                lines.append(f"数量: {p['qty']}")
                lines.append(f"开仓均价: {p['entry_price']}")
                lines.append(f"标记价格: {p['mark_price']}")
                lines.append(f"持仓价值: {p['position_value']} USDT")
                lines.append(f"未实现盈亏: {report['unrealized_pnl']} USDT")
                lines.append(f"当前杠杆: {report['leverage']}x")
                lines.append(f"强平价格: {report['liquidation_price']}")
            else:
                lines.append("当前无持仓")

            lines.append("\n三、今日表现")
            lines.append(f"今日已实现盈亏: {report['today_realized_pnl']} USDT")
            lines.append(f"今日总盈亏（含未实现）: {report['today_total_pnl']} USDT")

            if extra_msg:
                lines.append(f"\n备注: {extra_msg}")

            message = "\n".join(lines)
            self._send_dingtalk(message)
            logging.info("[账户报表] 已成功发送到钉钉")

        except Exception as e:
            logging.error(f"[发送账户报表到钉钉异常] {e}", exc_info=True)

    def _send_dingtalk(self, message: str):
        import requests
        DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=fddb9885a4e26dc6ba519d7cf9e7fe90ff9c400ecbe7fc783123c22d0d2007ed"
        try:
            data = {
                "msgtype": "text",
                "text": {"content": f"[交易风控]\n{message}"}
            }
            requests.post(DINGTALK_WEBHOOK, json=data, timeout=5)
        except Exception as e:
            logging.error(f"[发送钉钉失败] {e}")

    # ==================== 智能开仓与平仓 ====================
    def smart_open_position(self, symbol: str, side: str, requested_qty: float = None,
                            atr: float = None, atr_multiplier: float = 2.0):
        equity = self.get_account_equity()
        if equity <= 0:
            return {"status": "error", "message": "无法获取账户权益"}

        if not self.check_daily_loss_limit():
            return {"status": "rejected", "reason": "每日亏损熔断触发"}
        if not self.check_consecutive_losses():
            return {"status": "rejected", "reason": "连续亏损保护触发"}

        current = self.get_current_position(symbol)
        if current and current["side"] == side:
            return {"status": "rejected", "reason": f"已有{side}持仓，禁止加仓"}
        if current and current["side"] != side:
            self.close_all_positions(symbol)

        final_qty = 0
        if atr and atr > 0:
            risk_amount = equity * 0.015
            stop_distance = atr * atr_multiplier
            final_qty = round(risk_amount / stop_distance, 3)
        else:
            final_qty = requested_qty or (equity * 0.02 / 50)
            final_qty = round(final_qty, 3)

        if final_qty <= 0:
            return {"status": "rejected", "reason": "计算后仓位过小"}

        if not self.check_total_risk(symbol, side, final_qty):
            return {"status": "rejected", "reason": "总风险检查未通过"}

        try:
            order_side = "BUY" if side == "LONG" else "SELL"
            order = self.client.futures_create_order(
                symbol=symbol, side=order_side, type="MARKET",
                quantity=final_qty, positionSide="BOTH"
            )
            logging.info(f"[开{side}成功] {symbol} | Qty: {final_qty}")

            self.send_account_report_to_dingtalk(symbol, extra_msg=f"已成功开{side}仓（动态仓位）")
            return {"status": "success", "order": order, "qty": final_qty}
        except BinanceAPIException as e:
            return {"status": "error", "message": str(e)}

    def close_all_positions(self, symbol: str):
        try:
            position = self.get_current_position(symbol)
            if not position:
                return {"status": "skipped", "reason": "无持仓"}

            qty = position['qty']
            side = "SELL" if position['side'] == "LONG" else "BUY"

            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET",
                quantity=qty, reduceOnly=True, positionSide="BOTH"
            )
            logging.info(f"[全平成功] {symbol}")

            self.send_account_report_to_dingtalk(symbol, extra_msg="已执行全平操作")
            return {"status": "success", "order": order}
        except Exception as e:
            return {"status": "error", "message": str(e)}

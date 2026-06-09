from binance.client import Client
from binance.exceptions import BinanceAPIException
import logging
from datetime import datetime, date
import time
import hmac
import hashlib
import base64
import urllib.parse

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class BinanceClient:
    def __init__(self, api_key, api_secret, risk_percent=0.85, max_leverage=3.0,
                 atr_multiplier_sl=0.92, max_position_value_usdt=5000,
                 daily_loss_limit_percent=5.0, max_consecutive_losses=3,
                 client_name="未知账户"):
        self.client = Client(api_key, api_secret)
        self.risk_percent = risk_percent
        self.max_leverage = max_leverage
        self.atr_multiplier_sl = atr_multiplier_sl
        self.max_position_value_usdt = max_position_value_usdt
        self.daily_loss_limit_percent = daily_loss_limit_percent
        self.max_consecutive_losses = max_consecutive_losses
        self.client_name = client_name          # ← 新增
        self.consecutive_losses = 0
        self.last_trade_date = None

        # 钉钉加签
        self.dingtalk_secret = "SEC17a8188a34e2401dbf0cb29344aa32ddbdaf9db9b0da5b5c328d52f4a55dd91c"
        self.dingtalk_access_token = "fddb9885a4e26dc6ba519d7cf9e7fe90ff9c400ecbe7fc783123c22d0d2007ed"

    # ==================== 钉钉加签 ====================
    def _send_dingtalk(self, message: str):
        try:
            timestamp = str(round(time.time() * 1000))
            string_to_sign = f"{timestamp}\n{self.dingtalk_secret}"
            hmac_code = hmac.new(self.dingtalk_secret.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))

            url = f"https://oapi.dingtalk.com/robot/send?access_token={self.dingtalk_access_token}&timestamp={timestamp}&sign={sign}"
            data = {"msgtype": "text", "text": {"content": f"[交易风控]\n{message}"}}

            import requests
            resp = requests.post(url, json=data, timeout=5)
            if resp.status_code == 200 and resp.json().get("errcode") == 0:
                logging.info("[账户报表] 已成功发送到钉钉（加签模式）")
            else:
                logging.error(f"[发送钉钉失败] {resp.text}")
        except Exception as e:
            logging.error(f"[发送钉钉异常] {e}", exc_info=True)

    def get_account_report(self, symbol: str = "ETHUSDT"):
        try:
            equity = self.get_account_equity()
            position = self.get_current_position(symbol)
            today_realized = self.get_today_realized_pnl(symbol)
            mark_price = self.get_mark_price(symbol)
            today_trade_count = self.get_today_trade_count(symbol)

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
                "mark_price": round(mark_price, 2),
                "today_trade_count": today_trade_count,
                "maintenance_margin": 0.0
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
                maint_margin = float(pos_info.get('maintMargin', 0)) or 0

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
                    "liquidation_price": round(liquidation_price, 2),
                    "maintenance_margin": round(maint_margin, 2)
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
            lines.append(f"**客户/账户**: {self.client_name}")   # ← 自动显示客户名称

            # 一、账户概况
            lines.append("\n【一、账户概况】")
            lines.append(f"总权益: {report['equity']} USDT")
            lines.append(f"钱包余额: {report['wallet_balance']} USDT")
            lines.append(f"可用保证金: {report['available_margin']} USDT")
            lines.append(f"保证金使用率: {report['margin_ratio']}%")

            # 二、当前持仓
            lines.append("\n【二、当前持仓】")
            if report.get("position"):
                p = report["position"]
                side_cn = "多头" if p['side'] == "LONG" else "空头"
                lines.append(f"方向: **{side_cn}**")
                lines.append(f"数量: {p['qty']}")
                lines.append(f"开仓均价: {p['entry_price']}")
                lines.append(f"标记价格: {p['mark_price']}")
                lines.append(f"持仓价值: {p['position_value']} USDT")
                lines.append(f"未实现盈亏: {report['unrealized_pnl']} USDT")
                lines.append(f"**当前杠杆: {report['leverage']}x**")
                lines.append(f"**强平价格: {report['liquidation_price']}**")
                lines.append(f"维持保证金: {report.get('maintenance_margin', 0)} USDT")
            else:
                lines.append("当前无持仓")

            # 三、今日表现
            lines.append("\n【三、今日表现】")
            lines.append(f"今日已实现盈亏: {report['today_realized_pnl']} USDT")
            lines.append(f"今日总盈亏（含未实现）: {report['today_total_pnl']} USDT")
            lines.append(f"今日交易次数: {report['today_trade_count']} 笔")

            if extra_msg:
                lines.append(f"\n备注: {extra_msg}")

            message = "\n".join(lines)
            self._send_dingtalk(message)
            logging.info("[账户报表] 已成功发送到钉钉")

        except Exception as e:
            logging.error(f"[发送账户报表到钉钉异常] {e}", exc_info=True)

    # ==================== 以下为原有方法 ====================
    def get_mark_price(self, symbol: str):
        try:
            return float(self.client.futures_mark_price(symbol=symbol)['markPrice'])
        except:
            return 1680.0

    def get_account_equity(self):
        try:
            return float(self.client.futures_account()['totalWalletBalance'])
        except:
            return 0.0

    def get_current_position(self, symbol: str):
        try:
            for p in self.client.futures_position_information(symbol=symbol):
                if float(p['positionAmt']) != 0:
                    return {"side": "LONG" if float(p['positionAmt']) > 0 else "SHORT",
                            "qty": abs(float(p['positionAmt'])),
                            "entry_price": float(p['entryPrice'])}
            return None
        except:
            return None

    def get_today_realized_pnl(self, symbol: str = None):
        try:
            start = int(datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            income = self.client.futures_income_history(symbol=symbol, incomeType="REALIZED_PNL", startTime=start, limit=1000)
            return sum(float(i['income']) for i in income)
        except:
            return 0.0

    def get_today_trade_count(self, symbol: str = None):
        try:
            start = int(datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            trades = self.client.futures_account_trades(symbol=symbol, startTime=start, limit=1000)
            return len(trades)
        except:
            return 0

    def check_daily_loss_limit(self):
        pnl = self.get_today_realized_pnl()
        eq = self.get_account_equity()
        return not (eq > 0 and abs(pnl) / eq * 100 >= self.daily_loss_limit_percent)

    def check_consecutive_losses(self):
        return self.consecutive_losses < self.max_consecutive_losses

    def check_total_risk(self, symbol: str, side: str, new_qty: float) -> bool:
        try:
            eq = self.get_account_equity()
            if eq <= 0: return False
            cur = self.get_current_position(symbol)
            cur_val = cur['qty'] * self.get_mark_price(symbol) if cur else 0
            new_val = new_qty * self.get_mark_price(symbol)
            total_val = cur_val + new_val
            return total_val / eq <= 0.30 and total_val / eq <= 8
        except:
            return False

    def smart_open_position(self, symbol: str, side: str, requested_qty: float = None,
                            atr: float = None, atr_multiplier: float = 2.0):
        equity = self.get_account_equity()
        if equity <= 0: return {"status": "error", "message": "无法获取账户权益"}

        if not self.check_daily_loss_limit(): return {"status": "rejected", "reason": "每日亏损熔断触发"}
        if not self.check_consecutive_losses(): return {"status": "rejected", "reason": "连续亏损保护触发"}

        current = self.get_current_position(symbol)
        if current and current["side"] == side: return {"status": "rejected", "reason": f"已有{side}持仓，禁止加仓"}
        if current and current["side"] != side: self.close_all_positions(symbol)

        final_qty = round((equity * 0.015) / (atr * atr_multiplier), 3) if atr and atr > 0 else round(requested_qty or (equity * 0.02 / 50), 3)
        if final_qty <= 0: return {"status": "rejected", "reason": "计算后仓位过小"}
        if not self.check_total_risk(symbol, side, final_qty): return {"status": "rejected", "reason": "总风险检查未通过"}

        try:
            order_side = "BUY" if side == "LONG" else "SELL"
            order = self.client.futures_create_order(symbol=symbol, side=order_side, type="MARKET", quantity=final_qty, positionSide="BOTH")
            logging.info(f"[开{side}成功] {symbol} | Qty: {final_qty}")
            side_cn = "多头" if side == "LONG" else "空头"
            self.send_account_report_to_dingtalk(symbol, extra_msg=f"已成功开{side_cn}仓（动态仓位）")
            return {"status": "success", "order": order, "qty": final_qty}
        except BinanceAPIException as e:
            return {"status": "error", "message": str(e)}

    def close_all_positions(self, symbol: str):
        try:
            position = self.get_current_position(symbol)
            if not position: return {"status": "skipped", "reason": "无持仓"}
            qty = position['qty']
            side = "SELL" if position['side'] == "LONG" else "BUY"
            side_cn = "多头" if position['side'] == "LONG" else "空头"
            order = self.client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=qty, reduceOnly=True, positionSide="BOTH")
            logging.info(f"[全平成功] {symbol}")
            self.send_account_report_to_dingtalk(symbol, extra_msg=f"已执行全平（原{side_cn}）")
            return {"status": "success", "order": order}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_mark_price(self, symbol: str):
        try:
            return float(self.client.futures_mark_price(symbol=symbol)['markPrice'])
        except:
            return 1680.0

    def get_account_equity(self):
        try:
            return float(self.client.futures_account()['totalWalletBalance'])
        except:
            return 0.0

    def get_current_position(self, symbol: str):
        try:
            for p in self.client.futures_position_information(symbol=symbol):
                if float(p['positionAmt']) != 0:
                    return {"side": "LONG" if float(p['positionAmt']) > 0 else "SHORT",
                            "qty": abs(float(p['positionAmt'])),
                            "entry_price": float(p['entryPrice'])}
            return None
        except:
            return None

    def get_today_realized_pnl(self, symbol: str = None):
        try:
            start = int(datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            income = self.client.futures_income_history(symbol=symbol, incomeType="REALIZED_PNL", startTime=start, limit=1000)
            return sum(float(i['income']) for i in income)
        except:
            return 0.0

    def get_today_trade_count(self, symbol: str = None):
        try:
            start = int(datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            trades = self.client.futures_account_trades(symbol=symbol, startTime=start, limit=1000)
            return len(trades)
        except:
            return 0

    def check_daily_loss_limit(self):
        pnl = self.get_today_realized_pnl()
        eq = self.get_account_equity()
        return not (eq > 0 and abs(pnl) / eq * 100 >= self.daily_loss_limit_percent)

    def check_consecutive_losses(self):
        return self.consecutive_losses < self.max_consecutive_losses

    def check_total_risk(self, symbol: str, side: str, new_qty: float) -> bool:
        try:
            eq = self.get_account_equity()
            if eq <= 0: return False
            cur = self.get_current_position(symbol)
            cur_val = cur['qty'] * self.get_mark_price(symbol) if cur else 0
            new_val = new_qty * self.get_mark_price(symbol)
            total_val = cur_val + new_val
            return total_val / eq <= 0.30 and total_val / eq <= 8
        except:
            return False

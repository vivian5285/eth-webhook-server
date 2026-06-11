# tp_monitor.py（最终完整优化版）
import time
import threading
import logging
from binance import ThreadedWebsocketManager
from binance_client import BinanceClient
from position_manager import PositionManager

class TPMonitor:
    def __init__(self, symbol: str = "ETHUSDT", check_interval: int = 4):
        self.symbol = symbol
        self.client = BinanceClient()
        self.pm = PositionManager()
        self.check_interval = check_interval
        self.current_price = None
        self.running = False
        self.twm = None
        self.last_action_time = 0
        self.last_qty = 0

    def start(self):
        if self.running:
            return
        self.running = True

        if self.twm:
            try:
                self.twm.stop()
            except:
                pass

        self.twm = ThreadedWebsocketManager(
            api_key=self.client.client.API_KEY,
            api_secret=self.client.client.API_SECRET
        )

        try:
            self.twm.start()
            self.twm.start_aggtrade_socket(callback=self._on_price_update, symbol=self.symbol.lower())
        except Exception as e:
            logging.error(f"[WebSocket启动异常] {e}")

        existing_pos = self.pm.get_position()
        if existing_pos:
            logging.info(f"[TP监控] 检测到历史持仓，恢复监控")

        threading.Thread(target=self._check_tp_loop, daemon=True).start()
        logging.info(f"[TP监控] 智能TP监控已启动（支持手动干预优化版） | {self.symbol}")

    def _on_price_update(self, msg):
        try:
            if "p" in msg:
                self.current_price = float(msg["p"])
        except Exception as e:
            logging.error(f"[价格更新异常] {e}")

    def _check_tp_loop(self):
        while self.running:
            try:
                real_pos = self.client.get_current_position(self.symbol)
                cached_pos = self.pm.get_position()

                # ==================== 手动全平检测 ====================
                if not real_pos and cached_pos:
                    logging.warning("[TP监控] 检测到手动全平，清理本地缓存")
                    self._send_manual_full_close_report(cached_pos)
                    self.pm.clear_position()
                    time.sleep(self.check_interval)
                    continue

                if real_pos:
                    current_qty = abs(float(real_pos["positionAmt"]))
                    entry_price = float(real_pos.get("entryPrice", 0))
                    side = "long" if float(real_pos["positionAmt"]) > 0 else "short"

                    # 检测手动加减仓
                    if self.last_qty > 0 and abs(current_qty - self.last_qty) > max(self.last_qty * 0.05, 0.01):
                        self._handle_manual_position_change(cached_pos, real_pos, current_qty, self.last_qty)

                    self.last_qty = current_qty

                    if not cached_pos:
                        time.sleep(self.check_interval)
                        continue

                    if self.current_price is None or time.time() - self.last_action_time < 2.5:
                        time.sleep(0.8)
                        continue

                    price = self.current_price
                    tp = cached_pos.get("tp_prices", {})
                    hit = cached_pos.get("tp_hit", [])

                    self._check_early_breakeven(price, entry_price, side, hit)

                    # 执行分批止盈
                    if side == "long":
                        if "tp1" not in hit and price >= tp.get("tp1", 0):
                            self._execute_tp("tp1", price, cached_pos, 0.30)
                        elif "tp2" not in hit and price >= tp.get("tp2", 0):
                            self._execute_tp("tp2", price, cached_pos, 0.30)
                        elif "tp3" not in hit and price >= tp.get("tp3", 0):
                            self._execute_tp("tp3", price, cached_pos, 1.0)
                    else:
                        if "tp1" not in hit and price <= tp.get("tp1", 999999):
                            self._execute_tp("tp1", price, cached_pos, 0.30)
                        elif "tp2" not in hit and price <= tp.get("tp2", 999999):
                            self._execute_tp("tp2", price, cached_pos, 0.30)
                        elif "tp3" not in hit and price <= tp.get("tp3", 999999):
                            self._execute_tp("tp3", price, cached_pos, 1.0)

            except Exception as e:
                logging.error(f"[TP检查循环异常] {e}")

            time.sleep(self.check_interval)

    def _handle_manual_position_change(self, cached_pos, real_pos, current_qty, last_qty):
        change_qty = current_qty - last_qty
        new_entry_price = float(real_pos.get("entryPrice", 0))

        if change_qty > 0:
            # 手动加仓 → 完全重置TP
            logging.info(f"[手动加仓] 检测到加仓 {change_qty}，系统将完全重置TP")
            self._recalculate_tp_after_add(cached_pos, real_pos, new_entry_price)
            self._send_manual_action_report_with_tp_comparison("手动加仓", cached_pos, new_entry_price, change_qty)
        else:
            # 手动减仓
            logging.info(f"[手动减仓] 检测到减仓 {abs(change_qty)}")
            self._send_manual_action_report(
                "手动减仓", cached_pos,
                f"检测到手动减仓 {abs(change_qty)}，系统将继续按当前剩余仓位执行30/30/100%止盈计划"
            )

    def _recalculate_tp_after_add(self, cached_pos, real_pos, new_entry_price):
        """手动加仓后完全重置TP"""
        atr = cached_pos.get("atr") or cached_pos.get("entry_atr") or 30
        is_long = float(real_pos["positionAmt"]) > 0

        if is_long:
            new_tp1 = new_entry_price + (atr * 1.28)
            new_tp2 = new_entry_price + (atr * 2.5)
            new_tp3 = new_entry_price + (atr * 3.6)
        else:
            new_tp1 = new_entry_price - (atr * 1.28)
            new_tp2 = new_entry_price - (atr * 2.5)
            new_tp3 = new_entry_price - (atr * 3.6)

        new_tp_prices = {
            "tp1": new_tp1,
            "tp2": new_tp2,
            "tp3": new_tp3
        }

        self.pm.update_position_after_manual_change(new_entry_price, new_tp_prices)

    def _send_manual_full_close_report(self, pos: dict):
        """手动全平专用推送（优化文案）"""
        try:
            from app import send_dingtalk
            msg = (
                f"**⚠️ 手动全平识别**\n\n"
                f"**分析**：检测到你手动全平了仓位，系统已自动清理TP缓存。\n\n"
                f"**原持仓信息**：\n"
                f"方向：{pos.get('side')}\n"
                f"入场价：{pos.get('entry_price')}\n\n"
                f"**当前状态**：账户已无持仓，TP缓存已清理。\n"
                f"系统将保持干净状态，等待下一个 TradingView 信号到来后重新建立持仓和TP计划。"
            )
            send_dingtalk("手动全平识别", msg)
        except Exception as e:
            logging.error(f"[手动全平推送失败] {e}")

    def _send_manual_action_report_with_tp_comparison(self, action_type: str, old_pos: dict, new_entry_price: float, change_qty: float):
        """加仓后推送新旧TP对比"""
        try:
            from app import send_dingtalk

            old_tp = old_pos.get("tp_prices", {})
            old_entry = old_pos.get("entry_price", 0)
            atr = old_pos.get("atr", 30)

            msg = (
                f"**🚀 手动加仓识别 - 系统已完全重置TP**\n\n"
                f"**加仓数量**：{change_qty}\n"
                f"**新平均入场价**：{new_entry_price}\n\n"
                f"**旧TP设置**（加仓前）\n"
                f"• 入场价：{old_entry}\n"
                f"• TP1：{old_tp.get('tp1')}\n"
                f"• TP2：{old_tp.get('tp2')}\n"
                f"• TP3：{old_tp.get('tp3')}\n\n"
                f"**新TP设置**（系统重新计算后）\n"
                f"• 入场价：{new_entry_price}\n"
                f"• TP1：{round(new_entry_price + (atr * 1.28), 2)}\n"
                f"• TP2：{round(new_entry_price + (atr * 2.5), 2)}\n"
                f"• TP3：{round(new_entry_price + (atr * 3.6), 2)}\n\n"
                f"系统已清空已触发记录，将按新TP继续执行30/30/100%铁律。"
            )
            send_dingtalk("手动加仓 - TP已重置", msg)
        except Exception as e:
            logging.error(f"[加仓TP对比推送失败] {e}")

    def _send_manual_action_report(self, action_type: str, pos: dict, analysis: str):
        try:
            from app import send_dingtalk
            msg = (
                f"**⚠️ 手动干预识别 - {action_type}**\n\n"
                f"**分析**：{analysis}\n\n"
                f"**当前持仓**：方向 {pos.get('side')} | 入场价 {pos.get('entry_price')}\n"
                f"系统将继续按铁律执行分批止盈。"
            )
            send_dingtalk(f"手动{action_type}识别", msg)
        except Exception as e:
            logging.error(f"[手动干预推送失败] {e}")

    def _check_early_breakeven(self, price, entry_price, side, hit):
        if not entry_price or "tp1" in hit:
            return
        if side == "long":
            profit_pct = (price - entry_price) / entry_price * 100
        else:
            profit_pct = (entry_price - price) / entry_price * 100

        if profit_pct >= 0.55:
            logging.info(f"[早期保本移动] 当前浮盈 {profit_pct:.2f}%，提前进入紧追踪模式")

    def _execute_tp(self, level: str, price: float, pos: dict, percent: float):
        logging.info(f"[TP触发] {level} @ {price}")
        self.pm.mark_tp_hit(level)
        self.last_action_time = time.time()

        entry_price = float(pos.get("entry_price", 0))
        side = pos.get("side", "long")

        profit_amount = None
        try:
            current_pos = self.client.get_current_position(self.symbol)
            if current_pos:
                current_qty = abs(float(current_pos["positionAmt"]))
                close_qty = current_qty * percent if percent < 1.0 else current_qty
                if side == "long":
                    profit_amount = (price - entry_price) * close_qty
                else:
                    profit_amount = (entry_price - price) * close_qty
        except:
            pass

        if percent >= 1.0:
            self.client.close_all_positions(self.symbol)
            self.pm.clear_position()
        else:
            self.client.close_partial_position(self.symbol, percent)

        try:
            from app import send_tp_hit_report
            report = self.client.get_detailed_report()
            send_tp_hit_report(level, price, profit_amount=profit_amount, report=report)
        except Exception as e:
            logging.error(f"[TP报表发送失败] {e}")

    def stop(self):
        self.running = False
        if self.twm:
            try:
                self.twm.stop()
            except:
                pass
        logging.info("[TP监控] 已停止")

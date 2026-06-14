#!/usr/bin/env python3
# profit_taker.py（最终版 - 包含详细钉钉报告）

import time
import logging
import threading
from binance_client import binance_client
from position_manager import position_manager
from order_executor import order_executor
from position_supervisor import position_supervisor
from dingtalk import send_dingtalk_message

logger = logging.getLogger(__name__)
SYMBOL = "ETHUSDT"

TP1_RATIO = 0.40
TP2_RATIO = 0.40
MANUAL_ADD_THRESHOLD = 0.15


class ProfitTaker:
    def __init__(self):
        self.running = False
        self._thread = None
        self._last_manual_check_time = 0

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("[ProfitTaker] VPS 完全接管 40/40/20 模式已启动")

    def _run(self):
        while self.running:
            try:
                self._check_tp_and_manual_change()
                time.sleep(1.5)
            except Exception as e:
                logger.error(f"[ProfitTaker] 异常: {e}")
                time.sleep(5)

    def _check_tp_and_manual_change(self):
        pos = position_manager.get_position()
        if not pos or pos.get("tp_stage", 0) >= 2:
            return

        side = pos.get("side")
        initial_qty = pos.get("initial_qty", 0)
        current_qty = pos.get("current_qty", initial_qty)

        if initial_qty <= 0 or current_qty <= 0:
            return

        current_price = binance_client.get_current_price(SYMBOL)
        if current_price is None:
            return

        # TP 距离监控（18-50 USD）
        self._check_tp_distance(pos)

        # 监督层方向对齐检查
        position_supervisor.check_and_align_with_latest_signal()

        # 自主 40/40/20 减仓
        tp1_price = pos.get("tp1_price")
        tp2_price = pos.get("tp2_price")
        tp1_hit = pos.get("tp1_hit", False)
        tp2_hit = pos.get("tp2_hit", False)

        hit_level = None
        if side == "LONG":
            if not tp1_hit and current_price >= tp1_price:
                hit_level = "TP1"
            elif tp1_hit and not tp2_hit and current_price >= tp2_price:
                hit_level = "TP2"
        else:
            if not tp1_hit and current_price <= tp1_price:
                hit_level = "TP1"
            elif tp1_hit and not tp2_hit and current_price <= tp2_price:
                hit_level = "TP2"

        if hit_level:
            self._execute_scale_out(hit_level, initial_qty, current_qty, side)

        # 人工加减仓检测
        now = time.time()
        if now - self._last_manual_check_time > 8:
            self._detect_manual_change(pos, current_qty)
            self._last_manual_check_time = now

    def _check_tp_distance(self, pos: dict):
        """检查从入场到 TP3 的 USD 距离"""
        try:
            entry = pos.get("entry_price", 0)
            tp3 = pos.get("tp3_price", 0)
            side = pos.get("side")
            if entry <= 0 or tp3 <= 0:
                return
            distance_usd = abs(tp3 - entry)
            if distance_usd < 18 or distance_usd > 50:
                position_supervisor.send_detailed_report(
                    "TP 距离异常提醒",
                    {
                        "当前距离": f"{distance_usd:.2f} USD",
                        "建议范围": "18~50 USD",
                        "方向": side,
                        "入场价": entry,
                        "TP3": tp3
                    },
                    "⚠️", "WARNING"
                )
        except Exception as e:
            logger.error(f"[ProfitTaker] TP 距离检查异常: {e}")

    def _execute_scale_out(self, level: str, initial_qty: float, current_qty: float, side: str):
        ratio = TP1_RATIO if level == "TP1" else TP2_RATIO
        close_qty = round(initial_qty * ratio, 3)
        if close_qty > current_qty:
            close_qty = current_qty
        if close_qty <= 0:
            return

        close_side = "SELL" if side == "LONG" else "BUY"

        try:
            binance_client.close_position(SYMBOL, close_side, close_qty)
            new_current = max(0.0, current_qty - close_qty)
            position_manager.update_after_partial_close(new_current, level)

            if level == "TP1":
                order_executor.move_to_breakeven()

            position_supervisor.force_reconcile(source=f"profit_taker_{level.lower()}")

            # 详细钉钉报告
            position_supervisor.send_detailed_report(
                f"{level} 自主减仓成功",
                {
                    "方向": side,
                    "减仓数量": close_qty,
                    "剩余数量": new_current,
                    "触发级别": level
                },
                "✅", "DECISION"
            )

        except Exception as e:
            logger.error(f"[ProfitTaker] {level} 减仓失败: {e}")

    def _detect_manual_change(self, pos: dict, memory_current_qty: float):
        try:
            binance_qty = binance_client.get_position_qty(SYMBOL)
            if binance_qty is None or abs(binance_qty - memory_current_qty) < 0.01:
                return

            diff = binance_qty - memory_current_qty
            if diff < 0:
                position_manager.update_current_qty(binance_qty)
            else:
                add_ratio = diff / memory_current_qty if memory_current_qty > 0 else 0
                if add_ratio > MANUAL_ADD_THRESHOLD:
                    self._recalculate_tp_on_significant_add(pos, binance_qty, add_ratio)
                else:
                    position_manager.update_current_qty(binance_qty)
        except Exception as e:
            logger.error(f"[ProfitTaker] 人工变化检测异常: {e}")

    def _recalculate_tp_on_significant_add(self, pos: dict, new_qty: float, add_ratio: float):
        try:
            new_avg = 0
            try:
                p = binance_client.client.futures_position_information(symbol=SYMBOL)[0]
                new_avg = float(p.get("entryPrice", 0))
            except:
                new_avg = pos.get("entry_price", 0)

            atr = pos.get("atr", 30)
            side = pos.get("side")
            entry = new_avg

            if side == "LONG":
                tp1 = round(entry + atr * 1.08, 2)
                tp2 = round(entry + atr * 1.95, 2)
                tp3 = round(entry + atr * 3.0, 2)
                sl = round(entry - atr * 0.92, 2)
            else:
                tp1 = round(entry - atr * 1.08, 2)
                tp2 = round(entry - atr * 1.95, 2)
                tp3 = round(entry - atr * 3.0, 2)
                sl = round(entry + atr * 0.92, 2)

            position_manager.update_current_qty(new_qty)
            with position_manager._lock:
                if position_manager._position:
                    position_manager._position.update({
                        "entry_price": entry,
                        "tp1_price": tp1,
                        "tp2_price": tp2,
                        "tp3_price": tp3,
                        "sl_price": sl,
                        "tp1_hit": False,
                        "tp2_hit": False,
                        "tp_stage": 0
                    })

            # 撤销旧止损单并重新挂单
            old_sl = position_manager.get_sl_order_id()
            if old_sl:
                try:
                    binance_client.cancel_order(SYMBOL, old_sl)
                except:
                    pass

            close_side = "SELL" if side == "LONG" else "BUY"
            new_sl_order = binance_client.place_stop_loss_order(SYMBOL, close_side, sl, new_qty)
            if new_sl_order:
                position_manager.set_sl_order_id(new_sl_order.get("orderId"))

            position_supervisor.force_reconcile(source="manual_add_recalc")

            # 详细报告
            position_supervisor.report_manual_add_recalc(add_ratio, entry, tp1, tp2, tp3, sl)

        except Exception as e:
            logger.error(f"[ProfitTaker] 重算TP失败: {e}")


profit_taker = ProfitTaker()

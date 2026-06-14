#!/usr/bin/env python3
# order_executor.py（执行层 - 完整版：混合模式TP逻辑）

import logging
import time
from binance_client import binance_client
from position_manager import position_manager
from dingtalk import send_dingtalk_message

logger = logging.getLogger(__name__)


class OrderExecutor:
    def __init__(self):
        # ==================== 核心参数（建议后续移到 config.py） ====================
        self.tp1_close_ratio = 0.30      # TP1 平仓比例
        self.tp2_close_ratio = 0.30      # TP2 平仓比例
        self.tp3_close_ratio = 0.40      # TP3 挂限价单比例

        self.atr_sl_mult = 1.0           # 止损 ATR 倍数
        self.atr_tp1_mult = 1.3          # TP1 ATR 倍数
        self.atr_tp2_mult = 2.5          # TP2 ATR 倍数
        self.atr_tp3_mult = 3.8          # TP3 ATR 倍数

        self.risk_percent = 0.90         # 单笔风险比例（%）

    # ==================== 开新仓主入口 ====================
    def open_position(self, side: str, signal_data: dict):
        logger.info(f"[OrderExecutor] 开始处理 {side} 开仓信号")

        try:
            # 1. 撤销旧 TP3 限价单
            self._cancel_tp3_limit_order()

            # 2. 如果有旧仓位，先全平
            self._close_existing_position()

            # 3. 获取 ATR
            atr = float(signal_data.get("atr", 0))
            if atr <= 0:
                logger.error("[OrderExecutor] ATR 无效，无法开仓")
                send_dingtalk_message(f"【开仓失败】{side} - ATR 无效")
                return

            # 4. 计算仓位数量
            qty = self._calculate_position_size(atr)
            if qty <= 0:
                logger.error("[OrderExecutor] 仓位计算结果无效")
                return

            # 5. 市价开新仓
            entry_price = self._place_market_entry(side, qty)
            if entry_price <= 0:
                return

            # 6. 计算 TP / SL 价格
            tp1_price, tp2_price, tp3_price, sl_price = self._calculate_tp_sl_prices(
                side, entry_price, atr
            )

            # 7. 设置分批止盈（TP1/TP2 市价 + TP3 限价）
            self._setup_take_profit_levels(side, qty, tp1_price, tp2_price, tp3_price)

            # 8. 更新内存持仓状态
            position_manager.set_position({
                "side": side,
                "qty": qty,
                "entry_price": entry_price,
                "sl_price": sl_price,
                "tp1_price": tp1_price,
                "tp2_price": tp2_price,
                "tp3_price": tp3_price,
            })

            # 9. 发送开仓成功通知
            from position_supervisor import position_supervisor
            position_supervisor.notify_open_success(side, qty, entry_price, tp1_price, tp2_price, tp3_price)

            logger.info(f"[OrderExecutor] {side} 开仓完成，数量: {qty}")

        except Exception as e:
            logger.error(f"[OrderExecutor] 开新仓异常: {e}")
            send_dingtalk_message(f"【开新仓异常】{side}\n{str(e)}")

    # ==================== 全平 ====================
    def close_position(self, reason: str = "手动全平"):
        try:
            self._cancel_tp3_limit_order()
            binance_client.close_position()
            position_manager.clear_position()
            send_dingtalk_message(f"【全平】{reason}")
            logger.info(f"[OrderExecutor] 全平完成，原因: {reason}")
        except Exception as e:
            logger.error(f"[OrderExecutor] 全平失败: {e}")

    # ==================== 移动止损到保本 ====================
    def move_to_breakeven(self):
        """在 TP1 命中后调用，把止损移到入场价"""
        try:
            pos = position_manager.get_position()
            if not pos:
                return

            side = pos.get("side")
            entry_price = pos.get("entry_price")
            if not entry_price:
                return

            # TODO: 这里接入你原来的移动止损到保本的逻辑
            logger.info(f"[OrderExecutor] 移动止损到保本，方向: {side}，入场价: {entry_price}")
            send_dingtalk_message(f"【移动止损】已移至保本价 {entry_price}")

        except Exception as e:
            logger.error(f"[OrderExecutor] 移动止损失败: {e}")

    # ==================== 内部私有方法 ====================
    def _cancel_tp3_limit_order(self):
        if position_manager.has_tp3_limit_order():
            try:
                # TODO: 接入你原来的撤销 TP3 限价单具体逻辑
                position_manager.clear_tp3_limit_order()
                logger.info("[OrderExecutor] 已撤销 TP3 限价单")
            except Exception as e:
                logger.error(f"[OrderExecutor] 撤销 TP3 限价单失败: {e}")

    def _close_existing_position(self):
        current = position_manager.get_position()
        if current and current.get("qty", 0) > 0:
            try:
                binance_client.close_position()
                position_manager.clear_position()
                time.sleep(0.5)
                logger.info("[OrderExecutor] 已全平旧仓位")
            except Exception as e:
                logger.error(f"[OrderExecutor] 全平旧仓位失败: {e}")

    def _calculate_position_size(self, atr: float) -> float:
        try:
            equity = float(binance_client.get_account_balance().get("USDT", 0))
            risk_amount = equity * (self.risk_percent / 100)
            qty = risk_amount / atr if atr > 0 else 0
            return round(max(qty, 0), 3)
        except Exception as e:
            logger.error(f"[OrderExecutor] 计算仓位失败: {e}")
            return 0

    def _place_market_entry(self, side: str, qty: float) -> float:
        try:
            order = binance_client.place_market_order(side, qty)
            avg_price = float(order.get("avgPrice", 0)) if order else 0
            if avg_price > 0:
                logger.info(f"[OrderExecutor] 市价开仓成功，均价: {avg_price}")
                return avg_price
            else:
                logger.error("[OrderExecutor] 市价开仓失败，未获取到成交价")
                return 0
        except Exception as e:
            logger.error(f"[OrderExecutor] 市价开仓异常: {e}")
            return 0

    def _calculate_tp_sl_prices(self, side: str, entry_price: float, atr: float):
        if side == "LONG":
            tp1 = round(entry_price + atr * self.atr_tp1_mult, 2)
            tp2 = round(entry_price + atr * self.atr_tp2_mult, 2)
            tp3 = round(entry_price + atr * self.atr_tp3_mult, 2)
            sl = round(entry_price - atr * self.atr_sl_mult, 2)
        else:
            tp1 = round(entry_price - atr * self.atr_tp1_mult, 2)
            tp2 = round(entry_price - atr * self.atr_tp2_mult, 2)
            tp3 = round(entry_price - atr * self.atr_tp3_mult, 2)
            sl = round(entry_price + atr * self.atr_sl_mult, 2)
        return tp1, tp2, tp3, sl

    def _setup_take_profit_levels(self, side: str, total_qty: float, tp1_price: float, tp2_price: float, tp3_price: float):
        try:
            tp1_qty = round(total_qty * self.tp1_close_ratio, 3)
            tp2_qty = round(total_qty * self.tp2_close_ratio, 3)
            tp3_qty = round(total_qty * self.tp3_close_ratio, 3)

            close_side = "SELL" if side == "LONG" else "BUY"

            # TP1 市价平仓
            if tp1_qty > 0:
                binance_client.place_market_order(close_side, tp1_qty)
                logger.info(f"[OrderExecutor] TP1 市价平仓 {tp1_qty} @ {tp1_price}")

            # TP2 市价平仓
            if tp2_qty > 0:
                binance_client.place_market_order(close_side, tp2_qty)
                logger.info(f"[OrderExecutor] TP2 市价平仓 {tp2_qty} @ {tp2_price}")

            # TP3 挂限价单
            if tp3_qty > 0:
                binance_client.place_limit_order(close_side, tp3_qty, tp3_price)
                position_manager.set_tp3_limit_order(True)
                logger.info(f"[OrderExecutor] TP3 限价单已挂出 {tp3_qty} @ {tp3_price}")

        except Exception as e:
            logger.error(f"[OrderExecutor] 设置止盈失败: {e}")


# 全局单例
order_executor = OrderExecutor()

# ...（前面代码保持不变，只修改 _handle_tp_trigger 方法）

    def _handle_tp_trigger(self, level: str, current_price: float):
        try:
            if level == "TP1":
                self.executor.partial_close(0.40, f"{level} 触发")
                self._move_tp3_after_partial(current_price)   # 新增：移动 TP3

            elif level == "TP2":
                self.executor.partial_close(0.40, f"{level} 触发")
                self._move_tp3_after_partial(current_price)   # 新增：移动 TP3

            elif level == "TP3":
                self.executor.partial_close(0.20, f"{level} 触发")
                self.clear_tp_levels()

            pnl = self.position_manager.get_unrealized_pnl()
            send_dingtalk_message(f"🎯 【{level} 触发】 当前价 {current_price} | 未实现盈亏 {pnl:+.2f} USDT")

        except Exception as e:
            logger.error(f"[TPMonitor] 处理 {level} 触发失败: {e}")

    def _move_tp3_after_partial(self, current_price: float):
        """部分平仓后移动 TP3（移动止盈）"""
        try:
            atr = self.client.get_atr("ETHUSDT", "3h", 50, 14) or 22.0

            if self.position_side == "LONG":
                new_tp3 = round(current_price + atr * 2.3, 2)   # 当前价 + 2.3倍ATR
            else:
                new_tp3 = round(current_price - atr * 2.3, 2)

            with self._lock:
                self.tp3_price = new_tp3

            # 更新状态文件
            state_manager.save_state({
                "tp1": self.tp1_price,
                "tp2": self.tp2_price,
                "tp3": new_tp3,
                "side": self.position_side,
                "remaining_qty": self.position_qty * 0.2,   # 剩余约20%
                "entry_price": self.entry_price,
                "is_monitoring": True
            })

            send_dingtalk_message(f"📈 【TP3 已移动止盈】新 TP3 = {new_tp3}")
            logger.info(f"[TPMonitor] TP3 已移动至 {new_tp3}")

        except Exception as e:
            logger.error(f"[TPMonitor] 移动 TP3 失败: {e}")

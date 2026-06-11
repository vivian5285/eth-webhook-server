# daily_report_scheduler.py（优化最终版）
import logging
import threading
import time
from datetime import datetime, timedelta
from binance_client import BinanceClient

binance_client = BinanceClient()

class DailyReportScheduler:
    def __init__(self, send_time: str = "00:00"):
        """
        send_time: 每天发送报告的时间，格式 "HH:MM"（默认 00:00 HKT）
        """
        self.send_time = send_time
        self.thread = None
        self.running = False

    def _get_today_realized_pnl(self):
        """获取今日已实现盈亏"""
        try:
            now = datetime.now()
            start_time = int((now - timedelta(days=1)).timestamp() * 1000)  # 昨天到现在
            income_history = binance_client.client.futures_income_history(
                incomeType="REALIZED_PNL",
                startTime=start_time,
                limit=1000
            )
            total_pnl = sum(float(item['income']) for item in income_history)
            return round(total_pnl, 2)
        except Exception as e:
            logging.error(f"[日报] 获取今日已实现盈亏失败: {e}")
            return 0.0

    def _get_current_snapshot(self):
        """获取当前账户快照"""
        try:
            balance = binance_client.get_account_balance() or {}
            position = binance_client.get_current_position("ETHUSDT")
            return {
                "equity": balance.get("totalWalletBalance", 0),
                "available": balance.get("availableBalance", 0),
                "position": position
            }
        except Exception as e:
            logging.error(f"[日报] 获取账户快照失败: {e}")
            return {}

    def _send_daily_report(self):
        """发送每日报告"""
        try:
            today_pnl = self._get_today_realized_pnl()
            snapshot = self._get_current_snapshot()
            position = snapshot.get("position")

            position_text = "无持仓"
            if position:
                position_text = f"{position['side'].upper()} {position['qty']} 张 @ {position['avg_price']}"

            title = "📊 每日交易报告"
            content = (
                f"**日期**：{datetime.now().strftime('%Y-%m-%d')}\n\n"
                f"**💰 今日已实现盈亏**：{today_pnl} USDT\n\n"
                f"**📈 当前账户状态**\n"
                f"- 账户权益：{snapshot.get('equity', 0):.2f} USDT\n"
                f"- 可用余额：{snapshot.get('available', 0):.2f} USDT\n"
                f"- 当前持仓：{position_text}\n\n"
                f"**⏰ 生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

            binance_client._send_dingtalk(title, content)
            logging.info("[日报] 每日报告已发送")

        except Exception as e:
            logging.error(f"[日报] 发送失败: {e}")

    def _run(self):
        """后台循环检查是否到达发送时间"""
        while self.running:
            now = datetime.now().strftime("%H:%M")
            if now == self.send_time:
                self._send_daily_report()
                # 避免同一分钟重复发送
                time.sleep(60)
            time.sleep(30)  # 每30秒检查一次

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logging.info(f"[日报] 每日报告调度器已启动（每天 {self.send_time} 发送）")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logging.info("[日报] 每日报告调度器已停止")


# 全局实例（默认每天 00:00 发送）
daily_report_scheduler = DailyReportScheduler(send_time="00:00")

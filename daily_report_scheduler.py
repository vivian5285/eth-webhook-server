# daily_report_scheduler.py
import threading
import time
import logging
from datetime import datetime
from binance_client import BinanceClient

class DailyReportScheduler:
    def __init__(self, binance_client: BinanceClient, report_time: str = "00:05"):
        """
        report_time: 格式 "HH:MM"，例如 "00:05" 表示每天00:05推送
        """
        self.client = binance_client
        self.report_time = report_time
        self.running = False
        self.thread = None

    def _should_send_report(self) -> bool:
        now = datetime.now().strftime("%H:%M")
        return now == self.report_time

    def _send_daily_report(self):
        try:
            report = self.client.get_detailed_report()  # 假设 binance_client 有此方法
            if not report:
                logging.warning("[每日日报] 获取报告失败")
                return

            title = "📊 ETH 每日账户完整日报"
            content = (
                f"**日期**：{datetime.now().strftime('%Y-%m-%d')}\n\n"
                f"**账户权益**：{report.get('equity', 0):.2f} USDT\n"
                f"**可用余额**：{report.get('available', 0):.2f} USDT\n"
                f"**当前持仓**：{report.get('position_side', '无')} | {report.get('position_qty', 0)}\n"
                f"**浮动盈亏**：{report.get('unrealized_pnl', 0):.2f} USDT\n"
                f"**今日已实现盈亏**：{report.get('daily_realized_pnl', 0):.2f} USDT\n\n"
                f"**风险敞口**：{report.get('risk_exposure', '正常')}\n"
                f"**杠杆倍数**：{report.get('leverage', 0)}x\n\n"
                f"> 每日自动推送 | 如有异常请及时检查"
            )
            self.client._send_dingtalk(title, content)
            logging.info("[每日日报] 已成功推送")
        except Exception as e:
            logging.error(f"[每日日报发送失败] {e}")

    def _run(self):
        self.running = True
        logging.info(f"[每日日报调度器] 已启动，每天 {self.report_time} 推送")
        while self.running:
            try:
                if self._should_send_report():
                    self._send_daily_report()
                    # 避免同一分钟重复发送
                    time.sleep(70)
                time.sleep(30)
            except Exception as e:
                logging.error(f"[每日日报调度器异常] {e}")
                time.sleep(60)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

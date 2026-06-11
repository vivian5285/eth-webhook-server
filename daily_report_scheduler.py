# daily_report_scheduler.py（优化加强版）
import threading
import time
import logging
from datetime import datetime
from binance_client import BinanceClient

class DailyReportScheduler:
    def __init__(self, binance_client: BinanceClient, report_time: str = "00:05"):
        """
        report_time: 格式 "HH:MM"，例如 "00:05" 表示每天北京时间 00:05 推送
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
            report = self.client.get_detailed_report()
            if not report:
                logging.warning("[每日日报] 获取报告失败，跳过本次推送")
                return

            title = "📊 ETH 每日账户完整日报"

            # 持仓信息格式化
            if report.get("position_side") != "无":
                position_info = (
                    f"**持仓方向**：{report.get('position_side')}\n"
                    f"**持仓数量**：{report.get('position_qty', 0)}\n"
                    f"**开仓均价**：{report.get('entry_price', 0)}\n"
                    f"**浮动盈亏**：{report.get('unrealized_pnl', 0):.2f} USDT\n"
                    f"**当前杠杆**：{report.get('leverage', 0)}x"
                )
            else:
                position_info = "**当前无持仓**"

            content = (
                f"**日期**：{datetime.now().strftime('%Y-%m-%d')}\n\n"
                f"**📈 账户概览**\n"
                f"- 账户权益：{report.get('equity', 0):.2f} USDT\n"
                f"- 可用余额：{report.get('available', 0):.2f} USDT\n\n"
                f"**📍 持仓详情**\n"
                f"{position_info}\n\n"
                f"**💰 今日盈亏**\n"
                f"- 已实现盈亏：{report.get('daily_realized_pnl', 0):.2f} USDT\n"
                f"- 未实现盈亏：{report.get('unrealized_pnl', 0):.2f} USDT\n\n"
                f"**⚠️ 风险提示**\n"
                f"- 风险状态：{report.get('risk_exposure', '正常')}\n\n"
                f"> 每日自动推送 | 如有异常请及时检查"
            )

            self.client._send_dingtalk(title, content)
            logging.info("[每日日报] 已成功推送完整报告")

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
        logging.info("[每日日报调度器] 线程已启动")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
            logging.info("[每日日报调度器] 已停止")

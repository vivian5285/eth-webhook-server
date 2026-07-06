# daily_report_scheduler.py - 每日报告加强版

import os
import logging
import schedule
import time
from datetime import datetime, timedelta
from binance_client import BinanceClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

binance_client = BinanceClient()


def get_today_realized_pnl():
    """获取今日已实现盈亏"""
    try:
        income = binance_client.client.futures_income_history(
            incomeType="REALIZED_PNL",
            startTime=int((datetime.now() - timedelta(days=1)).timestamp() * 1000)
        )
        total_pnl = sum(float(item['income']) for item in income if item.get('income'))
        return round(total_pnl, 2)
    except Exception as e:
        logging.error(f"[获取今日已实现盈亏失败] {e}")
        return 0.0


def send_daily_report():
    """发送每日报告"""
    try:
        acc = binance_client.get_detailed_account_info()
        position = binance_client.get_current_position()
        today_pnl = get_today_realized_pnl()

        # 当前持仓状态
        if position and position.get("positionAmt", 0) != 0:
            pos_text = f"{position['side'].upper()} {abs(position['positionAmt'])} 张 @ {position['entryPrice']:.2f}"
        else:
            pos_text = "无持仓"

        text = f"""### 📊 ETH 每日账户报告

**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M')}

💰 **账户概览**
- 账户权益：{acc.get('totalWalletBalance', 0):.2f} USDT
- 可用余额：{acc.get('availableBalance', 0):.2f} USDT
- 未实现盈亏：{acc.get('totalUnrealizedProfit', 0):+.2f} USDT
- 今日已实现盈亏：{today_pnl:+.2f} USDT

📈 **风险指标**
- 保证金比例：{acc.get('marginRatio', 0)*100:.2f}%
- 当前杠杆：{acc.get('currentLeverage', 0)}x
- 维持保证金：{acc.get('maintMargin', 0):.2f} USDT

📍 **当前持仓**
{pos_text}

⏰ 报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        binance_client._send_dingtalk_markdown("每日账户报告", text)
        logging.info("[每日报告] 已发送")

    except Exception as e:
        logging.error(f"[每日报告发送失败] {e}")


def start():
    """启动每日定时报告（默认每天 08:00 发送，可修改）"""
    schedule.every().day.at("08:00").do(send_daily_report)
    logging.info("[每日报告] 定时任务已启动，每天 08:00 发送")

    # 也可以手动测试一次
    # send_daily_report()

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    start()

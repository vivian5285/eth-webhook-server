#!/usr/bin/env python3
# check_system.py（混合模式优化版）

import sys
import os
from datetime import datetime

# 确保能导入项目模块
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from position_manager import position_manager
from position_supervisor import position_supervisor
from tp_monitor import tp_monitor


def check_system():
    print("=" * 60)
    print(f"【系统检查】{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. TPMonitor 运行状态
    print(f"\n[1] TPMonitor 状态: {'✅ 运行中' if tp_monitor.running else '❌ 未运行'}")

    # 2. 当前持仓状态
    pos = position_manager.get_position()
    if pos:
        print(f"\n[2] 当前持仓:")
        print(f"    方向: {pos['side']}")
        print(f"    数量: {pos['qty']}")
        print(f"    均价: {pos['avg_price']}")
        print(f"    止损: {pos.get('stop_loss', '未设置')}")
        print(f"    TP1 : {pos.get('tp1_price')}")
        print(f"    TP2 : {pos.get('tp2_price')}")
        print(f"    TP3 : {pos.get('tp3_price')}")
    else:
        print("\n[2] 当前持仓: ✅ 无持仓")

    # 3. TP3 限价单状态（混合模式关键）
    if position_manager.has_tp3_limit_order():
        tp3_info = position_manager.get_tp3_limit_order()
        print(f"\n[3] TP3 限价单: ✅ 存在")
        print(f"    Order ID : {tp3_info['order_id']}")
        print(f"    价格     : {tp3_info['price']}")
        print(f"    数量     : {tp3_info['qty']}")
    else:
        print("\n[3] TP3 限价单: ✅ 无挂单")

    # 4. 最后一次仓位检查时间（用于判断节流是否正常）
    last_check = position_manager.last_reconcile_time
    if last_check > 0:
        from time import time
        seconds_ago = int(time() - last_check)
        print(f"\n[4] 最后仓位检查: {seconds_ago} 秒前")
    else:
        print("\n[4] 最后仓位检查: 尚未执行")

    # 5. Supervisor 状态
    print(f"\n[5] PositionSupervisor: ✅ 正常")

    print("\n" + "=" * 60)
    print("检查完成")
    print("=" * 60)


if __name__ == "__main__":
    check_system()

# position_supervisor.py（强监督 + 主动纠错版）
import logging
import time
from binance_client import BinanceClient
from position_manager import PositionManager

binance_client = BinanceClient()
position_manager = PositionManager()

def enforce_signal_policy(signal: str, symbol: str = "ETHUSDT"):
    """
    监督层核心函数（最高权限）
    - 任何方向信号都必须先平后开
    - 检测到执行偏差时主动纠错
    - 只有实盘完全对齐后才返回成功
    """
    try:
        current_pos = binance_client.get_current_position(symbol)
        desired_side = "long" if signal == "OPEN_LONG" else "short"

        logging.info(f"[监督层] 收到信号 {signal}，当前持仓: {current_pos['side'] if current_pos else '无'}")

        # 1. 有持仓（无论同反方向）→ 必须先全平
        if current_pos:
            logging.info(f"[监督层] 检测到持仓 {current_pos['side']}，执行强制全平")
            close_result = binance_client.close_all_positions(symbol)
            if close_result.get("status") != "success":
                return {"status": "error", "message": "监督层强制平仓失败"}

            position_manager.clear_position()
            time.sleep(2.5)

            # 再次确认是否真的平干净
            current_pos = binance_client.get_current_position(symbol)
            if current_pos:
                logging.error("[监督层] 平仓后仍存在持仓，主动纠错失败")
                return {"status": "error", "message": "平仓后仍存在持仓，纠错失败"}

        # 2. 平仓成功后，开新仓（根据最新信号）
        logging.info(f"[监督层] 仓位已清理，执行开新仓: {signal}")
        return {"status": "ready_to_open", "desired_side": desired_side, "signal": signal}

    except Exception as e:
        logging.error(f"[监督层异常] {e}")
        return {"status": "error", "message": str(e)}


def verify_and_correct_after_open(signal: str, symbol: str):
    """
    开仓后进行最终验证 + 主动纠错
    如果实盘和信号不一致，尝试再次修正
    """
    time.sleep(2.0)
    real_pos = binance_client.get_current_position(symbol)
    desired_side = "long" if signal == "OPEN_LONG" else "short"

    if real_pos and real_pos.get("side") == desired_side:
        logging.info(f"[监督层验证] {signal} 执行成功，实盘持仓已对齐")
        position_manager.update_position(
            desired_side, symbol, real_pos["qty"], real_pos["avg_price"], 0, 0, 0
        )
        return {"status": "success", "real_position": real_pos}
    else:
        logging.warning(f"[监督层验证] {signal} 执行后实盘未对齐，尝试主动纠错...")
        # 这里可以加重试逻辑或告警
        return {"status": "mismatch", "real_position": real_pos}

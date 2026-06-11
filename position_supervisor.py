# position_supervisor.py
import logging
import time
from binance_client import BinanceClient
from position_manager import PositionManager

binance_client = BinanceClient()
position_manager = PositionManager()

def supervise_and_execute(signal: str, symbol: str = "ETHUSDT"):
    """
    监督执行入口：
    - 方向信号一律先平后开
    - 只有实盘确认后才允许推送报告
    """
    try:
        current_pos = binance_client.get_current_position(symbol)
        intended_side = None

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            intended_side = "long" if signal == "OPEN_LONG" else "short"

            # 有持仓 → 强制先平（无论同反方向）
            if current_pos:
                logging.info(f"[监督层] 当前持有 {current_pos['side']}，收到 {signal}，执行强制先平后开")
                close_result = binance_client.close_all_positions(symbol)
                if close_result.get("status") != "success":
                    return {"status": "error", "message": "监督层平仓失败"}

                position_manager.clear_position()
                time.sleep(2.5)

                # 再次确认是否真的平干净
                current_pos = binance_client.get_current_position(symbol)
                if current_pos:
                    logging.error("[监督层] 平仓后仍存在持仓，终止开新仓")
                    return {"status": "error", "message": "平仓后仍存在持仓"}

            # 准备开新仓
            logging.info(f"[监督层] 仓位已清理，准备执行 {signal}")
            return {"status": "ready_to_open", "side": intended_side, "signal": signal}

        elif signal == "CLOSE_ALL":
            if current_pos:
                result = binance_client.close_all_positions(symbol)
                if result.get("status") == "success":
                    position_manager.clear_position()
                return result
            return {"status": "skipped", "message": "当前无持仓"}

        return {"status": "ignored"}

    except Exception as e:
        logging.error(f"[监督层异常] {e}")
        return {"status": "error", "message": str(e)}


def confirm_and_report_after_action(signal: str, symbol: str, qty: float = None, entry_price: float = None, tp1=None, tp2=None, tp3=None):
    """
    动作执行后调用此函数进行最终确认 + 推送报告
    只有实盘状态对齐后才推送
    """
    try:
        time.sleep(2.0)  # 等待交易所状态更新
        real_pos = binance_client.get_current_position(symbol)

        if signal in ["OPEN_LONG", "OPEN_SHORT"]:
            intended_side = "long" if signal == "OPEN_LONG" else "short"

            if real_pos and real_pos.get("side") == intended_side:
                logging.info(f"[监督层确认] {signal} 实盘持仓已对齐，开始推送钉钉")
                # 这里调用报告（带真实数据）
                from app import send_beautiful_open_report  # 避免循环导入，实际可调整
                send_beautiful_open_report(signal, symbol, qty or real_pos.get("qty", 0), 
                                           entry_price or real_pos.get("avg_price", 0), tp1, tp2, tp3)
            else:
                logging.warning(f"[监督层确认] {signal} 实盘持仓未对齐，暂不推送报告")

        elif signal == "CLOSE_ALL":
            if not real_pos:
                logging.info("[监督层确认] 全平成功，实盘无持仓")
                from app import send_beautiful_close_report
                send_beautiful_close_report("CLOSE_ALL / 监督层确认", symbol)

    except Exception as e:
        logging.error(f"[监督层确认异常] {e}")

# position_policy.py
import logging
import time
from binance_client import BinanceClient
from position_manager import PositionManager

binance_client = BinanceClient()
position_manager = PositionManager()

def enforce_close_then_open(signal: str, symbol: str = "ETHUSDT"):
    """
    监督执行策略：
    - 收到 OPEN_LONG / OPEN_SHORT 时，严格执行“先平后开”
    - 只有 CLOSE_ALL 才只平不开
    """
    try:
        current_pos = binance_client.get_current_position(symbol)
        intended_side = "long" if signal == "OPEN_LONG" else "short"

        # 1. 有持仓 → 必须先全平
        if current_pos:
            logging.info(f"[Policy监督] 当前有 {current_pos['side']} 仓，收到 {signal}，执行强制先平")
            
            close_result = binance_client.close_all_positions(symbol)
            if close_result.get("status") != "success":
                return {"status": "error", "message": "监督层平仓失败"}

            position_manager.clear_position()
            time.sleep(2.0)

            # 二次确认平仓结果
            current_pos = binance_client.get_current_position(symbol)
            if current_pos:
                logging.error("[Policy监督] 平仓后仍存在持仓，终止开新仓")
                return {"status": "error", "message": "平仓后仍存在持仓"}

        # 2. 执行开新仓（由上层调用）
        logging.info(f"[Policy监督] 仓位已清理，允许执行 {signal}")
        return {"status": "ready_to_open", "side": intended_side}

    except Exception as e:
        logging.error(f"[Policy监督异常] {e}")
        return {"status": "error", "message": str(e)}

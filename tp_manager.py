# tp_manager.py（最终强壮版）
import logging

def get_actual_tp_prices(entry_price: float, atr: float, side: str, 
                         tp1_mult: float = 1.28, 
                         tp2_mult: float = 2.5, 
                         tp3_mult: float = 3.6) -> dict:
    """
    返回实际出场价格（USDT）
    """
    if atr <= 0:
        logging.warning("[TP计算] ATR <= 0，使用默认值")
        atr = 10  # 兜底值，防止除零或异常

    try:
        if side.lower() == "long":
            return {
                "tp1": round(entry_price + atr * tp1_mult, 2),
                "tp2": round(entry_price + atr * tp2_mult, 2),
                "tp3": round(entry_price + atr * tp3_mult, 2),
            }
        elif side.lower() == "short":
            return {
                "tp1": round(entry_price - atr * tp1_mult, 2),
                "tp2": round(entry_price - atr * tp2_mult, 2),
                "tp3": round(entry_price - atr * tp3_mult, 2),
            }
        else:
            raise ValueError("side 必须是 'long' 或 'short'")
    except Exception as e:
        logging.error(f"[TP价格计算异常] {e}")
        # 兜底返回 entry_price 附近
        return {
            "tp1": round(entry_price * 1.01, 2),
            "tp2": round(entry_price * 1.02, 2),
            "tp3": round(entry_price * 1.03, 2),
        }


def get_tp_distances(atr: float, 
                     tp1_mult: float = 1.28, 
                     tp2_mult: float = 2.5, 
                     tp3_mult: float = 3.6) -> dict:
    """返回 TP 的 ATR 距离（用于其他风控逻辑）"""
    return {
        "tp1_distance": round(atr * tp1_mult, 2),
        "tp2_distance": round(atr * tp2_mult, 2),
        "tp3_distance": round(atr * tp3_mult, 2),
    }

# tp_manager.py（最终完整强壮版）
import logging

def get_actual_tp_prices(entry_price: float, atr: float, side: str,
                         tp1_mult: float = 1.28,
                         tp2_mult: float = 2.5,
                         tp3_mult: float = 3.6) -> dict:
    """
    返回实际出场价格（推荐使用）
    """
    if atr <= 0:
        logging.warning("[TP计算] ATR <= 0，使用默认值")
        atr = 10

    try:
        if side.lower() == "long":
            return {
                "tp1": round(entry_price + atr * tp1_mult, 2),
                "tp2": round(entry_price + atr * tp2_mult, 2),
                "tp3": round(entry_price + atr * tp3_mult, 2),
            }
        else:  # short
            return {
                "tp1": round(entry_price - atr * tp1_mult, 2),
                "tp2": round(entry_price - atr * tp2_mult, 2),
                "tp3": round(entry_price - atr * tp3_mult, 2),
            }
    except Exception as e:
        logging.error(f"[TP价格计算异常] {e}")
        return {
            "tp1": round(entry_price * 1.01, 2),
            "tp2": round(entry_price * 1.02, 2),
            "tp3": round(entry_price * 1.03, 2),
        }


# 兼容旧代码（app.py 里可能还在调用这个名字）
def calculate_tp_prices(entry_price: float, atr: float, side: str) -> dict:
    return get_actual_tp_prices(entry_price, atr, side)


def get_tp_distances(atr: float,
                     tp1_mult: float = 1.28,
                     tp2_mult: float = 2.5,
                     tp3_mult: float = 3.6) -> dict:
    """返回 TP 的 ATR 距离"""
    return {
        "tp1_distance": round(atr * tp1_mult, 2),
        "tp2_distance": round(atr * tp2_mult, 2),
        "tp3_distance": round(atr * tp3_mult, 2),
    }

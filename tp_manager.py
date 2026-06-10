# tp_manager.py（最终适配版）
import logging

def get_actual_tp_prices(entry_price: float, atr: float, side: str,
                         tp1_mult: float = 1.28,
                         tp2_mult: float = 2.5,
                         tp3_mult: float = 3.6) -> dict:
    """
    返回真实的出场价格（绝对价格）
    """
    if atr <= 0:
        logging.warning("[TP计算] ATR <= 0，使用默认值 10")
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
        # 兜底返回
        offset = atr * 1.5 if atr > 0 else 50
        if side.lower() == "long":
            return {
                "tp1": round(entry_price + offset, 2),
                "tp2": round(entry_price + offset * 2, 2),
                "tp3": round(entry_price + offset * 3, 2),
            }
        else:
            return {
                "tp1": round(entry_price - offset, 2),
                "tp2": round(entry_price - offset * 2, 2),
                "tp3": round(entry_price - offset * 3, 2),
            }


# 兼容旧代码（可选保留）
def calculate_tp_prices(entry_price: float, atr: float, side: str) -> dict:
    return get_actual_tp_prices(entry_price, atr, side)

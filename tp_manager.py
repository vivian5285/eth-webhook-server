# tp_manager.py
def calculate_tp_prices(entry_price: float, atr: float, direction: str = "long"):
    if direction == "long":
        return {
            "tp1": round(entry_price + atr * 1.28, 2),
            "tp2": round(entry_price + atr * 2.5, 2),
            "tp3": round(entry_price + atr * 3.6, 2)
        }
    else:
        return {
            "tp1": round(entry_price - atr * 1.28, 2),
            "tp2": round(entry_price - atr * 2.5, 2),
            "tp3": round(entry_price - atr * 3.6, 2)
        }

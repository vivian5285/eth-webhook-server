# bias_checker.py
def check_simple_bias(client, symbol: str, timeframe: str = "45m"):
    # TODO: 实现简单指标判断（MACD + KDJ + 成交量 + EMA）
    # 当前先返回 NEUTRAL，后续补充
    return "NEUTRAL"

def is_obvious_conflict(signal: str, bias: str) -> bool:
    if signal == "OPEN_SHORT" and bias == "BULLISH":
        return True
    if signal == "OPEN_LONG" and bias == "BEARISH":
        return True
    return False

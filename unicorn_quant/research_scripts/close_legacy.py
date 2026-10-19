"""2026-10-18 宝贝要求现在就对齐：擂台服务停掉期间，用擂台自己的平仓函数平掉 CSCO/MSTR 两张遗留纸面单(手续费/资金费/净值照常结算)。"""
import sys, time
sys.path.insert(0, "/home/stratroster/eth-webhook-server")
from strategy_engine import multi_strategy_runner as M
from strategy_engine import klines
STRAT = "stock_sector_rot6m5_long"
keys = [("CSCOUSDT", STRAT), ("MSTRUSDT", STRAT)]
M._hydrate_keys_from_db(keys)
bar_time = 1792195200000          # 2026-10-17 日线(晚于开仓那根 10-16)
for key in keys:
    pos = M._open_positions.get(key)
    if not pos:
        print(key, "内存里没有这笔持仓(可能已平)"); continue
    b = klines.get_bars(key[0], "1h", 3)
    px = float(b[-1]["c"])
    M._close_position(key, px, bar_time, "对齐回测：正股本轮EMA多头内已跌破3×ATR止损(10-15)，按规则锁定；手动对齐上线前遗留单")
    print(key, "已平 @", px)

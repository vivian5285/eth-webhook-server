p = "/home/stratroster/eth-webhook-server/strategy_engine/strategies/stock_sector_rotation.py"
s = open(p, encoding="utf-8").read()
old = '''            _lows = [float(x["l"]) for x in perp if int(x["t"]) / 1000 >= _w[0] - 1800 and int(x["t"]) / 1000 + 3600 <= _w[1] + 1800]
            if _lows and min(_lows) <= _stp3:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(min(price, _stp3), 6), "bar_time": bar_time,'''
new = '''            _hit = next((x for x in perp if int(x["t"]) / 1000 >= _w[0] - 1800 and int(x["t"]) / 1000 + 3600 <= _w[1] + 1800
                         and float(x["l"]) <= _stp3), None)
            if _hit is not None:                       # 成交价同回测：止损价；跳空开在止损下方就按开盘价
                return {"action": "CLOSE_QUICK_EXIT", "price": round(min(float(_hit["o"]), _stp3), 6), "bar_time": bar_time,'''
assert s.count(old) == 1
open(p, "w", encoding="utf-8").write(s.replace(old, new)); print("ok")

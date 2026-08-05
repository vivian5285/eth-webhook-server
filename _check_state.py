#!/usr/bin/env python3
import sys, json, os
base = os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else '/home/trading/binance-engine'
syms = ['ETHUSDT', 'XAUUSDT', 'BNBUSDT', 'ZECUSDT', 'BCHUSDT']
for sym in syms:
    f = f'/home/trading/binance-engine/binance_vps_state_{sym}.json'
    if os.path.exists(f):
        with open(f) as fh:
            d = json.load(fh)
        print(f'{sym}: watched={d.get("watched_qty")} initial={d.get("initial_qty")} tp_consumed={d.get("tp_levels_consumed")} hard_sl={d.get("frozen_hard_sl_px")} radar={d.get("radar_activated")} entry={d.get("watched_entry")}')
        tv_tps = d.get('tv_tps', [])
        print(f'  tv_tps={tv_tps}')
    else:
        print(f'{sym}: NO STATE FILE')

#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/trading/binance-engine')
from binance_client import binance_client
try:
    eth = binance_client.get_current_price('ETHUSDT')
    xau = binance_client.get_current_price('XAUUSDT')
    print(f"ETHUSDT={eth}")
    print(f"XAUUSDT={xau}")
except Exception as e:
    print(f"ERROR: {e}")
    # fallback: try binance ws
    import websocket, json
    import threading, time
    
    result = {}
    lock = threading.Lock()
    
    def on_message(ws, msg):
        try:
            d = json.loads(msg)
            s = d.get('s', '')
            if s == 'ETHUSDT':
                result['ETHUSDT'] = float(d.get('c', 0))
            elif s == 'XAUUSDT':
                result['XAUUSDT'] = float(d.get('c', 0))
            with lock:
                if len(result) >= 2:
                    ws.close()
        except:
            pass
    
    def on_error(ws, err):
        pass
    
    def on_close(ws, *args):
        pass
    
    def on_open(ws):
        ws.send(json.dumps({"method": "SUBSCRIBE", "params": ["ethusdt@ticker", "xauusdt@ticker"], "id": 1}))
    
    ws = websocket.WebSocketApp(
        "wss://stream.binance.com:9443/ws",
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open,
    )
    t = threading.Thread(target=ws.run_forever)
    t.daemon = True
    t.start()
    t.join(timeout=5)
    print(f"ETHUSDT={result.get('ETHUSDT', 'N/A')}")
    print(f"XAUUSDT={result.get('XAUUSDT', 'N/A')}")

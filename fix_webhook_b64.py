#!/usr/bin/env python3
import sys
with open('/home/trading/binance-engine/app.py', 'r', encoding='utf-8') as f:
    content = f.read()
if '/binance/webhook' in content:
    print('OK already exists')
    sys.exit(0)
old = "@app.route('/webhook', methods=['POST'])\n@app.route('/webhook/<path:ticker>', methods=['POST'])"
new = old + "\n@app.route('/binance/webhook', methods=['POST'])\n@app.route('/binance/webhook/<path:ticker>', methods=['POST'])"
if old not in content:
    print('ERROR: not found')
    sys.exit(1)
content = content.replace(old, new)
with open('/home/trading/binance-engine/app.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('OK added /binance/webhook route')

#!/bin/bash
# Check health on both Binance ports
for port in 5003 5007; do
  echo "=== port $port ==="
  curl -s "http://127.0.0.1:$port/health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('version','?'), d.get('status','?'))" 2>&1
done

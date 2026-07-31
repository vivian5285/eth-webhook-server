@echo off
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes root@187.77.130.144 "cd /home/trading/binance-engine && ls -la venv/bin/activate 2>/dev/null || echo NO_VENV && ls -la binance_client.py && python3 -c 'import binance_client; print(binance_client.BINANCE_CLIENT_VERSION)' 2>&1 | tail -3"

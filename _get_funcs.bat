@echo off
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes root@187.77.130.144 "cd /home/trading/binance-engine && git show HEAD^:reentry_profiles.py | grep -n 'def radar_gate_price_from_tps' && git show HEAD^:reentry_profiles.py | sed -n '/def radar_gate_price_from_tps/,/^def /p' | head -25"

@echo off
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes root@187.77.130.144 "cd /home/trading/binance-engine && git show HEAD^:reentry_profiles.py | sed -n '/^def activation_price\(/,/^def /p' | head -60"

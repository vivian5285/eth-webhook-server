@echo off
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes root@187.77.130.144 "cd /home/trading/binance-engine && git show HEAD^:reentry_profiles.py | awk '/^def activation_price/,/^def [a-z]/' | head -40 && echo === && git show HEAD^:reentry_profiles.py | awk '/^def activation_price_from_tp1/,/^def [a-z]/' | head -50"

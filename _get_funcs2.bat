@echo off
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes root@187.77.130.144 "cd /home/trading/binance-engine && git show HEAD^:reentry_profiles.py | grep -n 'def tp1_distance' && git show HEAD^:reentry_profiles.py | sed -n '/def tp1_distance/,/^def /p' && echo --- && git show HEAD^:reentry_profiles.py | grep 'TP1_ATR_MULT'"

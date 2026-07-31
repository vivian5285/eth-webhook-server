@echo off
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes root@187.77.130.144 "cd /home/trading/binance-engine && git show HEAD:webhook_parser.py | grep -c 'def radar_activation_price' && git show HEAD:webhook_parser.py | grep -A 30 'def radar_activation_price' | tail -32"

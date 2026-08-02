ssh -o StrictHostKeyChecking=no root@187.77.130.144 "su - trading -c 'cd ~/binance-engine && grep -n BINANCE_VPS_VERSION position_supervisor_binance.py | head -2'"

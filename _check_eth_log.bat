@echo off
echo === RECENT ETH LOG ===
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes root@187.77.130.144 "tail -150 /home/trading/binance-engine/logs/binance_brain.log | grep -i eth"
echo === END ===

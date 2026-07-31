@echo off
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 root@187.77.130.144 "kill -9 2362571 2240972 2>/dev/null; sleep 2; ss -tlnp | grep 5003 || echo PORT_FREE"

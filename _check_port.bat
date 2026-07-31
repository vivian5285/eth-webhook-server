@echo off
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes root@187.77.130.144 "ss -tlnp | grep 5003; echo STATUS_DONE"

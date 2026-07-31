@echo off
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes root@187.77.130.144 "echo === ROOT GUNICORN ===; ps aux | grep gunicorn | grep -v grep || echo NONE; pkill -9 -u root gunicorn 2>/dev/null; echo KILLED; echo === ROOT AFTER KILL ===; ps aux | grep gunicorn | grep -v grep || echo NONE; echo === TRADING ===; ps aux | grep gunicorn | grep trading || echo NONE"
echo === DONE ===

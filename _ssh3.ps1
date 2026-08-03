ssh -o StrictHostKeyChecking=no root@45.76.246.232 "tail -n 400 /root/logs/eth-webhook-server.log | grep -i bnb" 2>&1

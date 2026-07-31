@echo off
cd /d C:\Users\Administrator\Desktop\eth-webhook-server-main
echo Starting deployment...
python -c "import paramiko; print('connected')" > deploy_out.txt 2>&1
echo Done

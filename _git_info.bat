@echo off
cmd /c "cd /d C:\Users\Administrator\Desktop\eth-webhook-server-main && git log -5 --oneline && echo --- && git diff --stat"

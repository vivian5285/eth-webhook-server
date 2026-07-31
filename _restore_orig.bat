@echo off
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes root@187.77.130.144 "cd /home/trading/binance-engine && git show HEAD^:webhook_parser.py > webhook_parser_orig.py && git show HEAD^:reentry_profiles.py > reentry_profiles_orig.py && echo ORIG_RESTORED"

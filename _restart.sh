#!/bin/bash
su - trading -c 'cd ~/binance-engine && git fetch origin && git reset --hard origin/main'
systemctl restart binance-engine.service

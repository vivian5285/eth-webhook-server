#!/bin/bash
cd /home/trading/binance-engine
source venv/bin/activate
python -c "from adapters import BinanceWeightedSession; print('adapters import OK')"

#!/bin/bash
# 在VPS上以root运行。先scp本目录文件到/tmp/radar_envelope_deploy/(覆盖旧文件)。
set -eo pipefail
SRC=/tmp/radar_envelope_deploy
cd $SRC && python3 test_radar_envelope.py | tail -2
for a in binanceB binanceE; do
  d=/home/$a/binance-engine
  cp $d/position_supervisor_binance.py $d/position_supervisor_binance.py.bak_20261004_hotfix
  cp $SRC/radar_envelope.py $d/radar_envelope.py && chown $a:$a $d/radar_envelope.py
  python3 $SRC/hotfix_resolve_live_qty.py $d/position_supervisor_binance.py
  python3 -m py_compile $d/position_supervisor_binance.py $d/radar_envelope.py
done
cp $SRC/radar_envelope.py /home/coinw/coinw-hft-server/radar_envelope.py
for s in binanceB-engine binanceE-engine; do
  systemctl restart $s; sleep 30
  echo "$s $(systemctl is-active $s)"
  journalctl -u $s --since '-40sec' --no-pager | grep -E 'Traceback|ERROR|雷达包线' | head -5
done

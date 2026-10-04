#!/bin/bash
# 在实盘VPS(187.77.130.144)上以root运行。先把本目录4个.py文件scp到/tmp/radar_envelope_deploy/。
# 步骤：备份 -> 拷入radar_envelope.py -> 打接入补丁 -> 编译 -> 逐个重启并检查日志。任一步失败即停止。
set -eo pipefail
SRC=/tmp/radar_envelope_deploy
cd $SRC && python3 test_radar_envelope.py | tail -1
for a in binanceB binanceE; do
  d=/home/$a/binance-engine
  cp $d/position_supervisor_binance.py $d/position_supervisor_binance.py.bak_20261004_envelope
  cp $SRC/radar_envelope.py $d/radar_envelope.py
  python3 $SRC/hook_envelope_binance.py $d/position_supervisor_binance.py
  python3 -m py_compile $d/position_supervisor_binance.py $d/radar_envelope.py
  chown $a:$a $d/radar_envelope.py 2>/dev/null || true
done
d=/home/coinw/coinw-hft-server
cp $d/position_supervisor_coinw.py $d/position_supervisor_coinw.py.bak_20261004_envelope
cp $d/breath_stop.py $d/breath_stop.py.bak_20261004_envelope
cp $SRC/radar_envelope.py $d/radar_envelope.py
python3 $SRC/hook_envelope_coinw.py $d/breath_stop.py $d/position_supervisor_coinw.py
python3 -m py_compile $d/breath_stop.py $d/position_supervisor_coinw.py $d/radar_envelope.py
for s in binanceB-engine binanceE-engine coinw-engine; do
  systemctl restart $s; sleep 30
  echo "$s $(systemctl is-active $s)"
  journalctl -u $s --since '-40sec' --no-pager | grep -E 'Traceback|ERROR|雷达包线' | head -5
done

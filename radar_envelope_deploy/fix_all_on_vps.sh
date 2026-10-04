#!/bin/bash
# 在实盘VPS(187.77.130.144)以root运行，可重复执行(幂等)。先把本目录文件scp到/tmp/radar_envelope_deploy/。
# 内容：①包线模块换成修正版(ATR取数用字典K线) ②币安B/E：_resolve_live_qty(None)修复 ③币安B/E+CoinW：
# 4H预警/金字塔加仓里atr_series传列表K线的静默bug修复 ④编译→逐个重启→看日志。
set -eo pipefail
SRC=/tmp/radar_envelope_deploy
cd $SRC
py=python3
$py test_radar_envelope.py | tail -1
$py test_atr_dict_fix.py | tail -2 || true
for a in binanceB binanceE; do
  d=/home/$a/binance-engine
  [ -f $d/position_supervisor_binance.py.bak_20261004_atrfix ] || cp $d/position_supervisor_binance.py $d/position_supervisor_binance.py.bak_20261004_atrfix
  cp $SRC/radar_envelope.py $d/radar_envelope.py && chown $a:$a $d/radar_envelope.py
  $py $SRC/hotfix_resolve_live_qty.py $d/position_supervisor_binance.py
  $py $SRC/fix_atr_dict_bars.py $d/position_supervisor_binance.py
  $py -m py_compile $d/position_supervisor_binance.py $d/radar_envelope.py
done
d=/home/coinw/coinw-hft-server
[ -f $d/position_supervisor_coinw.py.bak_20261004_atrfix ] || cp $d/position_supervisor_coinw.py $d/position_supervisor_coinw.py.bak_20261004_atrfix
cp $SRC/radar_envelope.py $d/radar_envelope.py
$py $SRC/fix_atr_dict_bars.py $d/position_supervisor_coinw.py
$py -m py_compile $d/position_supervisor_coinw.py $d/radar_envelope.py
for s in binanceB-engine binanceE-engine coinw-engine; do
  systemctl restart $s; sleep 30
  echo "== $s $(systemctl is-active $s)"
  journalctl -u $s --since '-40sec' --no-pager | grep -E 'Traceback|ERROR|雷达包线' | head -5 || true
done
echo DONE

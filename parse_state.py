import sys, json
d = json.load(sys.stdin)
print('side=' + str(d.get('current_side')), 'qty=' + str(d.get('watched_qty')), 'entry=' + str(d.get('watched_entry')), 'monitoring=' + str(d.get('monitoring')), 'pipeline=' + str(d.get('pipeline', {}).get('phase')), 'last_signal=' + str(d.get('last_tv_signal', {}).get('action')), 'signal_time=' + str(d.get('last_tv_signal', {}).get('ts')))
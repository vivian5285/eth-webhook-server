import json, sys, subprocess
result = subprocess.run(['curl', '-s', '--max-time', '10', 'http://127.0.0.1:5003/api/console/overview'], capture_output=True, text=True)
try:
    d = json.loads(result.stdout)
    print('pipeline:', d.get('pipeline'))
    print('monitoring:', d.get('monitoring'))
    print('pause:', d.get('trading_paused'))
    print('pause_reason:', d.get('trading_pause_reason'))
except:
    print('parse error:', result.stdout[:500])

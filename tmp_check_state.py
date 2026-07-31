import json, urllib.request, http.cookiejar

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Login
login_data = json.dumps({'password': 'binance-console'}).encode()
with opener.open(urllib.request.Request('http://127.0.0.1:5003/api/console/login', data=login_data, headers={'Content-Type': 'application/json'})) as r:
    print('Login:', r.read().decode()[:100])

# Get overview
with opener.open('http://127.0.0.1:5003/api/console/overview') as r:
    d = json.loads(r.read())
    print('\n=== Positions ===')
    for p in d.get('positions', []):
        print(f"  {p.get('symbol')}: {p.get('side')} {p.get('qty')} @ {p.get('entry')}")
    print('\n=== Pipeline ===')
    print(' ', d.get('pipeline_status'))
    print('\n=== Monitoring ===')
    print(' ', d.get('monitoring'))

# Get health too
with urllib.request.urlopen('http://127.0.0.1:5003/health', timeout=15) as r:
    h = json.loads(r.read())
    print('\n=== Health ===')
    print('  status:', h.get('status'))
    print('  pipeline:', h.get('pipeline'))
    print('  trading_paused:', h.get('trading_paused'))
    print('  monitoring:', h.get('monitoring'))

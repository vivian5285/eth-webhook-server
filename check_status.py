#!/usr/bin/env python3
import json, urllib.request, http.cookiejar

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

login_data = json.dumps({'password': 'binance-console'}).encode()
login_req = urllib.request.Request(
    'http://127.0.0.1:5003/api/console/login',
    data=login_data,
    headers={'Content-Type': 'application/json'}
)
with opener.open(login_req) as resp:
    print('Login:', resp.read().decode())

with opener.open('http://127.0.0.1:5003/api/console/overview') as resp:
    d = json.load(resp)
    print('\n=== Positions ===')
    positions = d.get('positions', [])
    if not positions:
        print('  (none)')
    for p in positions:
        print('  {}: {} {} @ {}'.format(p.get('symbol',''), p.get('side',''), p.get('qty',0), p.get('entry',0)))
    print('\n=== Pipeline ===')
    print(d.get('pipeline_status', {}))
    print('\n=== Profiles ===')
    profiles = d.get('profiles', {}).get('profiles', [])
    if not profiles:
        print('  (none)')
    for p in profiles:
        print('  {}: active={}, risk={}, lev={}'.format(p.get('name',''), p.get('active',False), p.get('risk_pct',0), p.get('leverage',0)))

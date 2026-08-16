import sys, json, subprocess

for port, label in [(5003, '5003'), (5007, '5007')]:
    r = subprocess.run(['ssh', 'root@187.77.130.144', 'curl', '-s', f'http://127.0.0.1:{port}/health'], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
        print(f'{label} symbols={d.get("symbols")} version={d.get("version")} status={d.get("status")}')
    except:
        print(f'{label} raw={r.stdout[:200]}')

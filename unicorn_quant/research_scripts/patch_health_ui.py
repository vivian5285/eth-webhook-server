import re, shutil, time
U = "/home/stratroster/unicorn_quant"; ts = time.strftime("%H%M%S")
for f in ("server.py", "static/best.js", "static/best.css", "static/best.html"):
    shutil.copy(f"{U}/{f}", f"{U}/backups/{f.replace('/', '_')}.pre_health_{ts}")
def sub(path, old, new):
    s = open(f"{U}/{path}", encoding="utf-8").read(); assert s.count(old) == 1, (path, old[:60], s.count(old))
    open(f"{U}/{path}", "w", encoding="utf-8").write(s.replace(old, new))
sub("server.py", '''    return jsonify(health=h, actions=rd("recon_actions.json", [])[-40:][::-1], overrides=rd("recon_override.json", {}))''',
    '''    return jsonify(health=h, actions=rd("recon_actions.json", [])[-40:][::-1], overrides=rd("recon_override.json", {}),
                   data_health=rd("data_health.json", {}))     # 2026-10-19 复刻健康检查(engine/health.py)''')
sub("static/best.js", '''    <div class="mut small af-n">规则：持续两次检查才动；''',
    '''    ${(a.data_health && a.data_health.checks) ? `<div class="af-hc"><b>复刻健康检查</b><span class="mut small"> 每小时 · 上次 ${t(a.data_health.ts)} · 有问题会发钉钉</span>
      <ul>${a.data_health.checks.map(c => `<li class="${c.ok ? 'ok' : c.level}"><i>${c.ok ? '✓' : c.level === 'bad' ? '✕' : '!'}</i>${esc(c.text)}</li>`).join('')}</ul></div>` : ''}
    <div class="mut small af-n">规则：持续两次检查才动；''')
open(f"{U}/static/best.css", "a", encoding="utf-8").write("""
/* 2026-10-19 复刻健康检查清单 */
.af-hc{margin:10px 0 4px}.af-hc ul{list-style:none;margin:6px 0 0;padding:0;display:grid;grid-template-columns:1fr 1fr;gap:4px 14px;font-size:12px}
.af-hc li{display:flex;gap:6px;align-items:flex-start;color:var(--t2)}.af-hc li i{font-style:normal;font-weight:700;width:14px;flex:none}
.af-hc li.ok i{color:var(--up)}.af-hc li.warn i,.af-hc li.warn{color:#E8A33D}.af-hc li.bad i,.af-hc li.bad{color:var(--dn)}
@media (max-width:900px){.af-hc ul{grid-template-columns:1fr}}
""")
h = open(f"{U}/static/best.html", encoding="utf-8").read()
h = re.sub(r"best\.js\?v=(\w+)", lambda m: f"best.js?v={m.group(1)}k", h); h = re.sub(r"best\.css\?v=(\w+)", lambda m: f"best.css?v={m.group(1)}k", h)
open(f"{U}/static/best.html", "w", encoding="utf-8").write(h); print("ok")

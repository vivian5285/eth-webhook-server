"""2026-10-18 UI 升级：科技终端风格(宝贝：现在的颜色看不太清楚、晃眼睛)。
做法：覆盖 CSS 变量(强调色从粉色换成青蓝，深色=藏青终端，浅色=柔和灰蓝)，替换 JS/HTML 里写死的粉色。"""
import os, re
os.chdir("/home/stratroster/unicorn_quant/static")
THEME = """
/* ================= 2026-10-18 科技终端主题(覆盖上面的配色) ================= */
:root{
  --bg:#070B14;--surface:#0D1422;--card:#121B2D;--card2:#18233A;--line:rgba(120,170,255,.10);--line2:rgba(120,190,255,.22);
  --t1:#E6EDF7;--t2:#8A9AB5;--t3:#53627C;
  --pink:#22D3EE;--pink-d:#06B6D4;--pink-bg:rgba(34,211,238,.12);--on-accent:#03141B;
  --up:#2EBD85;--dn:#F0616D;--up-bg:rgba(46,189,133,.13);--dn-bg:rgba(240,97,109,.13);
  --shadow:0 0 0 1px var(--line),0 18px 48px rgba(0,0,0,.45);--glow:rgba(34,211,238,.10);
}
[data-theme=light]{
  --bg:#E9EEF5;--surface:#F4F7FB;--card:#FFFFFF;--card2:#EDF2F8;--line:rgba(20,40,80,.10);--line2:rgba(20,40,80,.20);
  --t1:#152036;--t2:#56657F;--t3:#93A0B5;
  --pink:#0E8FA8;--pink-d:#0B7489;--pink-bg:rgba(14,143,168,.10);--on-accent:#FFFFFF;
  --up:#14966A;--dn:#D9475A;--up-bg:rgba(20,150,106,.10);--dn-bg:rgba(217,71,90,.10);
  --shadow:0 0 0 1px var(--line),0 10px 30px rgba(20,40,80,.08);--glow:rgba(14,143,168,.08);
}
body{background:var(--bg);background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);background-size:48px 48px;background-attachment:fixed}
[data-theme=light] body{background-image:linear-gradient(rgba(20,40,80,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(20,40,80,.045) 1px,transparent 1px)}
.orb{opacity:.35!important;filter:blur(140px)!important}
.o2{background:rgba(59,130,246,.10)!important}.o3{background:rgba(34,211,238,.06)!important}
.card{backdrop-filter:blur(8px)}
.btn-pink{color:var(--on-accent)!important;box-shadow:0 6px 20px -10px var(--pink)!important;letter-spacing:.2px}
.btn-pink:hover{box-shadow:0 8px 24px -8px var(--pink)!important}
.nav{border-bottom:1px solid var(--line)}
.pills a.on{background:var(--pink-bg)!important;color:var(--pink)!important}
.big,.px .big,.kpi .v,.res,table.t td,.ytbl td{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}
.up{color:var(--up)}.dn{color:var(--dn)}
"""
for f in ("app.css", "best.css"):
    s = open(f, encoding="utf-8").read()
    if "科技终端主题" not in s:
        s += THEME
        open(f, "w", encoding="utf-8").write(s)
REPL = [("#FC72FF", "#22D3EE"), ("#fc72ff", "#22D3EE"), ("#FF8AF2", "#67E8F9"), ("#E04FE8", "#38BDF8"), ("#E85CEB", "#06B6D4"),
        ("rgba(252,114,255,", "rgba(34,211,238,"), ("%23FC72FF", "%2322D3EE"),
        ("#40B66B", "#2EBD85"), ("#FF5F52", "#F0616D"), ("#2FB563", "#2EBD85"), ("#F0524A", "#F0616D")]
for f in ("app.js", "best.js", "index.html", "best.html"):
    s = open(f, encoding="utf-8").read(); n = 0
    for a, b in REPL:
        n += s.count(a); s = s.replace(a, b)
    open(f, "w", encoding="utf-8").write(s); print(f, "colors replaced", n)
for f, pat in (("index.html", r"(app\.(?:css|js)\?v=)([\w]+)"), ("best.html", r"(best\.(?:css|js)\?v=)([\w]+)")):
    s = open(f, encoding="utf-8").read(); s = re.sub(pat, lambda m: m.group(1) + m.group(2) + "t", s)
    s = s.replace("/static/app.css?v=13\"", "/static/app.css?v=13t\"")
    open(f, "w", encoding="utf-8").write(s)
print("theme ok")

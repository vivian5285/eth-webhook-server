"""2026-10-17 8890 UI：苹果 iOS 毛玻璃风格(宝贝："页面显示不正常，UI改成苹果iOS风格毛玻璃质感动态，会清晰些")
+ 网络容错：接口失败自动重试3次，仍失败提示并可一键重载(之前跨境网络一断开，页面就空白卡在"回测中")。"""
import os, re
os.chdir("/home/stratroster/unicorn_quant/static")
IOS = """
/* ================= 2026-10-17 苹果 iOS 毛玻璃主题(覆盖上面所有配色) ================= */
:root{
  --bg:#F2F2F7;--surface:rgba(255,255,255,.62);--card:rgba(255,255,255,.78);--card2:rgba(118,118,128,.12);
  --line:rgba(60,60,67,.12);--line2:rgba(60,60,67,.22);
  --t1:#1C1C1E;--t2:#6E6E73;--t3:#AEAEB2;
  --pink:#007AFF;--pink-d:#0062CC;--pink-bg:rgba(0,122,255,.12);--on-accent:#FFFFFF;
  --up:#34C759;--dn:#FF3B30;--up-bg:rgba(52,199,89,.14);--dn-bg:rgba(255,59,48,.12);
  --shadow:0 0 0 .5px rgba(60,60,67,.10),0 8px 32px rgba(31,38,135,.10);--glow:rgba(0,122,255,.18);
  --glass-blur:saturate(180%) blur(24px);
}
[data-theme=light]{
  --bg:#F2F2F7;--surface:rgba(255,255,255,.62);--card:rgba(255,255,255,.80);--card2:rgba(118,118,128,.12);
  --line:rgba(60,60,67,.12);--line2:rgba(60,60,67,.22);--t1:#1C1C1E;--t2:#6E6E73;--t3:#AEAEB2;
  --pink:#007AFF;--pink-d:#0062CC;--pink-bg:rgba(0,122,255,.12);--on-accent:#FFFFFF;
  --up:#28A745;--dn:#FF3B30;--up-bg:rgba(52,199,89,.14);--dn-bg:rgba(255,59,48,.12);
  --shadow:0 0 0 .5px rgba(60,60,67,.10),0 8px 32px rgba(31,38,135,.10);--glow:rgba(0,122,255,.18);
}
[data-theme=dark]{
  --bg:#000000;--surface:rgba(28,28,30,.62);--card:rgba(44,44,46,.72);--card2:rgba(118,118,128,.24);
  --line:rgba(84,84,88,.45);--line2:rgba(120,120,128,.55);--t1:#F2F2F7;--t2:#AEAEB2;--t3:#636366;
  --pink:#0A84FF;--pink-d:#409CFF;--pink-bg:rgba(10,132,255,.20);--on-accent:#FFFFFF;
  --up:#30D158;--dn:#FF453A;--up-bg:rgba(48,209,88,.16);--dn-bg:rgba(255,69,58,.16);
  --shadow:0 0 0 .5px rgba(255,255,255,.08),0 10px 40px rgba(0,0,0,.55);--glow:rgba(10,132,255,.25);
}
html,body{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","PingFang SC","Helvetica Neue","Microsoft YaHei",sans-serif!important;-webkit-font-smoothing:antialiased}
body{background:var(--bg)!important;background-image:none!important;letter-spacing:-.01em}
/* 动态彩色光晕(缓慢流动) */
.bg{z-index:-1}
.orb{opacity:1!important;filter:blur(90px)!important;animation:iosFloat 22s ease-in-out infinite!important;mix-blend-mode:normal}
.o1{width:620px!important;height:620px!important;left:-160px!important;top:-200px!important;background:radial-gradient(circle,rgba(0,122,255,.38),transparent 70%)!important}
.o2{width:560px!important;height:560px!important;right:-140px!important;top:80px!important;background:radial-gradient(circle,rgba(175,82,222,.30),transparent 70%)!important;animation-delay:-7s!important}
.o3{width:640px!important;height:640px!important;left:30%!important;bottom:-320px!important;background:radial-gradient(circle,rgba(48,209,88,.26),transparent 70%)!important;animation-delay:-13s!important}
[data-theme=dark] .o1{background:radial-gradient(circle,rgba(10,132,255,.35),transparent 70%)!important}
[data-theme=dark] .o2{background:radial-gradient(circle,rgba(191,90,242,.28),transparent 70%)!important}
[data-theme=dark] .o3{background:radial-gradient(circle,rgba(48,209,88,.20),transparent 70%)!important}
@keyframes iosFloat{0%,100%{transform:translate(0,0) scale(1)}33%{transform:translate(60px,40px) scale(1.08)}66%{transform:translate(-40px,20px) scale(.95)}}
/* 毛玻璃卡片 */
.card,.kpi,.nav,.statusbar,.sheet,.chip,.seg,.pair,.search,.box,.rv-why,.rule,.pend div,.badge,.tape{
  -webkit-backdrop-filter:saturate(180%) blur(24px)!important;backdrop-filter:saturate(180%) blur(24px)!important}
.card{background:var(--surface)!important;border:.5px solid var(--line)!important;border-radius:22px!important;box-shadow:var(--shadow)!important}
.kpi{background:var(--card)!important;border:.5px solid var(--line)!important;border-radius:18px!important;box-shadow:0 2px 12px rgba(0,0,0,.04)}
.nav{background:color-mix(in srgb,var(--bg) 55%,transparent)!important;border-bottom:.5px solid var(--line)!important}
.chip,.seg,.pair,.search{background:var(--card)!important;border:.5px solid var(--line)!important}
.seg button.on{background:var(--surface)!important;color:var(--t1)!important;box-shadow:0 1px 6px rgba(0,0,0,.12)!important;border-radius:12px}
.pills a.on{background:var(--pink-bg)!important;color:var(--pink)!important}
.btn-pink{background:var(--pink)!important;color:#fff!important;border-radius:14px!important;box-shadow:0 6px 18px -8px var(--pink)!important;font-weight:600}
.btn-pink:hover{filter:brightness(1.06)}
.btn-pink.long{background:var(--up)!important}.btn-pink.short{background:var(--dn)!important}
input,select{border-radius:12px!important}
table.t th{background:transparent!important;backdrop-filter:none!important}
.chart-wrap,#chartMain{border-radius:16px}
.tag.c{background:rgba(255,149,0,.15);color:#FF9500}.tag.s{background:rgba(0,122,255,.12);color:#007AFF}
/* 动效：卡片进场 + 按钮按压 */
.card{animation:iosIn .45s cubic-bezier(.2,.8,.2,1) both}
@keyframes iosIn{from{opacity:0;transform:translateY(8px) scale(.995)}to{opacity:1;transform:none}}
button,.chip,.pills a{transition:transform .15s ease,background .2s ease,box-shadow .2s ease}
button:active,.chip:active{transform:scale(.97)}
@media (prefers-reduced-motion:reduce){.orb,.card{animation:none!important}}
/* 网络提示 */
.netbar{position:fixed;left:50%;top:76px;transform:translateX(-50%);z-index:300;padding:10px 16px;border-radius:14px;background:var(--card);
  -webkit-backdrop-filter:saturate(180%) blur(24px);backdrop-filter:saturate(180%) blur(24px);box-shadow:var(--shadow);border:.5px solid var(--line);font-size:13px;display:flex;gap:10px;align-items:center}
.netbar button{border:0;background:var(--pink);color:#fff;border-radius:10px;padding:5px 12px;cursor:pointer}
"""
for f in ("app.css", "best.css"):
    s = open(f, encoding="utf-8").read()
    if "苹果 iOS 毛玻璃主题" not in s:
        s += IOS
        open(f, "w", encoding="utf-8").write(s)
REPL = [("#22D3EE", "#007AFF"), ("#67E8F9", "#5AC8FA"), ("#38BDF8", "#5856D6"), ("#06B6D4", "#0062CC"),
        ("rgba(34,211,238,", "rgba(0,122,255,"), ("%2322D3EE", "%23007AFF"),
        ("#2EBD85", "#34C759"), ("#F0616D", "#FF3B30")]
for f in ("app.js", "best.js", "index.html", "best.html"):
    s = open(f, encoding="utf-8").read(); n = 0
    for a, b in REPL:
        n += s.count(a); s = s.replace(a, b)
    open(f, "w", encoding="utf-8").write(s); print(f, n)
# ---- 网络容错：app.js 的 api() 自动重试 ----
s = open("app.js", encoding="utf-8").read()
if "netRetry" not in s:
    old = "const api = async (path, body) => {\n  const r = await fetch('/api/' + path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });"
    assert old in s, "api() signature changed"
    s = s.replace(old, """// 2026-10-17 网络容错：跨境网络偶尔断开(ERR_EMPTY_RESPONSE)，自动重试3次，仍失败给出"重新加载"提示
const netRetry = async (path, body) => {
  let err;
  for (let k = 0; k < 4; k++) {
    try { return await fetch('/api/' + path, body === undefined ? { cache: 'no-store' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); }
    catch (e) { err = e; await new Promise(r => setTimeout(r, 600 * (k + 1))); }
  }
  if (!document.querySelector('.netbar')) { const d = document.createElement('div'); d.className = 'netbar'; d.innerHTML = '网络连接不稳定，部分数据没加载出来 <button onclick="location.reload()">重新加载</button>'; document.body.append(d); }
  throw err;
};
const api = async (path, body) => {
  const r = await netRetry(path, body);""")
    open("app.js", "w", encoding="utf-8").write(s)
s = open("best.js", encoding="utf-8").read()
if "fetchRetry" not in s:
    s = s.replace("const tsec = ms => Math.floor(ms / 1000);", """const tsec = ms => Math.floor(ms / 1000);
// 2026-10-17 网络容错：所有接口失败自动重试3次
const _fetch0 = window.fetch.bind(window);
window.fetch = async (u, o) => { let e; for (let k = 0; k < 4; k++) { try { return await _fetch0(u, o); } catch (x) { e = x; await new Promise(r => setTimeout(r, 600 * (k + 1))); } } throw e; };
const fetchRetry = true;""", 1)
    open("best.js", "w", encoding="utf-8").write(s)
for f, pat in (("index.html", r"(app\.(?:css|js)\?v=)([\w]+)"), ("best.html", r"((?:best|app)\.(?:css|js)\?v=)([\w]+)")):
    s = open(f, encoding="utf-8").read(); s = re.sub(pat, lambda m: m.group(1) + m.group(2) + "i", s)
    open(f, "w", encoding="utf-8").write(s)
print("ios ok")

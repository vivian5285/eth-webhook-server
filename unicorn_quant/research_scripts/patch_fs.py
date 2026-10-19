import os
os.chdir("/home/stratroster/unicorn_quant/static")
# ---------- 主页图表全屏 ----------
s = open("index.html", encoding="utf-8").read()
if 'id="fsBtn"' not in s:
    old = '<button class="chip" id="sigBtn">策略信号 开</button>'
    assert old in s
    s = s.replace(old, old + '\n      <button class="chip" id="fsBtn" title="全屏看K线(快捷键 F，Esc 退出)">⛶ 全屏</button>')
    s = s.replace("/static/app.js?v=17", "/static/app.js?v=18fs").replace("/static/app.css?v=14", "/static/app.css?v=15fs")
    open("index.html", "w", encoding="utf-8").write(s)
s = open("app.css", encoding="utf-8").read()
if ":fullscreen" not in s:
    s += """
/* 2026-10-18 K线全屏复盘 */
#sec-chart:fullscreen{background:var(--bg);padding:10px 8px;overflow:auto;border-radius:0;min-height:100vh}
#sec-chart:fullscreen .chart-wrap{border-radius:10px}
"""
    open("app.css", "w", encoding="utf-8").write(s)
s = open("app.js", encoding="utf-8").read()
if "fsBtn" not in s:
    s += """

/* ---------- 2026-10-18 K线全屏(复盘用) ---------- */
function fsFit() {
  const on = document.fullscreenElement === $('#sec-chart');
  const m = $('#chartMain');
  if (on) {
    const sub = $('#chartSub').children.length ? $('#chartSub').offsetHeight : 0;
    m.style.height = Math.max(300, window.innerHeight - m.getBoundingClientRect().top - sub - 16) + 'px';
  } else m.style.height = '';
  $('#fsBtn').textContent = on ? '✕ 退出全屏' : '⛶ 全屏';
  setTimeout(() => { sizeCanvas(); drawAll(); }, 60);
}
function toggleFs() {
  if (document.fullscreenElement) return document.exitFullscreen();
  const el = $('#sec-chart');
  (el.requestFullscreen ? el.requestFullscreen() : Promise.reject()).catch(() => toast('浏览器不支持全屏', 'err'));
}
$('#fsBtn').addEventListener('click', toggleFs);
document.addEventListener('fullscreenchange', fsFit);
window.addEventListener('resize', () => { if (document.fullscreenElement) fsFit(); });
document.addEventListener('keydown', e => { if ((e.key === 'f' || e.key === 'F') && !/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) { e.preventDefault(); toggleFs(); } });
"""
    open("app.js", "w", encoding="utf-8").write(s)
# ---------- 最优方案页复盘K线全屏 ----------
s = open("best.html", encoding="utf-8").read()
if 'id="kfsBtn"' not in s:
    old = '<button class="chip sm" id="nextBtn">下一笔 ›</button>'
    assert old in s
    s = s.replace(old, old + '<button class="chip sm" id="kfsBtn" title="全屏(F)">⛶ 全屏</button>')
    s = s.replace("best.js?v=6", "best.js?v=7").replace("best.css?v=3", "best.css?v=4")
    open("best.html", "w", encoding="utf-8").write(s)
s = open("best.css", encoding="utf-8").read()
if ":fullscreen" not in s:
    s += """
/* 2026-10-18 复盘K线全屏：左边K线，右边开平仓原因 */
#review .rv:fullscreen{background:var(--bg);padding:12px;margin:0;grid-template-columns:minmax(0,1fr) 360px}
#review .rv:fullscreen .kc{height:calc(100vh - 70px)}
#review .rv:fullscreen .rv-why{max-height:calc(100vh - 24px)}
"""
    open("best.css", "w", encoding="utf-8").write(s)
s = open("best.js", encoding="utf-8").read()
if "kfsBtn" not in s:
    s += """

/* 2026-10-18 复盘K线全屏(按钮或快捷键F，Esc退出；全屏里仍可用 ↑↓ 切换上一笔/下一笔) */
function kFs() {
  if (document.fullscreenElement) return document.exitFullscreen();
  const el = document.querySelector('#review .rv');
  el.requestFullscreen && el.requestFullscreen();
}
document.getElementById('kfsBtn').addEventListener('click', kFs);
document.addEventListener('fullscreenchange', () => { document.getElementById('kfsBtn').textContent = document.fullscreenElement ? '✕ 退出全屏' : '⛶ 全屏'; });
document.addEventListener('keydown', e => { if ((e.key === 'f' || e.key === 'F') && !/INPUT|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); kFs(); } });
"""
    open("best.js", "w", encoding="utf-8").write(s)
print("fs patched")

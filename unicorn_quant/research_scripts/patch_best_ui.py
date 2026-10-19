import shutil, time, os, re
R = "/home/stratroster/unicorn_quant/static"
bk = f"/home/stratroster/unicorn_quant/backups/pre_bestui_{time.strftime('%Y%m%d_%H%M%S')}"; os.makedirs(bk, exist_ok=True)
for f in ("best.js", "best.css", "best.html"): shutil.copy(f"{R}/{f}", bk)
def sub(path, old, new):
    s = open(f"{R}/{path}", encoding="utf-8").read(); assert s.count(old) == 1, (path, old[:60], s.count(old))
    open(f"{R}/{path}", "w", encoding="utf-8").write(s.replace(old, new))
# 1) 表头放进 thead
sub("best.js", """  $('#tTbl').innerHTML = `<tr><th>开仓日</th>""", """  $('#tTbl').innerHTML = `<thead><tr><th>开仓日</th>""")
sub("best.js", """<th>开仓原因</th><th>平仓原因</th></tr>` +
    pg.map((t, j) => {""", """<th>开仓原因</th><th>平仓原因</th></tr></thead><tbody>` +
    pg.map((t, j) => {""")
sub("best.js", """      <td class="wrap" title="${esc(t.entry_reason)}">${esc(t.entry_reason)}</td><td class="wrap" title="${esc(t.exit_reason)}">${esc(t.exit_reason)}</td></tr>`;
    }).join('');""", """      <td class="wrap" title="${esc(t.entry_reason)}">${esc(t.entry_reason)}</td><td class="wrap" title="${esc(t.exit_reason)}">${esc(t.exit_reason)}</td></tr>`;
    }).join('') + '</tbody>';""")
# 2) 标记文字 + 悬停卡片
sub("best.js", """  const mk = [];
  for (const t of all) {
    const L = t.side === 'LONG', me = focus && t === focus;
    mk.push({ time: tt(t.entry_t), position: L ? 'belowBar' : 'aboveBar', color: L ? '#34C759' : '#FF3B30', shape: L ? 'arrowUp' : 'arrowDown', text: me ? (L ? '开多' : '开空') : '', size: me ? 2 : 1 });
    if (!t.open) mk.push({ time: tt(t.exit_t), position: L ? 'aboveBar' : 'belowBar', color: t.ret > 0 ? '#34C759' : '#FF3B30', shape: 'circle', text: me ? `平 ${pct(t.ret, 1)}` : '', size: me ? 2 : 1 });
  }
  mk.sort((a, b) => a.time - b.time);
  candle.setMarkers(mk);""", """  // 2026-10-18 标记直接写 多/空/平多/平空/止损；8h子腿用小号；放大到约 220 根以内才显示文字(缩远了只显示图标，避免字挤成一团)
  S.ev = new Map();
  const addEv = (time, e) => { const a = S.ev.get(time) || []; a.push(e); S.ev.set(time, a); };
  for (const t of all) {
    addEv(tt(t.entry_t), { k: 'in', t });
    if (!t.open) addEv(tt(t.exit_t), { k: 'out', t });
  }
  S.mkAll = all; S.mkFocus = focus; S.mkTT = tt;
  setMarks();""")
sub("best.js", """function why(t) {""", """function setMarks() {
  const all = S.mkAll || [], focus = S.mkFocus, tt = S.mkTT; if (!tt) return;
  const r = kC.timeScale().getVisibleLogicalRange(), txt = !r || (r.to - r.from) <= 220;
  const mk = [];
  for (const t of all) {
    const L = t.side === 'LONG', me = focus && t === focus, daily = !t.sub || /日线/.test(t.sub) || t.leg === '美股';
    const sz = me ? 2 : daily ? 1.2 : 0.7, show = me || (txt && daily);
    const legc = /超级/.test(t.sub || '') ? '#AF52DE' : /布林/.test(t.sub || '') ? '#FF9500' : null;
    mk.push({ time: tt(t.entry_t), position: L ? 'belowBar' : 'aboveBar', color: legc || (L ? '#34C759' : '#FF3B30'), shape: L ? 'arrowUp' : 'arrowDown', text: show ? (L ? '多' : '空') : '', size: sz });
    if (!t.open) {
      const stop = /硬止损/.test(t.exit_reason || '');
      mk.push({ time: tt(t.exit_t), position: L ? 'aboveBar' : 'belowBar', color: legc || (stop ? '#FF8A4C' : t.ret > 0 ? '#30B0C7' : '#8E8E93'), shape: stop ? 'square' : 'circle',
                text: show ? (stop ? '止损' : (L ? '平多' : '平空')) + (me ? ' ' + pct(t.ret, 1) : '') : '', size: sz });
    }
  }
  mk.sort((a, b) => a.time - b.time);
  candle.setMarkers(mk);
}
function hoverCard(p) {
  let el = document.getElementById('kTip');
  if (!el) { el = document.createElement('div'); el.id = 'kTip'; el.className = 'ktip'; el.hidden = true; $('#kChart').parentElement.style.position = 'relative'; $('#kChart').parentElement.appendChild(el); }
  const evs = p && p.time && S.ev ? S.ev.get(p.time) : null;
  if (!evs || !p.point) { el.hidden = true; return; }
  el.innerHTML = evs.map(({ k, t }) => {
    const L = t.side === 'LONG', stop = k === 'out' && /硬止损/.test(t.exit_reason || '');
    const lab = k === 'in' ? (L ? '开多' : '开空') : stop ? '硬止损' : (L ? '平多' : '平空');
    const cl = k === 'in' ? (L ? 'l' : 's') : stop ? 'st' : (t.ret > 0 ? 'w' : 'x');
    return `<div class="ke"><div class="kh"><span class="kb ${cl}">${lab}</span><span class="tag ${t.leg === '币圈' ? 'c' : 's'}">${esc(t.sub || t.leg)}</span>
      <b>${k === 'in' ? px(t.entry) : px(t.exit)}</b>${k === 'out' ? ` <b class="${cls(t.ret)}">${pct(t.ret, 2)}</b> <span class="mut">${t.days}天</span>` : ''}</div>
      <div class="kr">${esc(k === 'in' ? t.entry_reason : t.exit_reason)}</div></div>`;
  }).join('');
  el.hidden = false;
  const box = $('#kChart').getBoundingClientRect(), off = $('#kChart').offsetTop, W = el.offsetWidth, H = el.offsetHeight;
  let x = p.point.x + 18, y = p.point.y + 12 + off;
  if (x + W > box.width - 70) x = p.point.x - W - 18;
  if (y + H > box.height + off) y = Math.max(off + 4, box.height + off - H - 4);
  el.style.left = Math.max(4, x) + 'px'; el.style.top = y + 'px';
}
function why(t) {""")
sub("best.js", """  eqC.timeScale().subscribeVisibleLogicalRangeChange(r => r && ddC.timeScale().setVisibleLogicalRange(r));""",
"""  eqC.timeScale().subscribeVisibleLogicalRangeChange(r => r && ddC.timeScale().setVisibleLogicalRange(r));
  let _mkTxt = null;
  kC.timeScale().subscribeVisibleLogicalRangeChange(r => { const t = !r || (r.to - r.from) <= 220; if (t !== _mkTxt) { _mkTxt = t; setMarks(); } });
  kC.subscribeCrosshairMove(p => hoverCard(p));""")
open(f"{R}/best.css", "a", encoding="utf-8").write("""
/* 2026-10-18 交易表表头不透明(之前透明，滚动时和下面的行叠在一起) + K线悬停卡片 */
:root{--th-bg:#F7F7FA}[data-theme=light]{--th-bg:#F7F7FA}[data-theme=dark]{--th-bg:#1C1C1E}
.tbl-wrap table.t thead th,.tbl-wrap table.t th{position:sticky;top:0;z-index:3;background:var(--th-bg)!important;box-shadow:0 1px 0 var(--line)}
.ktip{position:absolute;z-index:20;width:340px;max-height:320px;overflow:auto;padding:10px 12px;border-radius:14px;background:var(--card);border:.5px solid var(--line);
  box-shadow:0 12px 36px rgba(0,0,0,.28);-webkit-backdrop-filter:saturate(180%) blur(24px);backdrop-filter:saturate(180%) blur(24px);pointer-events:none;font-size:12px;line-height:1.5}
.ktip[hidden]{display:none}
.ktip .ke+.ke{margin-top:8px;padding-top:8px;border-top:.5px solid var(--line)}
.ktip .kh{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:3px}
.ktip .kr{color:var(--t2)}
.ktip .kb{padding:1px 8px;border-radius:7px;font-weight:700;font-size:11px}
.ktip .kb.l,.ktip .kb.w{background:var(--up-bg);color:var(--up)}.ktip .kb.s,.ktip .kb.x{background:var(--dn-bg);color:var(--dn)}.ktip .kb.st{background:rgba(255,138,76,.18);color:#FF8A4C}
""")
s = open(f"{R}/best.html", encoding="utf-8").read()
s = re.sub(r"best\.js\?v=\w+", "best.js?v=13tip", s); s = re.sub(r"best\.css\?v=\w+", "best.css?v=6tip", s)
open(f"{R}/best.html", "w", encoding="utf-8").write(s)
print("ok", bk)

import re, shutil, time
U = "/home/stratroster/unicorn_quant/static"; ts = time.strftime("%H%M%S")
for f in ("best.js", "best.css", "best.html"):
    shutil.copy(f"{U}/{f}", f"/home/stratroster/unicorn_quant/backups/{f}.pre_legs_{ts}")
def sub(path, old, new):
    s = open(f"{U}/{path}", encoding="utf-8").read(); assert s.count(old) == 1, (path, old[:50], s.count(old))
    open(f"{U}/{path}", "w", encoding="utf-8").write(s.replace(old, new))
# ---- HTML：图例 + 子腿筛选 ----
sub("best.html", '<div id="kChart" class="kc"></div>',
    '''<div class="legbar" id="legBar"><span class="lgseg" id="legSeg"><button class="chip sm on" data-leg="all">三条腿都看</button><button class="chip sm" data-leg="日线">日线EMA</button><button class="chip sm" data-leg="超级">8h超级趋势</button><button class="chip sm" data-leg="布林">8h布林</button></span>
          <span class="lgtxt"><b>▲</b>开多 <b>▼</b>开空 <b>●</b>平仓 <b>■</b>止损 · 颜色：<i class="d1"></i>日线腿(绿多/红空) <i class="d2"></i>8h超级趋势 <i class="d3"></i>8h布林 · 底部色条 = 三条腿合计的<b>真实净仓位</b></span></div>
        <div id="kChart" class="kc"></div>''')
# ---- JS ----
sub("best.js", "  kC.subscribeCrosshairMove(p => hoverCard(p));\n}",
    """  kC.subscribeCrosshairMove(p => hoverCard(p));
  // 2026-10-19 底部"净仓位"色条：三条子腿各占1/3，合计就是账户里这个币的真实仓位(+1=满仓多，-1=满仓空)
  candle.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.17 } });
  posS = kC.addHistogramSeries({ priceScaleId: 'pos', priceLineVisible: false, lastValueVisible: false, base: 0,
    priceFormat: { type: 'custom', formatter: v => (v > 0 ? '多 ' : v < 0 ? '空 ' : '') + Math.round(Math.abs(v) * 100) + '%' } });
  kC.priceScale('pos').applyOptions({ scaleMargins: { top: 0.86, bottom: 0.01 }, visible: false });
  document.querySelectorAll('#legSeg button').forEach(b => b.addEventListener('click', () => {
    S.legF = b.dataset.leg; document.querySelectorAll('#legSeg button').forEach(x => x.classList.toggle('on', x === b)); setMarks();
  }));
}
let posS = null;""")
sub("best.js", "  S.mkAll = all; S.mkFocus = focus; S.mkTT = tt;\n  setMarks();",
    """  S.mkAll = all; S.mkFocus = focus; S.mkTT = tt;
  const crypto = all.length && all[0].leg === '币圈';
  $('#legBar').style.display = crypto ? '' : 'none';
  if (!crypto) S.legF = 'all';
  setMarks();
  if (posS) {   // 每根K线当天各子腿的持仓相加(方向×子腿权重)
    const iv = all.map(t => [t.entry_t, t.open ? Infinity : t.exit_t, (t.side === 'LONG' ? 1 : -1) * (t.w ?? 1)]);
    posS.setData(rows.map(r => {
      let v = 0; for (const [a, b, s] of iv) if (a < r[0] + 864e5 && b > r[0]) v += s;
      v = Math.round(v * 1000) / 1000;
      return { time: tsec(r[0]), value: v, color: v > 0 ? 'rgba(52,199,89,.55)' : v < 0 ? 'rgba(255,59,48,.55)' : 'rgba(0,0,0,0)' };
    }));
  }""")
sub("best.js", "  for (const t of all) {\n    const L = t.side === 'LONG', me = focus && t === focus,",
    "  const lf = S.legF || 'all';\n  for (const t of all) {\n    if (lf !== 'all' && t !== focus && !(t.sub || '').includes(lf)) continue;   // 2026-10-19 子腿筛选\n    const L = t.side === 'LONG', me = focus && t === focus,")
# 悬停卡片里加一行净仓位
sub("best.js", "  }).join('');\n  el.hidden = false;",
    """  }).join('') + (() => { const pd = posS && p.seriesData ? p.seriesData.get(posS) : null; if (!pd) return '';
      const v = pd.value; return `<div class="kr mut">当天三条腿合计净仓位：<b class="${v > 0 ? 'up' : v < 0 ? 'dn' : ''}">${v > 0 ? '多' : v < 0 ? '空' : '空仓'} ${v ? Math.round(Math.abs(v) * 100) + '%' : ''}</b></div>`; })();
  el.hidden = false;""")
# ---- CSS ----
open(f"{U}/best.css", "a", encoding="utf-8").write("""
/* 2026-10-19 复盘K线：子腿筛选 + 图例 */
.legbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;margin:0 0 6px;font-size:12px;color:var(--t2)}
.legbar .lgseg{display:flex;gap:4px}
.legbar .lgseg .chip.on{background:var(--pink-bg);color:var(--pink);border-color:var(--pink)}
.legbar .lgtxt b{color:var(--t1)}
.legbar .lgtxt i{display:inline-block;width:10px;height:10px;border-radius:3px;margin:0 3px 0 6px;vertical-align:-1px}
.legbar .lgtxt i.d1{background:linear-gradient(90deg,#34C759 50%,#FF3B30 50%)}.legbar .lgtxt i.d2{background:#AF52DE}.legbar .lgtxt i.d3{background:#FF9500}
#review .rv.fs-on .kc{height:calc(100vh - 100px)}
""")
h = open(f"{U}/best.html", encoding="utf-8").read()
h = re.sub(r"best\.js\?v=(\w+)", lambda m: f"best.js?v={m.group(1)}g", h); h = re.sub(r"best\.css\?v=(\w+)", lambda m: f"best.css?v={m.group(1)}g", h)
open(f"{U}/best.html", "w", encoding="utf-8").write(h); print("ok")

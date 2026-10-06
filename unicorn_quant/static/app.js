/* 独角兽量化 前端：Lightweight Charts 画图，数据/指标/复盘全部来自 Python 后端 */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const TZ = -new Date().getTimezoneOffset() * 60;
const sec = ms => Math.floor(ms / 1000) + TZ;
const PALETTE = ['#F5B83D', '#FC72FF', '#4C82FB', '#40B66B', '#FF8A4C', '#9B7BFF', '#2FD6C8', '#FF5F52'];
const COMMON = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ZECUSDT', 'XRPUSDT', 'DOGEUSDT', 'SNDKUSDT'];
const INDS = [
  { k: 'ema', n: 'EMA', d: '指数均线', p: '7' }, { k: 'ma', n: 'MA', d: '简单均线', p: '20' },
  { k: 'boll', n: 'BOLL', d: '布林带', p: '20:2' }, { k: 'st', n: 'Supertrend', d: '超级趋势', p: '10:3' },
  { k: 'dc', n: 'Donchian', d: '唐奇安通道', p: '20' }, { k: 'vwap', n: 'VWAP', d: '日内VWAP', p: '' },
  { k: 'rsi', n: 'RSI', d: '相对强弱(副图)', p: '14' }, { k: 'macd', n: 'MACD', d: '指数平滑异同(副图)', p: '12:26:9' },
  { k: 'atr', n: 'ATR', d: '真实波幅(副图)', p: '14' },
];
const S = {
  symbol: localStorage.uq_sym || 'BTCUSDT', interval: localStorage.uq_itv || '4h', inds: JSON.parse(localStorage.uq_inds || '["ema:7","ema:25"]'),
  sub: null, bars: [], indData: [], side: 'LONG', mode: 'paper', tool: '', theme: localStorage.uq_theme || 'dark',
  strategies: [], bt: null, paper: null, symbols: [], tickers: {}, authed: false, rec: 'pp', lastPx: 0, drawings: [],
};
const api = async (path, body) => {
  const r = await fetch('/api/' + path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  let j = {}; try { j = await r.json(); } catch (e) { }
  if (r.status === 401 && path !== 'login') { setAuth(false); }
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
};
const toast = (m, t = '') => { const d = document.createElement('div'); d.className = 'toast ' + t; d.textContent = m; $('#toasts').append(d); setTimeout(() => d.remove(), 4200); };
const fmt = (v, d) => { if (v == null || isNaN(v)) return '--'; d = d ?? (Math.abs(v) >= 1000 ? 2 : Math.abs(v) >= 1 ? 3 : 5); return (+v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }); };
const pct = v => (v >= 0 ? '+' : '') + fmt(v, 2) + '%';
const cls = v => v >= 0 ? 'up' : 'dn';
const tstr = ms => { const d = new Date(ms); return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
const base = s => s.replace(/USDT$/, '');

/* ---------- 主题 ---------- */
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function chartTheme() {
  const dark = S.theme === 'dark';
  return {
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: dark ? '#9B9B9B' : '#7D7D7D', fontFamily: 'Inter, sans-serif' },
    grid: { vertLines: { color: dark ? 'rgba(255,255,255,.035)' : 'rgba(0,0,0,.05)' }, horzLines: { color: dark ? 'rgba(255,255,255,.05)' : 'rgba(0,0,0,.06)' } },
    rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, rightOffset: 6 },
    crosshair: { mode: 0, vertLine: { color: 'rgba(252,114,255,.5)', labelBackgroundColor: '#FC72FF' }, horzLine: { color: 'rgba(252,114,255,.5)', labelBackgroundColor: '#FC72FF' } },
  };
}
function setTheme(t) {
  S.theme = t; document.documentElement.dataset.theme = t; localStorage.uq_theme = t;
  if (main) { main.applyOptions(chartTheme()); sub && sub.applyOptions(chartTheme()); }
  drawAll();
}

/* ---------- 图表 ---------- */
let main, sub, candle, vol, overlays = {}, subSeries = [], busy = false;
function initCharts() {
  main = LightweightCharts.createChart($('#chartMain'), { ...chartTheme(), autoSize: true });
  candle = main.addCandlestickSeries({ upColor: '#40B66B', downColor: '#FF5F52', borderVisible: false, wickUpColor: '#40B66B', wickDownColor: '#FF5F52', priceLineColor: '#FC72FF' });
  vol = main.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false });
  main.priceScale('vol').applyOptions({ scaleMargins: { top: .82, bottom: 0 } });
  main.priceScale('right').applyOptions({ scaleMargins: { top: .08, bottom: .2 } });
  main.timeScale().subscribeVisibleLogicalRangeChange(r => { if (sub && r) sub.timeScale().setVisibleLogicalRange(r); drawAll(); });
  main.subscribeCrosshairMove(p => legendMove(p));
  new ResizeObserver(() => { sizeCanvas(); drawAll(); }).observe($('.chart-wrap'));
}
function mkSub() {
  if (sub) { sub.remove(); sub = null; subSeries = []; }
  $('#chartSub').innerHTML = '';
  if (!S.sub) return;
  sub = LightweightCharts.createChart($('#chartSub'), { ...chartTheme(), autoSize: true, timeScale: { ...chartTheme().timeScale, visible: false } });
  sub.timeScale().subscribeVisibleLogicalRangeChange(r => { if (r) main.timeScale().setVisibleLogicalRange(r); });
}
const toLine = arr => arr.map(([t, v]) => ({ time: sec(t), value: v }));
function colorFor(spec) { const i = S.inds.indexOf(spec); return PALETTE[(i < 0 ? 0 : i) % PALETTE.length]; }

async function loadChart(keepRange) {
  if (busy) return; busy = true; $('#skel').classList.remove('off');
  try {
    const subSpec = S.inds.find(s => /^(rsi|macd|atr)/.test(s));
    S.sub = S.inds.includes(S.sub) ? S.sub : subSpec || null;
    const r = await api(`chart?symbol=${S.symbol}&interval=${S.interval}&limit=900&ind=${encodeURIComponent(S.inds.join(','))}`);
    S.bars = r.bars; S.indData = r.indicators;
    candle.setData(r.bars.map(b => ({ time: sec(b.t), open: b.o, high: b.h, low: b.l, close: b.c })));
    vol.setData(r.bars.map(b => ({ time: sec(b.t), value: b.v, color: b.c >= b.o ? 'rgba(64,182,107,.35)' : 'rgba(255,95,82,.35)' })));
    Object.values(overlays).flat().forEach(s => main.removeSeries(s)); overlays = {};
    mkSub();
    for (const ind of r.indicators) {
      const col = colorFor(ind.id);
      if (ind.pane === 'main') {
        const list = [];
        if (ind.dirs) {
          const up = candleLineSeg(ind, 1), dn = candleLineSeg(ind, -1);
          [[up, '#40B66B'], [dn, '#FF5F52']].forEach(([d, c]) => { const s = main.addLineSeries({ color: c, lineWidth: 2, priceLineVisible: false, lastValueVisible: false }); s.setData(d); list.push(s); });
        } else {
          const keys = Object.keys(ind.lines);
          keys.forEach((k, i) => {
            const s = main.addLineSeries({ color: ind.id.startsWith('boll') ? (k === 'mid' ? '#F5B83D' : '#4C82FB') : col, lineWidth: 1.6, lineStyle: ind.id.startsWith('boll') && k !== 'mid' ? 0 : 0, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
            s.setData(toLine(ind.lines[k])); list.push(s);
          });
        }
        overlays[ind.id] = list;
      } else if (sub && ind.id === S.sub) {
        if (ind.hist) { const h = sub.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false }); h.setData(ind.hist.map(([t, v]) => ({ time: sec(t), value: v, color: v >= 0 ? 'rgba(64,182,107,.6)' : 'rgba(255,95,82,.6)' }))); subSeries.push(h); }
        Object.keys(ind.lines).forEach((k, i) => { const s = sub.addLineSeries({ color: PALETTE[i % 2 ? 1 : 0], lineWidth: 1.6, priceLineVisible: false }); s.setData(toLine(ind.lines[k])); subSeries.push(s); });
        if (ind.id.startsWith('rsi')) { const s0 = subSeries[0]; [30, 70].forEach(v => s0.createPriceLine({ price: v, color: 'rgba(255,255,255,.2)', lineStyle: 2, lineWidth: 1, axisLabelVisible: false })); }
      }
    }
    if (!keepRange) { const n = r.bars.length; main.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 130), to: n + 6 }); }
    if (sub) { const lr = main.timeScale().getVisibleLogicalRange(); lr && sub.timeScale().setVisibleLogicalRange(lr); }
    applyMarkers(); renderChips(); updateTop(); drawAll();
  } catch (e) { toast(e.message, 'err'); }
  finally { busy = false; $('#skel').classList.add('off'); }
}
function candleLineSeg(ind, dir) {
  const line = Object.values(ind.lines)[0], m = new Map(ind.dirs);
  return line.filter(([t]) => m.get(t) === dir).map(([t, v]) => ({ time: sec(t), value: v }));
}
function autoSignals() { if (S.authed) runBacktest(true); }
function setSig(on) { S.sig = on; localStorage.uq_sig = on ? '1' : '0'; $('#sigBtn').textContent = '策略信号 ' + (on ? '开' : '关'); $('#sigBtn').classList.toggle('act', on); drawAll(); }
function applyMarkers() { candle.setMarkers([]); drawAll(); }

/* 进出场标记：canvas绘制 —— 带价格的标签、进出场连线(盈绿亏红)、硬止损线 */
function drawTrades(W) {
  if (!S.sig || !S.bt || S.bt.symbol !== S.symbol || S.bt.interval !== S.interval || !S.bars.length) return;
  const bmap = new Map(S.bars.map(b => [b.t, b])), ts = main.timeScale(), H = $('#chartMain').clientHeight - 30;
  const X = t => ts.timeToCoordinate(sec(t)), Y = p => candle.priceToCoordinate(p), used = {};
  const GREEN = '#2FB563', RED = '#F0524A', PINK = '#E04FE8', ORANGE = '#FF9F1A';
  const pill = (x, y, label, price, color, up, key) => {
    const n = used[key] = (used[key] || 0) + 1, off = (n - 1) * 27;
    cx.save(); cx.font = '600 12px Inter, sans-serif';
    const w1 = cx.measureText(label).width; cx.font = '500 11px Inter, sans-serif';
    const t2 = fmt(price), w = w1 + cx.measureText(t2).width + 24, h = 22;
    const ay = up ? y - off : y + off, px = Math.min(Math.max(x - w / 2, 2), W - w);
    let py = up ? ay - 7 - h : ay + 7; py = Math.min(Math.max(py, 2), H - h);
    cx.fillStyle = color; cx.globalAlpha = .96;
    cx.beginPath(); cx.moveTo(x, ay); cx.lineTo(x - 5, up ? ay - 7 : ay + 7); cx.lineTo(x + 5, up ? ay - 7 : ay + 7); cx.closePath(); cx.fill();
    cx.shadowColor = 'rgba(0,0,0,.4)'; cx.shadowBlur = 8; cx.beginPath(); cx.roundRect(px, py, w, h, 11); cx.fill(); cx.shadowBlur = 0;
    cx.fillStyle = '#fff'; cx.globalAlpha = 1; cx.textBaseline = 'middle'; cx.font = '600 12px Inter, sans-serif'; cx.fillText(label, px + 10, py + h / 2 + .5);
    cx.globalAlpha = .88; cx.font = '500 11px Inter, sans-serif'; cx.fillText(t2, px + 14 + w1, py + h / 2 + .5); cx.restore();
  };
  const last = S.bars[S.bars.length - 1];
  const items = S.bt.trades.concat(S.bt.open ? [{ ...S.bt.open, open: true, pnl: S.bt.open.float_pnl, exit_t: last.t, exit: last.c, ret: null }] : []);
  for (const t of items) {
    const long = t.side === 'LONG', eb = bmap.get(t.entry_t), xb = bmap.get(t.exit_t);
    const x1 = X(t.entry_t), x2 = X(t.exit_t); if (x1 == null || x2 == null || !eb || !xb) continue;
    if (x2 < -80 || x1 > W + 80) continue;
    const y1 = Y(t.entry), y2 = Y(t.exit), win = t.pnl > 0, stop = t.why === '硬止损';
    if (y1 == null || y2 == null) continue;
    cx.save(); cx.lineWidth = 1.8;
    // 硬止损线
    if (t.stop != null) { const ys = Y(t.stop); if (ys != null) { cx.strokeStyle = ORANGE; cx.globalAlpha = stop ? .95 : .45; cx.setLineDash([2, 4]); cx.beginPath(); cx.moveTo(x1, ys); cx.lineTo(t.open ? W : x2, ys); cx.stroke(); if (x2 - x1 > 70 || t.open) { cx.setLineDash([]); cx.globalAlpha = .9; cx.fillStyle = ORANGE; cx.font = '600 10px Inter'; cx.textBaseline = 'bottom'; cx.fillText('硬止损 ' + fmt(t.stop), x1 + 4, ys - 2); } } }
    // 进出场连线
    cx.globalAlpha = 1; cx.setLineDash([5, 4]); cx.strokeStyle = win ? GREEN : RED; cx.beginPath(); cx.moveTo(x1, y1); cx.lineTo(x2, y2); cx.stroke(); cx.setLineDash([]);
    [[x1, y1], [x2, y2]].forEach(([x, y]) => { cx.fillStyle = win ? GREEN : RED; cx.beginPath(); cx.arc(x, y, 3.4, 0, 7); cx.fill(); cx.strokeStyle = '#fff'; cx.lineWidth = 1; cx.stroke(); });
    if (t.ret != null && x2 - x1 > 46) { const mx = (x1 + x2) / 2, my = (y1 + y2) / 2, txt = pct(t.ret); cx.font = '600 11px Inter'; const tw = cx.measureText(txt).width; cx.fillStyle = win ? GREEN : RED; cx.beginPath(); cx.roundRect(mx - tw / 2 - 6, my - 18, tw + 12, 16, 8); cx.fill(); cx.fillStyle = '#fff'; cx.textBaseline = 'middle'; cx.fillText(txt, mx - tw / 2, my - 10); }
    cx.restore();
    // 开仓标签
    pill(x1, long ? Y(eb.l) + 4 : Y(eb.h) - 4, long ? '开多' : '开空', t.entry, long ? GREEN : RED, !long, t.entry_t + (long ? 'b' : 'a'));
    // 平仓标签
    if (!t.open) pill(x2, long ? Y(xb.h) - 4 : Y(xb.l) + 4, stop ? '硬止损' : (long ? '平多' : '平空'), t.exit, stop ? ORANGE : PINK, long, t.exit_t + (long ? 'a' : 'b'));
  }
}
function renderChips() {
  $('#indChips').innerHTML = S.inds.map((s, i) => {
    const name = (S.indData.find(d => d.id === s) || {}).name || s;
    return `<span class="chip ${S.sub === s ? 'act' : ''}" data-i="${i}" data-s="${s}"><i style="background:${PALETTE[i % PALETTE.length]}"></i>${name}<u data-rm="${i}">✕</u></span>`;
  }).join('');
}
function legendMove(p) {
  const lg = $('#legend');
  const d = p && p.time ? p.seriesData.get(candle) : null;
  const b = d || (S.bars.length ? (() => { const x = S.bars[S.bars.length - 1]; return { open: x.o, high: x.h, low: x.l, close: x.c }; })() : null);
  if (!b) return;
  const c = b.close >= b.open ? 'up' : 'dn';
  lg.innerHTML = `<span>开 <b class="${c}">${fmt(b.open)}</b></span><span>高 <b class="${c}">${fmt(b.high)}</b></span><span>低 <b class="${c}">${fmt(b.low)}</b></span><span>收 <b class="${c}">${fmt(b.close)}</b></span><span class="${c}">${pct((b.close / b.open - 1) * 100)}</span>`
    + Object.entries(overlays).map(([id, ss]) => { const v = p && p.time ? ss[0] && p.seriesData.get(ss[0]) : null; return v ? `<span style="color:${colorFor(id)}">${(S.indData.find(x => x.id === id) || {}).name || id} ${fmt(v.value)}</span>` : ''; }).join('');
}
function updateTop() {
  const last = S.bars[S.bars.length - 1]; if (!last) return;
  const el = $('#px'); const prev = S.lastPx; S.lastPx = last.c;
  el.textContent = fmt(last.c); if (prev && prev !== last.c) { el.classList.remove('flash-up', 'flash-dn'); void el.offsetWidth; el.classList.add(last.c > prev ? 'flash-up' : 'flash-dn'); }
  const tk = S.tickers[S.symbol]; const ch = $('#chg');
  if (tk) { ch.textContent = pct(tk.chg) + ' 24h'; ch.className = 'chg ' + cls(tk.chg); }
  legendMove(null); calcTrade(); if (S.tickers[S.symbol]) renderWidgets();
}
async function tick() {
  if (!S.authed || busy || document.hidden) return;
  try {
    const t0 = performance.now(), r = await api(`bar?symbol=${S.symbol}&interval=${S.interval}`); $('#sbLat').textContent = '延迟 ' + Math.round(performance.now() - t0) + ' ms';
    for (const b of r.bars) {
      candle.update({ time: sec(b.t), open: b.o, high: b.h, low: b.l, close: b.c });
      vol.update({ time: sec(b.t), value: b.v, color: b.c >= b.o ? 'rgba(64,182,107,.35)' : 'rgba(255,95,82,.35)' });
      const i = S.bars.findIndex(x => x.t === b.t);
      if (i >= 0) S.bars[i] = b; else { S.bars.push(b); setTimeout(() => loadChart(true), 800); }
    }
    updateTop();
  } catch (e) { }
}

/* ---------- 绘图（水平线/趋势线/矩形，存浏览器） ---------- */
const cv = $('#draw'), cx = cv.getContext('2d'); let pending = null, hover = null;
const dkey = () => 'uq_draw_' + S.symbol;
const loadDraw = () => { try { S.drawings = JSON.parse(localStorage[dkey()] || '[]'); } catch (e) { S.drawings = []; } };
const saveDraw = () => { try { localStorage[dkey()] = JSON.stringify(S.drawings); } catch (e) { } };
function sizeCanvas() { const w = $('#chartMain'), r = devicePixelRatio || 1; cv.width = w.clientWidth * r; cv.height = w.clientHeight * r; cv.style.width = w.clientWidth + 'px'; cv.style.height = w.clientHeight + 'px'; cx.setTransform(r, 0, 0, r, 0, 0); }
const xy = p => ({ x: main.timeScale().timeToCoordinate(p.t), y: candle.priceToCoordinate(p.p) });
function drawAll() {
  if (!main) return; cx.clearRect(0, 0, cv.width, cv.height);
  const W = $('#chartMain').clientWidth - 62;
  const items = [...S.drawings, ...(pending && hover ? [{ ...pending, p2: hover, ghost: 1 }] : [])];
  for (const d of items) {
    cx.save(); cx.strokeStyle = '#FC72FF'; cx.fillStyle = 'rgba(252,114,255,.12)'; cx.lineWidth = 1.6; if (d.ghost) cx.setLineDash([5, 4]);
    const a = xy(d.p1), b = d.p2 ? xy(d.p2) : null;
    if (d.type === 'h') { if (a.y != null) { cx.beginPath(); cx.moveTo(0, a.y); cx.lineTo(W, a.y); cx.stroke(); cx.fillStyle = '#FC72FF'; cx.font = '11px Inter'; cx.fillText(fmt(d.p1.p), 8, a.y - 5); } }
    else if (a.x != null && a.y != null && b && b.x != null && b.y != null) {
      if (d.type === 't') { cx.beginPath(); cx.moveTo(a.x, a.y); cx.lineTo(b.x, b.y); cx.stroke(); }
      else { cx.fillRect(a.x, a.y, b.x - a.x, b.y - a.y); cx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y); }
    }
    cx.restore();
  }
  drawTrades(W);
}
cv.addEventListener('click', e => {
  const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
  const t = main.timeScale().coordinateToTime(x), p = candle.coordinateToPrice(y); if (t == null || p == null) return;
  const pt = { t, p };
  if (S.tool === 'h') { S.drawings.push({ type: 'h', p1: pt }); saveDraw(); setTool(''); }
  else if (!pending) pending = { type: S.tool, p1: pt };
  else { S.drawings.push({ type: pending.type, p1: pending.p1, p2: pt }); pending = null; hover = null; saveDraw(); setTool(''); }
  drawAll();
});
cv.addEventListener('mousemove', e => { if (!pending) return; const r = cv.getBoundingClientRect(); const t = main.timeScale().coordinateToTime(e.clientX - r.left), p = candle.coordinateToPrice(e.clientY - r.top); if (t != null && p != null) { hover = { t, p }; drawAll(); } });
function setTool(t) {
  if (t === 'clear') { S.drawings = []; saveDraw(); pending = null; drawAll(); t = ''; }
  S.tool = t; pending = null; $$('#drawTools button').forEach(b => b.classList.toggle('on', b.dataset.tool === t)); cv.classList.toggle('on', !!t);
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') { setTool(''); $$('.modal').forEach(m => m.hidden = true); } });

/* ---------- 品种/周期 ---------- */
function renderItv() {
  const L = { '1m': '1分', '5m': '5分', '15m': '15分', '30m': '30分', '1h': '1时', '2h': '2时', '4h': '4时', '6h': '6时', '12h': '12时', '1d': '1天', '1w': '1周' };
  $('#itvSeg').innerHTML = Object.keys(L).map(k => `<button data-i="${k}" class="${k === S.interval ? 'on' : ''}">${L[k]}</button>`).join('');
}
function setPairUI() {
  $('#pairName').textContent = S.symbol; $('#pairIco').textContent = base(S.symbol)[0]; $('#outSym').textContent = base(S.symbol); $('#outIco').textContent = base(S.symbol)[0];
  document.title = `${S.symbol} · 独角兽量化`; if (typeof updateGo === 'function') updateGo();
}
async function setSymbol(sym) {
  S.symbol = sym; localStorage.uq_sym = sym; setPairUI(); loadDraw(); S.bt = null; S.cmp = null; S.cmpDone = false; btLoading();
  await loadChart(); renderCurPos(); renderRec(); autoSignals();
}
function openPair() {
  $('#mdPair').hidden = false; $('#pairQ').value = ''; renderPairs(''); setTimeout(() => $('#pairQ').focus(), 50);
}
function renderPairs(q) {
  q = q.trim().toUpperCase();
  $('#popular').innerHTML = COMMON.map(s => `<span class="chip" data-sym="${s}"><i class="ico sm">${base(s)[0]}</i>${base(s)}</span>`).join('');
  let list = S.symbols.filter(s => !q || s.symbol.includes(q)); list.sort((a, b) => ((S.tickers[b.symbol] || {}).vol || 0) - ((S.tickers[a.symbol] || {}).vol || 0));
  $('#pairList').innerHTML = list.slice(0, 80).map(s => { const t = S.tickers[s.symbol] || {}; return `<div class="pitem" data-sym="${s.symbol}"><i class="ico">${s.base[0]}</i><div class="nm"><b>${s.base}${s.tradfi ? ' <span class="tag s" style="background:var(--pink-bg);color:var(--pink)">TradFi</span>' : ''}</b><span>${s.symbol}</span></div><div class="pr">${t.price ? fmt(t.price) : ''}<div class="${cls(t.chg || 0)}" style="font-size:12px">${t.chg != null ? pct(t.chg) : ''}</div></div></div>`; }).join('') || '<div class="empty">没有匹配的交易对</div>';
}

/* ---------- 指标弹窗 ---------- */
function openInd() {
  $('#mdInd').hidden = false;
  $('#indGrid').innerHTML = INDS.map(i => `<div class="ind" data-k="${i.k}"><b>${i.n}</b><span>${i.d}</span><input value="${i.p}" placeholder="参数" ${i.p ? '' : 'disabled'} onclick="event.stopPropagation()"></div>`).join('');
}

/* ---------- 交易卡 ---------- */
const lev = () => +$('#lev').value, amt = () => parseFloat($('#amt').value) || 0;
function calcTrade() {
  const px = S.lastPx, a = amt(), l = lev(), notional = a * l;
  $('#lvLbl').textContent = '杠杆 ' + l + 'x'; $('#amtUsd').textContent = '杠杆后名义 $' + fmt(notional, 2);
  $('#outQty').textContent = px ? fmt(notional / px, px > 1000 ? 5 : px > 10 ? 3 : 2) : '0'; $('#infPx').textContent = fmt(px); $('#infFee').textContent = '$' + fmt(notional * 0.0005, 3);
}
function setSide(s) { S.side = s; $$('#sideSeg button').forEach(b => b.classList.toggle('on', b.dataset.s === s)); updateGo(); }
function setMode(m) { S.mode = m; $$('#modeSeg button').forEach(b => b.classList.toggle('on', b.dataset.m === m)); $('#infMode').textContent = m === 'paper' ? '模拟盘' : '实盘验证单'; $('#modeHint').textContent = m === 'paper' ? '模拟盘用币安实时价撮合，不动真钱。' : '只向交易所提交"验证单"，校验账户/精度/规则，不会成交。真实下单需服务器端显式开启。'; updateGo(); }
function updateGo() { const b = $('#goBtn'); b.className = 'btn-pink big ' + (S.side === 'LONG' ? 'long' : 'short'); b.textContent = S.authed ? (S.mode === 'paper' ? (S.side === 'LONG' ? '开多 ' : '开空 ') + base(S.symbol) : '提交验证单') : '连接后交易'; }
async function submitOrder() {
  if (!S.authed) return openLogin();
  const body = { symbol: S.symbol, side: S.side, usdt: amt(), leverage: lev() };
  try {
    if (S.mode === 'paper') { const r = await api('paper/open', body); toast(`已${S.side === 'LONG' ? '开多' : '开空'} ${S.symbol} @ ${fmt(r.price)}`, 'ok'); await loadPaper(); }
    else { const r = await api('exchange/order', body); toast(r.note || '已完成', 'ok'); }
  } catch (e) { toast(e.message, 'err'); }
}
async function loadPaper() {
  if (!S.authed) return;
  try { S.paper = await api('paper'); $('#cashLbl').textContent = '可用 ' + fmt(S.paper.cash, 2) + ' USDT'; const d = S.paper.equity - S.paper.start; $('#acctLine').innerHTML = '权益 ' + fmt(S.paper.equity, 2) + ` <b class="${cls(d)}">${d >= 0 ? '+' : ''}${fmt(d, 2)}</b>`; renderCurPos(); renderRec(); } catch (e) { }
}
function renderCurPos() {
  const p = (S.paper?.positions || []).find(x => x.symbol === S.symbol);
  $('#curPos').innerHTML = p ? `<div class="pos"><div><span class="tag ${p.side === 'LONG' ? 'l' : 's'}">${p.side === 'LONG' ? '多' : '空'} ${p.lev}x</span> 开仓 ${fmt(p.entry)}<small>浮盈 <b class="${cls(p.pnl)}">${fmt(p.pnl, 2)} (${pct(p.pnl_pct)})</b></small></div><button class="xbtn" data-close-pos="${p.symbol}">平仓</button></div>` : '';
}

/* ---------- 回测工作台 ---------- */
const FAV = ['dual_ema_band', 'ema_cross', 'rsi_revert', 'hma_trend', 'ttm_squeeze', 'keltner_channel', 'turtle_breakout', 'chanlun_pivot', 'supertrend_adx', 'heikin_ashi_trend'];
let btSeq = 0;
const stTitle = id => (S.strategies.find(x => x.id === id) || {}).title || id;
async function loadStrategies() {
  try {
    const r = await api('strategies'); S.strategies = r.strategies.filter(s => s.source !== 'error');
    $('#stSel').innerHTML = S.strategies.map(s => `<option value="${s.id}">${s.title}${s.source === 'repo' ? '' : ' ★'}</option>`).join('');
    $('#stCount').textContent = S.strategies.length + ' 个策略'; S.fav = FAV.filter(id => S.strategies.some(s => s.id === id));
    $('#stSel').value = localStorage.uq_st && S.strategies.some(s => s.id === localStorage.uq_st) ? localStorage.uq_st : 'dual_ema_band';
    showStDesc(); renderPills();
  } catch (e) { }
}
function renderPills() { $('#stPills').innerHTML = (S.fav || []).map(id => `<a data-st="${id}" class="${id === $('#stSel').value ? 'on' : ''}">${stTitle(id)}</a>`).join(''); }
function syncPills() { localStorage.uq_st = $('#stSel').value; renderPills(); }
function showStDesc() { const s = S.strategies.find(x => x.id === $('#stSel').value); $('#stDesc').textContent = s ? (s.desc || '').slice(0, 90) : ''; $('#stParams').value = s && s.params && Object.keys(s.params).length ? JSON.stringify(s.params) : ''; }
function selectStrategy(id) { $('#stSel').value = id; showStDesc(); syncPills(); return runBacktest(); }
function btLoading() { const b = $('#wbBody'); b && b.classList.add('loading'); $('#btCtx').textContent = `${S.symbol} · ${S.interval} · 回测中…`; }
async function runBacktest(silent) {
  if (!S.authed) return silent ? 0 : openLogin();
  const my = ++btSeq, b = $('#btBtn'); b.disabled = true; b.textContent = '回测中…'; btLoading();
  const done = () => { if (my === btSeq) { b.disabled = false; b.textContent = '运行回测'; $('#wbBody').classList.remove('loading'); } };
  let params = null; try { params = $('#stParams').value.trim() ? JSON.parse($('#stParams').value) : null; } catch (e) { done(); return toast('参数不是合法 JSON', 'err'); }
  try {
    const r = await api('backtest', { symbol: S.symbol, interval: S.interval, strategy: $('#stSel').value, limit: +$('#stBars').value, leverage: +$('#stLev').value || 1, params });
    if (my !== btSeq || r.symbol !== S.symbol || r.interval !== S.interval) return;
    S.bt = r; $('#btCtx').textContent = `${r.symbol} · ${r.interval} · ${r.stats.bars}根 · ${r.stats.days}天 · ${stTitle(r.strategy)}`;
    renderBT(); drawAll(); done();
    if (!silent) toast(`回测完成：${r.stats.trades} 笔，收益 ${pct(r.stats.total_return)}`, 'ok');
  } catch (e) { done(); $('#wbBody').innerHTML = `<div class="empty">${e.message}</div>`; if (!silent) toast(e.message, 'err'); }
}
const nn = (v, d = 2, suf = '') => v == null ? '--' : fmt(v, d) + suf;
const barMs = () => S.bt && S.bt.equity.length > 1 ? S.bt.equity[1].t - S.bt.equity[0].t : 14400000;
const dur = n => { const h = n * barMs() / 3600000; return h >= 48 ? (h / 24).toFixed(1) + '天' : h.toFixed(0) + '小时'; };
function renderBT() {
  const r = S.bt, s = r.stats, T = (l, v, c = '', sub = '', hero) => `<div class="tile ${hero ? 'hero' : ''}"><span>${l}</span><b class="${c}">${v}</b>${sub ? `<small>${sub}</small>` : ''}</div>`;
  const tab = S.wbTab || 'trades';
  $('#wbBody').innerHTML = `<div class="kpi">${T('总收益', pct(s.total_return), cls(s.total_return), '期末权益 ' + fmt(s.final_equity, 0), 1)}${T('年化收益', s.annualized == null ? '--' : pct(s.annualized), s.annualized >= 0 ? 'up' : 'dn', '样本 ' + s.days + ' 天')}${T('最大回撤', '-' + fmt(s.max_drawdown, 2) + '%', 'dn')}${T('夏普比率', nn(s.sharpe), s.sharpe >= 0 ? 'up' : 'dn', '按K线收益年化')}${T('索提诺', nn(s.sortino), s.sortino >= 0 ? 'up' : 'dn')}${T('卡玛比率', nn(s.calmar), s.calmar >= 0 ? 'up' : 'dn', '年化/最大回撤')}</div>
  <div class="kpi2">${T('买入持有', pct(s.buy_hold), cls(s.buy_hold))}${T('胜率', fmt(s.win_rate, 1) + '%', '', s.trades + ' 笔')}${T('盈亏因子 PF', nn(s.profit_factor))}${T('单笔期望', fmt(s.expectancy, 2), cls(s.expectancy), 'USDT')}${T('平均盈 / 亏', `<span class="up">${fmt(s.avg_win, 0)}</span> / <span class="dn">${fmt(s.avg_loss, 0)}</span>`, '', '盈亏比 ' + nn(s.payoff))}${T('平均持仓', dur(s.avg_hold_bars), '', s.avg_hold_bars + ' 根K线')}
  ${T('市场暴露', fmt(s.exposure, 1) + '%')}${T('最大连赢 / 连亏', `<span class="up">${s.max_win_streak}</span> / <span class="dn">${s.max_loss_streak}</span>`)}${T('硬止损触发', s.stop_hits + ' 次', s.stop_hits ? 'dn' : '')}${T('最佳 / 最差一笔', `<span class="up">${fmt(s.best, 0)}</span> / <span class="dn">${fmt(s.worst, 0)}</span>`)}${T('手续费合计', fmt(s.fees, 2), '', '0.05%单边')}${T('当前持仓', r.open ? `${r.open.side === 'LONG' ? '多' : '空'} <span class="${cls(r.open.float_pnl)}">${fmt(r.open.float_pnl, 0)}</span>` : '空仓', '', r.open ? '@ ' + fmt(r.open.entry) : '')}</div>
  <canvas id="eqCanvas"></canvas>
  <div class="wb-tabs" id="wbTabs"><a data-wt="trades" class="${tab === 'trades' ? 'on' : ''}">成交明细 (${r.trades.length})</a><a data-wt="ls" class="${tab === 'ls' ? 'on' : ''}">多空分解</a><a data-wt="cmp" class="${tab === 'cmp' ? 'on' : ''}">策略对比</a></div>
  <div class="wb-pane" id="wbPane"></div>`;
  eqDraw(); renderPane();
}
function renderPane() {
  const r = S.bt, tab = S.wbTab || 'trades', el = $('#wbPane'); if (!el || !r) return;
  if (tab === 'trades') {
    const L = x => x === 'LONG' ? '<span class="tag l">多</span>' : '<span class="tag s">空</span>';
    el.innerHTML = r.trades.length ? `<table><tr><th>#</th><th>方向</th><th>开仓时间</th><th>开仓价</th><th>硬止损价</th><th>平仓时间</th><th>平仓价</th><th>盈亏(USDT)</th><th>收益率</th><th>持仓</th><th>离场</th></tr>${r.trades.map((x, i) => [x, i]).reverse().map(([x, i]) => `<tr data-trade="${i}"><td>${i + 1}</td><td>${L(x.side)}</td><td>${tstr(x.entry_t)}</td><td>${fmt(x.entry)}</td><td>${x.stop ? fmt(x.stop) : '--'}</td><td>${tstr(x.exit_t)}</td><td>${fmt(x.exit)}</td><td class="${cls(x.pnl)}">${fmt(x.pnl, 2)}</td><td class="${cls(x.ret)}">${pct(x.ret)}</td><td>${dur(x.bars)}</td><td>${x.why === '硬止损' ? '<span class="tag" style="background:rgba(255,159,26,.16);color:#FF9F1A">硬止损</span>' : x.why}</td></tr>`).join('')}</table>` : '<div class="empty">该区间没有成交</div>';
  } else if (tab === 'ls') {
    const B = (n, o) => `<div class="tile"><span>${n}</span><b class="${cls(o.pnl)}">${fmt(o.pnl, 2)} USDT</b><small>${o.trades} 笔 · 胜率 ${fmt(o.win_rate, 1)}% · 平均 ${fmt(o.avg, 2)}</small></div>`;
    el.innerHTML = `<div class="ls">${B('做多', r.long)}${B('做空', r.short)}</div>`;
  } else {
    const rows = S.cmp;
    el.innerHTML = `<div class="cmp-bar"><button class="btn-pink sm" id="cmpBtn">${rows && S.cmpDone ? '重新对比' : rows ? '对比中…' : '运行对比'}</button><span class="mut small">同品种同周期同根数，按总收益排序；点击一行切换策略</span></div>` + (rows ? `<table><tr><th>策略</th><th>总收益</th><th>年化</th><th>最大回撤</th><th>夏普</th><th>胜率</th><th>PF</th><th>笔数</th></tr>${rows.slice().sort((a, b) => (b.stats?.total_return ?? -1e9) - (a.stats?.total_return ?? -1e9)).map(x => x.error ? `<tr><td>${stTitle(x.id)}</td><td colspan="7" class="mut">${x.error}</td></tr>` : `<tr data-cmp="${x.id}" style="cursor:pointer"><td>${stTitle(x.id)}</td><td class="${cls(x.stats.total_return)}">${pct(x.stats.total_return)}</td><td>${x.stats.annualized == null ? '--' : pct(x.stats.annualized)}</td><td class="dn">-${fmt(x.stats.max_drawdown, 1)}%</td><td>${nn(x.stats.sharpe)}</td><td>${fmt(x.stats.win_rate, 1)}%</td><td>${nn(x.stats.profit_factor)}</td><td>${x.stats.trades}</td></tr>`).join('')}</table>` : '');
  }
}
async function runCompare() {
  const b = $('#cmpBtn'); b.disabled = true; S.cmp = []; S.cmpDone = false; const tag = S.symbol + S.interval;
  for (let i = 0; i < S.fav.length; i++) {
    if (tag !== S.symbol + S.interval) return;
    const id = S.fav[i], bb = $('#cmpBtn'); if (bb) bb.textContent = `对比中 ${i + 1}/${S.fav.length}…`;
    const st = S.strategies.find(x => x.id === id);
    try { const r = await api('backtest', { symbol: S.symbol, interval: S.interval, strategy: id, limit: +$('#stBars').value, leverage: 1, params: st && st.params && Object.keys(st.params).length ? st.params : null }); S.cmp.push({ id, stats: r.stats }); }
    catch (e) { S.cmp.push({ id, error: e.message.slice(0, 60) }); }
    if (S.wbTab === 'cmp') renderPane();
  }
  S.cmpDone = true; renderPane();
}
function focusTrade(i) {
  const t = S.bt.trades[i], a = S.bars.findIndex(b => b.t === t.entry_t), z = S.bars.findIndex(b => b.t === t.exit_t);
  if (a < 0) return toast('该笔早于图上加载的900根K线，请在更大范围查看', '');
  main.timeScale().setVisibleLogicalRange({ from: Math.max(0, a - 30), to: Math.min(S.bars.length + 5, (z < 0 ? a : z) + 30) });
  $('#sec-chart').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function eqDraw() {
  const c = $('#eqCanvas'); if (!c || !S.bt) return; const r = devicePixelRatio || 1, w = c.clientWidth, h = 240; c.width = w * r; c.height = h * r;
  const g = c.getContext('2d'); g.setTransform(r, 0, 0, r, 0, 0); const E = S.bt.equity; if (E.length < 2) return;
  const dark = S.theme === 'dark', grid = dark ? 'rgba(255,255,255,.06)' : 'rgba(0,0,0,.07)', txt = dark ? '#9B9B9B' : '#7D7D7D';
  const L = 52, R = 12, topH = 160, ddT = 180, ddH = 44, n = E.length;
  const all = E.flatMap(x => [x.v, x.bh]), mn = Math.min(...all), mx = Math.max(...all), pad = (mx - mn) * .08 || 1;
  const X = i => L + i / (n - 1) * (w - L - R), Y = v => 12 + (1 - (v - (mn - pad)) / ((mx - mn) + 2 * pad)) * (topH - 12);
  g.font = '11px Inter'; g.fillStyle = txt; g.strokeStyle = grid; g.lineWidth = 1;
  for (let k = 0; k <= 4; k++) { const v = mn - pad + (mx - mn + 2 * pad) * k / 4, y = Y(v); g.beginPath(); g.moveTo(L, y); g.lineTo(w - R, y); g.stroke(); g.fillText(fmt(v, 0), 6, y + 4); }
  const line = (key, color, wd, dash) => { g.beginPath(); E.forEach((x, i) => i ? g.lineTo(X(i), Y(x[key])) : g.moveTo(X(i), Y(x[key]))); g.strokeStyle = color; g.lineWidth = wd; g.setLineDash(dash || []); g.stroke(); g.setLineDash([]); };
  const gr = g.createLinearGradient(0, 0, 0, topH); gr.addColorStop(0, 'rgba(252,114,255,.28)'); gr.addColorStop(1, 'rgba(252,114,255,0)');
  g.beginPath(); E.forEach((x, i) => i ? g.lineTo(X(i), Y(x.v)) : g.moveTo(X(i), Y(x.v))); g.lineTo(X(n - 1), topH); g.lineTo(X(0), topH); g.fillStyle = gr; g.fill();
  line('bh', dark ? '#7A7A7A' : '#B0B0B0', 1.4, [4, 3]); line('v', '#FC72FF', 2);
  const mxd = Math.max(...E.map(x => x.dd), .1); g.fillStyle = txt; g.fillText('回撤', 6, ddT + 12); g.fillText('-' + fmt(mxd, 1) + '%', 6, ddT + ddH);
  g.beginPath(); g.moveTo(X(0), ddT); E.forEach((x, i) => g.lineTo(X(i), ddT + x.dd / mxd * ddH)); g.lineTo(X(n - 1), ddT); g.closePath(); g.fillStyle = 'rgba(255,95,82,.35)'; g.fill();
  g.beginPath(); E.forEach((x, i) => i ? g.lineTo(X(i), ddT + x.dd / mxd * ddH) : g.moveTo(X(i), ddT + x.dd / mxd * ddH)); g.strokeStyle = '#FF5F52'; g.lineWidth = 1.2; g.stroke();
  g.fillStyle = txt; g.fillText(tstr(E[0].t), L, h - 4); const lt = tstr(E[n - 1].t); g.fillText(lt, w - R - g.measureText(lt).width, h - 4);
  g.fillStyle = '#FC72FF'; g.fillRect(w - 250, 6, 10, 3); g.fillStyle = txt; g.fillText('策略权益', w - 236, 11); g.fillStyle = dark ? '#7A7A7A' : '#B0B0B0'; g.fillRect(w - 170, 6, 10, 3); g.fillStyle = txt; g.fillText('买入持有', w - 156, 11);
}

/* ---------- 行情组件：跑马灯/自选/涨跌统计/状态栏 ---------- */
function renderWidgets() {
  const tk = S.tickers, top = Object.entries(tk).filter(([k]) => k.endsWith('USDT')).sort((a, b) => b[1].vol - a[1].vol).slice(0, 16);
  $('#tapeIn').innerHTML = top.map(([k, v]) => `<a data-sym="${k}">${base(k)} <b>${fmt(v.price)}</b><span class="${cls(v.chg)}">${pct(v.chg)}</span></a>`).join('');
  $('#wlBody').innerHTML = COMMON.map(k => { const v = tk[k]; if (!v) return ''; return `<div class="wli ${k === S.symbol ? 'on' : ''}" data-sym="${k}"><i class="ico sm">${base(k)[0]}</i><span class="n">${base(k)}</span><span class="p">${fmt(v.price)}<br><span class="c ${cls(v.chg)}">${pct(v.chg)}</span></span></div>`; }).join('');
  const t = tk[S.symbol]; $('#mstats').innerHTML = t ? `<div><span>24h 最高</span><b>${fmt(t.hi)}</b></div><div><span>24h 最低</span><b>${fmt(t.lo)}</b></div><div><span>24h 成交额</span><b>${t.vol >= 1e9 ? fmt(t.vol / 1e9, 2) + 'B' : fmt(t.vol / 1e6, 1) + 'M'}</b></div>` : '';
}
function clockTick() { const d = new Date(), z = n => String(n).padStart(2, '0'), off = -d.getTimezoneOffset() / 60; $('#sbClock').textContent = `${z(d.getHours())}:${z(d.getMinutes())}:${z(d.getSeconds())}  UTC${off >= 0 ? '+' : ''}${off}`; $('#sbSym').textContent = `${S.symbol} · ${S.interval}`; $('#sbMode').textContent = S.mode === 'paper' ? '模拟盘' : '实盘验证'; }

/* ---------- 记录表 ---------- */
function renderRec(which) {
  if (which) { S.rec = which; $$('#recTabs a').forEach(a => a.classList.toggle('on', a.dataset.r === which)); }
  const el = $('#recBody'); let h = '';
  if (S.rec === 'pp') {
    const p = S.paper?.positions || [];
    h = (S.paper ? `<div class="hint" style="text-align:left;margin:0 0 8px">模拟盘权益 <b>${fmt(S.paper.equity, 2)}</b> USDT（初始 ${fmt(S.paper.start, 0)}）　<a data-reset style="color:var(--pink)">重置账户</a></div>` : '')
      + (p.length ? `<table><tr><th>品种</th><th>方向</th><th>开仓价</th><th>标记价</th><th>保证金</th><th>浮动盈亏</th><th></th></tr>${p.map(x => `<tr><td>${x.symbol}</td><td><span class="tag ${x.side === 'LONG' ? 'l' : 's'}">${x.side === 'LONG' ? '多' : '空'} ${x.lev}x</span></td><td>${fmt(x.entry)}</td><td>${fmt(x.mark)}</td><td>${fmt(x.margin, 2)}</td><td class="${cls(x.pnl)}">${fmt(x.pnl, 2)} (${pct(x.pnl_pct)})</td><td><a data-close-pos="${x.symbol}" class="tag l" style="background:var(--pink-bg);color:var(--pink)">平仓</a></td></tr>`).join('')}</table>` : '<div class="empty">暂无模拟持仓</div>');
  } else {
    const hs = S.paper?.history || [];
    h = hs.length ? `<table><tr><th>品种</th><th>方向</th><th>开仓价</th><th>平仓价</th><th>盈亏</th><th>平仓时间</th></tr>${hs.map(x => `<tr><td>${x.symbol}</td><td><span class="tag ${x.side === 'LONG' ? 'l' : 's'}">${x.side === 'LONG' ? '多' : '空'}</span></td><td>${fmt(x.entry)}</td><td>${fmt(x.exit)}</td><td class="${cls(x.pnl)}">${fmt(x.pnl, 2)}</td><td>${tstr(x.t_close)}</td></tr>`).join('')}</table>` : '<div class="empty">暂无历史</div>';
  }
  el.innerHTML = h;
}

/* ---------- 登录 ---------- */
function setAuth(ok) { S.authed = ok; $('#authBtn').textContent = ok ? '已连接' : '连接'; $('#authBtn').style.opacity = ok ? .75 : 1; updateGo(); }
function openLogin() { $('#mdLogin').hidden = false; $('#loginErr').textContent = ''; setTimeout(() => $('#pw').focus(), 50); }
async function doLogin() {
  try { await api('login', { password: $('#pw').value }); $('#mdLogin').hidden = true; $('#pw').value = ''; await boot(); }
  catch (e) { $('#loginErr').textContent = e.message; }
}

/* ---------- 事件 ---------- */
function bind() {
  document.addEventListener('click', async e => {
    const t = e.target;
    const c = t.closest('[data-close]'); if (c || t.classList.contains('modal')) { (t.closest('.modal') || {}).hidden = true; if (c) c.closest('.modal').hidden = true; return; }
    if (t.closest('#pairBtn') || t.closest('#navSearch') || t.closest('#tokenBtn2')) return openPair();
    const sym = t.closest('[data-sym]'); if (sym) { $('#mdPair').hidden = true; return setSymbol(sym.dataset.sym); }
    const itv = t.closest('#itvSeg button'); if (itv) { S.interval = itv.dataset.i; localStorage.uq_itv = S.interval; renderItv(); S.bt = null; S.cmp = null; S.cmpDone = false; btLoading(); await loadChart(); return autoSignals(); }
    const rm = t.closest('[data-rm]'); if (rm) { S.inds.splice(+rm.dataset.rm, 1); localStorage.uq_inds = JSON.stringify(S.inds); return loadChart(true); }
    const chip = t.closest('#indChips .chip'); if (chip && /^(rsi|macd|atr)/.test(chip.dataset.s)) { S.sub = chip.dataset.s; return loadChart(true); }
    if (t.closest('#addInd')) return openInd();
    const ind = t.closest('.ind'); if (ind) { const inp = ind.querySelector('input'); const spec = ind.dataset.k + (inp && inp.value ? ':' + inp.value : ''); if (!S.inds.includes(spec)) S.inds.push(spec); if (/^(rsi|macd|atr)/.test(spec)) S.sub = spec; localStorage.uq_inds = JSON.stringify(S.inds); $('#mdInd').hidden = true; return loadChart(true); }
    const tool = t.closest('#drawTools button'); if (tool) return setTool(tool.dataset.tool);
    const sd = t.closest('#sideSeg button'); if (sd) return setSide(sd.dataset.s);
    const md = t.closest('#modeSeg button'); if (md) return setMode(md.dataset.m);
    const q = t.closest('.quick a'); if (q) { const cash = S.paper?.cash || 10000; $('#amt').value = Math.floor(cash * +q.dataset.p * 100) / 100; return calcTrade(); }
    if (t.closest('#goBtn')) return submitOrder();
    const cp = t.closest('[data-close-pos]'); if (cp) { try { const r = await api('paper/close', { symbol: cp.dataset.closePos }); toast(`已平仓，盈亏 ${fmt(r.pnl, 2)} USDT`, r.pnl >= 0 ? 'ok' : ''); loadPaper(); } catch (er) { toast(er.message, 'err'); } return; }
    if (t.closest('[data-reset]')) { if (confirm('重置模拟账户为 10,000 USDT？')) { await api('paper/reset', {}); loadPaper(); } return; }
    if (t.closest('#btBtn')) return runBacktest();
    if (t.closest('#sigBtn')) return setSig(!S.sig);
    const sp = t.closest('[data-st]'); if (sp) return selectStrategy(sp.dataset.st);
    const wt = t.closest('#wbTabs a'); if (wt) { S.wbTab = wt.dataset.wt; $$('#wbTabs a').forEach(a => a.classList.toggle('on', a === wt)); return renderPane(); }
    const tr = t.closest('tr[data-trade]'); if (tr) return focusTrade(+tr.dataset.trade);
    const cm = t.closest('tr[data-cmp]'); if (cm) return selectStrategy(cm.dataset.cmp);
    if (t.closest('#cmpBtn')) return runCompare();
    const rt = t.closest('#recTabs a'); if (rt) return renderRec(rt.dataset.r);
    if (t.closest('#authBtn')) return S.authed ? (await api('logout', {}), setAuth(false), toast('已断开')) : openLogin();
    if (t.closest('#loginGo')) return doLogin();
    if (t.closest('#themeBtn')) return setTheme(S.theme === 'dark' ? 'light' : 'dark');
    const nv = t.closest('#mainNav a'); if (nv) { $$('#mainNav a').forEach(a => a.classList.toggle('on', a === nv)); const go = nv.dataset.go; const el = go === 'records' ? $('#sec-records') : go === 'chart' ? $('#sec-chart') : go === 'strategy' ? $('#sec-bt') : $('#sec-trade'); el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  });
  $('#pairQ').addEventListener('input', e => renderPairs(e.target.value));
  $('#pw').addEventListener('keydown', e => e.key === 'Enter' && doLogin());
  $('#amt').addEventListener('input', calcTrade); $('#lev').addEventListener('input', calcTrade);
  $('#stSel').addEventListener('change', () => { showStDesc(); syncPills(); autoSignals(); });
  $('#stBars').addEventListener('change', autoSignals);
  window.addEventListener('resize', () => S.bt && eqDraw());
  document.addEventListener('keydown', e => { if (e.key === '/' && !/INPUT|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); openPair(); } });
}

/* ---------- 启动 ---------- */
async function boot() {
  const me = await (await fetch('/api/me')).json();
  setAuth(me.ok);
  if (!me.ok) { $('#skel').classList.add('off'); if (me.configured) openLogin(); return; }
  try {
    const [sy, tk] = await Promise.all([api('symbols'), api('tickers')]); S.symbols = sy.symbols; S.tickers = tk;
    await Promise.all([loadChart(), loadStrategies(), loadPaper()]); autoSignals();
  } catch (e) { toast(e.message, 'err'); }
  renderWidgets(); $('#sbConn').textContent = '已连接';
  setInterval(() => api('tickers').then(t => { S.tickers = t; updateTop(); renderWidgets(); }).catch(() => { }), 20000);
}
document.documentElement.dataset.theme = S.theme;
initCharts(); S.sig = localStorage.uq_sig !== '0'; setSig(S.sig); sizeCanvas(); loadDraw(); renderItv(); setPairUI(); setSide('LONG'); setMode('paper'); bind(); calcTrade(); renderRec('pp'); clockTick(); setInterval(clockTick, 1000);
setInterval(tick, 4000); setInterval(loadPaper, 8000);
boot();

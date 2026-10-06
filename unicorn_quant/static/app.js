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
  strategies: [], bt: null, paper: null, symbols: [], tickers: {}, authed: false, rec: 'bt', lastPx: 0, drawings: {},
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
function applyMarkers() {
  if (!S.bt || S.bt.symbol !== S.symbol || S.bt.interval !== S.interval) { candle.setMarkers([]); return; }
  const have = new Set(S.bars.map(b => b.t));
  const mk = S.bt.markers.filter(m => have.has(m.t)).map(m => {
    const long = m.side === 'LONG', entry = m.kind === 'entry';
    return entry ? { time: sec(m.t), position: long ? 'belowBar' : 'aboveBar', color: long ? '#40B66B' : '#FF5F52', shape: long ? 'arrowUp' : 'arrowDown', text: long ? '多' : '空' }
      : { time: sec(m.t), position: long ? 'aboveBar' : 'belowBar', color: '#FC72FF', shape: 'circle', text: /止损|止盈/.test(m.text || '') ? m.text : '平' };
  }).sort((a, b) => a.time - b.time);
  candle.setMarkers(mk);
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
  legendMove(null); calcTrade();
}
async function tick() {
  if (!S.authed || busy || document.hidden) return;
  try {
    const r = await api(`bar?symbol=${S.symbol}&interval=${S.interval}`);
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
  document.title = `${S.symbol} · 独角兽量化`;
}
async function setSymbol(sym) {
  S.symbol = sym; localStorage.uq_sym = sym; setPairUI(); loadDraw(); S.bt = null; $('#btStats').innerHTML = ''; eqClear();
  await loadChart(); renderCurPos(); renderRec();
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
  try { S.paper = await api('paper'); $('#cashLbl').textContent = '可用 ' + fmt(S.paper.cash, 2) + ' USDT'; renderCurPos(); renderRec(); } catch (e) { }
}
function renderCurPos() {
  const p = (S.paper?.positions || []).find(x => x.symbol === S.symbol);
  $('#curPos').innerHTML = p ? `<div class="pos"><div><span class="tag ${p.side === 'LONG' ? 'l' : 's'}">${p.side === 'LONG' ? '多' : '空'} ${p.lev}x</span> 开仓 ${fmt(p.entry)}<small>浮盈 <b class="${cls(p.pnl)}">${fmt(p.pnl, 2)} (${pct(p.pnl_pct)})</b></small></div><button class="xbtn" data-close-pos="${p.symbol}">平仓</button></div>` : '';
}

/* ---------- 复盘 ---------- */
async function loadStrategies() {
  try {
    const r = await api('strategies'); S.strategies = r.strategies;
    $('#stSel').innerHTML = r.strategies.filter(s => s.source !== 'error').map(s => `<option value="${s.id}">${s.title}${s.source === 'repo' ? '' : ' ★'}</option>`).join('');
    $('#stCount').textContent = r.strategies.length + ' 个策略'; showStDesc();
  } catch (e) { }
}
function showStDesc() { const s = S.strategies.find(x => x.id === $('#stSel').value); $('#stDesc').textContent = s ? (s.desc || '') : ''; $('#stParams').value = s && s.params && Object.keys(s.params).length ? JSON.stringify(s.params) : ''; }
async function runBacktest() {
  if (!S.authed) return openLogin();
  const b = $('#btBtn'); b.disabled = true; b.textContent = '复盘中…';
  let params = null; try { params = $('#stParams').value.trim() ? JSON.parse($('#stParams').value) : null; } catch (e) { b.disabled = false; b.textContent = '复盘并画到图上'; return toast('参数不是合法 JSON', 'err'); }
  try {
    const r = await api('backtest', { symbol: S.symbol, interval: S.interval, strategy: $('#stSel').value, limit: +$('#stBars').value, leverage: +$('#stLev').value || 1, params });
    S.bt = r; applyMarkers(); renderStats(); renderRec('bt'); eqDraw();
    const n = Math.max(0, S.bars.length - 1); toast(`复盘完成：${r.stats.trades} 笔，收益 ${pct(r.stats.total_return)}`, 'ok');
    if (r.range[0] < S.bars[0]?.t) toast('提示：图上只显示最近900根，更早的信号在成交明细里', '');
  } catch (e) { toast(e.message, 'err'); }
  b.disabled = false; b.textContent = '复盘并画到图上';
}
function renderStats() {
  const s = S.bt.stats; const T = (l, v, c = '') => `<div class="tile"><span>${l}</span><b class="${c}">${v}</b></div>`;
  $('#btStats').innerHTML = `<div class="tiles">${T('总收益', pct(s.total_return), cls(s.total_return))}${T('买入持有', pct(s.buy_hold), cls(s.buy_hold))}${T('最大回撤', '-' + fmt(s.max_drawdown, 2) + '%', 'dn')}${T('交易笔数', s.trades)}${T('胜率', fmt(s.win_rate, 1) + '%')}${T('盈亏比(PF)', s.profit_factor ?? '--')}</div>`
    + (S.bt.open ? `<div class="hint">当前持仓：${S.bt.open.side === 'LONG' ? '多' : '空'} @ ${fmt(S.bt.open.entry)}，浮盈 ${fmt(S.bt.open.float_pnl, 2)}</div>` : '');
}
function eqClear() { const c = $('#eqCanvas'); c.getContext('2d').clearRect(0, 0, c.width, c.height); }
function eqDraw() {
  const c = $('#eqCanvas'), r = devicePixelRatio || 1, w = c.clientWidth, h = 110; c.width = w * r; c.height = h * r; const g = c.getContext('2d'); g.setTransform(r, 0, 0, r, 0, 0);
  const d = S.bt.equity.map(x => x.v); if (d.length < 2) return; const mn = Math.min(...d), mx = Math.max(...d);
  const X = i => 10 + i / (d.length - 1) * (w - 20), Y = v => h - 12 - (v - mn) / ((mx - mn) || 1) * (h - 28);
  const grad = g.createLinearGradient(0, 0, 0, h); grad.addColorStop(0, 'rgba(252,114,255,.35)'); grad.addColorStop(1, 'rgba(252,114,255,0)');
  g.beginPath(); d.forEach((v, i) => i ? g.lineTo(X(i), Y(v)) : g.moveTo(X(i), Y(v))); g.strokeStyle = '#FC72FF'; g.lineWidth = 1.8; g.stroke();
  g.lineTo(X(d.length - 1), h); g.lineTo(X(0), h); g.fillStyle = grad; g.fill();
  g.fillStyle = '#9B9B9B'; g.font = '11px Inter'; g.fillText('权益曲线 ' + fmt(d[0], 0) + ' → ' + fmt(d[d.length - 1], 0), 12, 14);
}

/* ---------- 记录表 ---------- */
function renderRec(which) {
  if (which) { S.rec = which; $$('#recTabs a').forEach(a => a.classList.toggle('on', a.dataset.r === which)); }
  const el = $('#recBody'); let h = '';
  if (S.rec === 'bt') {
    const t = S.bt?.trades || [];
    h = t.length ? `<table><tr><th>开仓时间</th><th>方向</th><th>开仓价</th><th>平仓价</th><th>盈亏(USDT)</th><th>收益率</th><th>持仓K线</th><th>离场原因</th></tr>${t.slice().reverse().map(x => `<tr><td>${tstr(x.entry_t)}</td><td><span class="tag ${x.side === 'LONG' ? 'l' : 's'}">${x.side === 'LONG' ? '多' : '空'}</span></td><td>${fmt(x.entry)}</td><td>${fmt(x.exit)}</td><td class="${cls(x.pnl)}">${fmt(x.pnl, 2)}</td><td class="${cls(x.ret)}">${pct(x.ret)}</td><td>${x.bars}</td><td>${x.why}</td></tr>`).join('')}</table>` : '<div class="empty">还没有复盘 —— 在右侧「策略复盘」选择策略并运行</div>';
  } else if (S.rec === 'pp') {
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
    const itv = t.closest('#itvSeg button'); if (itv) { S.interval = itv.dataset.i; localStorage.uq_itv = S.interval; renderItv(); S.bt = null; return loadChart(); }
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
    const st = t.closest('#sideTabs a'); if (st) { $$('#sideTabs a').forEach(a => a.classList.toggle('on', a === st)); $('#tab-trade').hidden = st.dataset.tab !== 'trade'; $('#tab-strategy').hidden = st.dataset.tab !== 'strategy'; return; }
    const rt = t.closest('#recTabs a'); if (rt) return renderRec(rt.dataset.r);
    if (t.closest('#authBtn')) return S.authed ? (await api('logout', {}), setAuth(false), toast('已断开')) : openLogin();
    if (t.closest('#loginGo')) return doLogin();
    if (t.closest('#themeBtn')) return setTheme(S.theme === 'dark' ? 'light' : 'dark');
    const nv = t.closest('#mainNav a'); if (nv) { $$('#mainNav a').forEach(a => a.classList.toggle('on', a === nv)); const go = nv.dataset.go; if (go === 'strategy') { $('#tabStrategy').click(); } if (go === 'trade') { $('[data-tab=trade]').click(); } const el = go === 'records' ? $('#sec-records') : go === 'chart' ? $('#sec-chart') : $('#sec-trade'); el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  });
  $('#pairQ').addEventListener('input', e => renderPairs(e.target.value));
  $('#pw').addEventListener('keydown', e => e.key === 'Enter' && doLogin());
  $('#amt').addEventListener('input', calcTrade); $('#lev').addEventListener('input', calcTrade);
  $('#stSel').addEventListener('change', showStDesc);
  document.addEventListener('keydown', e => { if (e.key === '/' && !/INPUT|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); openPair(); } });
}

/* ---------- 启动 ---------- */
async function boot() {
  const me = await (await fetch('/api/me')).json();
  setAuth(me.ok);
  if (!me.ok) { $('#skel').classList.add('off'); if (me.configured) openLogin(); return; }
  try {
    const [sy, tk] = await Promise.all([api('symbols'), api('tickers')]); S.symbols = sy.symbols; S.tickers = tk;
    await Promise.all([loadChart(), loadStrategies(), loadPaper()]);
  } catch (e) { toast(e.message, 'err'); }
  setInterval(() => api('tickers').then(t => { S.tickers = t; updateTop(); }).catch(() => { }), 20000);
}
document.documentElement.dataset.theme = S.theme;
initCharts(); sizeCanvas(); loadDraw(); renderItv(); setPairUI(); setSide('LONG'); setMode('paper'); bind(); calcTrade(); renderRec('bt');
setInterval(tick, 4000); setInterval(loadPaper, 8000);
boot();

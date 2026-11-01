/* SNDK单票方案 单独复盘(2026-11-01)：1h K线 + EMA7/25 + 每一笔开平仓/止损 + 回测净值 + 交易表。数据 = /api/sndk_solo(engine/sndk_solo_view.py，和实盘执行器同一套重放) */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const fmt = (v, d = 2) => v == null || isNaN(v) ? '--' : (+v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 1) => v == null ? '--' : (v >= 0 ? '+' : '') + fmt(v, d) + '%';
const cls = v => v == null ? '' : v >= 0 ? 'up' : 'dn';
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const BJ = 8 * 3600;                                       // 图上统一显示北京时间
const tt = ms => Math.floor(ms / 1000) + BJ;
const bj = ms => { const d = new Date(ms + BJ * 1000); return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`; };
const bjd = ms => { const d = new Date(ms + BJ * 1000); return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`; };
const H = 3600000;
const S = { d: null, rng: +(localStorage.uq_sk_rng || 30), stop: localStorage.uq_sk_stop !== '0', wk: localStorage.uq_sk_wk === '1', res: '', theme: localStorage.uq_theme || 'dark', byBar: {} };
let kc, cs, l7, l25, posS, eqc, eqS = {}, stL = [];

function opts() {
  const dark = S.theme === 'dark';
  return {
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: dark ? '#AEAEB2' : '#6E6E73', fontFamily: '-apple-system, Inter, sans-serif' },
    grid: { vertLines: { color: dark ? 'rgba(255,255,255,.03)' : 'rgba(0,0,0,.04)' }, horzLines: { color: dark ? 'rgba(255,255,255,.05)' : 'rgba(0,0,0,.06)' } },
    rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false, rightOffset: 6, timeVisible: true, secondsVisible: false }, crosshair: { mode: 0 }, autoSize: true,
  };
}
function setTheme(t) { S.theme = t; document.documentElement.dataset.theme = t; localStorage.uq_theme = t; if (kc) { kc.applyOptions(opts()); eqc.applyOptions(opts()); drawK(); } }

async function load() {
  document.documentElement.dataset.theme = S.theme;
  const r = await fetch('/api/sndk_solo'); const j = await r.json();
  if (j.error) { $('#title').textContent = 'SNDK单票方案'; $('#sub').textContent = j.error; return; }
  S.d = j.data || j;
  for (const f of [head, charts, drawK, drawEq, trades, bind]) { try { f(); } catch (e) { console.warn(f.name, e); } }
  let ranged = false;
  new ResizeObserver(() => { if (!ranged && $('#kc').clientWidth > 0) { ranged = true; try { setRange(); } catch (e) { } } }).observe($('#kc'));
}

function head() {
  const d = S.d, m = d.summary, st = d.state, last = d.rows[d.rows.length - 1];
  $('#title').innerHTML = `${esc(d.name)} <span class="px">${fmt(st.close)}</span>`;
  $('#sub').textContent = `SNDKUSDT 永续 · C账户 · 回测从 ${bjd(d.list_start)} 上线起 · 实盘 ${bjd(d.armed_at)} ${bj(d.armed_at)} 开始 · 数据到 ${bjd(last[0])} ${bj(last[0] + H)} 收盘`;
  $('#rules').textContent = d.rules;
  const b = [];
  if (st.pos) b.push(`<span class="bd l">回测持多中 · 止损 ${fmt(st.stop)}</span>`); else b.push('<span class="bd">回测空仓</span>');
  b.push(`<span class="bd ${st.ema7 > st.ema25 ? 'tu' : 'td'}">EMA7 ${fmt(st.ema7)} ${st.ema7 > st.ema25 ? '>' : '<'} EMA25 ${fmt(st.ema25)}</span>`);
  b.push(`<span class="bd half">ADX ${fmt(st.adx, 1)} → 现在金叉开 ${st.size_now} 倍</span>`);
  if (st.lock) b.push('<span class="bd pend">刚止损：等 EMA7 先跌回 EMA25 下方才再进</span>');
  if (st.pend != null) b.push(`<span class="bd pend">待执行：${esc(st.pend_why)}</span>`);
  $('#state').innerHTML = b.join('');
  const K = [
    ['回测收益', `<span class="${cls(m.bt.ret)}">${pct(m.bt.ret)}</span>`, `最大回撤 ${fmt(m.bt.max_dd, 1)}%（不含手续费，同执行器）`],
    ['交易', `${m.n}笔`, `胜率 ${m.win}% · 止损出场 ${m.n_stop} 笔`],
    ['每笔(按仓位)', `<span class="${cls(m.avg_ret)}">${pct(m.avg_ret, 2)}</span>`, `最好 ${pct(m.best, 1)} · 最差 ${pct(m.worst, 1)}`],
    ['买入持有', `<span class="${cls(m.bh.ret)}">${pct(m.bh.ret)}</span>`, `最大回撤 ${fmt(m.bh.max_dd, 1)}%`],
    ['实盘开始后(回测)', `<span class="${cls(m.since_armed.ret)}">${pct(m.since_armed.ret)}</span>`, `同期买入持有 ${pct(m.bh_since_armed.ret)}`],
  ];
  $('#kpis').innerHTML = K.map(([k, v, s]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="s mut small">${s}</div></div>`).join('');
}

function charts() {
  kc = LightweightCharts.createChart($('#kc'), opts());
  posS = kc.addHistogramSeries({ priceScaleId: 'pos', priceLineVisible: false, lastValueVisible: false, base: 0 });
  kc.priceScale('pos').applyOptions({ scaleMargins: { top: 0.965, bottom: 0 }, visible: false });
  cs = kc.addCandlestickSeries({ upColor: '#34C759', downColor: '#FF3B30', borderVisible: false, wickUpColor: '#34C759', wickDownColor: '#FF3B30', priceLineVisible: true });
  l7 = kc.addLineSeries({ color: '#F5B83D', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  l25 = kc.addLineSeries({ color: '#4C82FB', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  kc.subscribeCrosshairMove(p => crossInfo(p));
  eqc = LightweightCharts.createChart($('#eqc'), opts());
}

function drawK() {
  const d = S.d, R = d.rows;
  cs.setData(R.map(r => ({ time: tt(r[0]), open: r[1], high: r[2], low: r[3], close: r[4] })));
  l7.setData(R.map(r => ({ time: tt(r[0]), value: r[5] })));
  l25.setData(R.map(r => ({ time: tt(r[0]), value: r[6] })));
  S.byBar = {};
  const held = {}, stp = {};
  for (const t of d.trades) {
    const z = t.out_t || (R[R.length - 1][0] + H);
    for (let x = t.t; x < z; x += H) { held[x] = 1; stp[x] = t.stop; }
    (S.byBar[t.t] = S.byBar[t.t] || []).push({ k: 'in', t });
    if (t.out_t) (S.byBar[t.out_t] = S.byBar[t.out_t] || []).push({ k: 'out', t });
  }
  posS.setData(R.map(r => held[r[0]] ? { time: tt(r[0]), value: 1, color: 'rgba(52,199,89,.7)' } : { time: tt(r[0]) }));
  for (const x of stL) kc.removeSeries(x); stL = [];          // 每笔一条止损线(一条线会把不同笔连起来)
  if (S.stop) for (const t of d.trades) {
    const z = t.out_t || (R[R.length - 1][0] + H), pts = [];
    for (let x = t.t; x < z; x += H) pts.push({ time: tt(x), value: t.stop });
    if (t.out_t) pts.push({ time: tt(t.out_t), value: t.stop });
    const ls = kc.addLineSeries({ color: '#FF3B30', lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, autoscaleInfoProvider: () => null });
    ls.setData(pts); stL.push(ls);
  }
  const mk = [];
  for (const t of d.trades) {
    mk.push({ time: tt(t.t), position: 'belowBar', shape: 'arrowUp', color: '#34C759', text: `多${t.m}x`, size: 1.1 });
    if (t.out_t) mk.push(t.kind === 'stop'
      ? { time: tt(t.out_t), position: 'aboveBar', shape: 'square', color: '#FF3B30', text: `止损 ${pct(t.pnl, 1)}`, size: 0.9 }
      : { time: tt(t.out_t), position: 'aboveBar', shape: 'circle', color: t.pnl > 0 ? '#30B0C7' : '#8E8E93', text: pct(t.pnl, 1), size: 0.9 });
  }
  if (S.wk) for (const s of d.skipped) mk.push({ time: tt(s.t), position: s.want ? 'belowBar' : 'aboveBar', shape: 'circle', color: 'rgba(142,142,147,.55)', text: '周末', size: 0.5 });
  const a = R.find(r => r[0] >= d.armed_at);
  if (a) mk.push({ time: tt(a[0]), position: 'aboveBar', shape: 'arrowDown', color: '#AF52DE', text: '实盘开始', size: 1 });
  mk.sort((x, y) => x.time - y.time);
  cs.setMarkers(mk);
  $('#klgd').innerHTML = `<span><i class="ln e7"></i>EMA7</span><span><i class="ln e25"></i>EMA25</span><span><i class="tri u"></i>开多(×仓位倍数)</span>`
    + `<span><i class="dot c"></i>EMA死叉平仓(赚)</span><span><i class="dot g"></i>平仓(亏)</span><span><i class="dot" style="background:#FF3B30;border-radius:2px"></i>4×ATR止损出场</span>`
    + `<span><i class="ln stp"></i>止损线(虚线)</span><span><i class="dot" style="background:rgba(52,199,89,.8);border-radius:2px"></i>底部绿条=持仓中</span>` + (S.wk ? '<span><i class="dot wk"></i>纽约周末被跳过的交叉</span>' : '');
  $('#kSub').textContent = ` ${d.trades.length} 笔 · 北京时间`;
  $('#stopBtn').classList.toggle('on', S.stop); $('#wkBtn').classList.toggle('on', S.wk);
}
function setRange() {
  const R = S.d.rows; if (!R.length || !$('#kc').clientWidth) return;
  $$('#rngSeg button').forEach(b => b.classList.toggle('on', +b.dataset.v === S.rng));
  if (!S.rng) { kc.timeScale().fitContent(); return; }
  const z = R[R.length - 1][0], i0 = R.findIndex(r => r[0] >= z - S.rng * 86400000);
  try { kc.timeScale().setVisibleLogicalRange({ from: Math.max(0, i0), to: R.length + 4 }); } catch (e) { kc.timeScale().fitContent(); }
}
function crossInfo(p) {
  if (!p || !p.time) { $('#kinfo').innerHTML = ''; return; }
  const b = p.seriesData.get(cs), e7 = p.seriesData.get(l7), e25 = p.seriesData.get(l25);
  const ms = (p.time - BJ) * 1000, row = S.d.rows.find(r => r[0] === ms);
  if (b) $('#kinfo').innerHTML = `<b>${bjd(ms)} ${bj(ms)}</b> 开 ${fmt(b.open)} 高 ${fmt(b.high)} 低 ${fmt(b.low)} 收 <b class="${b.close >= b.open ? 'up' : 'dn'}">${fmt(b.close)}</b> · <span style="color:#F5B83D">EMA7 ${fmt(e7 && e7.value)}</span> <span style="color:#4C82FB">EMA25 ${fmt(e25 && e25.value)}</span> · ADX ${row ? fmt(row[7], 1) : '--'}`;
  const ev = S.byBar[ms];
  if (ev) $('#evt').innerHTML = ev.map(evHtml).join('');
}
function evHtml({ k, t }) {
  return k === 'in'
    ? `<div class="ev"><span class="bd l">${bjd(t.t)} ${bj(t.t)} 开多 @ ${fmt(t.px)} · ${t.m} 倍 · 止损 ${fmt(t.stop)}</span> ${esc(t.why_in)}</div>`
    : `<div class="ev"><span class="bd ${t.pnl > 0 ? 'tu' : 'td'}">${bjd(t.out_t)} ${bj(t.out_t)} 平多 @ ${fmt(t.out)} · 价格 ${pct(t.ret, 2)} · 账户 ${pct(t.pnl, 2)} · ${t.bars} 小时</span> ${esc(t.why_out)}</div>`;
}

function drawEq() {
  const d = S.d;
  for (const s of Object.values(eqS)) eqc.removeSeries(s); eqS = {};
  const add = (k, data, color, w) => { eqS[k] = eqc.addLineSeries({ color, lineWidth: w, priceLineVisible: false, lastValueVisible: true }); eqS[k].setData(data.map(([x, v]) => ({ time: tt(x), value: v }))); };
  add('bh', d.bh_curve, '#8E8E93', 1.4); add('bt', d.curve, '#AF52DE', 2.4);
  eqc.timeScale().fitContent();
  const m = d.summary;
  $('#eqLgd').innerHTML = `<span><i class="ln" style="background:#AF52DE"></i>SNDK单票方案 回测 ${pct(m.bt.ret)} · 最大回撤 ${fmt(m.bt.max_dd, 1)}%</span><span><i class="ln" style="background:#8E8E93"></i>买入持有 ${pct(m.bh.ret)} · 最大回撤 ${fmt(m.bh.max_dd, 1)}%</span>`;
  $('#eqSub').textContent = `${bjd(d.list_start)} 上线起，起点 = 1；回测按每笔仓位倍数复利、不含手续费(和执行器口径一致)`;
}

function trades() {
  const d = S.d, all = d.trades.slice().sort((a, b) => b.t - a.t);
  const L = all.filter(t => !S.res || (S.res === 'w' ? t.out_t && t.pnl > 0 : S.res === 'l' ? t.out_t && t.pnl <= 0 : S.res === 's' ? t.kind === 'stop' : t.t >= d.armed_at));
  const c = L.filter(t => t.out_t), w = c.filter(t => t.pnl > 0).length;
  $('#tSub').textContent = ` ${L.length} 笔 · 胜率 ${c.length ? Math.round(100 * w / c.length) : '--'}% · 点任意一行在 K 线上定位`;
  $('#tTbl').innerHTML = '<thead><tr><th>#</th><th>开仓(北京)</th><th>平仓(北京)</th><th>仓位</th><th>ADX</th><th>止损</th><th>价格涨跌</th><th>对账户</th><th>小时</th><th>开仓原因</th><th>平仓原因</th></tr></thead><tbody>'
    + L.map((t, i) => `<tr data-i="${all.indexOf(t)}" class="${t.out_t ? '' : 'open'}"><td class="mut">${L.length - i}</td>
      <td class="small">${bjd(t.t)} ${bj(t.t)}<div><b>${fmt(t.px)}</b></div></td>
      <td class="small">${t.out_t ? bjd(t.out_t) + ' ' + bj(t.out_t) : '<span class="bd l">持仓中</span>'}<div>${t.out_t ? '' : '现价 '}<b>${fmt(t.out)}</b>${t.kind === 'stop' ? ' <span class="x">止损</span>' : ''}</div></td>
      <td class="num">${t.m}x</td><td class="num">${fmt(t.adx, 1)}</td><td class="num">${fmt(t.stop)}</td>
      <td class="num ${cls(t.ret)}">${pct(t.ret, 2)}</td><td class="num ${cls(t.pnl)}"><b>${pct(t.pnl, 2)}</b></td><td class="num">${t.bars}</td>
      <td class="why">${esc(t.why_in)}</td><td class="why">${esc(t.why_out)}</td></tr>`).join('') + '</tbody>';
  S.all = all;
}
function focusTrade(t) {
  const R = S.d.rows, a = t.t - 24 * H, z = (t.out_t || R[R.length - 1][0]) + 24 * H;
  const i0 = R.findIndex(r => r[0] >= a), i1 = R.findIndex(r => r[0] >= z);
  try { kc.timeScale().setVisibleLogicalRange({ from: Math.max(0, i0), to: i1 < 0 ? R.length + 4 : i1 }); } catch (e) { }
  $('#evt').innerHTML = [{ k: 'in', t }].concat(t.out_t ? [{ k: 'out', t }] : []).map(evHtml).join('');
  $('#kCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function toggleFs(force) {
  const el = $('#kCard'), on = force === undefined ? !el.classList.contains('fs-on') : force;
  el.classList.toggle('fs-on', on); document.body.style.overflow = on ? 'hidden' : '';
  $('#fsBtn').textContent = on ? '✕ 退出全屏' : '⛶ 全屏';
}
function bind() {
  $('#themeBtn').onclick = () => setTheme(S.theme === 'dark' ? 'light' : 'dark');
  $('#rngSeg').onclick = e => { const b = e.target.closest('button'); if (!b) return; S.rng = +b.dataset.v; localStorage.uq_sk_rng = S.rng; setRange(); };
  $('#stopBtn').onclick = () => { S.stop = !S.stop; localStorage.uq_sk_stop = S.stop ? '1' : '0'; drawK(); setRange(); };
  $('#wkBtn').onclick = () => { S.wk = !S.wk; localStorage.uq_sk_wk = S.wk ? '1' : '0'; drawK(); setRange(); };
  $('#fRes').onchange = e => { S.res = e.target.value; trades(); };
  $('#tTbl').addEventListener('click', e => { const tr = e.target.closest('tr[data-i]'); if (!tr) return; $$('#tTbl tr.sel').forEach(x => x.classList.remove('sel')); tr.classList.add('sel'); focusTrade(S.all[+tr.dataset.i]); });
  $('#fsBtn').onclick = () => toggleFs();
  document.addEventListener('keydown', e => { if (/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return; if (e.key === 'f' || e.key === 'F') toggleFs(); });
}
load();

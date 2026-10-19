/* 最优方案页：方案F 复盘(权益曲线 / 当前状态 / 每笔开平仓原因 / 已淘汰策略) */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const S = { d: null, ser: ['F', 'F0', 'E2', 'crypto', 'stock'], on: new Set(['F', 'E2', 'crypto', 'stock']), log: true, leg: '', sym: '', res: '', year: '', page: 0, list: [], sel: -1, bars: {}, theme: localStorage.uq_theme || 'dark' };
const SER = { F: { n: '主方案 2倍+波动率控制', c: '#007AFF', w: 3 }, F0: { n: '2倍(不控制)', c: '#9B9B9B', w: 1 }, E2: { n: '1.5倍+波动率控制', c: '#A78BFA', w: 2 }, A: { n: '各半 1倍', c: '#9B9B9B', w: 1 }, crypto: { n: '币圈腿', c: '#F5B83D', w: 2 }, stock: { n: '美股腿', c: '#4C82FB', w: 2 } };
const PER = 50;
const fmt = (v, d = 2) => v == null || isNaN(v) ? '--' : (+v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 1) => v == null ? '--' : (v >= 0 ? '+' : '') + fmt(v, d) + '%';
const cls = v => v == null ? '' : v >= 0 ? 'up' : 'dn';
const day = ms => { const d = new Date(ms); return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`; };
const px = v => v == null ? '--' : fmt(v, v >= 1000 ? 1 : v >= 10 ? 2 : v >= 1 ? 3 : 5);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const tsec = ms => Math.floor(ms / 1000);
// 2026-10-17 网络容错：所有接口失败自动重试3次
const _fetch0 = window.fetch.bind(window);
window.fetch = async (u, o) => { let e; for (let k = 0; k < 4; k++) { try { return await _fetch0(u, o); } catch (x) { e = x; await new Promise(r => setTimeout(r, 600 * (k + 1))); } } throw e; };
const fetchRetry = true;
// 这笔交易对方案F总资金的大致贡献：币圈每币占腿的1/5、腿权重0.75；美股腿权重0.75
const cw = t => ((S.d.crypto_weights || {})[new Date(t.entry_t).getUTCFullYear()] || {})[t.symbol] ?? 0.2;   // 2026-10-19 币种按年度权重
const contrib = t => t.pnl_pct == null ? null : t.pnl_pct * (t.leg === "币圈" ? S.d.plan.w_crypto * cw(t) * (t.w || 1) : S.d.plan.w_stock);

function theme() {
  const dark = S.theme === 'dark';
  return {
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: dark ? '#9B9B9B' : '#7D7D7D', fontFamily: 'Inter, sans-serif' },
    grid: { vertLines: { color: dark ? 'rgba(255,255,255,.03)' : 'rgba(0,0,0,.04)' }, horzLines: { color: dark ? 'rgba(255,255,255,.05)' : 'rgba(0,0,0,.06)' } },
    rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false, rightOffset: 4 },
    crosshair: { mode: 0 },
  };
}
let eqC, ddC, kC, eqS = {}, ddS, candle, l8, l30, stopLine;
function charts() {
  eqC = LightweightCharts.createChart($('#eqChart'), { ...theme(), autoSize: true, timeScale: { ...theme().timeScale, minBarSpacing: 0.02 } });
  ddC = LightweightCharts.createChart($('#ddChart'), { ...theme(), autoSize: true, timeScale: { visible: false, minBarSpacing: 0.02 } });
  kC = LightweightCharts.createChart($('#kChart'), { ...theme(), autoSize: true, timeScale: { ...theme().timeScale, minBarSpacing: 0.1 } });
  candle = kC.addCandlestickSeries({ upColor: '#34C759', downColor: '#FF3B30', borderVisible: false, wickUpColor: '#34C759', wickDownColor: '#FF3B30' });
  l8 = kC.addLineSeries({ color: '#F5B83D', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  l30 = kC.addLineSeries({ color: '#4C82FB', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  eqC.timeScale().subscribeVisibleLogicalRangeChange(r => { if (r && S.ddReady) { try { ddC.timeScale().setVisibleLogicalRange(r); } catch (e) { } } });   // 2026-10-19 回撤图有数据后才同步，否则图表库报 Value is null
  let _mkTxt = null;
  kC.timeScale().subscribeVisibleLogicalRangeChange(r => { const t = !r || (r.to - r.from) <= 220; if (t !== _mkTxt) { _mkTxt = t; setMarks(); } });
  kC.subscribeCrosshairMove(p => hoverCard(p));
  // 2026-10-19 底部"净仓位"色条：三条子腿各占1/3，合计就是账户里这个币的真实仓位(+1=满仓多，-1=满仓空)
  candle.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.17 } });
  posS = kC.addHistogramSeries({ priceScaleId: 'pos', priceLineVisible: false, lastValueVisible: false, base: 0,
    priceFormat: { type: 'custom', formatter: v => (v > 0 ? '多 ' : v < 0 ? '空 ' : '') + Math.round(Math.abs(v) * 100) + '%' } });
  kC.priceScale('pos').applyOptions({ scaleMargins: { top: 0.86, bottom: 0.01 }, visible: false });
  document.querySelectorAll('#legSeg button').forEach(b => b.addEventListener('click', () => {
    S.legF = b.dataset.leg; document.querySelectorAll('#legSeg button').forEach(x => x.classList.toggle('on', x === b)); setMarks();
  }));
}
let posS = null;
function setTheme(t) {
  S.theme = t; document.documentElement.dataset.theme = t; localStorage.uq_theme = t;
  [eqC, ddC, kC].forEach(c => c && c.applyOptions(theme()));
}

/* ---------- 顶部 KPI ---------- */
function kpis() {
  const F = S.d.series.F, C = S.d.series.crypto, St = S.d.series.stock;
  const k = (l, v, s, c = '') => `<div class="kpi"><div class="l">${l}</div><div class="v ${c}">${v}</div><div class="s">${s}</div></div>`;
  $('#kpis').innerHTML =
    k('年化收益', pct(F.cagr), `币圈 ${pct(C.cagr)} · 美股 ${pct(St.cagr)}`, 'up') +
    k('最大回撤', '-' + fmt(F.max_dd, 1) + '%', `币圈 -${fmt(C.max_dd, 1)}% · 美股 -${fmt(St.max_dd, 1)}%`, 'dn') +
    k('收益/回撤', fmt(F.ratio, 2), `只做币圈 ${fmt(C.ratio, 2)}`) +
    k('今年以来', pct(F.ytd), `币圈 ${pct(C.ytd)} · 美股 ${pct(St.ytd)}`, cls(F.ytd)) +
    k('近12个月', pct(F.last12m), `期间回撤 -${fmt(F.last12m_dd, 1)}%`, cls(F.last12m));
  const t = new Date(S.d.asof * 1000);
  $('#asof').textContent = `数据更新 ${t.toLocaleString('zh-CN', { hour12: false })}`;
  try { planCard(); } catch (e) { console.warn('planCard', e); }
}

/* ---------- 2026-10-18 定稿方案卡：回测数字随每天回测自动更新；资金费口径和收益预期是 10-18 测算的固定值 ---------- */
function seg(series, a, z) {
  const v = series.filter(p => p[0] >= a && p[0] < z); if (v.length < 30) return null;
  let pk = 0, dd = 0; for (const [, x] of v) { pk = Math.max(pk, x); dd = Math.max(dd, 1 - x / pk); }
  const yrs = (v[v.length - 1][0] - v[0][0]) / 864e5 / 365.25, cagr = Math.pow(v[v.length - 1][1] / v[0][1], 1 / yrs) - 1;
  return { cagr: 100 * cagr, dd: 100 * dd, ratio: dd ? cagr / dd : null };
}
function planCard() {
  const s = S.d.series, F = s.F, C = s.crypto, St = s.stock, cut = Date.UTC(2022, 0, 1);
  const h1 = seg(F.series, 0, cut), h2 = seg(F.series, cut, 9e15);
  const r = (l, a, b, c, note = '', strong = false) => `<tr class="${strong ? 'pc-main' : ''}"><td>${l}</td><td class="r">${a}</td><td class="r">${b}</td><td class="r">${c}</td><td class="mut small">${note}</td></tr>`;
  const P = x => pct(x, 1), D = x => '-' + fmt(x, 1) + '%', Q = x => x == null ? '--' : fmt(x, 2);
  $('#planCard').innerHTML = `
    <div class="sec-h"><div><b>定稿方案卡</b><span class="mut small">2026-10-18 定稿 · 回测数字每天自动更新 · 研究过程见「全品种复盘 → 研究结论」</span></div></div>
    <div class="pc-grid">
      <div>
        <div class="pc-t">方案组成</div>
        <ul class="pc-ul">
          <li><b>币圈 1 倍</b>：BTC / ETH / BNB / SOL / XRP，按过去 3 年表现每年 1 月 1 日定权重(单币≤40%)，今年 <b>${Object.entries(S.d.crypto_weights_current || {}).sort((a, b) => b[1] - a[1]).map(([s, w]) => s.replace('USDT', '') + ' ' + Math.round(w * 100) + '%').join(' · ') || '等权'}</b>；日线 EMA8/30 + 8h 超级趋势 + 8h 布林突破 <b>各 1/3</b>；减半周期(牛只多/熊只空/筑底都做)、山寨跟 BTC 同向、熊市底部反转例外、前 5 日突破单</li>
          <li><b>美股 1 倍</b>：每月过去 6 个月行业涨幅前 5；正股日线 EMA8&gt;EMA30 只做多；3×ATR 止损(止损后等均线翻多才重进)；行业掉出前 5 离场</li>
          <li><b>总杠杆 ≤ 2 倍</b>，组合 60 天实际波动超过年化 40% 就按比例降仓(只降不加)</li>
          <li><b>执行</b>：全部 USDT 合约、单一全仓账户、每个仓位挂灾难止损单；信号出来后下一根开盘成交</li>
        </ul>
      </div>
      <div>
        <div class="pc-t">回测(2017-09 至今)</div>
        <table class="t pc-tbl"><thead><tr><th></th><th class="r">年化</th><th class="r">最大回撤</th><th class="r">收益/回撤</th><th></th></tr></thead><tbody>
          ${r('<b>主方案 币1+美股1</b>', P(F.cagr), D(F.max_dd), Q(F.ratio), '不含资金费', true)}
          ${r('主方案 扣真实资金费(估)', P(F.cagr - 11.3), '约' + D(F.max_dd + 1), '≈' + fmt((F.cagr - 11.3) / (F.max_dd + 1), 2), '资金费每年约拖累11个点(10-18实测)')}
          ${h1 ? r('　前半 2017-09~2021', P(h1.cagr), D(h1.dd), Q(h1.ratio)) : ''}
          ${h2 ? r('　后半 2022~今', P(h2.cagr), D(h2.dd), Q(h2.ratio)) : ''}
          ${r('币圈腿单独(1倍)', P(C.cagr), D(C.max_dd), Q(C.ratio))}
          ${r('美股腿单独(1倍)', P(St.cagr), D(St.max_dd), Q(St.ratio))}
          ${r('今年以来 / 近12个月', P(F.ytd), P(F.last12m), '', '主方案')}
        </tbody></table>
      </div>
      <div>
        <div class="pc-t">收益预期(扣资金费，随机重排模拟)</div>
        <table class="t pc-tbl"><thead><tr><th>情景</th><th class="r">1年中位</th><th class="r">1年最差5%</th><th class="r">亏钱概率</th><th class="r">3年中位</th></tr></thead><tbody>
          <tr><td>像全部历史</td><td class="r up">+91%</td><td class="r">+12%</td><td class="r">2%</td><td class="r">+599%</td></tr>
          <tr><td>像2022年以后</td><td class="r up">+84%</td><td class="r">+4%</td><td class="r">4%</td><td class="r">+533%</td></tr>
          <tr class="pc-main"><td><b>保守(收益减半)</b></td><td class="r"><b>+33%</b></td><td class="r dn">-24%</td><td class="r">22%</td><td class="r">+140%</td></tr>
        </tbody></table>
        <div class="pc-note">心理预期按保守情景：<b>一年 30~60%，回撤 25~35%</b>；3 年内遇到一次超过 30% 回撤的概率约 63%，要提前接受。原则：<b>不爆仓、长期正期望，就永远有机会</b>。</div>
      </div>
    </div>
    <div class="pc-steps"><span class="on">① 纸面模拟 2~3 周(看下方每日对账)</span><span>② 小资金实盘(计划总资金 10~20%)</span><span>③ 实盘 2~3 个月逐月对比回测</span><span>④ 在预期内分批加资金 · 期间不改规则</span></div>`;
}

/* ---------- 权益曲线 + 年度表 ---------- */
function eqDraw() {
  Object.values(eqS).forEach(s => eqC.removeSeries(s)); eqS = {};
  for (const k of S.ser) {
    if (!S.on.has(k)) continue;
    const s = eqC.addLineSeries({ color: SER[k].c, lineWidth: SER[k].w, priceLineVisible: false, lastValueVisible: true, title: SER[k].n,
      priceFormat: { type: 'custom', formatter: v => fmt(v, v >= 100 ? 0 : 2) + 'x' } });
    s.setData(S.d.series[k].series.map(([t, v]) => ({ time: tsec(t), value: v })));
    eqS[k] = s;
  }
  eqC.priceScale('right').applyOptions({ mode: S.log ? 1 : 0 });
  if (!ddS) ddS = ddC.addAreaSeries({ lineColor: '#FF3B30', topColor: 'rgba(255,95,82,.05)', bottomColor: 'rgba(255,95,82,.35)', lineWidth: 1, priceLineVisible: false,
    priceFormat: { type: 'custom', formatter: v => fmt(v, 1) + '%' } });
  ddS.setData(S.d.series.F.dd_series.map(([t, v]) => ({ time: tsec(t), value: v })));
  S.ddReady = true;
  eqC.timeScale().fitContent(); ddC.timeScale().fitContent(); setTimeout(() => { eqC.timeScale().fitContent(); ddC.timeScale().fitContent(); }, 50);
  $('#serSeg').innerHTML = S.ser.map(k => `<button data-ser="${k}" class="${S.on.has(k) ? 'on' : ''}"><i style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${SER[k].c};margin-right:6px"></i>${SER[k].n}</button>`).join('');
  $('#logBtn').classList.toggle('on', S.log);
}
function yearTable() {
  const ys = Object.keys(S.d.series.F.yearly);
  const cell = v => { const a = Math.min(Math.abs(v) / 150, 1) * .55 + .08; const c = v >= 0 ? `rgba(64,182,107,${a})` : `rgba(255,95,82,${a})`; return `<td style="background:${c}">${pct(v, 0)}</td>`; };
  $('#yTbl').innerHTML = `<table><tr><th>年份</th>${ys.map(y => `<th>${y}</th>`).join('')}</tr>` +
    S.ser.map(k => `<tr><td>${SER[k].n}</td>${ys.map(y => cell(S.d.series[k].yearly[y])).join('')}</tr>`).join('') + '</table>';
}

/* ---------- 当前状态 ---------- */
function cryptoState() {
  const m = S.d.crypto.meta, st = S.d.crypto.state;
  $('#cBadges').innerHTML = `<span class="badge pk">减半后第 ${m.phase_month} 个月 · ${m.phase}市 · ${m.phase_rule}</span><span class="badge ${m.btc_trend === '多头' ? 'up' : 'dn'}">BTC日线 ${m.btc_trend}</span>`;
  $('#cTbl').innerHTML = `<tr><th>币种</th><th>日线趋势</th><th class="r">收盘</th><th>持仓</th><th class="r">浮动</th><th>下一步</th></tr>` +
    Object.entries(st).map(([s, x]) => {
      const p = x.position;
      return `<tr class="click" data-sym="${s}"><td><b>${s.replace('USDT', '')}</b> <span class="mut small">${x.date}</span></td>
        <td class="${x.trend === '多头' ? 'up' : 'dn'}">${x.trend}</td><td class="r">${px(x.close)}</td>
        <td>${p ? `<span class="${p.side === 'LONG' ? 'side-l' : 'side-s'}">${p.side === 'LONG' ? '多' : '空'}</span> <span class="mut small">${day(p.entry_t)} @ ${px(p.entry)}</span>` : '<span class="mut">空仓</span>'}</td>
        <td class="r ${cls(p?.ret)}">${p ? pct(p.ret) : ''}</td><td class="wrap">${esc(x.pending_reason || (p ? '继续持有' : '等待信号'))}</td></tr>`;
    }).join('');
}
function stockState() {
  const r = S.d.stock.rotation;
  $('#rotSub').textContent = `${r.month} · 按 ${r.scores.length} 个行业近6个月涨幅排名`;
  const mx = Math.max(...r.scores.map(x => Math.abs(x[1])), 1);
  $('#rank').innerHTML = r.scores.map(([sec, v], i) => {
    const sel = r.selected.includes(sec), w = Math.abs(v) / mx * 100;
    const names = sel ? (r.tradable[sec] || []).join(' · ') : '';
    return `<div class="rk ${sel ? 'sel' : ''}"><span class="n">${i + 1}</span><span class="nm">${sel ? '★ ' : ''}${sec}</span>
      <span class="bar"><i style="${v >= 0 ? 'left:0' : 'right:0'};width:${w}%;background:${sel ? 'var(--pink)' : v >= 0 ? 'rgba(64,182,107,.55)' : 'rgba(255,95,82,.55)'}"></i></span>
      <span class="v ${cls(v)}">${pct(v)}</span>${names ? `<span class="names">可交易：${names || '无'}</span>` : ''}</div>`;
  }).join('');
  const ps = S.d.stock.positions.sort((a, b) => b.ret - a.ret);
  $('#posCnt').textContent = `${ps.length} 只`;
  $('#sTbl').innerHTML = `<tr><th>股票</th><th>行业</th><th>开仓</th><th class="r">现价</th><th class="r">止损</th><th class="r">浮动</th></tr>` +
    ps.map(p => `<tr class="click" data-sym="${p.symbol}"><td><b>${p.symbol}</b></td><td class="mut">${p.sector}</td><td class="mut small">${day(p.entry_t)} @ ${px(p.entry)}</td>
      <td class="r">${px(p.close)}</td><td class="r mut">${px(p.stop)}</td><td class="r ${cls(p.ret)}">${pct(p.ret)}</td></tr>`).join('');
  const pd = S.d.stock.pending;
  $('#pend').innerHTML = pd.length ? '<b>下一个交易日开盘</b>' + pd.map(x => `<div><b>${x.symbol}</b> ${x.action}：${esc(x.reason)}</div>`).join('') : '';
}

/* ---------- 交易复盘 ---------- */
function filters() {
  const tr = S.d.trades;
  const syms = [...new Set(tr.filter(t => !S.leg || t.leg === S.leg).map(t => t.symbol))].sort();
  $('#fSym').innerHTML = `<option value="">全部品种(${syms.length})</option>` + syms.map(s => `<option ${s === S.sym ? 'selected' : ''}>${s}</option>`).join('');
  const ys = [...new Set(tr.map(t => new Date(t.entry_t).getUTCFullYear()))].sort((a, b) => b - a);
  $('#fYear').innerHTML = `<option value="">全部年份</option>` + ys.map(y => `<option ${String(y) === S.year ? 'selected' : ''}>${y}</option>`).join('');
}
function applyFilter() {
  S.list = S.d.trades.filter(t => (!S.leg || t.leg === S.leg) && (!S.sym || t.symbol === S.sym) &&
    (!S.year || new Date(t.entry_t).getUTCFullYear() === +S.year) &&
    (!S.res || (S.res === 'o' ? t.open : S.res === 'w' ? !t.open && t.ret > 0 : !t.open && t.ret <= 0)));
  S.page = 0; renderTrades();
  const cl = S.list.filter(t => !t.open), w = cl.filter(t => t.ret > 0), l = cl.filter(t => t.ret <= 0);
  const avg = a => a.length ? a.reduce((s, t) => s + t.ret, 0) / a.length : 0;
  const aw = avg(w), al = avg(l);
  $('#sumLine').innerHTML = `<span>交易 <b>${S.list.length}</b> 笔(已平 ${cl.length})</span><span>胜率 <b>${cl.length ? fmt(w.length / cl.length * 100, 1) : '--'}%</b></span>
    <span>平均每笔 <b class="${cls(avg(cl))}">${pct(avg(cl), 2)}</b></span><span>平均盈利 <b class="up">${pct(aw, 2)}</b></span><span>平均亏损 <b class="dn">${pct(al, 2)}</b></span>
    <span>盈亏比 <b>${al ? fmt(-aw / al, 2) : '--'}</b></span><span>止损出场 <b>${cl.filter(t => /止损/.test(t.exit_reason)).length}</b></span>`;
}
function renderTrades() {
  const pg = S.list.slice(S.page * PER, (S.page + 1) * PER);
  $('#tTbl').innerHTML = `<thead><tr><th>开仓日</th><th>品种</th><th>方向</th><th class="r">开仓价</th><th>平仓日</th><th class="r">平仓价</th><th class="r">天数</th><th class="r">收益</th><th class="r">占方案F</th><th>开仓原因</th><th>平仓原因</th></tr></thead><tbody>` +
    pg.map((t, j) => {
      const i = S.page * PER + j;
      return `<tr class="click ${i === S.sel ? 'sel' : ''}" data-i="${i}"><td>${day(t.entry_t)}</td>
      <td><span class="tag ${t.leg === '币圈' ? 'c' : 's'}">${t.sub || t.leg}</span> <b>${t.symbol.replace('USDT', '')}</b></td>
      <td class="${t.side === 'LONG' ? 'side-l' : 'side-s'}">${t.side === 'LONG' ? '多' : '空'}</td><td class="r">${px(t.entry)}</td>
      <td>${t.open ? '<span class="tag o">持仓中</span>' : day(t.exit_t)}</td><td class="r">${px(t.exit)}</td><td class="r">${t.days}</td>
      <td class="r ${cls(t.ret)}"><b>${pct(t.ret, 2)}</b></td><td class="r ${cls(contrib(t))}">${contrib(t) == null ? '' : pct(contrib(t), 2)}</td>
      <td class="wrap" title="${esc(t.entry_reason)}">${esc(t.entry_reason)}</td><td class="wrap" title="${esc(t.exit_reason)}">${esc(t.exit_reason)}</td></tr>`;
    }).join('') + '</tbody>';
  const n = Math.ceil(S.list.length / PER);
  const btn = p => `<button data-pg="${p}" class="${p === S.page ? 'on' : ''}">${p + 1}</button>`;
  let h = '';
  for (let p = 0; p < n; p++) if (p < 2 || p > n - 3 || Math.abs(p - S.page) < 3) h += btn(p); else if (!h.endsWith('…')) h += '<span class="mut">…</span>';
  $('#pager').innerHTML = n > 1 ? h : '';
}
async function getBars(sym) {
  if (!S.bars[sym]) {   // 2026-10-19 存 Promise：同一品种并发请求只发一次；失败了清掉，下次重试
    S.bars[sym] = fetch('/api/optimal/bars?symbol=' + encodeURIComponent(sym)).then(r => { if (!r.ok) throw new Error('K线加载失败'); return r.json(); });
    S.bars[sym].catch(() => { delete S.bars[sym]; });
  }
  return S.bars[sym];
}
async function showSym(sym, focus) {
  if (!S.bars[sym]) $("#rvMeta").textContent = "加载K线…(首次几秒)";
  const rows = await getBars(sym);
  candle.setData(rows.map(r => ({ time: tsec(r[0]), open: r[1], high: r[2], low: r[3], close: r[4] })));
  l8.setData(rows.map(r => ({ time: tsec(r[0]), value: r[5] })));
  l30.setData(rows.map(r => ({ time: tsec(r[0]), value: r[6] })));
  const all = S.d.trades.filter(t => t.symbol === sym);
  // 美股K线时间是开盘时刻(UTC 13:30/14:30)，交易记录按日期记：按UTC日期对齐到当天那根K线
  const dmap = new Map(rows.map(r => [Math.floor(r[0] / 864e5), tsec(r[0])]));
  const tt = ms => dmap.get(Math.floor(ms / 864e5)) ?? tsec(ms);
  // 2026-10-18 标记直接写 多/空/平多/平空/止损；8h子腿用小号；放大到约 220 根以内才显示文字(缩远了只显示图标，避免字挤成一团)
  S.ev = new Map();
  const addEv = (time, e) => { const a = S.ev.get(time) || []; a.push(e); S.ev.set(time, a); };
  for (const t of all) {
    addEv(tt(t.entry_t), { k: 'in', t });
    if (!t.open) addEv(tt(t.exit_t), { k: 'out', t });
  }
  S.mkAll = all; S.mkFocus = focus; S.mkTT = tt;
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
  }
  if (stopLine) { candle.removePriceLine(stopLine); stopLine = null; }
  if (focus && focus.stop) stopLine = candle.createPriceLine({ price: focus.stop, color: '#FF8A4C', lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: '硬止损' });
  $('#rvSym').textContent = sym.replace('USDT', '');
  const c = all.filter(t => !t.open), w = c.filter(t => t.ret > 0).length;
  $('#rvMeta').textContent = `${all[0]?.leg || ''} · 共 ${all.length} 笔，胜率 ${c.length ? fmt(w / c.length * 100, 0) : '--'}%，平均每笔 ${pct(c.length ? c.reduce((s, t) => s + t.ret, 0) / c.length : 0, 2)}`;
  if (focus) {
    const a = tsec(focus.entry_t), b = tsec(focus.exit_t || rows[rows.length - 1][0]), pad = Math.max((b - a) * 0.6, 86400 * 25);
    try { kC.timeScale().setVisibleRange({ from: a - pad, to: b + pad }); } catch (e) { kC.timeScale().fitContent(); }
  } else kC.timeScale().fitContent();
}
function setMarks() {
  const all = S.mkAll || [], focus = S.mkFocus, tt = S.mkTT; if (!tt) return;
  const r = kC.timeScale().getVisibleLogicalRange(), txt = !r || (r.to - r.from) <= 220;
  const mk = [];
  const lf = S.legF || 'all';
  for (const t of all) {
    if (lf !== 'all' && t !== focus && !(t.sub || '').includes(lf)) continue;   // 2026-10-19 子腿筛选
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
  }).join('') + (() => { const pd = posS && p.seriesData ? p.seriesData.get(posS) : null; if (!pd) return '';
      const v = pd.value; return `<div class="kr mut">当天三条腿合计净仓位：<b class="${v > 0 ? 'up' : v < 0 ? 'dn' : ''}">${v > 0 ? '多' : v < 0 ? '空' : '空仓'} ${v ? Math.round(Math.abs(v) * 100) + '%' : ''}</b></div>`; })();
  el.hidden = false;
  const box = $('#kChart').getBoundingClientRect(), off = $('#kChart').offsetTop, W = el.offsetWidth, H = el.offsetHeight;
  let x = p.point.x + 18, y = p.point.y + 12 + off;
  if (x + W > box.width - 70) x = p.point.x - W - 18;
  if (y + H > box.height + off) y = Math.max(off + 4, box.height + off - H - 4);
  el.style.left = Math.max(4, x) + 'px'; el.style.top = y + 'px';
}
function why(t) {
  const L = t.side === 'LONG';
  $('#rvWhy').innerHTML = `<h4><span class="tag ${t.leg === '币圈' ? 'c' : 's'}">${t.sub || t.leg}</span> ${t.symbol.replace('USDT', '')} ${L ? '做多' : '做空'}${t.sector ? ` · <span class="mut">${t.sector}</span>` : ''}</h4>
    <div class="res ${cls(t.ret)}">${pct(t.ret, 2)} <span class="mut small">${t.open ? '浮动(持仓中)' : '单笔收益'}${contrib(t) != null ? ` · 占方案F约 ${pct(contrib(t), 2)}` : ''}</span></div>
    <div class="grid2"><span>开仓</span><b>${day(t.entry_t)} @ ${px(t.entry)}</b><span>平仓</span><b>${t.open ? '持仓中' : `${day(t.exit_t)} @ ${px(t.exit)}`}</b>
      <span>持有</span><b>${t.days} 天</b><span>${t.stop ? '硬止损' : '仓位'}</span><b>${t.stop ? px(t.stop) : `资金×${fmt(t.alloc, 2)}`}</b></div>
    <div class="wy wy-in ${L ? "" : "wy-short"}"><div class="h">开仓原因<span>${day(t.entry_t)}</span></div>${esc(t.entry_reason)}</div>
    <div class="wy wy-out"><div class="h">平仓原因<span>${t.open ? '--' : day(t.exit_t)}</span></div>${esc(t.exit_reason)}</div>`;
}
async function pick(i) {
  if (i < 0 || i >= S.list.length) return;
  S.sel = i; const t = S.list[i];
  if (Math.floor(i / PER) !== S.page) S.page = Math.floor(i / PER);
  renderTrades(); why(t);
  try { await showSym(t.symbol, t); } catch (e) { $('#rvMeta').textContent = e.message; }
}

/* ---------- 已淘汰策略 ---------- */
async function retired() {
  const r = await (await fetch('/api/retired')).json();
  const g = {};
  for (const [k, v] of Object.entries(r.retired)) {
    const key = /擂台纸面负收益/.test(v) ? '擂台纸面负收益(起始$1000)' : /擂台归档/.test(v) ? '擂台归档(负收益/雷同变体)' : /18组/.test(v) ? '平台内置策略(18组回测)' : '研究回测否决';
    (g[key] = g[key] || []).push([k, v]);
  }
  const no = Object.entries(r.not_optimal || {});
  if (no.length) g[`正收益但不是最优(只是下架，不算淘汰)`] = no;
  $('#retSub').textContent = `负收益永久淘汰 ${Object.keys(r.retired).length} 个 + 非最优下架 ${no.length} 个 · 工作台只保留方案F两条腿 · 名单生成于 ${r.generated}`;
  $('#retBody').innerHTML = Object.entries(g).map(([h, xs]) => `<div class="grp"><div class="gh">${h}（${xs.length}）</div>${xs.map(([k, v]) => `<div class="it"><code>${esc(k)}</code><span>${esc(v)}</span></div>`).join('')}</div>`).join('');
}

function bind() {
  document.addEventListener("click", e => {
    const t = e.target;
    if (!S.d && !t.closest("#themeBtn")) return;   // 数据还没加载完
    const sb = t.closest('[data-ser]'); if (sb) { const k = sb.dataset.ser; S.on.has(k) ? S.on.delete(k) : S.on.add(k); if (!S.on.size) S.on.add('F'); return eqDraw(); }
    if (t.closest('#logBtn')) { S.log = !S.log; return eqDraw(); }
    if (t.closest('#themeBtn')) return setTheme(S.theme === 'dark' ? 'light' : 'dark');
    const lg = t.closest('#legSeg button'); if (lg) { S.leg = lg.dataset.v; S.sym = ''; $$('#legSeg button').forEach(b => b.classList.toggle('on', b === lg)); filters(); return applyFilter(); }
    const pg = t.closest('[data-pg]'); if (pg) { S.page = +pg.dataset.pg; return renderTrades(); }
    const tr = t.closest('tr[data-i]'); if (tr) return pick(+tr.dataset.i);
    const sy = t.closest('tr[data-sym]'); if (sy) { S.sym = sy.dataset.sym; S.leg = ''; S.res = ''; S.year = ''; $$('#legSeg button').forEach(b => b.classList.toggle('on', !b.dataset.v)); $('#fRes').value = ''; filters(); applyFilter(); $('#review').scrollIntoView({ behavior: 'smooth' }); return pick(0); }
    if (t.closest('#prevBtn')) return pick(S.sel - 1);
    if (t.closest('#nextBtn')) return pick(S.sel + 1);
    if (t.closest('#retBtn')) { const b = $('#retBody'); b.hidden = !b.hidden; $('#retBtn').textContent = b.hidden ? '展开' : '收起'; }
  });
  $('#fSym').addEventListener('change', e => { S.sym = e.target.value; applyFilter(); });
  $('#fRes').addEventListener('change', e => { S.res = e.target.value; applyFilter(); });
  $('#fYear').addEventListener('change', e => { S.year = e.target.value; applyFilter(); });
  document.addEventListener('keydown', e => { if (/INPUT|SELECT/.test(document.activeElement.tagName)) return; if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); pick(S.sel + 1); } if (e.key === 'ArrowUp' || e.key === 'k') { e.preventDefault(); pick(S.sel - 1); } });
}

async function boot() {
  document.documentElement.dataset.theme = S.theme;
  charts(); bind();
  $('#rvMeta').textContent = '加载数据…(首次几秒)'; getBars('BTCUSDT').catch(() => {});   // 2026-10-19 主数据和K线同时下载
  try {
    const r = await fetch('/api/optimal');
    if (!r.ok) throw new Error((await r.json()).error || '数据未生成');
    S.d = await r.json();
  } catch (e) { $('#asof').textContent = '加载失败：' + e.message; return; }
  kpis(); eqDraw(); yearTable(); cryptoState(); stockState(); filters(); applyFilter(); pick(0); retired(); loadRecon();
}
boot();


/* 2026-10-18 复盘K线全屏：K线+原因面板铺满窗口(并尽量请求浏览器原生全屏)；F 切换，Esc 退出，全屏里 ↑↓ 切换上一笔/下一笔 */
function kFs(force) {
  const el = document.querySelector('#review .rv'), on = force === undefined ? !el.classList.contains('fs-on') : force;
  el.classList.toggle('fs-on', on); document.body.classList.toggle('kfs-open', on); document.body.style.overflow = on ? 'hidden' : '';   // 2026-10-19 外层卡片的毛玻璃(backdrop-filter)会让 fixed 只铺满卡片，全屏时关掉
  try { if (on && !document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {}); if (!on && document.fullscreenElement) document.exitFullscreen().catch(() => {}); } catch (e) { }
  document.getElementById('kfsBtn').textContent = on ? '✕ 退出全屏' : '⛶ 全屏';
}
document.getElementById('kfsBtn').addEventListener('click', e => { e.stopPropagation(); kFs(); });
// 2026-10-17 截图工具按Esc会退出浏览器原生全屏，但复盘K线继续铺满窗口；退出只用按钮或F键
document.addEventListener('keydown', e => {
  if (/INPUT|SELECT/.test(document.activeElement.tagName)) return;
  if (e.key === 'f' || e.key === 'F') { e.preventDefault(); kFs(); }
});


/* ---------- 2026-10-17 纸面对账：擂台 vs 回测 ---------- */
async function loadRecon() {
  let r;
  try { const x = await fetch('/api/optimal/recon'); if (!x.ok) throw 0; r = await x.json(); } catch (e) { $('#rcBody').innerHTML = '<div class="mut">对账数据还没生成(每天美股收盘后生成)</div>'; return; }
  $('#rcSub').textContent = `更新于 ${new Date(r.asof * 1000).toLocaleString('zh-CN', { hour12: false })} · 擂台上线时按当前状态直接开仓，所以先比"持仓状态"，再比上线后的新交易`;
  const tm = ms => { const d = new Date(ms); return `${d.getUTCMonth() + 1}-${String(d.getUTCDate()).padStart(2, '0')} ${String(d.getUTCHours()).padStart(2, '0')}:00`; };
  $('#rcBody').innerHTML = r.pairs.map(p => {
    if (!p.state) return `<div class="rule"><b>${p.name}</b> · <span class="mut">${p.status}</span></div>`;
    const st = p.state.map(x => `<span class="badge ${x.ok ? 'up' : x.note ? 'wn' : 'dn'}" title="${esc(x.note || '')}">${x.symbol.replace('USDT', '')} 擂台${x.arena === 'LONG' ? '多单' : x.arena === 'SHORT' ? '空单' : '空仓'} / 回测${x.backtest === 'LONG' ? '多单' : x.backtest === 'SHORT' ? '空单' : '空仓'}${x.note ? ' ⓘ' : ''}</span>`).join(' ')
      + (p.state.some(x => x.note) ? `<div class="mut small rcnote">${p.state.filter(x => x.note).map(x => `<b>${x.symbol.replace('USDT', '')}</b>：${esc(x.note)}`).join('；')}</div>` : '');
    const rows = [...p.matched.map(m => `<tr><td>配上</td><td>${m.symbol.replace('USDT', '')}</td><td>${m.side === 'LONG' ? '多' : '空'}</td><td>擂台 ${tm(m.arena_t)} @ ${px(m.arena_px)}</td><td>回测 ${tm(m.bt_t)} @ ${px(m.bt_px)}</td><td class="r ${m.diff_bp > 0 ? 'dn' : 'up'}">${m.diff_bp > 0 ? '+' : ''}${m.diff_bp}bp</td></tr>`),
      ...p.missed.map(m => `<tr><td class="dn">漏单</td><td>${m.symbol.replace('USDT', '')}</td><td>${m.side === 'LONG' ? '多' : '空'}</td><td>—</td><td>回测 ${tm(m.t)} @ ${px(m.px)}</td><td class="wrap" title="${esc(m.reason)}">${esc(m.reason)}</td></tr>`),
      ...p.extra.map(m => `<tr><td class="dn">多单</td><td>${m.symbol.replace('USDT', '')}</td><td>${m.side === 'LONG' ? '多' : '空'}</td><td>擂台 ${tm(m.t)} @ ${px(m.px)}</td><td>—</td><td></td></tr>`)].join('');
    return `<div class="rule" style="margin-bottom:10px"><b>${p.name}</b> <span class="mut small">${p.strategy}</span><br>
      持仓状态一致 <b>${p.state_ok}/${p.state_n}</b> · 上线后新交易：配上 <b>${p.matched.length}</b>，漏单 <b class="${p.missed.length ? 'dn' : ''}">${p.missed.length}</b>，多单 <b class="${p.extra.length ? 'dn' : ''}">${p.extra.length}</b>${p.avg_diff_bp != null ? `，平均开仓价差 <b>${p.avg_diff_bp}bp</b>(正=擂台更差)` : ''}
      <div class="badges" style="margin-top:8px">${st}</div>
      ${rows ? `<div class="tbl-wrap"><table class="t">${rows}</table></div>` : ''}</div>`;
  }).join('');
  loadAutofix();
}
/* 2026-10-19 自动对账修复器面板 */
async function loadAutofix() {
  let a; try { const x = await fetch('/api/optimal/autofix'); if (!x.ok) return; a = await x.json(); } catch (e) { return; }
  const h = a.health || {}, acts = a.actions || [];
  const t = s => new Date(s * 1000).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  const ov = Object.entries(a.overrides || {}).flatMap(([st, m]) => Object.entries(m).map(([s, o]) => `${s.replace('USDT', '')}(${st}) → ${o.want}`));
  const kcls = k => k === '修复' ? 'wn' : k === '重启' ? 'dn' : '';
  const el = document.createElement('div'); el.className = 'af';
  el.innerHTML = `<div class="af-h"><b>自动对账修复</b>
      <span class="badge ${h.autofix_on ? 'up' : 'dn'}">${h.autofix_on ? '运行中' : '已关闭'}</span>
      <span class="mut small">每小时检查一次 · 上次 ${h.ts ? t(h.ts) : '--'}</span></div>
    <div class="af-k">
      <div><em>自然一致率</em><b>${h.natural_rate == null ? '--' : h.natural_rate + '%'}</b><span>修复前看到的一致比例，共 ${h.checks || 0} 次检查(纸面验证看这个)</span></div>
      <div><em>本次检查</em><b>${h.natural_ok ?? '--'}/${h.natural_n ?? '--'}</b><span>持仓状态一致</span></div>
      <div><em>累计修复</em><b>${h.fixes_total || 0} 次</b><span>今天 ${h.fixed_today || 0} 次(上限 6)</span></div>
      <div><em>执行中的指令</em><b>${ov.length}</b><span>${ov.join('；') || '无'}</span></div>
    </div>
    ${acts.length ? `<div class="tbl-wrap short"><table class="t"><thead><tr><th>时间</th><th>类型</th><th>品种</th><th>擂台/回测</th><th>动作</th><th>原因</th></tr></thead><tbody>${acts.map(x => `<tr><td>${t(x.ts)}</td><td><span class="badge ${kcls(x.kind)}">${esc(x.kind)}</span></td><td>${esc((x.symbol || '').replace('USDT', ''))} <span class="mut small">${esc(x.strategy || '')}</span></td><td>${esc(x.arena || '')}${x.backtest ? ' / ' + esc(x.backtest) : ''}</td><td>${esc(x.action || '')}</td><td class="wrap">${esc(x.reason || '')}</td></tr>`).join('')}</tbody></table></div>` : '<div class="mut small">还没有任何修复或异常记录。</div>'}
    <div class="mut small af-n">规则：持续两次检查才动；只修纸面仓位，不改策略代码；同一品种一周被修 3 次就停手并标记"疑似代码问题"；每天最多 6 次。</div>`;
  $('#rcBody').prepend(el);
}

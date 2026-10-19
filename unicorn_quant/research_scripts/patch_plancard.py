import shutil, time, os, re
R = "/home/stratroster/unicorn_quant/static"
bk = f"/home/stratroster/unicorn_quant/backups/pre_plancard_{time.strftime('%Y%m%d_%H%M%S')}"; os.makedirs(bk, exist_ok=True)
for f in ("best.js", "best.css", "best.html"): shutil.copy(f"{R}/{f}", bk)
def sub(path, old, new):
    s = open(f"{R}/{path}", encoding="utf-8").read(); assert s.count(old) == 1, (path, old[:60], s.count(old))
    open(f"{R}/{path}", "w", encoding="utf-8").write(s.replace(old, new))
sub("best.html", """两条腿分别在两个子账户运行，按总资金计最高 2 倍名义仓位(不再加)""", """两条腿放在同一个 USDT 合约全仓账户，按总资金计最高 2 倍名义仓位(不再加)""")
sub("best.html", """    <div class="kpis" id="kpis"></div>
  </section>""", """    <div class="kpis" id="kpis"></div>
  </section>

  <section class="card pad" id="planCard"></section>""")
sub("best.js", """  const t = new Date(S.d.asof * 1000);
  $('#asof').textContent = `数据更新 ${t.toLocaleString('zh-CN', { hour12: false })}`;
}""", """  const t = new Date(S.d.asof * 1000);
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
          <li><b>币圈 1 倍</b>：BTC / ETH / BNB / SOL / XRP 各 1/5；日线 EMA8/30 <b>50%</b> + 8h 超级趋势 <b>25%</b> + 8h 布林突破 <b>25%</b>；减半周期(牛只多/熊只空/筑底都做)、山寨跟 BTC 同向、熊市底部反转例外、前 5 日突破单</li>
          <li><b>美股 1 倍</b>：每月过去 6 个月行业涨幅前 5；正股日线 EMA8&gt;EMA30 只做多；3×ATR 止损(止损后等均线翻多才重进)；行业掉出前 5 离场</li>
          <li><b>总杠杆 ≤ 2 倍</b>，组合 60 天实际波动超过年化 40% 就按比例降仓(只降不加)</li>
          <li><b>执行</b>：全部 USDT 合约、单一全仓账户、每个仓位挂灾难止损单；信号出来后下一根开盘成交</li>
        </ul>
      </div>
      <div>
        <div class="pc-t">回测(2017-09 至今)</div>
        <table class="t pc-tbl"><thead><tr><th></th><th class="r">年化</th><th class="r">最大回撤</th><th class="r">收益/回撤</th><th></th></tr></thead><tbody>
          ${r('<b>主方案 币1+美股1</b>', P(F.cagr), D(F.max_dd), Q(F.ratio), '不含资金费', true)}
          ${r('主方案 扣真实资金费', '+88.5%', '-22.8%', '≈3.9', '10-18 测算')}
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
}""")
open(f"{R}/best.css", "a", encoding="utf-8").write("""
/* 2026-10-18 定稿方案卡 */
#planCard{margin-top:14px}
.pc-grid{display:grid;grid-template-columns:1.15fr 1fr 1fr;gap:18px;margin-top:6px}
.pc-t{font-weight:600;margin:2px 0 8px;color:var(--t1)}
.pc-ul{margin:0;padding-left:18px;line-height:1.7;font-size:13px;color:var(--t2)}
.pc-ul b{color:var(--t1)}
.pc-tbl{font-size:12.5px;width:100%}
.pc-tbl td,.pc-tbl th{padding:5px 6px!important}
.pc-tbl tr.pc-main td{background:var(--pink-bg);font-weight:600}
.pc-note{margin-top:10px;padding:8px 10px;border-radius:12px;background:rgba(255,149,0,.12);font-size:12.5px;line-height:1.6;color:var(--t1)}
.pc-steps{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.pc-steps span{padding:6px 12px;border-radius:999px;background:var(--card2);font-size:12.5px;color:var(--t2)}
.pc-steps span.on{background:var(--pink);color:#fff}
@media (max-width:1100px){.pc-grid{grid-template-columns:1fr}}
""")
h = open(f"{R}/best.html", encoding="utf-8").read()
h = re.sub(r"best\.js\?v=\w+", "best.js?v=15pc", h); h = re.sub(r"best\.css\?v=\w+", "best.css?v=8pc", h)
open(f"{R}/best.html", "w", encoding="utf-8").write(h)
print("ok", bk)

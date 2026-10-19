import re
R = "/home/stratroster/unicorn_quant/static"
p = f"{R}/best.js"; s = open(p, encoding="utf-8").read()
old = """    const st = p.state.map(x => `<span class="badge ${x.ok ? 'up' : 'dn'}">${x.symbol.replace('USDT', '')} 擂台${x.arena === 'LONG' ? '多' : x.arena === 'SHORT' ? '空' : '空仓'} / 回测${x.backtest === 'LONG' ? '多' : x.backtest === 'SHORT' ? '空' : '空仓'}</span>`).join(' ');"""
new = """    const st = p.state.map(x => `<span class="badge ${x.ok ? 'up' : x.note ? 'wn' : 'dn'}" title="${esc(x.note || '')}">${x.symbol.replace('USDT', '')} 擂台${x.arena === 'LONG' ? '多' : x.arena === 'SHORT' ? '空' : '空仓'} / 回测${x.backtest === 'LONG' ? '多' : x.backtest === 'SHORT' ? '空' : '空仓'}${x.note ? ' ⓘ' : ''}</span>`).join(' ')
      + (p.state.some(x => x.note) ? `<div class="mut small rcnote">${p.state.filter(x => x.note).map(x => `<b>${x.symbol.replace('USDT', '')}</b>：${esc(x.note)}`).join('；')}</div>` : '');"""
assert s.count(old) == 1; s = s.replace(old, new); open(p, "w", encoding="utf-8").write(s)
open(f"{R}/best.css", "a", encoding="utf-8").write("\n.badge.wn{background:rgba(255,149,0,.15)!important;color:#FF9500!important}.rcnote{margin-top:6px;line-height:1.6}\n")
h = open(f"{R}/best.html", encoding="utf-8").read()
h = re.sub(r"best\.js\?v=\w+", "best.js?v=14rc", h); h = re.sub(r"best\.css\?v=\w+", "best.css?v=7rc", h)
open(f"{R}/best.html", "w", encoding="utf-8").write(h)
print("ok")

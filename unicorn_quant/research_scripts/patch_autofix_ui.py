import re, shutil, time
U = "/home/stratroster/unicorn_quant"
for f in ("server.py", "static/best.js", "static/best.css", "static/best.html"):
    shutil.copy(f"{U}/{f}", f"{U}/backups/{f.replace('/', '_')}.pre_autofix_{time.strftime('%H%M%S')}")
def sub(path, old, new):
    s = open(f"{U}/{path}", encoding="utf-8").read(); assert s.count(old) == 1, (path, old[:60], s.count(old))
    open(f"{U}/{path}", "w", encoding="utf-8").write(s.replace(old, new))
sub("server.py", '''"/api/optimal", "/api/optimal/bars", "/api/retired", "/api/optimal/recon",''',
    '''"/api/optimal", "/api/optimal/bars", "/api/retired", "/api/optimal/recon", "/api/optimal/autofix",''')
sub("server.py", '''@app.get("/api/retired")''', '''@app.get("/api/optimal/autofix")
@api
def optimal_autofix():
    """2026-10-19 擂台↔回测 自动对账修复器的状态和最近记录(engine/reconciler.py 每小时写)"""
    def rd(name, default):
        try:
            with open(os.path.join(OPT_DIR, name), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    h = rd("recon_health.json", {}); h.pop("history", None)
    return jsonify(health=h, actions=rd("recon_actions.json", [])[-40:][::-1], overrides=rd("recon_override.json", {}))


@app.get("/api/retired")''')
sub("static/best.js", """      ${rows ? `<div class="tbl-wrap"><table class="t">${rows}</table></div>` : ''}</div>`;
  }).join('');
}""", """      ${rows ? `<div class="tbl-wrap"><table class="t">${rows}</table></div>` : ''}</div>`;
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
}""")
open(f"{U}/static/best.css", "a", encoding="utf-8").write("""
/* 2026-10-19 自动对账修复面板 */
.af{margin-bottom:14px;padding:12px 14px;border-radius:16px;background:var(--card);border:.5px solid var(--line)}
.af-h{display:flex;gap:8px;align-items:center;margin-bottom:8px}
.af-k{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:8px}
.af-k>div{background:var(--card2);border-radius:12px;padding:8px 10px;display:flex;flex-direction:column;gap:2px}
.af-k em{font-style:normal;font-size:11px;color:var(--t2)}.af-k b{font-size:18px}.af-k span{font-size:11px;color:var(--t2)}
.af-n{margin-top:6px}
@media (max-width:900px){.af-k{grid-template-columns:1fr 1fr}}
""")
h = open(f"{U}/static/best.html", encoding="utf-8").read()
h = re.sub(r"best\.js\?v=\w+", "best.js?v=20af", h); h = re.sub(r"best\.css\?v=\w+", "best.css?v=9af", h)
open(f"{U}/static/best.html", "w", encoding="utf-8").write(h); print("ok")

import shutil, time, os, re
R = "/home/stratroster/unicorn_quant"
bk = f"{R}/backups/pre_stitch_{time.strftime('%Y%m%d_%H%M%S')}"; os.makedirs(bk, exist_ok=True)
for f in ("server.py", "static/app.js", "static/index.html"): shutil.copy(f"{R}/{f}", bk)
def sub(path, old, new):
    s = open(f"{R}/{path}", encoding="utf-8").read(); assert s.count(old) == 1, (path, old[:50], s.count(old))
    open(f"{R}/{path}", "w", encoding="utf-8").write(s.replace(old, new))
sub("server.py", """    bars = data.get_klines(sym, itv, limit, closed_only=True)
    if len(bars) < 100:
        raise ValueError("K线不足，无法复盘")""", """    bars = data.get_klines(sym, itv, limit, closed_only=True)
    # 2026-10-18 币安美股永续上线晚(MUUSDT 2026-05起)：回测美股腿时，上线前用正股日线补上，预热和开仓与 /best 一致
    if name == "plan_f_stock" and itv == "1d" and sym.upper().endswith("USDT") and bars:
        try:
            yb = stocks.get_klines(sym.upper()[:-4], "1d", 600, closed_only=True)
            pre = [{"t": x["t"] // 86400000 * 86400000, "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"], "v": x.get("v", 0)}
                   for x in yb if x["t"] // 86400000 * 86400000 < bars[0]["t"]]
            bars = pre + list(bars)
        except Exception:
            pass
    if len(bars) < 100:
        raise ValueError("K线不足，无法复盘")""")
# 前端：开仓在图表数据之前(正股补的那段)的单子，从第一根K线画起
sub("static/app.js", """    const long = t.side === 'LONG', eb = bmap.get(t.entry_t), xb = bmap.get(t.exit_t);
    const x1 = X(t.entry_t), x2 = X(t.exit_t); if (x1 == null || x2 == null || !eb || !xb) continue;""",
"""    const long = t.side === 'LONG', pre = t.entry_t < S.bars[0].t && t.exit_t >= S.bars[0].t;
    const eb = pre ? S.bars[0] : bmap.get(t.entry_t), xb = bmap.get(t.exit_t);
    const x1 = X(pre ? S.bars[0].t : t.entry_t), x2 = X(t.exit_t); if (x1 == null || x2 == null || !eb || !xb) continue;""")
sub("static/app.js", """    pill(x1, long ? Y(eb.l) + 4 : Y(eb.h) - 4, long ? '开多' : '开空', t.entry, long ? GREEN : RED, !long, t.entry_t + (long ? 'b' : 'a'));""",
"""    pill(x1, long ? Y(eb.l) + 4 : Y(eb.h) - 4, (long ? '开多' : '开空') + (pre ? '(' + new Date(t.entry_t).toISOString().slice(5, 10) + ' 正股)' : ''), t.entry, long ? GREEN : RED, !long, t.entry_t + (long ? 'b' : 'a'));""")
s = open(f"{R}/static/index.html", encoding="utf-8").read(); s = re.sub(r"app\.js\?v=[\w]+", "app.js?v=21stitch", s); open(f"{R}/static/index.html", "w", encoding="utf-8").write(s)
print("ok", bk)

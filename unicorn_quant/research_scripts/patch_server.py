import shutil, time, os
R = "/home/stratroster/unicorn_quant"
bk = f"{R}/backups/pre_universe_{time.strftime('%Y%m%d_%H%M%S')}"; os.makedirs(bk, exist_ok=True)
for f in ("server.py", "optimal_run.py", "static/index.html", "static/best.html"): shutil.copy(f"{R}/{f}", bk)
def sub(path, old, new):
    s = open(f"{R}/{path}", encoding="utf-8").read(); assert s.count(old) == 1, (path, old[:50], s.count(old))
    open(f"{R}/{path}", "w", encoding="utf-8").write(s.replace(old, new))
sub("server.py", '''@app.get("/api/retired")''', '''# ---------- 2026-10-18 全品种复盘：数据 = optimal.json 同一份交易，engine/universe.py 汇总 ----------
@app.get("/universe")
def universe_page():
    return send_from_directory(app.static_folder, "universe.html")


@app.get("/u/<sym>")
def sym_page(sym):
    return send_from_directory(app.static_folder, "sym.html")


@app.get("/api/universe")
@api
def universe_get():
    p = os.path.join(OPT_DIR, "universe.json")
    if not os.path.exists(p):
        raise ValueError("全品种复盘数据还没生成(optimal_run.py)")
    return _json_file(p)


_uidx = {"mt": 0, "v": None}


@app.get("/api/universe/index")
@api
def universe_index():
    p = os.path.join(OPT_DIR, "universe.json")
    mt = os.path.getmtime(p)
    if _uidx["mt"] != mt:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        _uidx.update(mt=mt, v=[[s["sym"], s["sector"], s["kind"], s["tradable"]] for s in d["symbols"]])
    return jsonify(symbols=_uidx["v"])


@app.get("/api/universe/<sym>")
@api
def universe_sym(sym):
    import re
    sym = sym.upper()
    if not re.fullmatch(r"[A-Z0-9]{1,12}", sym):
        raise ValueError("品种代码不合法")
    p = os.path.join(OPT_DIR, "u", f"{sym}.json")
    if not os.path.exists(p):
        raise ValueError(f"没有 {sym} 的复盘数据")
    return _json_file(p)


@app.get("/api/retired")''')
sub("optimal_run.py", '''try:                                   # 2026-10-17 擂台纸面 vs 回测 对账''', '''try:                                   # 2026-10-18 全品种复盘(universe.json + u/<SYM>.json)
    from engine import universe
    u = universe.build()
    print(time.strftime("%Y-%m-%d %H:%M:%S"), f"全品种复盘: {len(u['symbols'])} 个品种 {u['n_trades']} 笔", flush=True)
except Exception as e:
    print("全品种复盘失败:", e, flush=True)

try:                                   # 2026-10-17 擂台纸面 vs 回测 对账''')
sub("static/best.html", '''<nav class="pills"><a href="/">图表与回测</a><a class="on">最优方案</a></nav>''', '''<nav class="pills"><a href="/">图表与回测</a><a class="on">最优方案</a><a href="/universe">全品种复盘</a></nav>''')
print("ok", bk)
sub("static/index.html", '''  <a class="btn-pink sm" href="/best" style="text-decoration:none;white-space:nowrap">★ 最优方案</a>''', '''  <a class="btn-pink sm" href="/best" style="text-decoration:none;white-space:nowrap">★ 最优方案</a>
  <a class="chip sm" href="/universe" style="text-decoration:none;white-space:nowrap">◎ 全品种复盘</a>''')

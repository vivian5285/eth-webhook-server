import re, shutil, time
U = "/home/stratroster/unicorn_quant"; ts = time.strftime("%H%M%S")
for f in ("server.py", "static/best.js", "static/best.html"):
    shutil.copy(f"{U}/{f}", f"{U}/backups/{f.replace('/', '_')}.pre_cache_{ts}")
def sub(path, old, new):
    s = open(f"{U}/{path}", encoding="utf-8").read(); assert s.count(old) == 1, (path, old[:50], s.count(old))
    open(f"{U}/{path}", "w", encoding="utf-8").write(s.replace(old, new))
# 服务端：JSON 文件带 ETag(文件修改时间)，浏览器重复打开时没变就回 304，不再重新下载
sub("server.py", '''    if "gzip" in (request.headers.get("Accept-Encoding") or ""):
        r = app.response_class(hit[2], mimetype="application/json")
        r.headers["Content-Encoding"] = "gzip"
        r.headers["Vary"] = "Accept-Encoding"
        return r
    return app.response_class(hit[1], mimetype="application/json")''',
'''    etag = f'"{os.path.basename(p)}-{int(mt)}"'          # 2026-10-19 数据没变就回304，刷新页面不用重新下载几MB
    if etag in (request.headers.get("If-None-Match") or ""):
        r = app.response_class(status=304); r.headers["ETag"] = etag; return r
    if "gzip" in (request.headers.get("Accept-Encoding") or ""):
        r = app.response_class(hit[2], mimetype="application/json")
        r.headers["Content-Encoding"] = "gzip"
        r.headers["Vary"] = "Accept-Encoding"
    else:
        r = app.response_class(hit[1], mimetype="application/json")
    r.headers["ETag"] = etag
    return r''')
sub("server.py", '''    if request.path.startswith("/api/"):
        r.headers["Cache-Control"] = "no-store"''',
'''    if request.path.startswith("/api/"):
        r.headers["Cache-Control"] = "no-cache" if r.headers.get("ETag") else "no-store"   # 有ETag的可以缓存但每次先问服务器''')
# 前端：K线请求缓存成 Promise，并在主数据下载的同时先拉 BTC 的K线
sub("static/best.js", '''async function getBars(sym) {
  if (!S.bars[sym]) {
    const r = await fetch('/api/optimal/bars?symbol=' + encodeURIComponent(sym));
    if (!r.ok) throw new Error('K线加载失败');
    S.bars[sym] = await r.json();
  }
  return S.bars[sym];
}''', '''async function getBars(sym) {
  if (!S.bars[sym]) {   // 2026-10-19 存 Promise：同一品种并发请求只发一次；失败了清掉，下次重试
    S.bars[sym] = fetch('/api/optimal/bars?symbol=' + encodeURIComponent(sym)).then(r => { if (!r.ok) throw new Error('K线加载失败'); return r.json(); });
    S.bars[sym].catch(() => { delete S.bars[sym]; });
  }
  return S.bars[sym];
}''')
sub("static/best.js", "  if (!S.bars[sym]) $(\"#rvMeta\").textContent = \"加载K线…\";", "  if (!S.bars[sym]) $(\"#rvMeta\").textContent = \"加载K线…(首次几秒)\";")
sub("static/best.js", "  charts(); bind();\n  try {\n    const r = await fetch('/api/optimal');",
    "  charts(); bind();\n  $('#rvMeta').textContent = '加载数据…(首次几秒)'; getBars('BTCUSDT').catch(() => {});   // 2026-10-19 主数据和K线同时下载\n  try {\n    const r = await fetch('/api/optimal');")
h = open(f"{U}/static/best.html", encoding="utf-8").read()
h = re.sub(r"best\.js\?v=(\w+)", lambda m: f"best.js?v={m.group(1)}h", h)
open(f"{U}/static/best.html", "w", encoding="utf-8").write(h); print("ok")

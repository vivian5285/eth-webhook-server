# -*- coding: utf-8 -*-
"""独角兽量化 — 后端（Flask）。Python做行情/指标/复盘/模拟盘/交易所适配，前端用图表库绘制。"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory, session

from engine import backtest, data, exchange, paper, stocks, ta

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(HERE, "static"), static_url_path="/static")
app.secret_key = os.environ.get("UQ_SECRET") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=86400 * 7)
PASSWORD = os.environ.get("UQ_PASSWORD", "")
_bt_sem = threading.Semaphore(2)
_fails: dict = {}
_bt_cache: dict = {}
_bt_lock = threading.Lock()

MTF_NAME = {"240": "4h", "D": "1d", "1D": "1d", "60": "1h", "720": "12h"}


def _hist(bars):
    return backtest.Hist(lambda sy, iv, n: data.get_klines(sy, iv, n, closed_only=True), bars[-1]["t"] - bars[0]["t"])


def api(fn):
    @wraps(fn)
    def w(*a, **kw):
        if not session.get("ok"):
            return jsonify(error="未登录"), 401
        try:
            return fn(*a, **kw)
        except (ValueError, KeyError) as e:
            return jsonify(error=str(e)), 400
        except PermissionError as e:
            return jsonify(error=str(e)), 403
        except Exception as e:
            app.logger.exception("api error")
            return jsonify(error=f"{type(e).__name__}: {str(e)[:200]}"), 500
    return w


@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "DENY"
    if request.path.startswith("/api/"):
        r.headers["Cache-Control"] = "no-store"
    return r


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------- 登录 ----------
@app.post("/api/login")
def login():
    ip = request.headers.get("X-Real-IP") or request.remote_addr
    n, t0 = _fails.get(ip, (0, time.time()))
    if time.time() - t0 > 600:
        n, t0 = 0, time.time()
    if n >= 8:
        return jsonify(error="尝试过多，请10分钟后再试"), 429
    pw = (request.get_json(silent=True) or {}).get("password", "")
    if PASSWORD and hmac.compare_digest(pw.encode(), PASSWORD.encode()):
        session.permanent = True
        session["ok"] = True
        _fails.pop(ip, None)
        return jsonify(ok=True)
    _fails[ip] = (n + 1, t0)
    return jsonify(error="密码错误"), 401


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    return jsonify(ok=bool(session.get("ok")), configured=bool(PASSWORD))


# ---------- 行情/指标 ----------
@app.get("/api/symbols")
@api
def symbols():
    return jsonify(symbols=data.symbols(), intervals=data.INTERVALS)


@app.get("/api/tickers")
@api
def tickers():
    return jsonify(data.tickers_24h())


def _pack(inds, bars):
    out = []
    for spec in [s for s in inds.split(",") if s]:
        for item in ta.compute(spec, bars):
            def series(arr):
                return [[bars[i]["t"], round(v, 8)] for i, v in enumerate(arr) if v is not None]
            o = {"id": item["id"], "pane": item["pane"], "name": item["name"],
                 "lines": {k: series(v) for k, v in item["lines"].items()}}
            if "hist" in item:
                o["hist"] = series(item["hist"])
            if "dirs" in item:
                o["dirs"] = [[bars[i]["t"], d] for i, d in enumerate(item["dirs"]) if d]
            out.append(o)
    return out


@app.get("/api/chart")
@api
def chart():
    sym = request.args.get("symbol", "BTCUSDT")
    itv = request.args.get("interval", "4h")
    limit = int(request.args.get("limit", 500))
    bars = data.get_klines(sym, itv, limit)
    return jsonify(symbol=sym.upper(), interval=itv, bars=bars,
                   indicators=_pack(request.args.get("ind", ""), bars))


@app.get("/api/bar")
@api
def last_bars():
    sym = request.args.get("symbol", "BTCUSDT")
    itv = request.args.get("interval", "4h")
    return jsonify(bars=data.get_klines(sym, itv, 10)[-2:])


# ---------- 策略/复盘 ----------
@app.get("/api/strategies")
@api
def strategies():
    return jsonify(strategies=backtest.list_strategies())


@app.post("/api/backtest")
@api
def run_backtest():
    b = request.get_json(silent=True) or {}
    sym, itv, name = b.get("symbol", "BTCUSDT"), b.get("interval", "4h"), b.get("strategy", "dual_ema_band")
    limit = max(100, min(int(b.get("limit", 1500)), 6000))
    lev = float(b.get("leverage", 1))
    if not (0 < lev <= 20):
        raise ValueError("杠杆范围 0-20x")
    bars = data.get_klines(sym, itv, limit, closed_only=True)
    if len(bars) < 100:
        raise ValueError("K线不足，无法复盘")
    mtf = None
    if name not in backtest.BUILTIN and not stocks.is_stock(sym):
        names = {"240", "D"}  # 多周期策略常用的4H/日线，默认都给
        try:
            from strategy_engine.symbol_registry import get_symbol_config
            cfg = get_symbol_config(sym).get("mtf") or {}
            names |= set(cfg.keys() if isinstance(cfg, dict) else cfg)
        except Exception:
            pass
        mtf = {}
        for n in names:
            iv = MTF_NAME.get(str(n), str(n))
            if iv in data.INTERVALS:
                try:
                    mtf[n] = data.get_klines(sym, iv, 1500, closed_only=True)
                except Exception:
                    pass
    key = (sym.upper(), itv, name, json.dumps(b.get("params") or None, sort_keys=True), limit, lev, bool(b.get("long_only")),
           float(b.get("equity", 10000)), bars[-1]["t"])
    with _bt_lock:
        hit = _bt_cache.get(key)
    if hit and time.time() - hit[0] < 300:
        return jsonify(hit[1])
    with _bt_sem:
        res = backtest.run(bars, name, b.get("params") or None, mtf, float(b.get("equity", 10000)), lev,
                           window=260 if name in backtest.BUILTIN else 600, symbol=sym, hist=_hist(bars),
                           long_only=bool(b.get("long_only")))
    res["symbol"], res["interval"], res["strategy"] = sym.upper(), itv, name
    res["range"] = [bars[0]["t"], bars[-1]["t"]]
    with _bt_lock:
        _bt_cache[key] = (time.time(), res)
        if len(_bt_cache) > 60:
            for k in sorted(_bt_cache, key=lambda k: _bt_cache[k][0])[:20]:
                _bt_cache.pop(k, None)
    return jsonify(res)


@app.post("/api/compare")
@api
def compare():
    """同一品种+周期下批量跑多个策略，返回统计对比（最多10个）。"""
    b = request.get_json(silent=True) or {}
    sym, itv = b.get("symbol", "BTCUSDT"), b.get("interval", "4h")
    names = [n for n in (b.get("strategies") or [])][:10]
    limit = max(100, min(int(b.get("limit", 1500)), 6000))
    bars = data.get_klines(sym, itv, limit, closed_only=True)
    rows = []
    with _bt_sem:
        for n in names:
            try:
                p = backtest.BUILTIN[n]["params"] if n in backtest.BUILTIN else None
                r = backtest.run(bars, n, p, None, 10000, 1.0)
                rows.append({"id": n, "stats": r["stats"]})
            except Exception as e:
                rows.append({"id": n, "error": str(e)[:80]})
    return jsonify(rows=rows, symbol=sym.upper(), interval=itv)


# ---------- 全库扫描（后台子进程，结果落盘，多worker共享） ----------
SWEEP_DIR = os.path.join(HERE, "sweeps")


def _sweep_path(sym, itv, limit):
    os.makedirs(SWEEP_DIR, exist_ok=True)
    return os.path.join(SWEEP_DIR, f"{sym.upper()}_{itv}_{limit}.json")


def _read_sweep(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return None
    if st.get("status") == "running" and time.time() - st.get("updated", 0) > 180:
        st["status"] = "stalled"
    return st


@app.post("/api/sweep/start")
@api
def sweep_start():
    import subprocess
    import sys
    b = request.get_json(silent=True) or {}
    sym, itv = b.get("symbol", "BTCUSDT").upper(), b.get("interval", "4h")
    limit = max(300, min(int(b.get("limit", 1000)), 3000))
    path = _sweep_path(sym, itv, limit)
    cur = _read_sweep(path)
    if cur and cur.get("status") == "running":
        return jsonify(cur)
    running = [f for f in os.listdir(SWEEP_DIR) if f.endswith(".json") and (_read_sweep(os.path.join(SWEEP_DIR, f)) or {}).get("status") == "running"]
    if running:
        raise ValueError("已有一个扫描在运行，请等它结束")
    subprocess.Popen([sys.executable, os.path.join(HERE, "sweep.py"), sym, itv, str(limit)], cwd=HERE,
                     stdout=subprocess.DEVNULL, stderr=open(os.path.join(SWEEP_DIR, "sweep.err"), "a"),
                     start_new_session=True)
    time.sleep(0.6)
    return jsonify(_read_sweep(path) or {"status": "starting", "rows": [], "done": 0, "total": 0})


@app.get("/api/sweep/status")
@api
def sweep_status():
    sym, itv = request.args.get("symbol", "BTCUSDT").upper(), request.args.get("interval", "4h")
    limit = int(request.args.get("limit", 1000))
    return jsonify(_read_sweep(_sweep_path(sym, itv, limit)) or {"status": "none", "rows": [], "done": 0, "total": 0})


@app.get("/api/stocks")
@api
def stock_list():
    from concurrent.futures import ThreadPoolExecutor
    syms = list(stocks.SYMBOLS)
    with ThreadPoolExecutor(6) as ex:
        qs = list(ex.map(stocks.quote, syms))
    return jsonify(stocks=[{"symbol": k, **stocks.SYMBOLS[k], **(q or {})} for k, q in zip(syms, qs)],
                   intervals=stocks.INTERVALS)


def _matrix_path(itv, limit):
    os.makedirs(SWEEP_DIR, exist_ok=True)
    return os.path.join(SWEEP_DIR, f"US_{itv}_{limit}.json")


@app.post("/api/matrix/start")
@api
def matrix_start():
    import subprocess
    import sys
    b = request.get_json(silent=True) or {}
    itv = b.get("interval", "1d")
    if itv not in stocks.INTERVALS:
        raise ValueError("周期不支持")
    limit = max(300, min(int(b.get("limit", 1260)), 3000))
    syms = ",".join(x.upper() for x in (b.get("symbols") or []))
    path = _matrix_path(itv, limit)
    cur = _read_sweep(path)
    if cur and cur.get("status") == "running":
        return jsonify({k: v for k, v in cur.items() if k != "rows"})
    for f in os.listdir(SWEEP_DIR):
        if f.endswith(".json") and (_read_sweep(os.path.join(SWEEP_DIR, f)) or {}).get("status") == "running":
            raise ValueError("已有一个扫描在运行，请等它结束")
    subprocess.Popen([sys.executable, os.path.join(HERE, "sweep_stocks.py"), itv, str(limit), syms], cwd=HERE,
                     stdout=subprocess.DEVNULL, stderr=open(os.path.join(SWEEP_DIR, "sweep.err"), "a"),
                     start_new_session=True)
    time.sleep(0.8)
    cur = _read_sweep(path) or {"status": "starting", "done": 0, "total": 0}
    return jsonify({k: v for k, v in cur.items() if k != "rows"})


@app.get("/api/matrix/status")
@api
def matrix_status():
    itv, limit = request.args.get("interval", "1d"), int(request.args.get("limit", 1260))
    st = _read_sweep(_matrix_path(itv, limit)) or {"status": "none", "rows": [], "done": 0, "total": 0}
    if request.args.get("lite"):
        st = {k: v for k, v in st.items() if k != "rows"}
    return jsonify(st)


# ---------- 模拟盘 ----------
@app.get("/api/paper")
@api
def paper_state():
    return jsonify(paper.snapshot())


@app.post("/api/paper/open")
@api
def paper_open():
    b = request.get_json(silent=True) or {}
    if stocks.is_stock(b.get("symbol", "")):
        raise ValueError("美股仅支持看图与回测，暂不支持下单")
    r = paper.open_pos(b["symbol"], b["side"], float(b["usdt"]), float(b.get("leverage", 1)))
    return jsonify(r)


@app.post("/api/paper/close")
@api
def paper_close():
    return jsonify(paper.close_pos((request.get_json(silent=True) or {})["symbol"]))


@app.post("/api/paper/reset")
@api
def paper_reset():
    paper.reset()
    return jsonify(ok=True)


# ---------- 交易所 ----------
@app.get("/api/exchange/status")
@api
def ex_status():
    return jsonify(exchange.status())


@app.post("/api/exchange/order")
@api
def ex_order():
    b = request.get_json(silent=True) or {}
    if stocks.is_stock(b.get("symbol", "")):
        raise ValueError("美股仅支持看图与回测，暂不支持下单")
    return jsonify(exchange.place_market(b["symbol"], b["side"], float(b["usdt"]), float(b.get("leverage", 1)),
                                         bool(b.get("confirm"))))


if __name__ == "__main__":
    app.run("127.0.0.1", int(os.environ.get("PORT", 8890)), debug=False)

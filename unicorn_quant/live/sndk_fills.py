# -*- coding: utf-8 -*-
"""2026-11-01 SNDK单票方案 C账户实盘成交导出(只读)，给 8890 /sndk 复盘页和回测并排对照。

以 binanceC 身份运行(root crontab 每 10 分钟 sudo -u binanceC)，只调 GET 接口：userTrades / allOrders / account / positionRisk，
不下单、不 import 执行器(执行器 import 时会初始化客户端)。从 ARMED 时间起拉 SNDKUSDT 全部成交(交易所止损单触发的成交也在里面)，
按订单合并、再配对成一笔笔开平仓，写 /var/lib/sndk_solo/live_C.json(只有成交价/数量/手续费/盈亏，没有密钥)。
"""
import hashlib
import hmac
import json
import os
import time
import urllib.parse
import urllib.request

SYM = "SNDKUSDT"
ENV_FILE = os.environ.get("SNDK_SOLO_ENV_FILE", "/home/binanceC/binance-engine/.env")
STATE = "/home/binanceC/sndk_solo/state.json"
STATUS = "/home/binanceC/sndk_solo/status.json"
OUT_DIR = "/var/lib/sndk_solo"
OUT = os.path.join(OUT_DIR, "live_C.json")
WEEK = 7 * 86400000


def load_env(path):
    env = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env(ENV_FILE)


def get(path, **p):
    p["timestamp"] = int(time.time() * 1000); p["recvWindow"] = 10000
    q = urllib.parse.urlencode(p); sig = hmac.new(ENV["BINANCE_API_SECRET"].encode(), q.encode(), hashlib.sha256).hexdigest()
    r = urllib.request.Request(f"https://fapi.binance.com{path}?{q}&signature={sig}", headers={"X-MBX-APIKEY": ENV["BINANCE_API_KEY"]})
    return json.load(urllib.request.urlopen(r, timeout=20))


def rd(p, d=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return d


def windows(a, z):
    while a < z:
        yield a, min(a + WEEK - 1, z); a += WEEK


def main():
    st = rd(STATE, {}) or {}; armed = int(st.get("armed_at") or 0)
    now = int(time.time() * 1000)
    since = armed or now - WEEK
    fills, orders = [], {}
    for a, z in windows(since, now):
        fills += get("/fapi/v1/userTrades", symbol=SYM, startTime=a, endTime=z, limit=1000)
        for o in get("/fapi/v1/allOrders", symbol=SYM, startTime=a, endTime=z, limit=1000):
            orders[o["orderId"]] = o
        time.sleep(0.2)
    fills.sort(key=lambda t: (t["time"], t["id"]))
    # 按订单合并成"一次成交"
    ex = {}
    for t in fills:
        e = ex.setdefault(t["orderId"], {"order_id": t["orderId"], "t": t["time"], "side": t["side"], "pos_side": t.get("positionSide"),
                                          "qty": 0.0, "notional": 0.0, "fee": 0.0, "pnl": 0.0, "maker": 0, "n": 0})
        q, px = float(t["qty"]), float(t["price"])
        e["qty"] += q; e["notional"] += q * px; e["fee"] += float(t["commission"]); e["pnl"] += float(t["realizedPnl"])
        e["maker"] += 1 if t.get("maker") else 0; e["n"] += 1; e["t_last"] = t["time"]
    execs = []
    for e in sorted(ex.values(), key=lambda x: x["t"]):
        o = orders.get(e["order_id"], {})
        cid = o.get("clientOrderId", "")
        e.update(avg=round(e["notional"] / e["qty"], 4) if e["qty"] else None, qty=round(e["qty"], 6), fee=round(e["fee"], 6), pnl=round(e["pnl"], 6),
                 type=o.get("origType") or o.get("type"), client_id=cid, ours=cid.startswith("ss_") or (o.get("origType") or "").startswith("STOP"))
        e.pop("notional", None); execs.append(e)
    # 配对成开平仓(只做多：BUY 开 / SELL 平；双向持仓模式下 positionSide=LONG)
    trades, cur = [], None
    for e in execs:
        if e["side"] == "BUY":
            if cur is None:
                cur = {"in_t": e["t"], "in_qty": 0.0, "in_cost": 0.0, "out_qty": 0.0, "out_cost": 0.0, "fee": 0.0, "pnl": 0.0, "execs": []}
            cur["in_qty"] += e["qty"]; cur["in_cost"] += e["qty"] * (e["avg"] or 0)
        else:
            if cur is None:
                continue
            cur["out_qty"] += e["qty"]; cur["out_cost"] += e["qty"] * (e["avg"] or 0); cur["out_t"] = e["t_last"]; cur["out_type"] = e["type"]
        cur["fee"] += e["fee"]; cur["pnl"] += e["pnl"]; cur["execs"].append({k: e[k] for k in ("t", "side", "qty", "avg", "fee", "maker", "n", "type", "ours")})
        if cur["out_qty"] >= cur["in_qty"] - 1e-9 and cur["out_qty"] > 0:
            trades.append(cur); cur = None
    for t in trades + ([cur] if cur else []):
        t["in_avg"] = round(t["in_cost"] / t["in_qty"], 4) if t["in_qty"] else None
        t["out_avg"] = round(t["out_cost"] / t["out_qty"], 4) if t["out_qty"] else None
        t["open"] = t is cur
        for k in ("in_cost", "out_cost"):
            t.pop(k, None)
        t["fee"] = round(t["fee"], 6); t["pnl"] = round(t["pnl"], 6)
    acc = get("/fapi/v2/account")
    pos = [p for p in get("/fapi/v2/positionRisk", symbol=SYM) if float(p["positionAmt"]) != 0]
    status = rd(STATUS, {}) or {}
    res = {"ts": int(time.time()), "account": "C", "symbol": SYM, "armed_at": armed,
           "equity": round(float(acc["totalMarginBalance"]), 4), "arm_equity": (st.get("arm_base") or {}).get("equity"),
           "position": [{"side": p.get("positionSide"), "qty": float(p["positionAmt"]), "entry": float(p["entryPrice"]), "mark": float(p["markPrice"]),
                         "upnl": float(p["unRealizedProfit"])} for p in pos],
           "stop_px": status.get("stop_px"), "mode": status.get("mode"), "version": status.get("version"),
           "trades": trades + ([cur] if cur else []), "execs": execs,
           "foreign_execs": [e for e in execs if not e["ours"]]}
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT + ".tmp", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    os.chmod(OUT + ".tmp", 0o644); os.replace(OUT + ".tmp", OUT)
    print(f"ok {len(execs)} execs, {len(res['trades'])} trades, equity {res['equity']}")


if __name__ == "__main__":
    main()

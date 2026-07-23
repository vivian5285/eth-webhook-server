#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
20U 名义 · ETH/XAU · LONG/SHORT 生产内测矩阵（v15.7.2-breath-lock）
- soft-cap ~20U（不低于交易所 minNotional）
- 校验：永久硬止损×1.2、雷达共存、TP1/TP2、洁净平仓
- 校验：XAU 冷启动系数 ≈0.675（状态 breathing_coefficient）
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.request
from collections import Counter

from binance_client import (
    binance_client as c,
    is_orders_query_failed,
    is_position_query_failed,
)

SECRET = "528586"
WEBHOOK = "http://127.0.0.1:5003/webhook"
TARGET_NOTIONAL = 20.0
OUT = f"logs/live_20u_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
RESULTS = {"steps": [], "pass": True, "errors": [], "cycles": []}


def log(msg, **kw):
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "msg": msg, **kw}
    RESULTS["steps"].append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


def fail(msg, **kw):
    RESULTS["pass"] = False
    RESULTS["errors"].append(msg)
    log("FAIL: " + msg, **kw)


def secret():
    try:
        for line in open(".env", encoding="utf-8"):
            if line.startswith("WEBHOOK_SECRET="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return SECRET


def post(payload, timeout=90):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        WEBHOOK, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode()[:800]
        log("HTTP", status=r.status, body=body, action=payload.get("action"), symbol=payload.get("symbol"))
        return r.status, body


def filters(sym):
    info = c._load_symbol_filters(sym) or {}
    out = {"step": 0.001, "min_qty": 0.001, "tick": 0.01, "min_notional": 5.0}
    for f in info.get("filters") or []:
        ft = f.get("filterType")
        if ft in ("LOT_SIZE", "MARKET_LOT_SIZE"):
            out["step"] = float(f.get("stepSize") or out["step"])
            out["min_qty"] = float(f.get("minQty") or out["min_qty"])
        elif ft == "PRICE_FILTER":
            out["tick"] = float(f.get("tickSize") or out["tick"])
        elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
            out["min_notional"] = float(
                f.get("notional") or f.get("minNotional") or out["min_notional"]
            )
    return out


def round_step(q, step):
    step = float(step or 0.001)
    n = math.ceil(float(q) / step - 1e-12) * step
    prec = max(0, min(8, int(round(-math.log10(step))))) if step < 1 else 0
    return round(n, prec)


def qty_for_20u(sym, px, flt):
    """目标名义 20U；不低于交易所 minNotional / minQty。"""
    floor_n = max(float(flt["min_notional"]) * 1.02, TARGET_NOTIONAL)
    q = max(flt["min_qty"], floor_n / max(px, 1e-9))
    return round_step(q, flt["step"])


def atr_guess(sym, px):
    if sym.startswith("XAU"):
        return max(3.0, round(px * 0.005, 2))
    return max(8.0, round(px * 0.012, 2))


def amt(sym):
    pos = c.get_position(sym, prefer_ws=False)
    if is_position_query_failed(pos):
        raise RuntimeError(f"QUERY_FAILED {sym}")
    return float((pos or {}).get("positionAmt") or 0)


def abs_amt(sym):
    return abs(amt(sym))


def load_state(sym):
    path = f"binance_vps_state_{sym}.json"
    if not os.path.isfile(path):
        return {}
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


def audit_orders(sym):
    raw = c.get_open_orders(sym, include_algo=True)
    if is_orders_query_failed(raw):
        return {"ok": False, "err": "ORDERS_QUERY_FAILED", "limits": 0, "stops": 0, "dups": [], "total": -1}
    limits, stops = [], []
    for o in raw or []:
        ot = str(o.get("type") or o.get("orderType") or "").upper()
        px = o.get("price") or o.get("stopPrice") or o.get("triggerPrice") or 0
        try:
            px = round(float(px or 0), 2)
        except Exception:
            px = 0.0
        side = o.get("side")
        qty = float(o.get("origQty") or o.get("quantity") or 0)
        oid = o.get("orderId") or o.get("algoId")
        if ot == "LIMIT":
            limits.append((side, px, qty, oid))
        elif "STOP" in ot:
            stops.append((side, px, qty, oid, ot))
    dups = []
    for k, n in Counter([(s, p) for s, p, *_ in limits]).items():
        if n > 1:
            dups.append(("LIMIT", k, n))
    for k, n in Counter([(s, p) for s, p, *_ in stops]).items():
        if n > 1:
            dups.append(("STOP", k, n))
    ok = len(dups) == 0 and 1 <= len(stops) <= 2 and 1 <= len(limits) <= 3 and len(raw or []) < 10
    return {
        "ok": ok, "err": "", "limits": len(limits), "stops": len(stops),
        "dups": dups, "limit_detail": limits, "stop_detail": stops, "total": len(raw or []),
    }


def ensure_flat(sym, reason):
    a = abs_amt(sym)
    orders = c.get_open_orders(sym, include_algo=True)
    n = -1 if is_orders_query_failed(orders) else len(orders or [])
    if a <= 0 and n == 0:
        return True
    log("CLEANUP", symbol=sym, amt=a, orders=n, reason=reason)
    try:
        c.cancel_all_open_orders(sym)
    except Exception as e:
        log("cancel_err", symbol=sym, err=str(e))
    if a > 0:
        px = float(c.get_current_price(sym, prefer_ws=False) or 0)
        post({
            "action": "CLOSE_QUICK_EXIT", "symbol": sym, "ticker": sym,
            "price": px, "secret": secret(), "reason": reason,
            "bar_index": int(time.time()), "seq": 99,
        })
        time.sleep(6)
    time.sleep(2)
    a2 = abs_amt(sym)
    o2 = c.get_open_orders(sym, include_algo=True)
    n2 = -1 if is_orders_query_failed(o2) else len(o2 or [])
    if a2 > 0 or n2 > 0:
        fail(f"still dirty {sym}", amt=a2, orders=n2)
        return False
    return True


def hard_sl_expected(side, entry, tv_sl):
    dist = abs(float(entry) - float(tv_sl)) * 1.2
    if side == "LONG":
        return round(entry - dist, 2)
    return round(entry + dist, 2)


def wait_position(sym, want_side, timeout=100):
    t0 = time.time()
    while time.time() - t0 < timeout:
        a = amt(sym)
        side = "LONG" if a > 0 else ("SHORT" if a < 0 else "FLAT")
        au = audit_orders(sym)
        st = load_state(sym)
        log(
            "WAIT", symbol=sym, amt=a, side=side,
            stops=au.get("stops"), limits=au.get("limits"), dups=au.get("dups"),
            scenario=st.get("atr_scenario"), frozen=st.get("frozen_hard_sl_px"),
            radar=st.get("current_sl"), open_atr=st.get("open_atr"),
            breath=st.get("breathing_coefficient"),
        )
        if side == want_side and au.get("ok") and au.get("stops", 0) >= 1 and au.get("limits", 0) >= 1:
            if not au.get("dups") and au.get("total", 99) < 10:
                return True, au, st
        if au.get("dups") or (au.get("total") or 0) > 15:
            fail("duplicate/spam orders", symbol=sym, audit=au)
            return False, au, st
        time.sleep(5)
    return False, audit_orders(sym), load_state(sym)


def open_payload(sym, side, bar, seq, reason):
    px = float(c.get_current_price(sym, prefer_ws=False) or 0)
    flt = filters(sym)
    qty = qty_for_20u(sym, px, flt)
    atr = atr_guess(sym, px)
    bias = 0.12 if sym.startswith("ETH") else 0.06
    sig = round(px + (bias if side == "LONG" else -bias), 2)
    if side == "LONG":
        sl = round(sig - 1.5 * atr, 2)
        tp1 = round(sig + 1.35 * atr, 2)
        tp2 = round(sig + 2.5 * atr, 2)
        tp3 = round(sig + 3.5 * atr, 2)
    else:
        sl = round(sig + 1.5 * atr, 2)
        tp1 = round(sig - 1.35 * atr, 2)
        tp2 = round(sig - 2.5 * atr, 2)
        tp3 = round(sig - 3.5 * atr, 2)
    return {
        "action": side, "symbol": sym, "ticker": sym, "price": sig,
        "qty": qty, "atr": atr, "stop_loss": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "secret": secret(), "reason": reason, "bar_index": bar, "seq": seq, "leverage": 5,
    }, {"px": px, "sig": sig, "qty": qty, "atr": atr, "sl": sl, "tp1": tp1, "tp2": tp2,
        "notional": round(qty * px, 2)}


def verify_defense(sym, side, plan, st, au):
    entry = float(st.get("watched_entry") or plan["sig"] or 0)
    frozen = float(st.get("frozen_hard_sl_px") or 0)
    expect = hard_sl_expected(side, entry, plan["sl"])
    ok = True
    if frozen <= 0:
        fail("no frozen hard sl", symbol=sym)
        ok = False
    elif abs(frozen - expect) > 1.5:
        fail("hard sl mismatch", symbol=sym, frozen=frozen, expect=expect)
        ok = False
    else:
        log("HARD_SL_OK", symbol=sym, side=side, frozen=frozen, expect=expect)
    if side == "LONG" and frozen >= entry:
        fail("LONG hard SL must be below entry", frozen=frozen, entry=entry)
        ok = False
    if side == "SHORT" and frozen <= entry:
        fail("SHORT hard SL must be above entry", frozen=frozen, entry=entry)
        ok = False
    stops, limits = au.get("stops", 0), au.get("limits", 0)
    if not (1 <= stops <= 2):
        fail("stop count bad", symbol=sym, stops=stops)
        ok = False
    if not (1 <= limits <= 3):
        fail("TP limit count bad", symbol=sym, limits=limits)
        ok = False
    if au.get("dups"):
        fail("duplicate orders", symbol=sym, dups=au["dups"])
        ok = False
    breath = float(st.get("breathing_coefficient") or 0)
    if sym.startswith("XAU") and breath > 0:
        # 冷启动或近冷启动应贴近 0.675；允许采样后漂移
        if abs(breath - 0.675) > 0.35 and not (0.5 <= breath <= 1.2):
            fail("XAU breath coeff out of lock range", breath=breath)
            ok = False
        else:
            log("XAU_BREATH_OK", breath=breath, cold=0.675)
    if sym.startswith("ETH") and breath > 0:
        if not (1.2 <= breath <= 2.5):
            fail("ETH breath coeff out of lock range", breath=breath)
            ok = False
        else:
            log("ETH_BREATH_OK", breath=breath, cold=1.525)
    live_notional = abs(amt(sym)) * float(st.get("watched_entry") or plan["px"] or 0)
    log("NOTIONAL", symbol=sym, planned=plan["notional"], live=round(live_notional, 2))
    if live_notional > 45:
        fail("notional too large for 20U smoke", live=live_notional)
        ok = False
    return ok


def close_sym(sym, reason, bar, seq=9):
    px = float(c.get_current_price(sym, prefer_ws=False) or 0)
    post({
        "action": "CLOSE_QUICK_EXIT", "symbol": sym, "ticker": sym,
        "price": px, "secret": secret(), "reason": reason,
        "bar_index": bar, "seq": seq,
    })
    time.sleep(8)
    a = abs_amt(sym)
    o = c.get_open_orders(sym, include_algo=True)
    n = -1 if is_orders_query_failed(o) else len(o or [])
    if a > 0 or n > 0:
        try:
            c.cancel_all_open_orders(sym)
        except Exception:
            pass
        fail("close not sterile", symbol=sym, amt=a, orders=n)
        return False
    log("FLAT_OK", symbol=sym)
    return True


def run_cycle(sym, side, tag):
    if not ensure_flat(sym, f"PREFLAT_{tag}"):
        RESULTS["cycles"].append({"tag": tag, "ok": False})
        return False
    bar = int(time.time())
    payload, plan = open_payload(sym, side, bar, 1, f"LIVE20U_{tag}")
    log("OPEN_PLAN", symbol=sym, side=side, plan=plan)
    post(payload)
    ok, au, st = wait_position(sym, side, timeout=100)
    if not ok:
        fail(f"open wait fail {tag}", audit=au)
        ensure_flat(sym, f"ABORT_{tag}")
        RESULTS["cycles"].append({"tag": tag, "ok": False})
        return False
    if not verify_defense(sym, side, plan, st, au):
        ensure_flat(sym, f"ABORT_DEF_{tag}")
        RESULTS["cycles"].append({"tag": tag, "ok": False})
        return False
    time.sleep(10)
    au2 = audit_orders(sym)
    if au2.get("dups") or (au2.get("total") or 0) > 10:
        fail("hold dup/spam", symbol=sym, audit=au2)
        ensure_flat(sym, f"ABORT_HOLD_{tag}")
        RESULTS["cycles"].append({"tag": tag, "ok": False})
        return False
    closed = close_sym(sym, f"CLOSE_{tag}", bar + 1)
    RESULTS["cycles"].append({"tag": tag, "ok": closed, "notional": plan["notional"], "stops": au.get("stops"), "limits": au.get("limits")})
    return closed


def main():
    os.makedirs("logs", exist_ok=True)
    log("LIVE20U_START", version="v15.7.2-breath-lock", target_notional=TARGET_NOTIONAL)
    for sym in ("ETHUSDT", "XAUUSDT"):
        if not ensure_flat(sym, f"PREFLAT_ALL_{sym}"):
            RESULTS["pass"] = False
            break
    else:
        log("COOLDOWN_25s")
        time.sleep(25)
        matrix = (
            ("ETHUSDT", "LONG", "ETH_LONG"),
            ("ETHUSDT", "SHORT", "ETH_SHORT"),
            ("XAUUSDT", "LONG", "XAU_LONG"),
            ("XAUUSDT", "SHORT", "XAU_SHORT"),
        )
        for sym, side, tag in matrix:
            run_cycle(sym, side, tag)
            time.sleep(18)

    for sym in ("ETHUSDT", "XAUUSDT"):
        ensure_flat(sym, f"FINAL_FLAT_{sym}")

    log("LIVE20U_END", passed=RESULTS["pass"], errors=RESULTS["errors"], cycles=RESULTS["cycles"])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(RESULTS, f, ensure_ascii=False, indent=2)
    print("OUT=", OUT)
    print("PASS=", RESULTS["pass"])
    raise SystemExit(0 if RESULTS["pass"] else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v15.9.4 防线就绪 + 雷达强制激活(双STOP) + 重入引擎实盘烟测（~20U）

验证点（弥补「开平几十秒」矩阵未覆盖的部分）：
1) 开仓后：硬止损 + TP123(limits=3) + 雷达休眠(activated=False, frac=0.50)
2) POST /admin/smoke_arm_radar → 主进程挂雷达 STOP → stops≥2，无叠单，total≤5
3) 实盘拉 5m K 线跑双保险再入价（ETH/XAU×多空）+ 档位递进纯函数
4) 平仓无菌

说明：强制激活走主进程 admin 接口，避免外部脚本双写军师状态。
自然雷达扫出→限价再入→成交的完整闭环由 test_radar_reentry 覆盖；
本烟测证明「开仓防线 + 激活双 STOP + 重入定价/档位」。

环境：VPS binance-engine，密钥可用。
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.request

from binance_client import (
    binance_client as c,
    is_orders_query_failed,
    is_position_query_failed,
)
from reentry_profiles import (
    ACTIVATION_FRACS,
    get_reentry_profile,
    is_better_than_tv,
    next_activation_frac,
    tier_coeffs,
)
from smart_reentry_engine import bump_after_reentry_fill, init_cycle_on_open, plan_reentry_limit

SECRET = "528586"
WEBHOOK = "http://127.0.0.1:5003/webhook"
TARGET_NOTIONAL = 20.0
OUT = f"logs/live_radar_smoke_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
R = {"steps": [], "pass": True, "errors": []}


def log(msg, **kw):
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "msg": msg, **kw}
    R["steps"].append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


def fail(msg, **kw):
    R["pass"] = False
    R["errors"].append(msg)
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
        log(
            "HTTP",
            status=r.status,
            body=body,
            action=payload.get("action"),
            symbol=payload.get("symbol"),
        )
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


def qty_20u(sym, px, flt):
    floor_n = max(float(flt["min_notional"]) * 1.02, TARGET_NOTIONAL)
    q = max(flt["min_qty"], floor_n / max(px, 1e-9))
    return round_step(q, flt["step"])


def atr_guess(sym, px):
    if sym.startswith("XAU"):
        return max(3.0, round(px * 0.005, 2))
    return max(8.0, round(px * 0.012, 2))


def abs_amt(sym):
    pos = c.get_position(sym, prefer_ws=False)
    if is_position_query_failed(pos):
        raise RuntimeError(f"QUERY_FAILED {sym}")
    return abs(float((pos or {}).get("positionAmt") or 0))


def audit(sym):
    book = c.get_open_orders(sym, include_algo=True)
    if is_orders_query_failed(book):
        return {
            "ok": False,
            "err": "ORDERS_QUERY_FAILED",
            "limits": 0,
            "stops": 0,
            "dups": [],
            "total": -1,
        }
    limits, stops, prices = [], [], []
    for o in book or []:
        if not isinstance(o, dict):
            continue
        typ = str(o.get("type") or o.get("orderType") or "").upper()
        px = float(o.get("price") or o.get("stopPrice") or o.get("triggerPrice") or 0)
        if "LIMIT" in typ:
            limits.append(o)
            prices.append(("L", round(px, 4)))
        elif "STOP" in typ:
            stops.append(o)
            prices.append(("S", round(px, 4)))
    dups = [k for k, n in __import__("collections").Counter(prices).items() if n > 1]
    return {
        "ok": True,
        "limits": len(limits),
        "stops": len(stops),
        "total": len(book or []),
        "dups": dups,
    }


def load_state(sym):
    path = f"binance_vps_state_{sym}.json"
    if not os.path.isfile(path):
        return {}
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


def ensure_flat(sym, tag):
    for _ in range(8):
        a = abs_amt(sym)
        au = audit(sym)
        if a <= 0 and au.get("ok") and au.get("total", 0) == 0:
            log("FLAT_OK", symbol=sym, tag=tag)
            return True
        try:
            post(
                {
                    "action": "CLOSE_QUICK_EXIT",
                    "symbol": sym,
                    "price": float(c.get_current_price(sym) or 0),
                    "secret": secret(),
                    "reason": tag,
                    "bar_index": int(time.time()),
                }
            )
        except Exception as e:
            log("CLOSE_POST_ERR", err=str(e))
        time.sleep(3)
        try:
            c.cancel_all_orders(sym)
        except Exception:
            pass
        time.sleep(2)
    fail("not flat", symbol=sym, tag=tag, amt=abs_amt(sym), audit=audit(sym))
    return False


def open_long(sym):
    px = float(c.get_current_price(sym) or 0)
    flt = filters(sym)
    atr = atr_guess(sym, px)
    qty = qty_20u(sym, px, flt)
    sig = round(px * 0.9999, 2)
    sl = round(sig - 1.5 * atr, 2)
    tp1 = round(sig + 1.35 * atr, 2)
    tp2 = round(sig + 2.5 * atr, 2)
    tp3 = round(sig + 3.5 * atr, 2)
    payload = {
        "action": "LONG",
        "symbol": sym,
        "price": sig,
        "qty": qty,
        "atr": atr,
        "stop_loss": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "secret": secret(),
        "reason": "RADAR_SMOKE_ETH_LONG",
        "bar_index": int(time.time()),
        "seq": 1,
        "leverage": 5.0,
    }
    log("OPEN_PLAN", symbol=sym, qty=qty, px=px, sl=sl, tp1=tp1, atr=atr)
    post(payload)
    return payload


def wait_defense(sym, timeout=45):
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout:
        a = abs_amt(sym)
        au = audit(sym)
        st = load_state(sym)
        live = {
            "radar_pending_arm": st.get("radar_pending_arm"),
            "radar_activated": st.get("radar_activated"),
            "radar_activation_frac": st.get("radar_activation_frac"),
            "frozen_hard_sl_px": st.get("frozen_hard_sl_px"),
            "exit_ownership": st.get("exit_ownership"),
            "reentry_attempt": st.get("reentry_attempt"),
            "initial_stop": st.get("initial_stop"),
        }
        last = {"amt": a, "audit": au, "state": live}
        log("WAIT_DEFENSE", **last)
        if (
            a > 0
            and au.get("ok")
            and au.get("limits") == 3
            and au.get("stops", 0) >= 1
            and not au.get("dups")
            and au.get("total", 99) <= 5
            and float(st.get("frozen_hard_sl_px") or 0) > 0
        ):
            return True, last
        time.sleep(5)
    fail("defense not ready", **last)
    return False, last


def assert_dormant_radar(sym, last):
    """规格：开仓后雷达休眠至 50%×TP1，盘口只有硬止损(+TP123)。"""
    st = (last or {}).get("state") or load_state(sym)
    au = (last or {}).get("audit") or audit(sym)
    frac = float(st.get("radar_activation_frac") or 0)
    pending = st.get("radar_pending_arm")
    activated = bool(st.get("radar_activated"))
    log("DORMANT_CHECK", frac=frac, pending=pending, activated=activated, audit=au)
    if activated:
        fail("radar should be dormant after open", state=st)
        return False
    if frac and abs(frac - 0.50) > 1e-6:
        fail("expected frac 0.50 on first open", frac=frac)
        return False
    if pending is False:
        log("WARN_pending_arm_false_or_missing", pending=pending)
    if int(au.get("stops") or 0) != 1:
        log(
            "STOPS_NOTE",
            stops=au.get("stops"),
            note="1=仅硬止损(休眠)；≥2=已自然激活",
        )
    if int(au.get("limits") or 0) != 3:
        fail("tp123 missing", audit=au)
        return False
    log("DORMANT_OK", frac=frac or 0.50, stops=au.get("stops"), limits=3)
    return True


def hold_observe(sym, seconds=24):
    """持有观察：确认 TP123/硬止损不漂移、无叠单。"""
    t0 = time.time()
    n = 0
    while time.time() - t0 < seconds:
        au = audit(sym)
        st = load_state(sym)
        log(
            "HOLD",
            n=n,
            audit=au,
            pending=st.get("radar_pending_arm"),
            activated=st.get("radar_activated"),
            frac=st.get("radar_activation_frac"),
        )
        if au.get("dups"):
            fail("dups during hold", audit=au)
            return False
        if au.get("total", 0) > 5:
            fail("cap during hold", audit=au)
            return False
        if au.get("limits") not in (2, 3):
            if abs_amt(sym) > 0 and au.get("limits", 0) < 1:
                fail("limits vanished", audit=au)
                return False
        n += 1
        time.sleep(8)
    return True


def force_arm_via_admin(sym):
    """主进程强制激活雷达（双 STOP）。"""
    url = f"http://127.0.0.1:5003/admin/smoke_arm_radar/{sym}"
    body = json.dumps({"secret": secret()}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            log("SMOKE_ARM_HTTP", status=r.status, body=raw[:600])
            data = json.loads(raw) if raw else {}
    except Exception as e:
        fail("smoke_arm http failed", err=str(e))
        return False
    if not (data.get("armed") or data.get("already_activated") or data.get("radar_activated")):
        fail("smoke_arm rejected", data=data)
        return False
    time.sleep(4)
    au = audit(sym)
    st = load_state(sym)
    log("AFTER_ARM", audit=au, state={
        "radar_activated": st.get("radar_activated"),
        "radar_pending_arm": st.get("radar_pending_arm"),
        "frac": st.get("radar_activation_frac"),
    })
    if au.get("dups"):
        fail("dups after arm", audit=au)
        return False
    if au.get("total", 0) > 5:
        fail("cap after arm", audit=au)
        return False
    if int(au.get("stops") or 0) < 2:
        fail("dual stop missing after arm", audit=au)
        return False
    if int(au.get("limits") or 0) != 3:
        fail("tp123 missing after arm", audit=au)
        return False
    log("DUAL_STOP_OK", stops=au.get("stops"), limits=au.get("limits"), total=au.get("total"))
    return True


def reentry_engine_live_smoke():
    """不挂单：实盘 K 线 + 档位递进纯函数验证。"""
    ok_all = True
    if list(ACTIVATION_FRACS) != [0.50, 0.65, 0.80, 0.90, 0.95]:
        fail("activation_fracs mismatch", fracs=list(ACTIVATION_FRACS))
        ok_all = False
    for sym, side in (
        ("ETHUSDT", "LONG"),
        ("ETHUSDT", "SHORT"),
        ("XAUUSDT", "LONG"),
        ("XAUUSDT", "SHORT"),
    ):
        tv = float(c.get_current_price(sym) or 0)
        atr = atr_guess(sym, tv)
        kl5 = c.fetch_klines(sym, "5m", 12) or []
        plan, reason = plan_reentry_limit(
            side=side,
            tv_price=tv,
            symbol=sym,
            klines_5m=kl5,
            klines_3m=None,
        )
        log("REENTRY_PX", symbol=sym, side=side, tv=tv, plan=plan, reason=reason)
        if not plan:
            log("REENTRY_PX_ABORT_OK", symbol=sym, side=side, reason=reason)
        else:
            px = float(plan.get("limit_px") or 0)
            if not is_better_than_tv(side, px, tv):
                fail("plan not better than tv", symbol=sym, px=px, tv=tv)
                ok_all = False
        rp = get_reentry_profile(sym)
        t0 = tier_coeffs(0, rp)
        t1 = tier_coeffs(1, rp)
        frac1 = next_activation_frac(0.50, 1, rp)
        log(
            "TIER_CHECK",
            symbol=sym,
            t0_early=t0.get("early_be_atr"),
            t1_early=t1.get("early_be_atr"),
            frac1=frac1,
        )
        if sym.startswith("ETH"):
            if abs(float(t0["early_be_atr"]) - 0.50) > 1e-9:
                fail("ETH tier0 early_be", t0=t0)
                ok_all = False
            if abs(float(t1["early_be_atr"]) - 0.65) > 1e-9:
                fail("ETH tier1 early_be", t1=t1)
                ok_all = False
        else:
            if abs(float(t0["early_be_atr"]) - 0.65) > 1e-9:
                fail("XAU tier0 early_be", t0=t0)
                ok_all = False
            if abs(float(t1["early_be_atr"]) - 0.85) > 1e-9:
                fail("XAU tier1 early_be", t1=t1)
                ok_all = False
        if abs(frac1 - 0.65) > 1e-9:
            fail("frac bump 0.5→0.65", frac1=frac1)
            ok_all = False
        bumped = bump_after_reentry_fill(0, 0.50, sym)
        if int(bumped.get("reentry_attempt") or 0) != 1:
            fail("bump attempt", bumped=bumped)
            ok_all = False
        if abs(float(bumped.get("radar_activation_frac") or 0) - 0.65) > 1e-9:
            fail("bump frac", bumped=bumped)
            ok_all = False
        init = init_cycle_on_open(
            side=side,
            tv_price=tv,
            entry=tv,
            open_atr=atr,
            reentry_attempt=0,
            symbol=sym,
        )
        if abs(float(init.get("radar_activation_frac") or 0) - 0.50) > 1e-9:
            fail("init frac", init=init)
            ok_all = False
    return ok_all


def main():
    os.makedirs("logs", exist_ok=True)
    sym = "ETHUSDT"
    log("RADAR_SMOKE_START", version="v15.9.4-reentry-verify", symbol=sym)
    log("COOLDOWN_25s")
    time.sleep(25)

    if not reentry_engine_live_smoke():
        log("ENGINE_SMOKE_FAIL")
        R["pass"] = False
    else:
        log("ENGINE_SMOKE_OK")

    if not ensure_flat(sym, "PREFLAT"):
        R["pass"] = False
    else:
        open_long(sym)
        ready, last = wait_defense(sym, timeout=50)
        if ready:
            if not assert_dormant_radar(sym, last):
                R["pass"] = False
            if not hold_observe(sym, seconds=16):
                R["pass"] = False
            if not force_arm_via_admin(sym):
                R["pass"] = False
            else:
                time.sleep(10)
                au = audit(sym)
                log("HOLD_AFTER_ARM", audit=au)
                if au.get("dups") or au.get("total", 0) > 5:
                    fail("post-arm drift", audit=au)
        else:
            R["pass"] = False
        ensure_flat(sym, "FINAL_FLAT")

    log("RADAR_SMOKE_END", passed=R["pass"], errors=R["errors"])
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=2)
    print("OUT=", OUT)
    print("PASS=", R["pass"])
    raise SystemExit(0 if R["pass"] else 1)


if __name__ == "__main__":
    main()

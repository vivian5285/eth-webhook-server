#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极小仓 TP1 → 止损数量收缩 实盘验证

流程:
  1) 开 LONG 0.033
  2) 挂 TP1/TP2 + 呼吸止损（走军师写入）
  3) 记录止损 qty（期望=全仓）
  4) 市价 reduceOnly 卖出 TP1 份额（≈30%）模拟 TP1 成交
  5) 调用 _realign_remaining_tps_after_fill → _breath_resize_stop_on_tp
  6) 核对手停 qty 是否 ≈ 剩余仓位
  7) 市价全平收尾

用法（仅 VPS）:
  sudo -u trading ./venv/bin/python3 live_test_tp1_stop_resize.py --dry-run
  sudo -u trading ./venv/bin/python3 live_test_tp1_stop_resize.py --confirm
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)


def _ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(msg: str):
    print(f"[{_ts()}] {msg}", flush=True)


def _algo_stops(symbol: str):
    from binance_client import binance_client
    algos = []
    try:
        if hasattr(binance_client, "get_open_algo_orders"):
            algos = binance_client.get_open_algo_orders(symbol) or []
    except Exception as e:
        _log(f"algo query err: {e}")
    stops = []
    for a in algos:
        t = str(a.get("type") or "").upper()
        if t in ("STOP_MARKET", "STOP", "TAKE_PROFIT_MARKET"):
            stops.append(a)
    # also plain open orders
    try:
        for o in (binance_client.get_open_orders(symbol) or []):
            t = str(o.get("type") or "").upper()
            if t in ("STOP_MARKET", "STOP"):
                stops.append(o)
    except Exception:
        pass
    return stops


def _stop_snapshot(symbol: str):
    stops = _algo_stops(symbol)
    rows = []
    seen = set()
    for s in stops:
        oid = str(s.get("algoId") or s.get("orderId") or "")
        if oid and oid in seen:
            continue
        if oid:
            seen.add(oid)
        qty = float(s.get("origQty") or s.get("quantity") or 0)
        px = float(s.get("stopPrice") or s.get("triggerPrice") or 0)
        rows.append({"qty": qty, "stop": px, "id": oid, "type": s.get("type")})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--qty", type=float, default=0.033)
    ap.add_argument("--symbol", default="ETHUSDT")
    args = ap.parse_args()
    if not args.confirm and not args.dry_run:
        print("需要 --dry-run 或 --confirm")
        return 2

    from binance_client import binance_client
    from position_manager import position_manager
    from position_supervisor_binance import get_supervisor, BINANCE_VPS_VERSION
    from breath_stop import initial_stop_price, TP1_ATR, TP2_ATR
    import dingtalk

    symbol = args.symbol
    qty = float(args.qty)
    sup = get_supervisor(symbol)
    _log(f"version={BINANCE_VPS_VERSION} symbol={symbol} qty={qty}")

    # pause clear
    try:
        sup.trading_paused = False
        sup.trading_pause_reason = ""
        sup._atr_div_streak = 0
        sup.atr_degraded = False
        sup.atr_source = "vps"
    except Exception:
        pass

    px = 0.0
    for _ in range(8):
        try:
            px = float(binance_client.get_current_price(symbol, prefer_ws=False) or 0)
        except Exception:
            px = 0.0
        if px > 0:
            break
        time.sleep(0.5)
    atr, adx = 0.0, 0.0
    try:
        atr, adx = sup._refresh_market_metrics(force=True)
    except Exception as e:
        _log(f"metrics err: {e}")
    atr = float(atr or getattr(sup, "current_atr", 0) or 0)
    if px <= 0:
        # 行情短暂失败时用公开 ticker / 引擎兜底
        try:
            import urllib.request
            with urllib.request.urlopen(
                f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}",
                timeout=10,
            ) as resp:
                px = float(json.loads(resp.read().decode()).get("price") or 0)
            _log(f"mark via public ticker={px:.2f}")
        except Exception as e:
            _log(f"public ticker fail: {e}")
            try:
                eng = sup._market_engine()
                px = float(getattr(eng, "last_price", 0) or 0)
            except Exception:
                pass
    _log(f"mark={px:.2f} atr={atr:.4f} adx={float(adx or 0):.2f}")
    if atr <= 0 or px <= 0:
        raise RuntimeError("行情不可用")

    stop = float(initial_stop_price("LONG", px, atr))
    tp1 = round(px + TP1_ATR * atr, 2)
    tp2 = round(px + TP2_ATR * atr, 2)
    q1 = max(0.001, round(qty * 0.30, 3))
    q2 = max(0.001, round(qty * 0.30, 3))
    _log(f"plan stop={stop:.2f} tp1={tp1}@{q1} tp2={tp2}@{q2}")

    pos0 = position_manager.get_position(symbol)
    _log(f"pre POS={pos0}")
    if args.dry_run:
        _log("DRY-RUN OK — 未下单")
        return 0

    # flat first
    if not sup._ensure_flat_before_open(reason_tag="TP1_RESIZE_TEST·开仓前"):
        raise RuntimeError("无菌净场失败")

    binance_client.set_leverage(symbol, leverage=5)
    _log(f">>> 市价开 LONG {qty}")
    order = binance_client.place_market_order("LONG", qty, symbol=symbol)
    if not order:
        raise RuntimeError("开仓失败")
    time.sleep(2.0)
    pos = sup._get_active_position() or position_manager.get_position(symbol)
    if not pos:
        # normalize position_manager shape
        raise RuntimeError("开仓后无持仓")
    # unify fields
    if "size" not in pos and "positionAmt" in pos:
        amt = float(pos.get("positionAmt") or 0)
        pos = {
            "side": "LONG" if amt > 0 else "SHORT",
            "size": abs(amt),
            "entry_price": float(pos.get("entryPrice") or 0),
        }
    entry = float(pos.get("entry_price") or pos.get("entryPrice") or 0)
    real_qty = float(pos.get("size") or abs(float(pos.get("positionAmt") or 0)))
    stop = float(initial_stop_price("LONG", entry, atr))
    tp1 = round(entry + TP1_ATR * atr, 2)
    tp2 = round(entry + TP2_ATR * atr, 2)
    _log(f"filled LONG {real_qty} @ {entry:.2f} stop={stop:.2f}")

    # lock state
    sup.current_side = "LONG"
    sup.open_atr = atr
    sup.current_atr = atr
    sup.atr_source = "vps"
    sup.atr_degraded = False
    sup.initial_stop = stop
    sup.current_sl = stop
    sup.watched_entry = entry
    sup.watched_qty = real_qty
    sup.initial_qty = real_qty
    sup.remaining_qty_pct = 1.0
    sup.tp_levels_consumed = []
    sup.tv_tps = [tp1, tp2, round(entry + 3.6 * atr, 2)]
    sup.monitoring = True
    sup._save_state()

    # place TP + stop via engine
    o1 = binance_client.place_limit_order("SELL", q1, tp1, symbol=symbol, reduce_only=True)
    o2 = binance_client.place_limit_order("SELL", q2, tp2, symbol=symbol, reduce_only=True)
    _log(f"TP1={o1 and o1.get('orderId')} TP2={o2 and o2.get('orderId')}")
    sync = sup._sync_exchange_stop(real_qty, radar_sl=stop, reason="TP1_RESIZE_TEST开仓", force=True)
    _log(f"stop sync={sync}")
    time.sleep(1.5)
    before = _stop_snapshot(symbol)
    _log(f"BEFORE_TP1 stops={before}")
    if not before:
        raise RuntimeError("开仓后无止损单")
    stop_qty_before = sum(float(x["qty"]) for x in before)
    stop_px_before = before[0]["stop"]

    # simulate TP1 fill: cancel TP1 then market reduceOnly q1
    try:
        if o1 and o1.get("orderId"):
            binance_client.cancel_order(symbol, o1.get("orderId"))
    except Exception as e:
        _log(f"cancel TP1 warn: {e}")
    time.sleep(0.5)
    _log(f">>> 模拟 TP1 成交：市价 reduceOnly SELL {q1}")
    close_res = binance_client.place_market_order(
        "SELL", q1, symbol=symbol, reduce_only=True
    )
    _log(f"TP1 sim result={json.dumps(close_res, ensure_ascii=False)[:300] if close_res else None}")
    # 等待成交落账（避免 REST 仍报旧仓）
    live_qty = real_qty
    for i in range(12):
        time.sleep(0.5)
        pos2 = sup._get_active_position()
        if not pos2:
            p = position_manager.get_position(symbol)
            if p:
                amt = float(p.get("positionAmt") or 0)
                pos2 = {
                    "side": "LONG" if amt > 0 else "SHORT",
                    "size": abs(amt),
                    "entry_price": float(p.get("entryPrice") or 0),
                }
        live_qty = float(pos2["size"]) if pos2 else 0.0
        if live_qty > 0 and abs(live_qty - (real_qty - q1)) <= 0.0015:
            break
        _log(f"wait fill#{i+1} live_qty={live_qty}")
    _log(f"after TP1 sim live_qty={live_qty}")
    if live_qty <= 0:
        raise RuntimeError("TP1 模拟后仓位已空，无法验证收缩")

    # mark TP1 consumed + run realign path (includes breath resize)
    sup.watched_qty = live_qty
    if 1 not in (sup.tp_levels_consumed or []):
        sup.tp_levels_consumed = list(sup.tp_levels_consumed or []) + [1]
    _log(">>> 调用 _realign_remaining_tps_after_fill（含 _breath_resize_stop_on_tp）")
    align = getattr(sup, "_realign_remaining_tps_after_fill")(
        live_qty, dynamic_sl=stop, reason="LIVE_TEST模拟TP1成交"
    )
    _log(f"align={ {k: align.get(k) for k in ('matched','expected','rebuilt') if isinstance(align, dict)} }")
    time.sleep(1.5)
    after = _stop_snapshot(symbol)
    _log(f"AFTER_TP1 stops={after}")

    stop_qty_after = sum(float(x["qty"]) for x in after) if after else 0.0
    stop_px_after = after[0]["stop"] if after else 0.0
    # 再读一次实盘仓，作为收缩目标
    pos3 = sup._get_active_position()
    if pos3:
        expect_qty = float(pos3.get("size") or 0)
    else:
        expect_qty = live_qty
    ok_qty = abs(stop_qty_after - expect_qty) <= 0.0015 and stop_qty_after < stop_qty_before - 0.001
    ok_px = abs(stop_px_after - stop_px_before) <= 0.05 or abs(stop_px_after - stop) <= 0.05
    _log(
        f"VERIFY stop_qty {stop_qty_before} → {stop_qty_after} (live={expect_qty}) "
        f"ok_qty={ok_qty} | stop_px {stop_px_before} → {stop_px_after} ok_px={ok_px}"
    )

    try:
        dingtalk.report_system_alert(
            title=f"TP1止损qty收缩验证 [{symbol}]",
            detail=(
                f"before_qty={stop_qty_before} after_qty={stop_qty_after} live={live_qty} "
                f"ok_qty={ok_qty} | px {stop_px_before}→{stop_px_after} ok_px={ok_px} | "
                f"version={BINANCE_VPS_VERSION}"
            ),
            level="提示" if ok_qty else "紧急",
            suggestion="验证结束将市价全平剩余仓",
            immediate=True,
        )
    except Exception as e:
        _log(f"dingtalk skip: {e}")

    # flatten remainder
    _log(">>> 收尾全平")
    try:
        sup._close_all("TP1_RESIZE_TEST收尾全平")
    except Exception as e:
        _log(f"_close_all err, fallback market: {e}")
        p = position_manager.get_position(symbol)
        if p:
            amt = abs(float(p.get("positionAmt") or 0))
            if amt > 0:
                binance_client.place_market_order("SELL", amt, symbol=symbol, reduce_only=True)
    time.sleep(2.0)
    try:
        binance_client.cancel_all_open_orders(symbol)
    except Exception:
        pass
    final = position_manager.get_position(symbol)
    _log(f"FINAL POS={final}")
    _log(f"RESULT ok_qty={ok_qty} ok_px={ok_px}")
    return 0 if ok_qty else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        _log(f"FATAL: {e}")
        raise

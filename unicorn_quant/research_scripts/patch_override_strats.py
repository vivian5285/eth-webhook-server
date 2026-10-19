import ast, shutil, time
D = "/home/stratroster/eth-webhook-server/strategy_engine/strategies"
for f in ("daily_ema_brk.py", "intraday_trend_cycle.py", "stock_sector_rotation.py"):
    shutil.copy(f"{D}/{f}", f"{D}/{f}.bak_ovr_{time.strftime('%Y%m%d_%H%M')}")
def patch(fn, pairs):
    p = f"{D}/{fn}"; s = open(p, encoding="utf-8").read()
    for a, b in pairs:
        assert s.count(a) == 1, (fn, a[:70], s.count(a)); s = s.replace(a, b)
    ast.parse(s); open(p, "w", encoding="utf-8").write(s); print("ok", fn)
# ---- 币圈日线腿 ----
patch("daily_ema_brk.py", [
("""    if position:
        side = 1 if str(position.get("side") or "").upper() == "LONG" else -1
        _bt = _bt_pre_open(symbol)""",
"""    from strategy_engine.strategies import recon_override        # 2026-10-19 自动对账修复指令
    _ov = recon_override.get(SYNC_STRATEGY, symbol)
    if position:
        _pa = recon_override.position_action(_ov, position, price, bar_time)
        if _pa:
            return None if _pa.get("hold") else _pa
        side = 1 if str(position.get("side") or "").upper() == "LONG" else -1
        _bt = _bt_pre_open(symbol)"""),
("""    # ---- 空仓 ----
    bt_open = _bt_pre_open(symbol)""",
"""    # ---- 空仓 ----
    if _ov and _ov["want"] in ("LONG", "SHORT") and _coin_frac(symbol, float(p["target_notional_frac"])) > 0:
        side_ = 1 if _ov["want"] == "LONG" else -1
        stp = float(_ov.get("stop") or (price - side_ * 3.0 * datr))
        if (side_ > 0 and price > stp) or (side_ < 0 and price < stp):
            atr_pct = datr / price; alloc = min(1.0, float(p["vol_target"]) / atr_pct)
            from strategy_engine import position_sizing as ps
            stop_frac = max(abs(price - stp) / price, 1e-4)
            default_notional = min(ps.LEVERAGE * ps.NOTIONAL_MARGIN_HAIRCUT, 1.0 / stop_frac) * ps.RISK_PCT * ps.TIER_NOTIONAL_MULT[1]
            return {"action": _ov["want"], "price": round(price, 6), "atr": round(datr, 6), "stop_loss": round(stp, 6), "tier": 1,
                    "position_fraction": _coin_frac(symbol, float(p["target_notional_frac"])) * alloc / default_notional, "bar_time": bar_time,
                    "reason": f"自动对账修复：回测此时持{tag(side_)}，擂台跟随开{tag(side_)}，止损{stp:.6g}；{_ov.get('reason', '')}"}
    bt_open = _bt_pre_open(symbol)"""),
])
# ---- 币圈 8h 两条子腿 ----
patch("intraday_trend_cycle.py", [
("""    if position:
        side = str(position.get("side") or "").upper()
        want = 1 if side == "LONG" else -1""",
"""    from strategy_engine.strategies import recon_override        # 2026-10-19 自动对账修复指令
    _ov = recon_override.get("st8h_cycle_main5" if kind == "st" else "boll8h_cycle_main5", str(p.get("symbol") or "").upper())
    if position:
        _pa = recon_override.position_action(_ov, position, price, bar_time)
        if _pa:
            return None if _pa.get("hold") else _pa
        side = str(position.get("side") or "").upper()
        want = 1 if side == "LONG" else -1"""),
("""    if d == 0:
        return None
    action = "LONG" if d > 0 else "SHORT"
    if not allowed(action):
        return None""",
"""    _force = bool(_ov and _ov["want"] in ("LONG", "SHORT"))
    if _force:
        d = 1 if _ov["want"] == "LONG" else -1
    if d == 0:
        return None
    action = "LONG" if d > 0 else "SHORT"
    if not _force and not allowed(action):
        return None"""),
("""    if p.get("btc_filter") and symbol != "BTCUSDT":""", """    if p.get("btc_filter") and symbol != "BTCUSDT" and not _force:"""),
("""        "stop_loss": round(price - d * atr * float(p["atr_stop_mult"]), 6), "tier": 1,""",
"""        "stop_loss": round(float(_ov["stop"]) if _force and _ov.get("stop") else price - d * atr * float(p["atr_stop_mult"]), 6), "tier": 1,"""),
("""        "reason": f"{name}{'多头' if d > 0 else '空头'}；减半周期允许；" + ("BTC日线同向；" if symbol != "BTCUSDT" else "") + f"波动率调仓×{alloc:.2f}",""",
"""        "reason": (f"自动对账修复：回测此时持{'多' if d > 0 else '空'}，擂台跟随开仓；{_ov.get('reason', '')}" if _force else
                   f"{name}{'多头' if d > 0 else '空头'}；减半周期允许；" + ("BTC日线同向；" if symbol != "BTCUSDT" else "") + f"波动率调仓×{alloc:.2f}"),"""),
])
# ---- 美股 ----
patch("stock_sector_rotation.py", [
("""    if position:
        # 2026-10-18 对齐回测：""",
"""    from strategy_engine.strategies import recon_override        # 2026-10-19 自动对账修复指令
    _ov = recon_override.get("stock_sector_rot6m5_long", symbol)
    if position:
        _pa = recon_override.position_action(_ov, position, price, bar_time)
        if _pa:
            return None if _pa.get("hold") else _pa
        # 2026-10-18 对齐回测："""),
("""    if not rot or not in_rot or not up or stop_locked:
        return None""",
"""    _force = bool(_ov and _ov["want"] == "LONG")
    if not _force and (not rot or not in_rot or not up or stop_locked):
        return None"""),
])

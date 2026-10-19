"""2026-10-18 宝贝：工作台开多普遍晚了 + 全屏/拖动后右边标签重叠。
1) backtest.py：引擎支持"突破挂单"(ORDER：下一根盘中触价成交，带硬止损；CLEAR_STOP：均线跟上撤止损)
2) plan.py：plan_f_crypto 同步 optimal.py 的前5日盘中突破单(BRK_N)
3) app.js：叠加层坐标变化(价格轴缩放/全屏/拖动)就重绘；跑出绘图区的标签不画"""
import re, shutil, time, os
R = "/home/stratroster/unicorn_quant"
bk = f"{R}/backups/pre_brkwb_{time.strftime('%Y%m%d_%H%M%S')}"; os.makedirs(bk, exist_ok=True)
for f in ("engine/backtest.py", "engine/plan.py", "static/app.js", "static/index.html"):
    shutil.copy(f"{R}/{f}", bk)

def sub(path, old, new, cnt=1):
    s = open(f"{R}/{path}", encoding="utf-8").read()
    assert s.count(old) == cnt, (path, old[:60], s.count(old))
    s = s.replace(old, new); open(f"{R}/{path}", "w", encoding="utf-8").write(s)

# ---------- 1) 引擎 ----------
sub("engine/backtest.py", """    c0 = bars[warmup]["c"] if len(bars) > warmup else bars[0]["c"]
""", """    c0 = bars[warmup]["c"] if len(bars) > warmup else bars[0]["c"]
    order = None                      # 2026-10-18 突破挂单：上一根收盘后挂、只在下一根有效
""")
sub("engine/backtest.py", """                close(ex[0], bar["t"], ex[1], f"跌破硬止损 {ex[0]:.4g}" if ex[1] == "硬止损" else None)
        window_bars""", """                close(ex[0], bar["t"], ex[1], f"跌破硬止损 {ex[0]:.4g}" if ex[1] == "硬止损" else None)
        if order is not None:
            o, order = order, None
            lng = o["side"] == "LONG"
            if not pos and ((lng and bar["h"] >= o["price"]) or (not lng and bar["l"] <= o["price"])):
                px = max(bar["o"], o["price"]) if lng else min(bar["o"], o["price"])
                notional = equity * leverage * max(0.0, min(1.0, float(o.get("size_frac") or 1.0)))
                sd = o.get("stop_dist")
                stp = (px - sd if lng else px + sd) if sd else None
                pos = {"side": o["side"], "entry": px, "t": bar["t"], "qty": notional / px, "notional": notional,
                       "stop": stp, "tp": None, "bars": 0, "brk": True,
                       "reason": o.get("reason", "") + f"；盘中触价成交 {px:.4g}" + (f"；均线跟上前靠硬止损 {stp:.4g} 保护" if stp else "")}
                if stp is not None and ((lng and bar["l"] <= stp) or (not lng and bar["h"] >= stp)):
                    close(stp, bar["t"], "硬止损", f"当天突破进场又{'跌破' if lng else '涨破'}硬止损 {stp:.4g}")
        window_bars""")
sub("engine/backtest.py", """            sig = fn(by_tf, params, {"side": pos["side"], "entry_price": pos["entry"], "entry_bar_time": pos["t"]} if pos else None)""",
    """            sig = fn(by_tf, params, {"side": pos["side"], "entry_price": pos["entry"], "entry_bar_time": pos["t"],
                                     "stop": pos.get("stop"), "brk": bool(pos.get("brk"))} if pos else None)""")
sub("engine/backtest.py", """            elif act.startswith("CLOSE") and pos:""", """            elif act == "ORDER":
                if not pos:
                    order = {"side": sig.get("order_side"), "price": float(sig["order_price"]), "stop_dist": sig.get("stop_dist"),
                             "size_frac": sig.get("size_frac"), "reason": sig.get("reason", "")}
            elif act == "CLEAR_STOP":
                if pos:
                    pos["stop"] = None; pos["brk"] = False; pos["reason"] += sig.get("reason", "")
            elif act.startswith("CLOSE") and pos:""")

# ---------- 2) plan_f_crypto ----------
sub("engine/plan.py", "from .optimal import OUT, PHASE_RULE, SEC_OF, phase_of", "from .optimal import BRK_N, OUT, PHASE_RULE, SEC_OF, phase_of")
sub("engine/plan.py", """    btc_ok, btc_txt = True, ""
    if sym and sym != "BTCUSDT":""", """    btc_ok, btc_txt, bu = True, "", None
    if sym and sym != "BTCUSDT":""")
sub("engine/plan.py", """    side = {"LONG": 1, "SHORT": -1}.get((position or {}).get("side"))
    a = ta.atr(bars, 14)[-1] or 0""", """    side = {"LONG": 1, "SHORT": -1}.get((position or {}).get("side"))
    a = ta.atr(bars, 14)[-1] or 0
    if side and (position or {}).get("brk"):       # 突破单：均线跟上就转成趋势单(撤硬止损)，跟上之前继续拿(靠3×ATR止损)
        if raw == side:
            return _sig(b, "CLEAR_STOP", f"；{_day(b['t'])} 均线跟上，转为趋势单(撤掉硬止损)")
        return None""")
sub("engine/plan.py", """    if allowed and btc_ok:
        return _sig(b, "LONG" if raw > 0 else "SHORT", open_reason, size_frac=size, **extra)
    return None
""", """    if allowed and btc_ok:
        return _sig(b, "LONG" if raw > 0 else "SHORT", open_reason, size_frac=size, **extra)
    # 2026-10-18 同步 optimal.py 盘中突破单：均线还没交叉，先挂明天的突破单(前BRK_N日高/低)，触价就进场+3×ATR硬止损
    if len(bars) > BRK_N and a:
        is_btc = sym in ("", "BTCUSDT")
        if raw < 0 and (ph != "熊" or bottom.check(bars)[0]) and (is_btc or bu):
            lvl = max(x["h"] for x in bars[-BRK_N:])
            return {"price": b["c"], "bar_time": b["t"], "action": "ORDER", "order_side": "LONG", "order_price": lvl, "stop_dist": 3.0 * a, "size_frac": size,
                    "reason": f"{_day(b['t'])}收盘后挂突破单：明天涨破前{BRK_N}日最高 {lvl:.4g} 就开多(不等EMA8上穿EMA30)；{ph_txt}；ATR%={100 * a / b['c']:.1f}，仓位×{size:.2f}"}
        if raw > 0 and ph != "牛" and (is_btc or bu is False):
            lvl = min(x["l"] for x in bars[-BRK_N:])
            return {"price": b["c"], "bar_time": b["t"], "action": "ORDER", "order_side": "SHORT", "order_price": lvl, "stop_dist": 3.0 * a, "size_frac": size,
                    "reason": f"{_day(b['t'])}收盘后挂突破单：明天跌破前{BRK_N}日最低 {lvl:.4g} 就开空(不等EMA8下穿EMA30)；{ph_txt}；ATR%={100 * a / b['c']:.1f}，仓位×{size:.2f}"}
    return None
""")

# ---------- 3) 前端 ----------
sub("static/app.js", """    if (x == null || x < 0 || x > W) return;          // 屏幕外的标签不画(以前会被挤到边上)""",
    """    if (x == null || y == null || x < 0 || x > W || y < 0 || y > H) return;   // 屏幕外的标签不画(以前会被挤到边上)""")
sub("static/app.js", """  new ResizeObserver(() => { sizeCanvas(); drawAll(); }).observe($('.chart-wrap'));""",
    """  new ResizeObserver(() => { sizeCanvas(); drawAll(); }).observe($('.chart-wrap'));
  // 2026-10-18 价格轴自动缩放/全屏/上下拖动不会触发上面的事件，叠加层会用旧坐标把标签挤到边上：坐标一变就重绘
  let _ovSig = '';
  const _ovWatch = () => {
    try {
      const m = $('#chartMain'), r = main.timeScale().getVisibleLogicalRange(), last = S.bars[S.bars.length - 1];
      const p = last ? candle.priceToCoordinate(last.c) : 0, q = last ? candle.priceToCoordinate(last.c * 1.1) : 0;
      const sig = [m.clientWidth, m.clientHeight, r && r.from.toFixed(2), r && r.to.toFixed(2), p && p.toFixed(1), q && q.toFixed(1)].join('|');
      if (sig !== _ovSig) { _ovSig = sig; if (cv.width !== Math.round(m.clientWidth * (devicePixelRatio || 1))) sizeCanvas(); drawAll(); }
    } catch (e) { }
    requestAnimationFrame(_ovWatch);
  };
  requestAnimationFrame(_ovWatch);""")
s = open(f"{R}/static/index.html", encoding="utf-8").read()
s = re.sub(r"app\.js\?v=[\w]+", "app.js?v=20brkov", s); open(f"{R}/static/index.html", "w", encoding="utf-8").write(s)
print("patched, backup", bk)

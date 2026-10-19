# -*- coding: utf-8 -*-
"""方案F 实盘信号发布器(2026-10-19)。以 stratroster 身份运行，碰不到任何交易所密钥。

每 60 秒：读擂台方案F四条腿(纸面，已和回测对齐)的当前持仓方向 + 开仓时的波动率调仓系数、币种年度权重、组合波动率缩放，
算出"每个品种应持净仓位占账户净值的比例"(带正负号：正=多，负=空)，写到 /var/lib/planf/targets.json。
实盘执行器(binanceE 等账户用户身份运行)只读这个文件，乘以自己账户的净值去下单。

口径(与 engine/optimal.py 回测一致)：
  币圈：比例 = 币种权重 × 1/3 × Σ(三条子腿 方向 × min(1, 波动目标 ÷ 开仓时ATR%))；日线腿波动目标 3%，8h 两腿 3%×√(8/24)
  美股：比例 = min(1, 3% ÷ 开仓时ATR%) ÷ max(本月可交易只数, 5)，美股腿总和 ≤ 1
  再乘组合波动率缩放 scale_now(optimal.json，60 天波动超 40% 才降)，总名义 ≤ 2
同时给出"不含 BTC"版本(BTC 权重按比例分给其余币)：账户净值 < 5000U 时 BTC 0.001 个的最小下单太粗，回测比较这样最接近(比值 5.01 vs 就近取整 4.63)。
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import time

OPT = "/home/stratroster/unicorn_quant/optimal_data"
ARENA_DB = "/home/stratroster/eth-webhook-server/strategy_engine/data/shadow_v2.db"
ROT = "/home/stratroster/eth-webhook-server/strategy_engine/data/sector_rotation.json"
OUT_DIR = "/var/lib/planf"
CRYPTO_LEGS = {"daily_ema_brk_cycle_main5": 0.03, "st8h_cycle_main5": 0.03 * math.sqrt(8 / 24), "boll8h_cycle_main5": 0.03 * math.sqrt(8 / 24)}
STOCK_LEG = "stock_sector_rot6m5_long"
M5 = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]


def _rd(p, default=None):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def build() -> dict:
    now = time.time(); problems = []
    cw = (_rd(os.path.join(OPT, "crypto_weights.json"), {}) or {}).get("current") or {}
    if not cw:
        problems.append("读不到币种权重")
    opt = _rd(os.path.join(OPT, "optimal.json"), {}) or {}
    vs = float(((opt.get("vol_control") or {}).get("scale_now")) or 1.0)
    if now - float(opt.get("asof") or 0) > 12 * 3600:
        problems.append("回测结果超过12小时没更新(波动率缩放可能过期)")
    rot = _rd(ROT, {}) or {}
    k = max(len(rot.get("active_symbols") or []), 5)
    arena_ok = subprocess.run(["systemctl", "is-active", "--quiet", "strategy-roster"]).returncode == 0
    if not arena_ok:
        problems.append("擂台(strategy-roster)没在运行，信号可能过期")
    con = sqlite3.connect(f"file:{ARENA_DB}?mode=ro", uri=True, timeout=10)
    rows = con.execute("select strategy, symbol, side, entry, atr0, stop, created_at from shadow_positions_v2 where status='open' and strategy in (?,?,?,?)",
                       (*CRYPTO_LEGS, STOCK_LEG)).fetchall()
    con.close()
    legs, crypto_raw, stock = [], {}, {}
    for strat, sym, side, entry, atr0, stop, created in rows:
        d = 1 if str(side).upper() == "LONG" else -1
        atrp = (float(atr0) / float(entry)) if entry and atr0 else None
        if strat in CRYPTO_LEGS:
            alloc = min(1.0, CRYPTO_LEGS[strat] / atrp) if atrp else 1.0
            crypto_raw[sym] = crypto_raw.get(sym, 0.0) + d * alloc / 3.0
        else:
            alloc = min(1.0, 0.03 / atrp) if atrp else 1.0
            stock[sym] = stock.get(sym, 0.0) + d * alloc / k
        legs.append({"strategy": strat, "symbol": sym, "side": "LONG" if d > 0 else "SHORT", "entry": entry, "alloc": round(alloc, 4),
                     "stop": stop, "since": created})
    st_gross = sum(abs(v) for v in stock.values())
    if st_gross > 1.0:
        stock = {s: v / st_gross for s, v in stock.items()}
    def crypto_with(weights):
        return {s: weights.get(s, 0.0) * v for s, v in crypto_raw.items() if weights.get(s, 0.0)}
    w_nobtc = {s: v for s, v in cw.items() if s != "BTCUSDT"}
    tot = sum(w_nobtc.values()) or 1.0
    w_nobtc = {s: v / tot for s, v in w_nobtc.items()}
    def finish(cr):
        t = {**{s: v * vs for s, v in cr.items()}, **{s: v * vs for s, v in stock.items()}}
        g = sum(abs(v) for v in t.values())
        if g > 2.0:
            t = {s: v * 2.0 / g for s, v in t.items()}
        return {s: round(v, 6) for s, v in t.items() if abs(v) > 1e-9}
    return {"ts": now, "asof": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)) + " UTC", "ok": not problems, "problems": problems,
            "vol_scale": vs, "coin_weights": cw, "coin_weights_no_btc": {s: round(v, 4) for s, v in w_nobtc.items()}, "stock_slots": k,
            "btc_min_equity": 5000, "targets": finish(crypto_with(cw)), "targets_no_btc": finish(crypto_with(w_nobtc)),
            "stock_symbols": sorted(stock), "legs": legs,
            # 方案F 管辖的全部品种(5个币 + 美股池里币安可交易的)。执行器只碰这些，其它持仓一律不动只告警
            "universe": sorted(set(M5) | {s for lst in (rot.get("tradable") or {}).values() for s in lst}),
            "note": "比例=应持净仓位÷账户净值(正多负空)。账户净值<btc_min_equity 用 targets_no_btc。"}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    while True:
        try:
            res = build()
        except Exception as e:
            res = {"ts": time.time(), "ok": False, "problems": [f"发布器出错 {type(e).__name__}: {e}"], "targets": {}, "targets_no_btc": {}}
        tmp = os.path.join(OUT_DIR, "targets.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o644)
        os.replace(tmp, os.path.join(OUT_DIR, "targets.json"))
        time.sleep(60)


if __name__ == "__main__":
    main()

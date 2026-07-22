#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极小仓位真实 LONG 闭环测试（默认 0.033 ETH）

⚠️ 会真实下单。仅在生产 VPS（可访问 fapi + 已配置 API Key）上运行。

用法:
  python3 live_test_long_0033.py --dry-run          # 只跑行情/sizing/告警预检，不下单
  python3 live_test_long_0033.py --confirm          # 真实开仓 0.033 ETH LONG
  python3 live_test_long_0033.py --confirm --qty 0.033
  python3 live_test_long_0033.py --status           # 查持仓/挂单/暂停态
  python3 live_test_long_0033.py --close-confirm    # 市价全平本测试仓（收尾）

验收关注（全程日志检索）:
  - 不得出现: ATR_DEGRADE_MANUAL_RESUME / atr_source=tv_implied_degrade
  - 不得出现: CLOSE_THEN_OPEN_FAIL_ABORT（你口头说的 FLIP_CLEAN_ABORT 在本仓库对应此机制）
  - ATR_FALLBACK_* 是缺省常量名，不是降级告警；真正降级告警看 ATR_DEGRADE_*
  - 打印若干 ADX 时间点供对照 TV 90m 图
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


def _ban_words_scan(text: str) -> list:
    keys = (
        "ATR_DEGRADE_MANUAL_RESUME",
        "tv_implied_degrade",
        "CLOSE_THEN_OPEN_FAIL_ABORT",
        "FLIP_CLEAN_ABORT",
        "无菌空仓失败",
        "清仓失败·需人工介入",
        "ATR应急降级",
    )
    hit = [k for k in keys if k in (text or "")]
    return hit


def print_adx_checkpoints(eng, bars_90m, n: int = 8):
    """打印最近若干根 90m 的 ATR/ADX，供人工对照 TV。"""
    from market_engine import wilder_atr, wilder_adx, PERIOD_90M_MS

    _log("=== ADX/ATR 人工核对点（请对照 TV BINANCE:ETHUSDT.P 90m）===")
    rows = bars_90m[-n:] if bars_90m else []
    print("| # | open UTC | close | ATR(14) | ADX(14) |")
    print("|---:|:---|---:|---:|---:|")
    for i, b in enumerate(rows):
        prefix = bars_90m[: bars_90m.index(b) + 1] if b in bars_90m else bars_90m
        # 用到该根为止的前缀
        idx = len(bars_90m) - len(rows) + i
        prefix = bars_90m[: idx + 1]
        atr = wilder_atr(prefix) if len(prefix) >= 15 else 0.0
        adx = wilder_adx(prefix) if len(prefix) >= 30 else 0.0
        t0 = int(b[0])
        t1 = t0 + PERIOD_90M_MS
        ot = datetime.fromtimestamp(t0 / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"| {i+1} | {ot} | {float(b[4]):.2f} | {atr:.4f} | {adx:.2f} |")
    print("", flush=True)


def preflight(symbol: str, qty: float, dry: bool):
    from market_engine import (
        merge_30m_to_90m,
        resolve_tv_atr_for_compare,
        atr_divergence_pct,
        evaluate_atr_emergency_degrade,
        ATR_COMPARE_ALERT_PCT,
        TV_HARD_SL_ATR_MULT,
    )
    from breath_stop import initial_stop_price, INITIAL_SL_ATR
    from binance_client import binance_client

    _log(f"PREFLIGHT symbol={symbol} qty={qty} dry={dry}")
    px = float(binance_client.get_current_price(symbol) or 0)
    if px <= 0:
        raise RuntimeError("无法获取现价（检查 API/网络/地区）")
    _log(f"markPrice={px:.2f}")

    raw = binance_client.fetch_klines(symbol, "30m", 220)
    bars = merge_30m_to_90m(raw or [])
    if len(bars) < 20:
        raise RuntimeError(f"90m 合成不足: {len(bars)}")
    from market_engine import wilder_atr, wilder_adx

    atr = wilder_atr(bars)
    adx = wilder_adx(bars)
    _log(f"VPS ATR={atr:.4f} ADX={adx:.2f} bars90m={len(bars)}")
    print_adx_checkpoints(None, bars, n=8)

    # 用「假想 TV 硬止损 = entry - 1.0*ATR」做比对（实盘 webhook 会带真实 stop_loss）
    tv_sl = round(px - TV_HARD_SL_ATR_MULT * atr, 2)
    ref, src = resolve_tv_atr_for_compare(atr, entry=px, stop_loss=tv_sl)
    div = atr_divergence_pct(atr, ref)
    _log(
        f"ATR核对 VPS={atr:.4f} TV参考={ref:.4f}({src}) div={div:.2%} "
        f"阈值={ATR_COMPARE_ALERT_PCT:.0%} → "
        f"{'会提示' if div >= ATR_COMPARE_ALERT_PCT else '不会触发告警'}"
    )
    deg, dmeta = evaluate_atr_emergency_degrade(
        vps_atr=atr,
        atr_history=[],
        entry=px,
        stop_loss=tv_sl,
        div_streak=0,
        klines_ok=True,
    )
    _log(f"应急降级预检 degrade={deg} reason={dmeta.get('reason')}")

    stop = initial_stop_price("LONG", px, atr)
    _log(f"initialStop={stop:.2f} (=entry-{INITIAL_SL_ATR}*ATR) dist={abs(px-stop):.4f}")

    pos = None
    try:
        from position_manager import position_manager
        raw_pos = position_manager.get_position(symbol)
        if raw_pos and abs(float(raw_pos.get("positionAmt") or 0)) > 0:
            amt = float(raw_pos["positionAmt"])
            pos = {"side": "LONG" if amt > 0 else "SHORT", "size": abs(amt)}
    except Exception as e:
        _log(f"持仓查询跳过: {e}")
    _log(f"当前持仓: {pos or 'FLAT'}")

    return {
        "price": px,
        "atr": atr,
        "adx": adx,
        "stop": stop,
        "div": div,
        "degrade": deg,
        "pos": pos,
        "bars": bars,
        "tv_sl_guess": tv_sl,
    }


def run_live_open(symbol: str, qty: float, leverage: int = 5):
    """
    真实路径（尽量贴近生产）:
      无菌净场 → set_leverage → 市价 LONG qty → 挂简化 TP1/TP2 → 挂呼吸止损
    注: 不走 webhook 线程，避免 TV.qty/权益公式把 0.033 改掉；
        但仍用同一套 market_engine / breath_stop / binance_client。
    """
    from binance_client import binance_client
    from breath_stop import initial_stop_price, INITIAL_SL_ATR, TP1_ATR, TP2_ATR
    from position_supervisor_binance import get_supervisor
    import dingtalk

    pre = preflight(symbol, qty, dry=False)
    if pre["degrade"]:
        raise RuntimeError(
            f"预检即满足 ATR 应急降级条件 reason={pre} — 中止实盘测试"
        )
    if pre["div"] >= 0.20:
        raise RuntimeError(f"预检 ATR 偏差 {pre['div']:.1%}≥20% — 中止")

    sup = get_supervisor(symbol)
    if getattr(sup, "trading_paused", False):
        reason = getattr(sup, "trading_pause_reason", "")
        raise RuntimeError(f"trading_paused=True reason={reason} — 先 /admin/resume")

    _log(">>> 无菌净场（先平后开）")
    ok = sup._ensure_flat_before_open(reason_tag="LIVE_TEST_0.033·开仓前")
    if not ok:
        raise RuntimeError("无菌净场失败 — 已触发 CLOSE_THEN_OPEN_FAIL 路径，禁止开仓")

    px = float(binance_client.get_current_price(symbol) or pre["price"])
    atr = float(pre["atr"])
    stop = float(initial_stop_price("LONG", px, atr))
    tp1 = round(px + TP1_ATR * atr, 2)
    tp2 = round(px + TP2_ATR * atr, 2)
    # qty1/qty2 ≈ 30%/30%
    q1 = max(0.001, round(qty * 0.30, 3))
    q2 = max(0.001, round(qty * 0.30, 3))

    _log(f">>> set_leverage={leverage}x")
    binance_client.set_leverage(symbol, leverage=leverage)

    _log(f">>> 市价开 LONG {qty} {symbol} @~{px:.2f}")
    order = binance_client.place_market_order("LONG", qty, symbol=symbol)
    if not order:
        raise RuntimeError("市价开仓返回空 — 开仓失败")
    _log(f"开仓回报: {json.dumps(order, ensure_ascii=False)[:500]}")
    time.sleep(2.0)

    pos = sup._get_active_position()
    if not pos or float(pos.get("size") or 0) <= 0:
        raise RuntimeError("成交后 REST 无持仓")
    entry = float(pos["entry_price"])
    real_qty = float(pos["size"])
    _log(f"实盘持仓 LONG {real_qty} @ {entry:.2f}")

    # 锁定呼吸态
    stop = float(initial_stop_price("LONG", entry, atr))
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
    sup.tv_tps = [tp1, tp2, round(entry + 3.6 * atr, 2)]
    sup.monitoring = True
    try:
        sup._save_state()
    except Exception as e:
        _log(f"状态保存警告: {e}")

    _log(f">>> 挂 TP1={tp1} qty={q1} / TP2={tp2} qty={q2}")
    try:
        o1 = binance_client.place_limit_order("SELL", q1, tp1, symbol=symbol, reduce_only=True)
        o2 = binance_client.place_limit_order("SELL", q2, tp2, symbol=symbol, reduce_only=True)
        _log(f"TP1 order={o1 and o1.get('orderId')} TP2 order={o2 and o2.get('orderId')}")
    except Exception as e:
        _log(f"挂TP异常: {e}")

    _log(f">>> 挂呼吸止损 STOP @{stop:.2f} qty={real_qty}")
    try:
        # 走军师唯一写入路径
        sync = {
            "ok": False,
            "note": "call _sync_exchange_stop",
        }
        if hasattr(sup, "_sync_exchange_stop"):
            sync = sup._sync_exchange_stop(real_qty, radar_sl=stop, reason="LIVE_TEST开仓", force=True)
        _log(f"止损同步: {sync}")
    except Exception as e:
        _log(f"挂止损异常: {e}")

    # 钉钉（若已配置）
    try:
        dingtalk.report_system_alert(
            title=f"LIVE_TEST开仓成功 [{symbol}]",
            detail=(
                f"LONG {real_qty} @ {entry:.2f} | ATR={atr:.4f}(vps) ADX={pre['adx']:.2f} | "
                f"initialStop={stop:.2f} | TP1={tp1} TP2={tp2} | "
                f"标签 live_test_0.033 atr_source=vps | 请人工核 ADX 表"
            ),
            level="提示",
            suggestion="确认无 ATR_DEGRADE / CLOSE_THEN_OPEN_FAIL 后，再考虑放大仓位",
        )
    except Exception as e:
        _log(f"钉钉跳过: {e}")

    summary = {
        "ok": True,
        "entry": entry,
        "qty": real_qty,
        "atr": atr,
        "adx": pre["adx"],
        "initialStop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "atr_source": "vps",
        "div_preflight": pre["div"],
    }
    _log("=== LIVE OPEN SUMMARY ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def run_close(symbol: str):
    from position_supervisor_binance import get_supervisor
    sup = get_supervisor(symbol)
    _log(">>> 测试收尾：强制全平+撤单")
    ok = sup._close_all("LIVE_TEST_0.033·收尾全平", reset_state=True)
    _log(f"全平结果 ok={ok}")
    return ok


def show_status(symbol: str):
    from position_supervisor_binance import get_supervisor, BINANCE_VPS_VERSION
    from binance_client import binance_client

    sup = get_supervisor(symbol)
    pos = sup._get_active_position()
    px = binance_client.get_current_price(symbol)
    _log(f"version={BINANCE_VPS_VERSION}")
    _log(f"price={px}")
    _log(f"pos={pos}")
    _log(f"trading_paused={getattr(sup,'trading_paused',None)} reason={getattr(sup,'trading_pause_reason',None)}")
    _log(f"atr_source={getattr(sup,'atr_source',None)} atr_degraded={getattr(sup,'atr_degraded',None)}")
    _log(f"open_atr={getattr(sup,'open_atr',None)} current_sl={getattr(sup,'current_sl',None)}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--qty", type=float, default=0.033)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--confirm", action="store_true", help="确认真实下单")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--close-confirm", action="store_true")
    args = ap.parse_args()

    if args.qty <= 0 or args.qty > 0.05:
        _log("安全闸: 测试 qty 必须在 (0, 0.05] ETH")
        return 2

    try:
        if args.status:
            show_status(args.symbol)
            return 0
        if args.close_confirm:
            run_close(args.symbol)
            return 0
        if args.dry_run or not args.confirm:
            preflight(args.symbol, args.qty, dry=True)
            if not args.confirm:
                _log("未加 --confirm，仅预检。真实下单请: python3 live_test_long_0033.py --confirm")
            return 0
        run_live_open(args.symbol, args.qty)
        return 0
    except Exception as e:
        _log(f"FAIL: {e}")
        hits = _ban_words_scan(str(e))
        if hits:
            _log(f"告警关键词命中: {hits}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

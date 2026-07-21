#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
90m UTC 边界对齐自检（上线前必须跑通）。

用法:
  python3 check_90m_align.py
  python3 check_90m_align.py --symbol ETHUSDT --limit 300

验证：
  1) 每根合成 90m 的 open_time % (90*60*1000) == 0
  2) 每根 90m 对应 3 根完整 30m（t0, t0+30m, t0+60m）
  3) 相邻 90m 开盘相差恰好 90 分钟
  4) （可选）打印最近若干根 open 时间，便于与 TV 图表逐根比对

无需 API Key 时可跳过实盘拉取，仅跑单元对齐逻辑。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from market_engine import (
    PERIOD_30M_MS,
    PERIOD_90M_MS,
    bucket_90m_open_ms,
    merge_30m_to_90m,
    wilder_atr,
    atr_series,
)


def _ms_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def synthetic_30m(start_ms: int, n: int):
    """生成从 start_ms 起连续 n 根假 30m（仅测对齐）。"""
    rows = []
    px = 100.0
    for i in range(n):
        t = start_ms + i * PERIOD_30M_MS
        o = px
        h = px + 1
        l = px - 1
        c = px + 0.2
        rows.append([t, o, h, l, c, 1.0])
        px = c
    return rows


def audit_align(bars_90m, src_30m=None) -> list:
    fails = []
    if not bars_90m:
        fails.append("合成结果为空")
        return fails
    by_t = {}
    if src_30m:
        for r in src_30m:
            by_t[int(r[0])] = r
    prev = None
    for b in bars_90m:
        t = int(b[0])
        if t % PERIOD_90M_MS != 0:
            fails.append(f"open_time 未对齐 epoch90m: {t} ({_ms_iso(t)})")
        if bucket_90m_open_ms(t) != t:
            fails.append(f"bucket 函数不一致: {t}")
        if by_t:
            for off in (0, PERIOD_30M_MS, 2 * PERIOD_30M_MS):
                if (t + off) not in by_t:
                    fails.append(f"缺 30m 构件 {t}+{off//60000}m @ {_ms_iso(t)}")
                    break
        if prev is not None and t - prev != PERIOD_90M_MS:
            fails.append(f"相邻 90m 间距异常: {prev} -> {t} delta={t - prev}")
        prev = t
    return fails


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="ETHUSDT")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--live", action="store_true", help="拉币安公开 30m K 线")
    args = ap.parse_args()

    print("[VPS] 90m UTC epoch 对齐自检")
    print(f"PERIOD_90M_MS={PERIOD_90M_MS} ({PERIOD_90M_MS // 60000} min)")

    # 1) 单元：从不对齐起点开始的 30m 序列，合成后仍应对齐
    # 故意从「非 90m 边界」的 30m 起步（例如 epoch+30m）
    bad_start = PERIOD_90M_MS + PERIOD_30M_MS
    fake = synthetic_30m(bad_start, 60)
    merged = merge_30m_to_90m(fake)
    fails = audit_align(merged, fake)
    print(f"\n单元合成: 输入30m={len(fake)} 输出90m={len(merged)}")
    if merged:
        print(f"  首根90m open={merged[0][0]} {_ms_iso(merged[0][0])}")
        print(f"  末根90m open={merged[-1][0]} {_ms_iso(merged[-1][0])}")
    if fails:
        print("失败:")
        for f in fails[:20]:
            print(f"  ❌ {f}")
        return 1
    print("  ✅ 单元对齐通过（从不规则起点仍锚定 UTC 90m）")

    # 2) 可选实盘
    if args.live:
        raw = None
        err = None
        try:
            from binance_client import binance_client
            raw = binance_client.fetch_klines(args.symbol, "30m", args.limit)
        except Exception as e:
            err = e
        if not raw:
            # 无 SOCKS/密钥时走公开 REST，仅用于对齐验证
            try:
                import json
                import urllib.request
                url = (
                    "https://fapi.binance.com/fapi/v1/klines"
                    f"?symbol={args.symbol}&interval=30m&limit={int(args.limit)}"
                )
                with urllib.request.urlopen(url, timeout=20) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                raw = [
                    [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])]
                    for r in data
                ]
                print("  （binance_client 不可用，已改用公开 fapi klines）")
            except Exception as e2:
                print(f"❌ 拉K失败: {err or e2}")
                return 1
        bars = merge_30m_to_90m(raw)
        fails = audit_align(bars, raw)
        atr = wilder_atr(bars)
        series = atr_series(bars)
        print(f"\n实盘 {args.symbol}: 30m={len(raw)} → 90m={len(bars)} ATR={atr:.4f} series_n={len(series)}")
        print("最近 8 根 90m（请与 TV 90m 图逐根比对开盘时间）:")
        for b in bars[-8:]:
            print(f"  {_ms_iso(int(b[0]))}  O={float(b[1]):.2f} H={float(b[2]):.2f} "
                  f"L={float(b[3]):.2f} C={float(b[4]):.2f}")
        if fails:
            print("失败:")
            for f in fails[:20]:
                print(f"  ❌ {f}")
            return 1
        print("  ✅ 实盘合成对齐通过")
        print("  下一步：在 TV 同一品种 90m 图上核对这些开盘时间戳是否完全一致；")
        print("          再抽样比对 ATR(14)/ADX(14)，误差应 <5%。")
    else:
        print("\n（未加 --live，跳过交易所拉取；上线前请执行: python3 check_90m_align.py --live）")

    print("\n✅ check_90m_align 通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

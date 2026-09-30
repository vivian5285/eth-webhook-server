#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公开行情层——币安 USDT-M 永续 klines，无需 key。独立于 binance_futures.py
(那个是签名下单client)，职责分开。"""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)
_UA = {"User-Agent": "vwap-live/1.0"}


def _get(url, params, timeout=10, retries=3):
    q = f"{url}?{urllib.parse.urlencode(params)}"
    for a in range(retries):
        try:
            req = urllib.request.Request(q, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            if a == retries - 1:
                logger.warning("GET %s failed: %s", url, e)
            else:
                time.sleep(0.6 * (a + 1))
    return None


def klines(symbol, interval, limit=300, base_url="https://fapi.binance.com"):
    """返回按时间升序、已收盘K线（币安最后一根可能是当前未走完的，丢弃）。"""
    raw = _get(f"{base_url}/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": min(int(limit), 1500)})
    out = []
    for r in raw or []:
        try:
            out.append({"t": int(r[0]), "o": float(r[1]), "h": float(r[2]),
                        "l": float(r[3]), "c": float(r[4]), "v": float(r[5])})
        except (TypeError, ValueError, IndexError):
            continue
    if out:
        out = out[:-1]  # 最后一根可能未收盘
    return out


def mark_price(symbol, base_url="https://fapi.binance.com"):
    r = _get(f"{base_url}/fapi/v1/premiumIndex", {"symbol": symbol})
    try:
        return float(r["markPrice"]) if r else None
    except (TypeError, ValueError, KeyError):
        return None


def closes(bars):
    return [float(b["c"]) for b in bars]


def wilder_atr(bars, period=14):
    if len(bars) < period + 1:
        return 0.0
    tr = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(tr[:period]) / period
    for x in tr[period:]:
        a = (a * (period - 1) + x) / period
    return a


def wilder_adx(bars, period=14):
    n = len(bars)
    if n < period * 2 + 2:
        return 0.0
    pdm, mdm, trs = [], [], []
    for i in range(1, n):
        up = bars[i]["h"] - bars[i - 1]["h"]
        dn = bars[i - 1]["l"] - bars[i]["l"]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
        pc = bars[i - 1]["c"]
        trs.append(max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - pc), abs(bars[i]["l"] - pc)))
    st, sp, sm = sum(trs[:period]), sum(pdm[:period]), sum(mdm[:period])

    def _di(p, m, t):
        return (100 * p / t, 100 * m / t) if t > 0 else (0.0, 0.0)

    pdi, mdi = _di(sp, sm, st)
    dx = [100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0.0]
    for i in range(period, len(trs)):
        st = st - st / period + trs[i]
        sp = sp - sp / period + pdm[i]
        sm = sm - sm / period + mdm[i]
        pdi, mdi = _di(sp, sm, st)
        dx.append(100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0.0)
    if len(dx) < period:
        return 0.0
    a = sum(dx[:period]) / period
    for x in dx[period:]:
        a = (a * (period - 1) + x) / period
    return a

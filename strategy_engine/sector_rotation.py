#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股行业轮动排名——2026-10-17新增，给 strategies/stock_sector_rotation.py 用。

每个月按"上个月末收盘 ÷ 再往前 N 个月月末收盘"算13个行业(行业内正股等权)的涨幅，取前 K 名。
排名用全部正股(Yahoo，含币安没上的)，交易只做币安有美股永续(TRADIFI_PERPETUAL)的那些。
回测(/root/research/harness/combo2026.py、rot_tradable.py，2017起，78只正股，共用账户复利，总名义≤1倍)：
  6个月前5名·只做多·波动率3%：年化+24% 回撤19%，2018 -9%、2022 -6%、2026 +105%；
  跟币圈主流5日线各一半：年化+52% 回撤17%，2026 +59%。股票池是现存股票，有幸存者偏差。

由 cron 每个交易日美股收盘后跑一次(python -m strategy_engine.sector_rotation)，结果写到
data/sector_rotation.json；策略只读这个文件，不在擂台主循环里拉78只股票的数据。
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, List

SECTORS: Dict[str, List[str]] = {
    "存储": ["MU", "WDC", "STX", "SNDK", "NTAP"],
    "半导体": ["NVDA", "AMD", "AVGO", "QCOM", "TXN", "ADI", "MRVL", "INTC", "ARM", "TSM"],
    "半导体设备": ["AMAT", "LRCX", "KLAC", "TER", "ASML"],
    "AI算力/网络": ["SMCI", "DELL", "HPE", "ANET", "CIEN", "GLW", "VRT", "CSCO", "COHR"],
    "软件": ["MSFT", "ORCL", "ADBE", "CRM", "NOW", "INTU"],
    "互联网": ["GOOGL", "META", "AMZN", "NFLX", "UBER"],
    "金融": ["JPM", "BAC", "GS", "MS", "WFC"],
    "能源": ["XOM", "CVX", "COP", "SLB", "OXY"],
    "医药": ["JNJ", "UNH", "PFE", "MRK", "LLY", "ABBV"],
    "消费": ["WMT", "COST", "HD", "MCD", "KO", "PG", "NKE"],
    "工业军工": ["CAT", "DE", "GE", "HON", "LMT", "RTX", "BA"],
    "材料矿业": ["FCX", "NEM", "NUE", "AA"],
    "加密相关": ["COIN", "MSTR", "MARA", "HOOD"],
}
LOOKBACK_MONTHS = 6
TOP_K = 5
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "sector_rotation.json")


def _month(t_ms: int) -> tuple:
    g = time.gmtime(t_ms / 1000)
    return (g.tm_year, g.tm_mon)


def _binance_stock_perps() -> set:
    import requests
    r = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30)
    r.raise_for_status()
    return {s["symbol"] for s in r.json()["symbols"]
            if s.get("contractType") == "TRADIFI_PERPETUAL" and s.get("status") == "TRADING"}


def compute(now_ms: int = None) -> dict:
    from strategy_engine import yahoo_daily
    now_ms = int(now_ms or time.time() * 1000)
    cur = _month(now_ms)
    # 当前月份之前的所有月份(只用已经结束的月份的月末收盘)
    mclose: Dict[str, Dict[tuple, float]] = {}
    for lst in SECTORS.values():
        for tk in lst:
            bars = yahoo_daily.get_daily(tk)
            m: Dict[tuple, float] = {}
            for b in bars:
                k = _month(b["t"])
                if k < cur:
                    m[k] = b["c"]
            if m:
                mclose[tk] = m
            time.sleep(0.3)
    months = sorted({k for m in mclose.values() for k in m})
    if len(months) < LOOKBACK_MONTHS + 1:
        raise RuntimeError(f"正股月度数据不足({len(months)}个月)")
    prev, base = months[-1], months[-1 - LOOKBACK_MONTHS]
    scores = {}
    for sec, lst in SECTORS.items():
        r = [mclose[t][prev] / mclose[t][base] - 1 for t in lst if t in mclose and prev in mclose[t] and base in mclose[t]]
        if len(r) >= 2:
            scores[sec] = sum(r) / len(r)
    selected = sorted(scores, key=scores.get, reverse=True)[:TOP_K]
    # 2026-10-17：保存最近12个月每月的入选行业(策略用它推算"本轮持仓是从哪个月开始的"，复现回测的止损后不再进场规则)
    history = {}
    for j in range(max(LOOKBACK_MONTHS + 1, len(months) - 12), len(months) + 1):
        pv, bs = months[j - 1], months[j - 1 - LOOKBACK_MONTHS]
        sc_ = {}
        for sec, lst in SECTORS.items():
            r = [mclose[t][pv] / mclose[t][bs] - 1 for t in lst if t in mclose and pv in mclose[t] and bs in mclose[t]]
            if len(r) >= 2:
                sc_[sec] = sum(r) / len(r)
        mk = months[j] if j < len(months) else cur
        history["%04d-%02d" % mk] = sorted(sc_, key=sc_.get, reverse=True)[:TOP_K]
    listed = _binance_stock_perps()
    tradable = {sec: [t + "USDT" for t in SECTORS[sec] if t + "USDT" in listed] for sec in SECTORS}
    active = [s for sec in selected for s in tradable[sec]]
    return {
        "month": "%04d-%02d" % cur, "computed_at": int(time.time()),
        "lookback_months": LOOKBACK_MONTHS, "top_k": TOP_K,
        "window": ["%04d-%02d" % base, "%04d-%02d" % prev],
        "scores": {k: round(v, 4) for k, v in sorted(scores.items(), key=lambda x: -x[1])},
        "selected": selected, "tradable": tradable, "active_symbols": active,
        "ticker_of": {t + "USDT": t for lst in SECTORS.values() for t in lst},
        "sector_of": {t + "USDT": sec for sec, lst in SECTORS.items() for t in lst},
        "history": history,
    }


def load(max_age_days: float = 40.0) -> dict:
    """读排名文件；文件不存在、坏了或太旧(>40天)返回 {}。"""
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if time.time() - float(d.get("computed_at") or 0) > max_age_days * 86400:
            return {}
        return d
    except Exception:
        return {}


def main() -> int:
    d = compute()
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PATH)
    print(time.strftime("%Y-%m-%d %H:%M:%S"), d["month"], "窗口", d["window"], "选中", d["selected"],
          "可交易", len(d["active_symbols"]), "只", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DexScreener 价格/流动性快照 —— keyless、~300 req/min、批量最多 30 个地址。
真相账本(outcome_tracker)的价格轨迹全靠它，不烧 Birdeye 的 30K CU 额度。
"""
import logging
import time

import requests

logger = logging.getLogger(__name__)

_DS = "https://api.dexscreener.com/latest/dex/tokens/"
_UA = {"User-Agent": "Mozilla/5.0 chain-sniper/1.0", "Accept": "application/json"}

# DexScreener 的 chainId
_CHAIN_ID = {"solana": "solana", "bsc": "bsc"}


def dex_snapshot(chain, token_addresses, timeout=15):
    """返回 {token_address: {"price": float, "liq_usd": float, "vol_h24": float,
    "pc_m5": float, "pc_h1": float, "pc_h24": float, "pair": str}}。
    查不到的地址不出现在结果里（调用方据此判定"可能 rug / 池子没了"）。
    一次最多 30 个地址；调用方自己分批。"""
    if not token_addresses:
        return {}
    want_chain = _CHAIN_ID.get(chain, chain)
    joined = ",".join(token_addresses[:30])
    try:
        r = requests.get(_DS + joined, headers=_UA, timeout=timeout)
        r.raise_for_status()
        pairs = (r.json() or {}).get("pairs") or []
    except Exception as e:
        logger.warning("dexscreener snapshot failed (%d addrs): %s", len(token_addresses), e)
        return {}

    # 每个 token 可能有多个 pair —— 取本链、流动性最大的那个
    best = {}
    want_lc = {a.lower(): a for a in token_addresses}
    for p in pairs:
        if p.get("chainId") != want_chain:
            continue
        bt = ((p.get("baseToken") or {}).get("address") or "")
        key = want_lc.get(bt.lower())
        if not key:
            continue
        liq = float((p.get("liquidity") or {}).get("usd") or 0)
        if key in best and liq <= best[key]["liq_usd"]:
            continue
        pc = p.get("priceChange") or {}
        txm5 = ((p.get("txns") or {}).get("m5") or {})
        bt = p.get("baseToken") or {}
        best[key] = {
            "price": float(p.get("priceUsd") or 0) or None,
            "liq_usd": liq,
            "vol_h24": float((p.get("volume") or {}).get("h24") or 0),
            "pc_m5": float(pc.get("m5") or 0),
            "pc_h1": float(pc.get("h1") or 0),
            "pc_h24": float(pc.get("h24") or 0),
            "buys_m5": int(txm5.get("buys") or 0),
            "sells_m5": int(txm5.get("sells") or 0),
            "symbol": bt.get("symbol") or "",
            "name": bt.get("name") or "",
            "pair_created_ms": int(p.get("pairCreatedAt") or 0),
            "pair": p.get("pairAddress") or "",
        }
    return best


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    t0 = time.time()
    print(dex_snapshot("solana", ["So11111111111111111111111111111111111111112"]))
    print(dex_snapshot("bsc", ["0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82"]))
    print(f"{time.time()-t0:.2f}s")

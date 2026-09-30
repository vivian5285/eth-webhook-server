#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""趋势代币聚合器（模型B：代币动量模拟盘的"发现"这一步）——2026-09-06新增。

只用**免费、无需 API key** 的两个来源，多源交叉验证（一个代币要在 >=1 个
"趋势榜"来源出现、且 DexScreener 能查到健康的量/流动性快照，才进候选）：

  1. GeckoTerminal（api.geckoterminal.com，keyless，~30 req/min 免费）
     - /networks/{chain}/trending_pools  —— 官方趋势池榜
     - /networks/{chain}/pools?sort=h24_volume_usd_desc  —— 24h 成交额榜
  2. DexScreener（api.dexscreener.com，keyless，~300 req/min）
     - /token-boosts/top/v1  —— 被"boost"（付费推广）的代币，粗略当"注意力"信号
     - 每个候选地址再走 signals/price_feed.dex_snapshot 拿独立的
       价格/流动性/24h量/涨跌幅/建池时间/5m买卖笔数

返回 [{address, symbol, price, liq_usd, vol_h24, pc_h1, pc_h24, buys_m5,
sells_m5, pair_age_h, sources:[...]}]，按 (sources 数, vol_h24) 降序。
拉不到就返回已有缓存 / 空列表，不抛异常。
"""
from __future__ import annotations

import logging
import time

import requests

import signals.price_feed as price_feed

logger = logging.getLogger(__name__)

_GT = "https://api.geckoterminal.com/api/v2"
_DS_BOOSTS = "https://api.dexscreener.com/token-boosts/top/v1"
_UA = {"User-Agent": "Mozilla/5.0 chain-sniper/1.0", "Accept": "application/json"}

# {chain: {"ts": float, "feed": [...]}}
_cache: dict = {}
_CACHE_TTL_SEC = 90

# GeckoTerminal 免费限流温和退避（跟 discovery/wallet_finder 里那套一个思路，
# 这里量小得多：一轮就 2 个 GT 端点）。
_gt_backoff_until = 0.0


def _gt(path, params=None, timeout=15):
    global _gt_backoff_until
    if time.time() < _gt_backoff_until:
        return None
    try:
        r = requests.get(f"{_GT}{path}", params=params or {}, headers=_UA, timeout=timeout)
        if r.status_code == 429:
            _gt_backoff_until = time.time() + 20
            logger.warning("GT 429 %s -> backoff 20s", path)
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning("GT request failed %s: %s", path, e)
        return None


def _base_token_addr(pool):
    """从 GT pool 对象里取 base token 地址（relationships.base_token.data.id
    形如 'bsc_0x...' 或 'solana_...'，去掉链前缀）。"""
    try:
        tid = pool["relationships"]["base_token"]["data"]["id"]
    except (KeyError, TypeError):
        return ""
    if "_" in tid:
        return tid.split("_", 1)[1]
    return tid


def _gt_pool_list(chain, path, params):
    body = _gt(path, params)
    out = []
    for pool in (body or {}).get("data") or []:
        addr = _base_token_addr(pool)
        if addr:
            out.append(addr)
    return out


def _ds_boosted(chain):
    try:
        r = requests.get(_DS_BOOSTS, headers=_UA, timeout=15)
        r.raise_for_status()
        rows = r.json() or []
    except Exception as e:
        logger.warning("dexscreener boosts failed: %s", e)
        return []
    return [str(x.get("tokenAddress") or "") for x in rows
            if str(x.get("chainId") or "").lower() == chain and x.get("tokenAddress")]


def get_trending(chain, cfg):
    """返回该链的趋势代币候选列表（已带 DexScreener 快照字段），带 TTL 缓存。"""
    chain = str(chain or "").lower()
    now = time.time()
    hit = _cache.get(chain)
    if hit and (now - hit["ts"] < _CACHE_TTL_SEC):
        return hit["feed"]

    # 1) 收集候选地址 + 记录每个地址命中了哪些来源
    sources: dict = {}

    def _add(addr_list, src):
        for a in addr_list:
            a = a.strip()
            if not a:
                continue
            key = a.lower() if chain == "bsc" else a
            sources.setdefault(key, {"addr": a, "src": set()})["src"].add(src)

    _add(_gt_pool_list(chain, f"/networks/{chain}/trending_pools", {"page": 1}), "gt_trending")
    _add(_gt_pool_list(chain, f"/networks/{chain}/pools",
                       {"page": 1, "sort": "h24_volume_usd_desc"}), "gt_volume")
    _add(_ds_boosted(chain), "ds_boost")

    if not sources:
        return hit["feed"] if hit else []

    # 2) 批量拿 DexScreener 快照（一次最多 30 个地址，price_feed 内部分批需调用方做）
    addrs = [v["addr"] for v in sources.values()]
    snap = {}
    for i in range(0, len(addrs), 30):
        snap.update(price_feed.dex_snapshot(chain, addrs[i:i + 30]))
    # dex_snapshot 的 key 是"调用方传进去的原样地址"，这里统一按小写(bsc)对齐
    snap_lc = {}
    for k, v in snap.items():
        snap_lc[k.lower() if chain == "bsc" else k] = v

    feed = []
    for key, meta in sources.items():
        s = snap_lc.get(key)
        if not s or not s.get("price"):
            continue  # DexScreener 查不到 -> 可能池子太小/rug，跳过
        age_h = None
        if s.get("pair_created_ms"):
            age_h = max(0.0, (now * 1000 - s["pair_created_ms"]) / 3_600_000.0)
        feed.append({
            "address": meta["addr"],
            "symbol": s.get("symbol") or "",
            "price": s["price"],
            "liq_usd": s.get("liq_usd") or 0.0,
            "vol_h24": s.get("vol_h24") or 0.0,
            "pc_m5": s.get("pc_m5") or 0.0,
            "pc_h1": s.get("pc_h1") or 0.0,
            "pc_h24": s.get("pc_h24") or 0.0,
            "buys_m5": s.get("buys_m5") or 0,
            "sells_m5": s.get("sells_m5") or 0,
            "pair_age_h": age_h,
            "sources": sorted(meta["src"]),
        })

    feed.sort(key=lambda x: (len(x["sources"]), x["vol_h24"]), reverse=True)
    _cache[chain] = {"ts": now, "feed": feed}
    return feed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import types
    t0 = time.time()
    f = get_trending("bsc", types.SimpleNamespace())
    print(f"{len(f)} candidates in {time.time()-t0:.1f}s")
    for e in f[:12]:
        print(f"  {e['symbol']:12s} src={e['sources']} 24h={e['pc_h24']:+.1f}% "
              f"vol=${e['vol_h24']:,.0f} liq=${e['liq_usd']:,.0f} age={e['pair_age_h']}")

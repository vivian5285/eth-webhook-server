#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
聪明钱种子名单自动发现器 —— 2026-09-04 新增（宝贝要求："写个脚本从 API 拉
'近 30 天 >5x 代币的 top traders' 自动初筛，再靠复盘的每钱包命中率过滤，
后面长期靠复盘数据自动汰换"）。

BSC + Solana 对称。**只用免费、无需 API key 的 GeckoTerminal 公开接口**
（api.geckoterminal.com/api/v2）。实测其免费限速偏严、burst 敏感，所以严格
控制每轮请求数（~5 列表 + ~12 OHLCV + ~12 trades 每条链），周跑两次。

三步：
  A. 收集"跑出来的票"（runner）：拉两条链的 trending_pools + new_pools，按
     流动性 / FDV / 24h 成交量 / 建池时间过滤，取成交量最高的一批做日线
     OHLCV，留下 30 天内 max(high)/起点 >= MIN_RUNNER_MULT（默认 5）的。
     并剔除"同尾号地址簇"（BSC 上大量诈骗盘是一个部署者批量发 ...7777 之类
     的 vanity 地址，配自己的对倒钱包刷 5x）。
  B. 对涨幅最大的前 N 个 runner 池拉最近 ~300 笔成交（GeckoTerminal
     /trades），收集买入方钱包。⚠️ 免费接口只给最近 ~300 笔，拿不到 3 周前
     池子开盘头一小时的真实早期买家。这里用可行的替代信号：**同一个钱包在
     多个不同 runner 里反复出现在买方** = 反复押中热点的"叙事猎手"，跨票
     交叉验证。真正的优胜劣汰交给主引擎的每钱包复盘命中率去做。
  C. 聚合、打分、排除机器人/对倒/合约/纯囤不卖的 bundler，输出 top N 到
     watchlist/discovered_wallets.json（与人工 smart_wallets.json 分开）。
     合并：与上一轮取并集，累计 runs_seen，分数取历史最高。

主引擎 signals/smart_money.load_seed_watchlist_into_db() 会把本文件里
score >= DISCOVERY_SCORE_MIN 的钱包灌进 watched_wallets（source='discovery'）。

用法：
  python -m discovery.wallet_finder            # 正式跑，写文件
  python -m discovery.wallet_finder --dry      # 只打印，不写文件
  python -m discovery.wallet_finder --chain solana
"""
import argparse
import json
import logging
import math
import os
import sys
import time
from collections import Counter, defaultdict

import requests

logger = logging.getLogger("chain_sniper.discovery")

GT_BASE = "https://api.geckoterminal.com/api/v2"
_UA = {"User-Agent": "chain-sniper-discovery/1.0", "Accept": "application/json;version=20230302"}

# Birdeye（免费 30K CU/月，SOL+BSC）——有 key 时 Stage B 用它的 top_traders
# 拿真实已实现盈亏 + sniper-bot 标签，比扒 GeckoTerminal /trades 干净得多。
_BIRDEYE_KEY = os.getenv("BIRDEYE_API_KEY", "").strip()
_BIRDEYE_BASE = "https://public-api.birdeye.so"

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
OUT_PATH = os.path.join(_ROOT, "watchlist", "discovered_wallets.json")
LAST_RUN_PATH = os.path.join(_HERE, "last_run.json")

CHAINS = [c.strip() for c in os.getenv("DISCOVERY_CHAINS", "solana,bsc").split(",") if c.strip()]


# ⚠️ 地址大小写：BSC 十六进制、大小写无关 → 统一转小写；Solana base58、
# **大小写敏感** → 绝不能转小写（转了拿去查 Helius 会对不上）。
def _norm(addr, chain):
    s = str(addr or "")
    return s.lower() if chain == "bsc" else s


_QUOTE_TOKENS = {
    "solana": {
        "So11111111111111111111111111111111111111112",   # WSOL
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",   # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",   # USDT
    },
    "bsc": {
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",     # WBNB
        "0x55d398326f99059ff775485246999027b3197955",     # USDT
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",     # USDC
        "0xe9e7cea3dedca5984780bafc599bd69add087d56",     # BUSD
        "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",     # BTCB
        "0x2170ed0880ac9a755fd29b2688956bd959f933f8",     # ETH
        "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82",     # CAKE
    },
}

_NON_WALLET = {
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",       # Jupiter v6 (solana)
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",       # Raydium AMM v4 (solana)
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",        # pump.fun (solana)
    "0x10ed43c718714eb63d5aa57b78b54704e256024e",         # PancakeSwap v2 router
    "0x13f4ea83d0bd40e75c8222255bc855a974568dd4",         # PancakeSwap SmartRouter
}

# ---- 可调参数（env 覆盖）----
MIN_RUNNER_MULT = float(os.getenv("DISCOVERY_MIN_RUNNER_MULT", "5"))
MIN_POOL_LIQ_USD = float(os.getenv("DISCOVERY_MIN_POOL_LIQ_USD", "15000"))
MIN_FDV_USD = float(os.getenv("DISCOVERY_MIN_FDV_USD", "150000"))
MIN_VOL_H24_USD = float(os.getenv("DISCOVERY_MIN_VOL_H24_USD", "80000"))
MAX_POOL_AGE_DAYS = float(os.getenv("DISCOVERY_MAX_POOL_AGE_DAYS", "45"))
TRENDING_PAGES = int(os.getenv("DISCOVERY_TRENDING_PAGES", "3"))
NEW_POOL_PAGES = int(os.getenv("DISCOVERY_NEW_POOL_PAGES", "2"))
RUNNER_OHLCV_CAP = int(os.getenv("DISCOVERY_RUNNER_OHLCV_CAP", "14"))
TRADES_CAP = int(os.getenv("DISCOVERY_TRADES_CAP", "12"))
CLUSTER_SUFFIX_LEN = int(os.getenv("DISCOVERY_CLUSTER_SUFFIX_LEN", "4"))
CLUSTER_MAX = int(os.getenv("DISCOVERY_CLUSTER_MAX", "3"))
MIN_RUNNER_HITS = int(os.getenv("DISCOVERY_MIN_RUNNER_HITS", "2"))
MIN_CONVICTION_USD = float(os.getenv("DISCOVERY_MIN_CONVICTION_USD", "150"))
MAX_WALLET_BUY_USD = float(os.getenv("DISCOVERY_MAX_WALLET_BUY_USD", "600000"))
MAX_WALLET_TRADES = int(os.getenv("DISCOVERY_MAX_WALLET_TRADES", "180"))
EARLY_BUY_WINDOW_H = float(os.getenv("DISCOVERY_EARLY_BUY_WINDOW_H", "24"))
MAX_DISCOVERED_PER_CHAIN = int(os.getenv("DISCOVERY_MAX_PER_CHAIN", "35"))
MAX_TOTAL_WALLETS = int(os.getenv("DISCOVERY_MAX_TOTAL_WALLETS", "160"))
STALE_PRUNE_DAYS = float(os.getenv("DISCOVERY_STALE_PRUNE_DAYS", "28"))
_SLEEP = float(os.getenv("DISCOVERY_REQ_SLEEP_SEC", "4.0"))

_DAY = 86400.0
_RETRYABLE = (requests.exceptions.SSLError, requests.exceptions.ConnectionError,
              requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError)


def _gt(path, params=None, tries=5):
    """GeckoTerminal GET，节流 + 429/网络错误指数退避。失败返回 None，绝不抛。"""
    url = f"{GT_BASE}{path}"
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=_UA, timeout=25)
            if r.status_code == 429:
                wait = min(_SLEEP * (2 ** i) + 3, 45)
                logger.warning("GT 429 %s -> backoff %.0fs", path.split("?")[0], wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            time.sleep(_SLEEP)
            return r.json()
        except _RETRYABLE as e:
            wait = min(_SLEEP * (2 ** i) + 3, 45)
            logger.warning("GT net-err %s (try %d): %s -> backoff %.0fs", path.split("?")[0], i + 1, e, wait)
            time.sleep(wait)
        except Exception as e:
            logger.warning("GT fail %s (try %d): %s", path.split("?")[0], i + 1, e)
            time.sleep(_SLEEP)
    return None


def _pool_attr(p):
    return p.get("attributes", {}) or {}


def _base_token_addr(p):
    rel = (p.get("relationships", {}) or {}).get("base_token", {}) or {}
    tid = (rel.get("data", {}) or {}).get("id", "") or ""
    return tid.split("_", 1)[1] if "_" in tid else ""


def _iso_to_ts(s):
    if not s:
        return 0.0
    try:
        from datetime import datetime, timezone
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return 0.0


# ---- Stage A：收集 runner 池 ----

def collect_candidate_pools(chain):
    seen = {}
    for kind, pages in (("trending_pools", TRENDING_PAGES), ("new_pools", NEW_POOL_PAGES)):
        for page in range(1, pages + 1):
            body = _gt(f"/networks/{chain}/{kind}", {"page": page})
            if not body or not body.get("data"):
                break
            for p in body["data"]:
                addr = _pool_attr(p).get("address")
                if addr and addr not in seen:
                    seen[addr] = p
    quote_set = {_norm(q, chain) for q in _QUOTE_TOKENS.get(chain, set())}
    now = time.time()
    out = []
    for addr, p in seen.items():
        a = _pool_attr(p)
        if float(a.get("reserve_in_usd") or 0) < MIN_POOL_LIQ_USD:
            continue
        if float(a.get("fdv_usd") or 0) < MIN_FDV_USD:
            continue
        vol_h24 = float((a.get("volume_usd") or {}).get("h24") or 0)
        if vol_h24 < MIN_VOL_H24_USD:
            continue
        created = _iso_to_ts(a.get("pool_created_at"))
        if created and (now - created) > MAX_POOL_AGE_DAYS * _DAY:
            continue
        base = _norm(_base_token_addr(p), chain)
        if not base or base in quote_set:
            continue
        out.append({
            "pool": addr, "base_token": base, "name": a.get("name", ""),
            "created_ts": created, "liq_usd": float(a.get("reserve_in_usd") or 0),
            "vol_h24": vol_h24, "fdv": float(a.get("fdv_usd") or 0),
        })
    out.sort(key=lambda x: x["vol_h24"], reverse=True)
    logger.info("[%s] candidate pools after filter: %d", chain, len(out))
    return out


def _drop_vanity_clusters(runners, chain):
    """同尾号地址簇 = 一个部署者批量发币刷盘。某个 4 字尾号出现 > CLUSTER_MAX
    次，把该尾号的所有 runner 全部丢弃。"""
    suf = Counter()
    for r in runners:
        bt = r["base_token"]
        if chain == "bsc" and bt.startswith("0x") and len(bt) >= 2 + CLUSTER_SUFFIX_LEN:
            suf[bt[-CLUSTER_SUFFIX_LEN:]] += 1
        elif chain == "solana" and len(bt) >= CLUSTER_SUFFIX_LEN:
            suf[bt[-CLUSTER_SUFFIX_LEN:].lower()] += 1
    bad = {s for s, n in suf.items() if n > CLUSTER_MAX}
    if not bad:
        return runners
    kept = []
    for r in runners:
        bt = r["base_token"]
        tail = bt[-CLUSTER_SUFFIX_LEN:].lower() if chain == "solana" else bt[-CLUSTER_SUFFIX_LEN:]
        if tail in bad:
            continue
        kept.append(r)
    logger.warning("[%s] dropped %d runner(s) from vanity-address cluster(s) %s",
                   chain, len(runners) - len(kept), sorted(bad))
    return kept


def pool_30d_multiple(chain, pool):
    body = _gt(f"/networks/{chain}/pools/{pool}/ohlcv/day", {"limit": 45})
    if not body:
        return 0.0
    rows = ((body.get("data", {}) or {}).get("attributes", {}) or {}).get("ohlcv_list", []) or []
    if len(rows) < 2:
        return 0.0
    rows_sorted = sorted(rows, key=lambda x: x[0])  # [ts,o,h,l,c,v]
    start_open = float(rows_sorted[0][1] or 0)
    peak_high = max(float(r[2] or 0) for r in rows_sorted)
    if start_open <= 0:
        lows = [float(r[3] or 0) for r in rows_sorted if float(r[3] or 0) > 0]
        start_open = min(lows) if lows else 0.0
    return (peak_high / start_open) if start_open > 0 else 0.0


# ---- Stage B：runner 池的买家 ----

def pool_buyers(chain, pool, base_token, pool_created_ts):
    body = _gt(f"/networks/{chain}/pools/{pool}/trades")
    if not body or not body.get("data"):
        return {}
    early_cut = (pool_created_ts + EARLY_BUY_WINDOW_H * 3600) if pool_created_ts else 0
    buyers = defaultdict(lambda: {"buy_usd": 0.0, "buys": 0, "sells": 0, "first_ts": 0.0, "early": False})
    for t in body["data"]:
        a = t.get("attributes", {}) or {}
        w = _norm(a.get("tx_from_address"), chain)
        if not w or w in _NON_WALLET:
            continue
        kind = a.get("kind", "")
        ts = _iso_to_ts(a.get("block_timestamp"))
        usd = float(a.get("volume_in_usd") or 0)
        rec = buyers[w]
        if kind == "buy" and _norm(a.get("to_token_address"), chain) == base_token:
            rec["buys"] += 1
            rec["buy_usd"] += usd
            if rec["first_ts"] == 0 or (ts and ts < rec["first_ts"]):
                rec["first_ts"] = ts
            if early_cut and ts and ts <= early_cut:
                rec["early"] = True
        elif kind == "sell":
            rec["sells"] += 1
    return dict(buyers)


# ---- Stage B(v2)：Birdeye top_traders（有 BIRDEYE_API_KEY 时用）----

def _birdeye(path, params, chain):
    for i in range(3):
        try:
            r = requests.get(_BIRDEYE_BASE + path, timeout=20,
                             headers={"X-API-KEY": _BIRDEYE_KEY, "accept": "application/json",
                                      "x-chain": chain, "User-Agent": _UA["User-Agent"]},
                             params=params)
            if r.status_code == 429:
                time.sleep(2 * (i + 1))
                continue
            r.raise_for_status()
            j = r.json()
            return j.get("data") if isinstance(j, dict) else None
        except Exception as e:
            logger.warning("birdeye %s fail (try %d): %s", path, i + 1, e)
            time.sleep(1.5)
    return None


def birdeye_gainers(chain, limit=20):
    """Birdeye 全局周度 PnL 榜（不依赖 runner token）。返回同 pool_buyers 形状，
    每个钱包算 1 个"runner hit"(用一个哨兵 token key)，pnl_usd = realized_pnl。
    过滤：realized_pnl>0、trade_count>=5（滤掉单笔运气/囤货浮盈）。"""
    data = _birdeye("/trader/gainers-losers", {
        "type": "1W", "sort_by": "PnL", "sort_type": "desc", "offset": 0, "limit": limit,
    }, chain)
    items = (data or {}).get("items") or []
    out = {}
    for it in items:
        w = it.get("address")
        rp = float(it.get("realized_pnl") or 0)
        tc = int(it.get("trade_count") or 0)
        if not w or rp <= 0 or tc < 5:
            continue
        out[w if chain != "bsc" else w.lower()] = {
            "buy_usd": float(it.get("volume") or 0), "buys": max(tc // 2, 1),
            "sells": max(tc - tc // 2, 1), "first_ts": 0.0, "early": False,
            "pnl_usd": rp, "_gainers": True,
        }
    time.sleep(1.0)
    return out


def birdeye_buyers(chain, token):
    """返回跟 pool_buyers 同形状的 {wallet: {...}}，外加 pnl_usd（真实已实现盈亏）。
    过滤掉带 sniper-bot 标签的（那是另一个游戏）。"""
    data = _birdeye("/defi/v2/tokens/top_traders", {
        "address": token, "time_frame": "24h", "sort_type": "desc",
        "sort_by": "volume", "offset": 0, "limit": 10,   # 免费档 limit 上限 10
    }, chain)
    items = (data or {}).get("items") or []
    out = {}
    for it in items:
        w = it.get("owner")
        if not w:
            continue
        tags = it.get("tags") or []
        if any("sniper" in str(t).lower() or "bot" in str(t).lower() for t in tags):
            continue
        buys = int(it.get("tradeBuy") or 0)
        sells = int(it.get("tradeSell") or 0)
        if buys <= 0:
            continue
        out[w if chain != "bsc" else w.lower()] = {
            "buy_usd": float(it.get("volumeBuyUSD") or it.get("volumeBuy") or 0),
            "buys": buys, "sells": sells, "first_ts": 0.0, "early": False,
            "pnl_usd": float(it.get("realizedPnl") or 0),
        }
    time.sleep(1.0)  # 免费档限速
    return out


# ---- Stage C：聚合打分 ----

def _looks_like_bundler(g):
    """纯囤不卖 / 对倒 bundler：买很多次几乎不卖。BSC 上 ...7777 那批就是这个
    形态（buy 60-84 / sell 1-7）。"""
    b, s = g["buy_count"], g["sell_count"]
    if s == 0:
        return True
    if b >= 25 and s <= 3:
        return True
    if (s / max(b, 1)) < 0.12:
        return True
    return False


def _score(w):
    rh = min(w["runner_hits"] / 6.0, 1.0)
    eh = min(w["early_hits"] / 3.0, 1.0)
    money = min(math.log10(max(w["total_buy_usd"], 1.0)) / 5.0, 1.0)  # $1->0, $100k->1
    sr = w["sell_count"] / max(w["buy_count"], 1)
    balance = 1.0 if 0.25 <= sr <= 2.5 else (0.5 if 0.12 <= sr < 0.25 or 2.5 < sr <= 5 else 0.0)
    pnl = float(w.get("pnl_usd") or 0)
    if w.get("pnl_usd") is not None and (w["buy_count"] or w["sell_count"]):
        # 有真实已实现盈亏（Birdeye 路径）：正盈利加分、亏损扣分
        pnl_term = max(-1.0, min(1.0, pnl / 5000.0))   # ±$5k 打满
        s = 0.34 * rh + 0.12 * eh + 0.16 * money + 0.16 * balance + 0.22 * ((pnl_term + 1) / 2)
    else:
        s = 0.40 * rh + 0.18 * eh + 0.22 * money + 0.20 * balance
    if w["total_buy_usd"] < MIN_CONVICTION_USD:   # 微额撒网型，不是有信念的交易
        s *= 0.55
    # 上了周度 PnL 榜、真实已实现盈利够大的，给个下限，别被 runner_hits=0 拖死
    if w.get("from_gainers") and float(w.get("pnl_usd") or 0) >= 3000:
        s = max(s, 0.52)
    return round(s * 100, 1)


def discover_chain(chain):
    use_birdeye = bool(_BIRDEYE_KEY)
    buyers_fn = (lambda r: birdeye_buyers(chain, r["base_token"])) if use_birdeye \
        else (lambda r: pool_buyers(chain, r["pool"], r["base_token"], r["created_ts"]))
    logger.info("[%s] Stage B source: %s", chain, "birdeye_top_traders" if use_birdeye else "geckoterminal_trades")
    pools = collect_candidate_pools(chain)
    runners = []
    for p in pools[:RUNNER_OHLCV_CAP]:
        mult = pool_30d_multiple(chain, p["pool"])
        if mult >= MIN_RUNNER_MULT:
            p["mult"] = round(mult, 2)
            runners.append(p)
    runners = _drop_vanity_clusters(runners, chain)
    runners.sort(key=lambda r: r["mult"], reverse=True)
    runners = runners[:TRADES_CAP]
    logger.info("[%s] runner pools (>= %.0fx, post-filter): %d", chain, MIN_RUNNER_MULT, len(runners))

    agg = defaultdict(lambda: {
        "runner_tokens": set(), "early_tokens": set(),
        "total_buy_usd": 0.0, "buy_count": 0, "sell_count": 0, "first_ts": 0.0,
        "pnl_usd": 0.0, "has_pnl": False,
    })
    base_token_addrs = {r["base_token"] for r in runners}
    sources = [(r["base_token"], buyers_fn(r)) for r in runners]
    if use_birdeye:
        # 全局周度 PnL 榜作为额外来源（不依赖 runner token）
        sources.append(("__gainers__", birdeye_gainers(chain)))

    for tok_key, buyers in sources:
        for w, rec in buyers.items():
            if rec["buys"] <= 0:
                continue
            g = agg[w]
            g["runner_tokens"].add(tok_key)
            if rec.get("_gainers"):
                g["from_gainers"] = True
            if rec["early"]:
                g["early_tokens"].add(tok_key)
            g["total_buy_usd"] += rec["buy_usd"]
            g["buy_count"] += rec["buys"]
            g["sell_count"] += rec["sells"]
            if "pnl_usd" in rec:
                g["pnl_usd"] += float(rec["pnl_usd"])
                g["has_pnl"] = True
            if g["first_ts"] == 0 or (rec["first_ts"] and rec["first_ts"] < g["first_ts"]):
                g["first_ts"] = rec["first_ts"]

    # Birdeye 路径有真实已实现盈亏当质量信号，交叉验证放宽到 1（否则 runner
    # 太少时几乎筛不出人）；keyless 路径仍要求 MIN_RUNNER_HITS。
    min_hits = 1 if use_birdeye else MIN_RUNNER_HITS
    out, n_bundler = [], 0
    for w, g in agg.items():
        # 只上过 gainers 榜、没在任何 runner 里出现的，要求它确实有正盈利
        only_gainers = g.get("from_gainers") and len(g["runner_tokens"]) == 1 and "__gainers__" in g["runner_tokens"]
        if len(g["runner_tokens"]) < min_hits:
            continue
        if only_gainers and float(g.get("pnl_usd") or 0) <= 0:
            continue
        if g["total_buy_usd"] > MAX_WALLET_BUY_USD:
            continue
        if (g["buy_count"] + g["sell_count"]) > MAX_WALLET_TRADES:
            continue
        if w in base_token_addrs:
            continue
        if _looks_like_bundler(g):
            n_bundler += 1
            continue
        real_runners = sorted(t for t in g["runner_tokens"] if t != "__gainers__")
        row = {
            "address": w, "chain": chain,
            "runner_hits": len(real_runners), "early_hits": len(g["early_tokens"]),
            "total_buy_usd": round(g["total_buy_usd"], 1),
            "buy_count": g["buy_count"], "sell_count": g["sell_count"],
            "first_seen_ts": g["first_ts"],
            "from_gainers": bool(g.get("from_gainers")),
            "sample_runners": real_runners[:6],
        }
        if g["has_pnl"]:
            row["pnl_usd"] = round(g["pnl_usd"], 1)
        row["score"] = _score(row)
        out.append(row)
    out.sort(key=lambda r: r["score"], reverse=True)
    kept = out[:MAX_DISCOVERED_PER_CHAIN]
    logger.info("[%s] wallets: scored=%d bundler-dropped=%d kept=%d", chain, len(out), n_bundler, len(kept))
    return kept, runners


# ---- 合并写文件 ----

def _load_existing():
    try:
        with open(OUT_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"wallets": []}


def merge_and_write(new_rows, runners_by_chain, dry=False):
    today = time.strftime("%Y-%m-%d", time.gmtime())
    now = time.time()
    existing = {(w["address"], w["chain"]): w for w in _load_existing().get("wallets", [])}

    for r in new_rows:
        key = (r["address"], r["chain"])
        if key in existing:
            e = existing[key]
            e["runs_seen"] = int(e.get("runs_seen", 1)) + 1
            e["last_seen_run"] = today
            e["score"] = max(float(e.get("score", 0)), r["score"])
            e["runner_hits"] = max(int(e.get("runner_hits", 0)), r["runner_hits"])
            e["early_hits"] = max(int(e.get("early_hits", 0)), r["early_hits"])
            e["total_buy_usd"] = round(max(float(e.get("total_buy_usd", 0)), r["total_buy_usd"]), 1)
            e["sample_runners"] = r["sample_runners"]
            if "pnl_usd" in r:
                e["pnl_usd"] = round(float(e.get("pnl_usd", 0)) + r["pnl_usd"], 1)  # 累计跨轮已实现盈亏
        else:
            r.update(runs_seen=1, first_discovered=today, first_discovered_ts=now, last_seen_run=today)
            existing[key] = r

    merged = list(existing.values())

    def _keep(w):
        if int(w.get("runs_seen", 1)) >= 2:
            return True
        return (now - float(w.get("first_discovered_ts", now))) / _DAY < STALE_PRUNE_DAYS

    before = len(merged)
    merged = [w for w in merged if _keep(w)]
    merged.sort(key=lambda w: (float(w.get("score", 0)), int(w.get("runs_seen", 1))), reverse=True)
    merged = merged[:MAX_TOTAL_WALLETS]

    payload = {
        "generated_at": now,
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": "geckoterminal+birdeye" if _BIRDEYE_KEY else "geckoterminal_keyless",
        "params": {"min_runner_mult": MIN_RUNNER_MULT, "min_runner_hits": MIN_RUNNER_HITS,
                   "min_fdv_usd": MIN_FDV_USD, "min_vol_h24_usd": MIN_VOL_H24_USD},
        "counts": {
            "wallets_total": len(merged), "pruned_stale": before - len(merged),
            **{f"runners_{c}": len(runners_by_chain.get(c, [])) for c in CHAINS},
            **{f"new_wallets_{c}": sum(1 for r in new_rows if r["chain"] == c) for c in CHAINS},
        },
        "wallets": merged,
    }
    if dry:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        print("\n[--dry] 不写文件")
        return payload
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, OUT_PATH)
    with open(LAST_RUN_PATH, "w", encoding="utf-8") as f:
        json.dump({k: payload[k] for k in ("generated_at_iso", "method", "counts")}, f, ensure_ascii=False, indent=2)
    logger.info("wrote %s (%d wallets)", OUT_PATH, len(merged))
    return payload


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印，不写文件")
    ap.add_argument("--chain", choices=["solana", "bsc"], help="只跑一条链")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("urllib3", "requests", "urllib3.connectionpool"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    chains = [args.chain] if args.chain else CHAINS
    all_new, runners_by_chain = [], {}
    for ch in chains:
        try:
            rows, runners = discover_chain(ch)
        except Exception as e:
            logger.error("discover_chain(%s) failed: %s", ch, e, exc_info=True)
            rows, runners = [], []
        all_new.extend(rows)
        runners_by_chain[ch] = runners
    payload = merge_and_write(all_new, runners_by_chain, dry=args.dry)
    logger.info("done. counts=%s", payload["counts"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

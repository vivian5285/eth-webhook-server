#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Solana链适配器，实现 ChainAdapter 接口。Phase 1只做只读部分（元数据/价格/买家增长/
钱包事件轮询），execute_buy/execute_sell 留到 Phase 3 真正要下单时再实现——现在没有
资金、也没必要给还没验证过的信号层接上真钱执行通道。

数据源：Helius（DAS API 查代币元数据+价格，标准 Solana RPC 查签名历史，
parsed transaction history 端点粗略估计钱包活动），价格查不到 DAS 的 price_info 时
退化用 Jupiter 公开价格 API 兜底（新发的 pump.fun 币种 DAS 常常没有验证过的价格）。
"""
import logging
import time
from decimal import Decimal

import requests

from adapters.base import ChainAdapter
from models import TokenInfo, BuyerStats, WalletEvent

logger = logging.getLogger(__name__)

# 2026-09-05修复：price.jup.ag这个域名Jupiter已经彻底停用(DNS都解析不
# 出来了，不是限流/超时那种临时故障)，之前"getAsset限流+Jupiter兜底也
# 失败"两条路都走不通，导致这两个函数对同一批热门token每~4秒就打一轮
# 双重失败日志、Helius配额也白白浪费在注定失败的请求上。改成Jupiter
# 现在的价格API v3(api.jup.ag，已验证真实可用)，响应格式也变了——v6是
# {"data":{mint:{"price":...}}}，v3是{mint:{"usdPrice":...}}，不再套
# "data"这一层，字段名也从price改成usdPrice。
JUPITER_PRICE_API = "https://api.jup.ag/price/v3"

# 报价币/中转币——swap 的路由腿会把这些临时转进转出被监控钱包，不能当成
# "聪明钱买了某个新币"的信号（2026-09-04 修复：以前唯一被记录的候选是 WSOL
# 本身，就是这个 bug）。
_QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",   # WSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",   # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",   # USDT
}


class SolanaAdapter(ChainAdapter):
    chain_name = "solana"

    def __init__(self, cfg):
        self.cfg = cfg
        self._rpc_url = f"https://mainnet.helius-rpc.com/?api-key={cfg.helius_api_key}"
        self._parsed_tx_base = f"https://api.helius.xyz/v0/addresses"
        self._last_poll = {}  # wallet -> ts，每钱包错峰轮询，别把免费额度一次打光
        # 2026-09-05新增：get_current_price短TTL缓存——main.py/scorer.py/
        # exit_manager.py三处调用方各自独立轮询，同一个热门token经常在
        # 几秒内被问好几次价格，实测同一个pump.fun新币能在4秒内被连续查
        # 3-4次，每次都是"Helius限流+Jupiter兜底"两次真实请求，白白浪费
        # 配额、也是日志刷屏的主因。缓存成功和失败结果都缓存(失败也缓存
        # 是刻意的：真出故障时能自然形成退避，不会在配额已经紧张时越问
        # 越勤)，TTL设得比这套系统的决策粒度短得多，不影响信号时效性。
        self._price_cache = {}  # token_address -> (price_or_None, cached_at_ts)
        self._price_cache_ttl = 12.0

    def _cached_price(self, token_address):
        entry = self._price_cache.get(token_address)
        if entry and (time.time() - entry[1]) < self._price_cache_ttl:
            return True, entry[0]
        return False, None

    def _rpc(self, method, params, timeout=10):
        try:
            r = requests.post(
                self._rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=timeout,
            )
            r.raise_for_status()
            body = r.json()
            if "error" in body:
                logger.warning("helius rpc error method=%s err=%s", method, body["error"])
                return None
            return body.get("result")
        except Exception as e:
            logger.warning("helius rpc failed method=%s err=%s", method, e)
            return None

    # ---- 元数据 / 价格 ----

    def get_token_metadata(self, token_address):
        result = self._rpc("getAsset", {"id": token_address})
        if not result:
            return TokenInfo(chain=self.chain_name, address=token_address)
        content = result.get("content", {}) or {}
        metadata = content.get("metadata", {}) or {}
        token_info = result.get("token_info", {}) or {}
        return TokenInfo(
            chain=self.chain_name,
            address=token_address,
            symbol=metadata.get("symbol", "") or token_info.get("symbol", ""),
            name=metadata.get("name", ""),
            decimals=int(token_info.get("decimals", 9) or 9),
        )

    def get_current_price(self, token_address):
        hit, cached = self._cached_price(token_address)
        if hit:
            return cached

        price = self._get_current_price_uncached(token_address)
        self._price_cache[token_address] = (price, time.time())
        return price

    def _get_current_price_uncached(self, token_address):
        result = self._rpc("getAsset", {"id": token_address})
        if result:
            price = (result.get("token_info", {}) or {}).get("price_info", {}) or {}
            per_token = price.get("price_per_token")
            if per_token:
                return Decimal(str(per_token))

        # DAS 没有验证过价格（常见于刚发的新币）——退化用 Jupiter 公开价格 API
        try:
            r = requests.get(JUPITER_PRICE_API, params={"ids": token_address}, timeout=8)
            r.raise_for_status()
            data = r.json().get(token_address)
            if data and data.get("usdPrice"):
                return Decimal(str(data["usdPrice"]))
        except Exception as e:
            logger.warning("jupiter price fallback failed token=%s err=%s", token_address, e)
        return None

    # ---- 真实增长过滤用：独立买家数 ----
    #
    # 2026-08-12 修正：最早版本用 getSignaturesForAddress 查代币mint地址本身的签名历史，
    # 实测哪怕对USDC这种巨量交易的代币也只能查到几小时前的旧签名——因为标准 Transfer
    # 指令的account keys里不一定包含mint地址本身，getSignaturesForAddress基于account key
    # 索引，查不准。换成 Helius Enhanced Transactions 端点（跟 get_watched_wallet_events
    # 用的是同一个API），实测能拿到1-2秒延迟的真实新鲜数据，这个端点是按地址的完整活动
    # 解析出来的，不依赖account key是否包含mint本身。

    def get_recent_buyers(self, token_address, window_sec):
        now = time.time()
        since_prev_ts = now - window_sec * 2
        try:
            r = requests.get(
                f"{self._parsed_tx_base}/{token_address}/transactions",
                params={"api-key": self.cfg.helius_api_key, "limit": 100},
                timeout=15,
            )
            r.raise_for_status()
            txs = r.json()
        except Exception as e:
            logger.warning("get_recent_buyers failed token=%s err=%s", token_address, e)
            return BuyerStats(window_sec=window_sec, unique_buyers=0)

        buyers_now, buyers_prev, total_buys = set(), set(), 0
        since_now_ts = now - window_sec
        for tx in txs or []:
            ts = tx.get("timestamp", 0)
            if ts < since_prev_ts:
                continue
            for transfer in tx.get("tokenTransfers", []) or []:
                if transfer.get("mint") != token_address:
                    continue
                buyer = transfer.get("toUserAccount")
                if not buyer:
                    continue
                if ts >= since_now_ts:
                    buyers_now.add(buyer)
                    total_buys += 1
                elif ts >= since_prev_ts:
                    buyers_prev.add(buyer)

        return BuyerStats(
            window_sec=window_sec, unique_buyers=len(buyers_now),
            unique_buyers_prev_window=len(buyers_prev), total_buys=total_buys,
        )

    # ---- 聪明钱事件（Phase1轮询兜底，Phase2换Helius webhook实时推送）----

    def get_watched_wallet_events(self, since_ts):
        import time as _t
        import db
        events = []
        # 每钱包错峰：一个 poll 周期内只轮询"到点"的钱包，别一次把免费额度打光。
        # ⚠️ 本函数是同步的、被 async signal_loop 直接调用——绝不能在这里
        # time.sleep()，那会卡死整个 asyncio 事件循环（连 exit_manager 都停摆）。
        # 靠 wallet_poll_interval_sec 错峰 + 429 直接跳过本轮，不 sleep。
        per_wallet = float(getattr(self.cfg, "wallet_poll_interval_sec", 90))
        now = _t.time()
        for w in db.load_watchlist(chain=self.chain_name):
            addr = w["address"]
            if now - self._last_poll.get(addr, 0) < per_wallet:
                continue
            self._last_poll[addr] = now
            try:
                r = requests.get(
                    f"{self._parsed_tx_base}/{addr}/transactions",
                    params={"api-key": self.cfg.helius_api_key, "limit": 15},
                    timeout=12,
                )
                if r.status_code == 429:
                    logger.warning("helius 429 for wallet=%s, skip this round", addr)
                    self._last_poll[addr] = now + 60  # 下一轮更晚再试
                    continue
                r.raise_for_status()
                txs = r.json()
            except Exception as e:
                logger.warning("get_watched_wallet_events failed wallet=%s err=%s", addr, e)
                continue
            for tx in txs or []:
                ts = tx.get("timestamp", 0)
                if ts < since_ts:
                    continue
                tx_type = (tx.get("type") or "").upper()
                if tx_type not in ("SWAP", "TRANSFER"):
                    continue
                bought = self._extract_bought_token(tx, addr, tx_type)
                if bought is None:
                    continue
                mint, amount = bought
                events.append(WalletEvent(
                    chain=self.chain_name, wallet=addr, token_address=mint, side="buy",
                    amount=Decimal(str(amount or 0)), tx_hash=tx.get("signature", ""), ts=ts,
                ))
        return events

    @staticmethod
    def _extract_bought_token(tx, addr, tx_type):
        """从一笔 Helius 解析交易里挑出"这个钱包这次真正买进的那个币"。
        SWAP：优先用 events.swap.tokenOutputs（钱包收到的），排除报价币/中转币，
              取金额最大的那个；没有结构化 events 时退化到 tokenTransfers。
        TRANSFER：钱包收到的非报价币转账里取最大的。
        返回 (mint, amount) 或 None（None = 这不是一次"买新币"，比如是卖出或
        只在倒腾 WSOL/USDC）。"""
        candidates = []  # (mint, amount)

        swap = ((tx.get("events") or {}).get("swap") or {})
        outputs = swap.get("tokenOutputs") or []
        for o in outputs:
            if o.get("userAccount") and o.get("userAccount") != addr:
                continue
            mint = o.get("mint", "")
            if not mint or mint in _QUOTE_MINTS:
                continue
            raw = o.get("rawTokenAmount") or {}
            try:
                amt = float(raw.get("tokenAmount", 0)) / (10 ** int(raw.get("decimals", 0) or 0))
            except Exception:
                amt = 0.0
            candidates.append((mint, amt))

        if not candidates:
            for t in tx.get("tokenTransfers", []) or []:
                if t.get("toUserAccount") != addr:
                    continue
                mint = t.get("mint", "")
                if not mint or mint in _QUOTE_MINTS:
                    continue
                try:
                    amt = float(t.get("tokenAmount", 0) or 0)
                except Exception:
                    amt = 0.0
                candidates.append((mint, amt))

        if not candidates:
            return None
        return max(candidates, key=lambda c: c[1])

    # ---- 执行（Phase 3 才实现，现在没钱包资金，也没必要接）----

    def execute_buy(self, token_address, quote_amount, slippage_bps):
        raise NotImplementedError("execute_buy 留到 Phase 3 真钱冒烟测试时实现")

    def execute_sell(self, token_address, token_amount, slippage_bps):
        raise NotImplementedError("execute_sell 留到 Phase 3 真钱冒烟测试时实现")

    def get_wallet_balance(self):
        priv = self.cfg.solana_hot_wallet_private_key
        if not priv:
            return None
        # Phase1未接钱包，占位——Phase3实现签名后用 getBalance 查真实SOL余额
        return None


if __name__ == "__main__":
    import config
    logging.basicConfig(level=logging.INFO)
    cfg = config.load_config()
    adapter = SolanaAdapter(cfg)
    # USDC on Solana，验证过价格、流动性充足，拿来当已知可用的sanity check标的
    usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    print("metadata:", adapter.get_token_metadata(usdc))
    print("price:", adapter.get_current_price(usdc))

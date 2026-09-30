#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BSC 链适配器，实现 ChainAdapter 接口。2026-09-04：Moralis 免费额度停用后，
BSC 的聪明钱触发改走以下两条路，都不强制要付费 key：

  1. 【首选，有 key 才用】BscScan/Etherscan-V2 `account/tokentx` —— 每个监控
     钱包一次调用拿它最近的 ERC20 转入/转出。设 BSCSCAN_API_KEY(免费即时申请)
     后自动启用；免费档 5 req/s、10 万/天，够十几个钱包 60s 轮询。
  2. 【兜底，keyless】免费公共 BSC 节点分批扫最近 N 个区块的完整交易，挑
     tx.from 命中监控名单的，再拿回执解析出它买进的那个非报价币代币。
     公共节点普遍**禁止无 address 过滤的 eth_getLogs**（实测 publicnode/
     bnbchain/drpc 全部拒绝），所以只能扫块，区块数硬上限压到很小。

价格/元数据/买家增长一律走 keyless DexScreener（signals/price_feed.py），
不烧 Birdeye 的 30K CU。execute_buy/execute_sell 留到 Phase 3 真钱时实现。
"""
import logging
import time
from decimal import Decimal

import requests

from adapters.base import ChainAdapter
from models import TokenInfo, BuyerStats, WalletEvent
import signals.price_feed as price_feed

logger = logging.getLogger(__name__)

# keccak256("Transfer(address,address,uint256)")
_TRANSFER_SIG = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# 2026-09-04 实测：多数免费公共 BSC 节点禁止「无 address 过滤的 eth_getLogs」。
# 下面这两个允许 topics-only getLogs（keyless）——这是 get_watched_wallet_events
# 一把捞的关键。其余作兜底（只够 eth_blockNumber / getBlockByNumber）。
_DEFAULT_RPCS = [
    "https://1rpc.io/bnb",
    "https://0.48.club",
    "https://bsc-rpc.publicnode.com",
    "https://bsc-dataseed.bnbchain.org",
]

# 报价币/主流币（小写）——被监控钱包收到这些不算"买了个新币"
_QUOTE_BSC = {
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
    "0x55d398326f99059ff775485246999027b3197955",  # USDT
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # USDC
    "0xe9e7cea3dedca5984780bafc599bd69add087d56",  # BUSD
    "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",  # BTCB
    "0x2170ed0880ac9a755fd29b2688956bd959f933f8",  # ETH
    "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82",  # CAKE
}

_BSC_BLOCK_SEC = 3.0
_UA = {"User-Agent": "chain-sniper/1.0"}


def _topic_addr(topic):
    """topics[1/2] 是 32 字节左填充地址 -> '0x' + 后 40 hex（小写）。"""
    t = (topic or "").lower().replace("0x", "")
    return "0x" + t[-40:] if len(t) >= 40 else ""


class BscAdapter(ChainAdapter):
    chain_name = "bsc"

    def __init__(self, cfg):
        self.cfg = cfg
        raw = getattr(cfg, "bsc_rpc_urls", "") or ""
        self._rpcs = [u.strip() for u in raw.split(",") if u.strip()] or list(_DEFAULT_RPCS)
        self._rpc_i = 0
        self._last_block = 0
        self._last_poll = 0.0
        self._poll_interval = float(getattr(cfg, "bsc_poll_interval_sec", 60))
        self._max_blocks = int(getattr(cfg, "bsc_max_blocks_per_poll", 300))
        self._seen_tx = set()  # (eoa,txhash,token) 去重

    # ---- RPC ----

    def _rpc(self, method, params, timeout=15):
        # eth_getLogs 只有前两个节点支持 topics-only；其它方法哪个都行。
        order = self._rpcs if method != "eth_getLogs" else self._rpcs[:2] or self._rpcs
        for _ in range(len(order)):
            url = order[self._rpc_i % len(order)]
            self._rpc_i += 1
            try:
                r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                             "params": params}, timeout=timeout, headers=_UA)
                r.raise_for_status()
                body = r.json()
                if isinstance(body, dict) and "error" in body:
                    logger.warning("bsc rpc error %s @ %s: %s", method, url, body["error"])
                    continue
                return body.get("result")
            except Exception as e:
                logger.warning("bsc rpc fail %s @ %s: %s", method, url, e)
        return None

    # ---- 元数据 / 价格 / 买家增长（DexScreener）----

    def _ds(self, token):
        try:
            return price_feed.dex_snapshot("bsc", [token]).get(token)
        except Exception as e:
            logger.warning("bsc dexscreener fail token=%s err=%s", token, e)
            return None

    def get_token_metadata(self, token_address):
        info = self._ds(token_address) or {}
        return TokenInfo(chain="bsc", address=token_address,
                         symbol=info.get("symbol", ""), name=info.get("name", ""), decimals=18)

    def get_current_price(self, token_address):
        info = self._ds(token_address)
        if info and info.get("price"):
            return Decimal(str(info["price"]))
        return None

    def get_recent_buyers(self, token_address, window_sec):
        info = self._ds(token_address) or {}
        # DexScreener 给的是 m5 买入笔数（≈独立买家数的上估，过滤会略宽松）——
        # 免费无 key、一次调用，够 Phase1 用。
        buys = int(info.get("buys_m5") or 0)
        return BuyerStats(window_sec=window_sec, unique_buyers=buys, total_buys=buys)

    # ---- 聪明钱事件（keyless eth_getLogs 一把捞）----

    def get_watched_wallet_events(self, since_ts):
        """ERC20 Transfer 的 `to` 是 topics[2]。一次 eth_getLogs、
        topics=[Transfer签名, null, [所有监控钱包的32字节左填充地址]]，就能
        捞出这段区块里这些钱包收到的所有代币（=买入/转入）。2026-09-04 实测
        1rpc.io/bnb 和 0.48.club 允许这种 topics-only getLogs（keyless）。"""
        import db
        now = time.time()
        if now - self._last_poll < self._poll_interval:
            return []
        self._last_poll = now
        watched = [w["address"].lower() for w in db.load_watchlist(chain="bsc")]
        if not watched:
            return []
        try:
            return self._events_via_getlogs(watched, since_ts)
        except Exception as e:
            logger.warning("bsc get_watched_wallet_events failed: %s", e)
            return []

    def _events_via_getlogs(self, watched, since_ts):
        head_hex = self._rpc("eth_blockNumber", [])
        if not head_hex:
            return []
        head = int(head_hex, 16)
        cap = self._max_blocks
        if self._last_block <= 0:
            span = int((time.time() - since_ts) / _BSC_BLOCK_SEC)
            from_block = head - min(cap, max(1, span))
        else:
            from_block = max(self._last_block + 1, head - cap)
        if from_block > head:
            self._last_block = head
            return []

        padded = ["0x" + "0" * 24 + a.replace("0x", "") for a in watched]
        logs = self._rpc("eth_getLogs", [{
            "fromBlock": hex(from_block), "toBlock": hex(head),
            "topics": [_TRANSFER_SIG, None, padded],
        }], timeout=25)
        self._last_block = head
        if not logs:
            return []
        if len(logs) > 3000:
            # 某个监控地址其实是交易所/大户热钱包——不该在名单里。只处理最近的，
            # 并留个警告让人去 deactivate。
            logger.warning("bsc getLogs returned %d logs for %d wallets — a watched "
                           "address is likely an exchange/bot wallet", len(logs), len(watched))
            logs = logs[-3000:]

        # 时间戳用近似：ts ≈ now - (head - blockNumber) * 3s。40 个区块跨度
        # 才 ~2 分钟，since_ts 过滤带这点误差完全够；省掉几十次 getBlockByNumber。
        now2 = time.time()
        watched_set = set(watched)
        events = []
        for lg in logs:
            token = (lg.get("address") or "").lower()
            if not token or token in _QUOTE_BSC:
                continue
            topics = lg.get("topics") or []
            if len(topics) < 3:
                continue
            to_eoa = _topic_addr(topics[2])
            if to_eoa not in watched_set:
                continue
            bn = int(lg["blockNumber"], 16)
            ts = now2 - (head - bn) * _BSC_BLOCK_SEC
            if ts < since_ts:
                continue
            txh = lg.get("transactionHash", "")
            dedup = (to_eoa, txh, token)
            if dedup in self._seen_tx:
                continue
            self._seen_tx.add(dedup)
            try:
                raw = int(lg.get("data") or "0x0", 16)
            except ValueError:
                raw = 0
            events.append(WalletEvent(
                chain="bsc", wallet=to_eoa, token_address=token, side="buy",
                amount=Decimal(raw) / Decimal(10 ** 18), tx_hash=txh, ts=ts))
        if len(self._seen_tx) > 8000:
            self._seen_tx = set(list(self._seen_tx)[-3000:])
        if events:
            logger.info("bsc smart-money: %d transfer-in events (blocks %d..%d)",
                        len(events), from_block, head)
        return events

    # ---- 执行（Phase 3）----

    def execute_buy(self, token_address, quote_amount, slippage_bps):
        raise NotImplementedError("bsc execute_buy 留到 Phase 3 真钱冒烟测试")

    def execute_sell(self, token_address, token_amount, slippage_bps):
        raise NotImplementedError("bsc execute_sell 留到 Phase 3 真钱冒烟测试")

    def get_wallet_balance(self):
        return None


if __name__ == "__main__":
    import config
    logging.basicConfig(level=logging.INFO)
    a = BscAdapter(config.load_config())
    cake = "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82"
    print("price:", a.get_current_price(cake))
    print("meta:", a.get_token_metadata(cake))
    print("buyers:", a.get_recent_buyers(cake, 300))

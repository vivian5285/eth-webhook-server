#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WS数据摄取。接 wss://ws-live-data.polymarket.com，同时订阅Binance现货价（领先指标）
和Chainlink参考价（Polymarket实际结算用的真相源）。维护内存滚动缓冲区供 signals/edge.py 用，
带陈旧检测（fail-closed：数据太旧就不出信号，不能拿旧数据瞎判断）。

订阅格式与实测行为（2026-08-12 在沙盒和VPS两台机器上都验证过）：
- 订阅帧: {"action":"subscribe","subscriptions":[{"topic":"crypto_prices","type":"update",
  "filters":"btcusdt"}]} （Chainlink同理，topic换成crypto_prices_chainlink）
- **关键坑**：一开始把Binance和Chainlink两个订阅塞进同一条连接的同一个订阅帧，结果
  实测收到的数据批次消息里根本没有可靠的"topic"字段能用来区分来源（只有连接刚建立时的
  确认帧偶尔带，后续批量推送的payload直接是{"data":[...]}没有外层topic）——这导致
  Chainlink数据被误判成Binance数据，chainlink_buf永远是空的（症状：is_stale永远True，
  main.py日志一直在打"没有Chainlink tick"警告）。VPS上单独订阅Chainlink能收到真实新鲜数据，
  证明数据本身是通的，问题出在"用消息内容猜来源"这个设计上。
- **修法**：改成两条独立WS连接，一条只订Binance，一条只订Chainlink——来源由"这条消息是从
  哪个连接收到的"决定，不再依赖消息体里的字段，从根上消除歧义。
"""
import asyncio
import json
import logging
import time
from collections import deque
from decimal import Decimal

import websockets

logger = logging.getLogger(__name__)

_SYMBOL_TO_BINANCE = {"BTC": "btcusdt", "ETH": "ethusdt", "SOL": "solusdt", "XRP": "xrpusdt"}
_SYMBOL_TO_CHAINLINK = {"BTC": "btc/usd", "ETH": "eth/usd", "SOL": "sol/usd", "XRP": "xrp/usd"}

_BUFFER_MAXLEN = 600  # 约10分钟的秒级tick足够信号层用


class SymbolFeedState:
    def __init__(self, symbol):
        self.symbol = symbol
        self.binance_buf = deque(maxlen=_BUFFER_MAXLEN)     # [(ts, price), ...]
        self.chainlink_buf = deque(maxlen=_BUFFER_MAXLEN)
        self.last_binance_ts = 0.0
        self.last_chainlink_ts = 0.0

    def push_binance(self, ts, price):
        self.binance_buf.append((ts, price))
        self.last_binance_ts = ts

    def push_chainlink(self, ts, price):
        self.chainlink_buf.append((ts, price))
        self.last_chainlink_ts = ts

    def latest_binance(self):
        return self.binance_buf[-1] if self.binance_buf else None

    def latest_chainlink(self):
        return self.chainlink_buf[-1] if self.chainlink_buf else None

    def is_stale(self, stale_timeout_sec, now=None):
        now = now if now is not None else time.time()
        if not self.binance_buf or not self.chainlink_buf:
            return True
        return (now - self.last_binance_ts > stale_timeout_sec) or \
               (now - self.last_chainlink_ts > stale_timeout_sec)


class MarketFeed:
    """symbols: list[str]，如 ["BTC"]。Phase 1 只开BTC，其余symbol留给Phase 4。"""

    def __init__(self, ws_url, symbols, stale_timeout_sec=15):
        self.ws_url = ws_url
        self.symbols = [s.strip().upper() for s in symbols if s.strip()]
        self.stale_timeout_sec = stale_timeout_sec
        self.states = {s: SymbolFeedState(s) for s in self.symbols}

    def get_state(self, symbol):
        return self.states.get(symbol.upper())

    def _build_subscription(self, is_chainlink):
        subs = []
        table = _SYMBOL_TO_CHAINLINK if is_chainlink else _SYMBOL_TO_BINANCE
        for s in self.symbols:
            feed_name = table.get(s)
            if not feed_name:
                continue
            if is_chainlink:
                subs.append({
                    "topic": "crypto_prices_chainlink", "type": "*",
                    "filters": json.dumps({"symbol": feed_name}),
                })
            else:
                subs.append({"topic": "crypto_prices", "type": "update", "filters": feed_name})
        return {"action": "subscribe", "subscriptions": subs}

    def _handle_message(self, raw, is_chainlink):
        """source（Binance/Chainlink）由调用方传入——即"这条消息来自哪条专用连接"，
        不再从消息体里猜，这是修复误判bug的关键。"""
        if not raw:
            return
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return

        payload = msg.get("payload") or {}
        entries = []
        if isinstance(payload.get("data"), list):
            sym = payload.get("symbol")
            for item in payload["data"]:
                entries.append((item.get("timestamp"), item.get("value"), sym))
        elif "value" in payload:
            entries.append((payload.get("timestamp"), payload.get("value"), payload.get("symbol")))

        if not entries:
            return

        # 单symbol场景（Phase1只有BTC）缺symbol字段时直接归给它；多symbol要靠filters分流(Phase4)
        default_symbol = self.symbols[0] if len(self.symbols) == 1 else None

        for ts_ms, value, sym in entries:
            if ts_ms is None or value is None:
                continue
            symbol = self._resolve_symbol(sym, is_chainlink) or default_symbol
            if not symbol or symbol not in self.states:
                continue
            ts = float(ts_ms) / 1000.0
            price = Decimal(str(value))
            state = self.states[symbol]
            if is_chainlink:
                state.push_chainlink(ts, price)
            else:
                state.push_binance(ts, price)

    def _resolve_symbol(self, raw_sym, is_chainlink):
        if not raw_sym:
            return None
        s = str(raw_sym).lower()
        table = _SYMBOL_TO_CHAINLINK if is_chainlink else _SYMBOL_TO_BINANCE
        for symbol, feed_name in table.items():
            if feed_name.lower() == s:
                return symbol
        return None

    async def _connection_loop(self, is_chainlink, stop_event):
        """一条专用连接：只订Binance，或只订Chainlink。断线自动重连退避。"""
        label = "chainlink" if is_chainlink else "binance"
        backoff = 1.0
        while stop_event is None or not stop_event.is_set():
            try:
                async with websockets.connect(self.ws_url, ping_interval=15) as ws:
                    await ws.send(json.dumps(self._build_subscription(is_chainlink)))
                    logger.info("market_feed[%s] connected, symbols=%s", label, self.symbols)
                    backoff = 1.0
                    last_ping = time.time()
                    while stop_event is None or not stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=5)
                            self._handle_message(raw, is_chainlink)
                        except asyncio.TimeoutError:
                            pass
                        if time.time() - last_ping >= 5:
                            try:
                                await ws.send("PING")
                            except Exception:
                                break
                            last_ping = time.time()
            except Exception as e:
                logger.warning("market_feed[%s] disconnected (%s), reconnecting in %.1fs", label, e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def run(self, stop_event=None):
        await asyncio.gather(
            self._connection_loop(is_chainlink=False, stop_event=stop_event),
            self._connection_loop(is_chainlink=True, stop_event=stop_event),
        )


if __name__ == "__main__":
    import config
    logging.basicConfig(level=logging.INFO)
    cfg = config.load_config()
    symbols = [s.strip() for s in cfg.symbols.split(",") if s.strip()]
    feed = MarketFeed(cfg.ws_url, symbols, cfg.feed_stale_timeout_sec)

    async def self_test():
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(stop))
        for _ in range(6):
            await asyncio.sleep(5)
            for sym in symbols:
                st = feed.get_state(sym)
                print(
                    f"{sym}: binance={st.latest_binance()} chainlink={st.latest_chainlink()} "
                    f"stale={st.is_stale(cfg.feed_stale_timeout_sec)}"
                )
        stop.set()
        task.cancel()

    asyncio.run(self_test())

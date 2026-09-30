#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""py-clob-client 的轻量封装：认证、市场发现（Gamma API）、读价、下单。
单一交易场所，不做 chain_sniper 那种 ChainAdapter 抽象——过度设计。

市场发现机制（2026-08-12 实测确认，写进方案文档时还是"未确认"，现在补上）：
- 5分钟BTC市场的slug是可以直接计算的：`btc-updown-5m-{window_start_epoch}`，
  window_start_epoch = 当前时间戳向下取整到300秒边界。不需要枚举/搜索市场列表。
- Gamma API: GET https://gamma-api.polymarket.com/markets?slug=<slug>
  返回字段：clobTokenIds（JSON字符串数组，[Up的token_id, Down的token_id]）、
  endDate（ISO8601，窗口结束时间）、outcomes、outcomePrices、closed。
- 查询已结算窗口需要显式带 closed=true 参数，否则默认只返回未关闭市场。
- resolutionSource 字段确认结算用的是 Chainlink BTC/USD TWAP streams。
"""
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal

import requests

from models import TradeResult

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"


def window_start_for_now(window_minutes=5, now=None):
    now = now if now is not None else time.time()
    step = window_minutes * 60
    return int(now - (now % step))


def window_key_for(symbol, window_minutes, window_start_ts):
    # 目前只支持 BTC 的 btc-updown-5m 系列；其他symbol/窗口长度留给 Phase 4 再扩展slug规则
    if symbol.upper() == "BTC" and window_minutes == 5:
        return f"btc-updown-5m-{window_start_ts}"
    raise NotImplementedError(f"暂不支持 symbol={symbol} window_minutes={window_minutes} 的slug规则")


class PolymarketClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self._clob = None

    def _client(self):
        if self._clob is None:
            from py_clob_client.client import ClobClient
            c = ClobClient(
                self.cfg.clob_host,
                key=self.cfg.polygon_wallet_private_key,
                chain_id=self.cfg.chain_id,
                signature_type=self.cfg.signature_type,
                funder=self.cfg.polygon_funder_address,
            )
            c.set_api_creds(c.create_or_derive_api_creds())
            self._clob = c
            logger.info("polymarket clob client ready, address=%s", c.get_address())
        return self._clob

    # ---- 市场发现 ----

    def get_current_window(self, symbol="BTC", window_minutes=5):
        """返回当前活跃窗口的字典（window_key/token_id_up/token_id_down/window_start_ts/
        window_end_ts），查不到返回 None。"""
        window_start_ts = window_start_for_now(window_minutes)
        window_key = window_key_for(symbol, window_minutes, window_start_ts)
        try:
            r = requests.get(f"{GAMMA_API}/markets", params={"slug": window_key}, timeout=10)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.error("get_current_window failed: %s", e)
            return None
        if not data:
            return None
        m = data[0]
        try:
            import json as _json
            token_ids = _json.loads(m.get("clobTokenIds", "[]"))
            token_up, token_down = token_ids[0], token_ids[1]
        except Exception:
            logger.error("failed to parse clobTokenIds for %s", window_key)
            return None
        window_end_ts = self._parse_iso(m.get("endDate"))
        return {
            "window_key": window_key,
            "symbol": symbol,
            "window_minutes": window_minutes,
            "window_start_ts": float(window_start_ts),
            "window_end_ts": window_end_ts,
            "token_id_up": token_up,
            "token_id_down": token_down,
        }

    def get_resolution(self, window_key):
        """返回 'UP'/'DOWN'，未结算返回 None。"""
        try:
            r = requests.get(
                f"{GAMMA_API}/markets", params={"slug": window_key, "closed": "true"}, timeout=10
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.error("get_resolution failed: %s", e)
            return None
        if not data:
            return None
        m = data[0]
        if not m.get("closed"):
            return None
        try:
            import json as _json
            prices = _json.loads(m.get("outcomePrices", "[]"))
            up_price = float(prices[0])
            return "UP" if up_price >= 0.5 else "DOWN"
        except Exception:
            return None

    @staticmethod
    def _parse_iso(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None

    # ---- 读价 ----

    def get_midpoint(self, token_id):
        try:
            resp = self._client().get_midpoint(token_id)
            return Decimal(str(resp.get("mid"))) if isinstance(resp, dict) else Decimal(str(resp))
        except Exception as e:
            logger.warning("get_midpoint failed token=%s err=%s", token_id, e)
            return None

    def get_order_book(self, token_id):
        try:
            return self._client().get_order_book(token_id)
        except Exception as e:
            logger.warning("get_order_book failed token=%s err=%s", token_id, e)
            return None

    # ---- 下单（DRY_RUN 时上层不会调这些方法）----

    def place_order(self, window_key, side, order_style, shares, price):
        """side: BUY_UP/BUY_DOWN；order_style: MAKER(限价GTC)/TAKER(市价FOK)。"""
        from py_clob_client.clob_types import MarketOrderArgs, OrderArgs, OrderType
        from py_clob_client.order_builder.constants import BUY

        window = self._resolve_window_tokens(window_key)
        if window is None:
            return TradeResult(ok=False, error="window_not_found")
        token_id = window["token_id_up"] if side == "BUY_UP" else window["token_id_down"]

        try:
            client = self._client()
            if order_style == "TAKER":
                amount_usd = float(Decimal(str(shares)) * Decimal(str(price)))
                mo = MarketOrderArgs(token_id=token_id, amount=amount_usd, side=BUY,
                                      order_type=OrderType.FOK)
                signed = client.create_market_order(mo)
                resp = client.post_order(signed, OrderType.FOK)
            else:
                oa = OrderArgs(token_id=token_id, price=float(price), size=float(shares), side=BUY)
                signed = client.create_order(oa)
                resp = client.post_order(signed, OrderType.GTC)
            return self._parse_order_response(resp)
        except Exception as e:
            logger.error("place_order failed window=%s side=%s err=%s", window_key, side, e)
            return TradeResult(ok=False, error=str(e))

    def close_position(self, pos):
        """把已有持仓卖回市场（早退用）。pos 是 db.get_open_positions() 里的一行。"""
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        from py_clob_client.order_builder.constants import SELL

        window = self._resolve_window_tokens(pos["window_key"])
        if window is None:
            return TradeResult(ok=False, error="window_not_found")
        token_id = window["token_id_up"] if pos["side"] == "BUY_UP" else window["token_id_down"]
        try:
            client = self._client()
            mo = MarketOrderArgs(token_id=token_id, amount=float(pos["entry_shares"]), side=SELL,
                                  order_type=OrderType.FOK)
            signed = client.create_market_order(mo)
            resp = client.post_order(signed, OrderType.FOK)
            return self._parse_order_response(resp)
        except Exception as e:
            logger.error("close_position failed pos_id=%s err=%s", pos.get("id"), e)
            return TradeResult(ok=False, error=str(e))

    def _resolve_window_tokens(self, window_key):
        import db
        w = db.get_market_window(window_key)
        if w and w.get("token_id_up"):
            return w
        # DB 里没有就现查一次（比如进程重启后 exit_manager 处理旧仓位）
        parts = window_key.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            window_start_ts = int(parts[1])
            symbol = "BTC" if window_key.startswith("btc-") else None
            if symbol:
                return self.get_current_window(symbol) if window_start_for_now() == window_start_ts else None
        return None

    @staticmethod
    def _parse_order_response(resp):
        if not resp:
            return TradeResult(ok=False, error="empty_response")
        success = resp.get("success", resp.get("errorMsg") is None if isinstance(resp, dict) else False)
        if not success:
            return TradeResult(ok=False, error=str(resp.get("errorMsg", resp))[:200])
        order_id = resp.get("orderID", resp.get("orderId", ""))
        filled_price = Decimal(str(resp.get("price", 0) or 0))
        filled_shares = Decimal(str(resp.get("size", resp.get("takingAmount", 0)) or 0))
        fee_usd = float(resp.get("fee", 0) or 0)
        return TradeResult(ok=True, order_id=str(order_id), filled_price=filled_price,
                            filled_shares=filled_shares, fee_usd=fee_usd)


if __name__ == "__main__":
    import config
    logging.basicConfig(level=logging.INFO)
    cfg = config.load_config()
    pc = PolymarketClient(cfg)
    w = pc.get_current_window("BTC", cfg.window_minutes)
    print("current window:", w)
    if w:
        print("midpoint UP:", pc.get_midpoint(w["token_id_up"]))

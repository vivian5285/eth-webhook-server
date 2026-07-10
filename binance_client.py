#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
import json
import time
import threading
from binance.client import Client
import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

logger = logging.getLogger(__name__)
BINANCE_CLIENT_VERSION = "v13.25.0"
WS_MARKET_BASE = "wss://fstream.binance.com/market/ws"


class BinanceClient:
    def __init__(self):
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")
        self.client = Client(self.api_key, self.api_secret)
        self._symbol_filters = {}
        self._price_cache = {}
        self._price_cache_ts = {}
        self._price_lock = threading.Lock()
        self._pub_ws_running = False
        self._pub_ws_symbol = None
        self._rest_price_min_interval = 30
        self._last_rest_price_fetch = 0.0
        logger.info("🟢 Binance Client v13.9.2-algo-shield-audit 已加载")

    @staticmethod
    def _is_algo_switch_error(err):
        text = str(err or "")
        return "-4120" in text or "STOP_ORDER_SWITCH_ALGO" in text or "algo" in text.lower()

    @staticmethod
    def _truthy_close_position(val):
        if val is True:
            return True
        return str(val or "").strip().lower() in ("true", "1", "yes")

    def _futures_signed_request(self, method, path, params=None):
        params = dict(params or {})
        return self.client._request_futures_api(
            method.lower(), path, signed=True, data=params,
        )

    def _normalize_algo_order(self, raw):
        """Algo 条件单 → 与普通 open order 兼容的结构（供硬止损/雷达审计）"""
        if not isinstance(raw, dict):
            return None
        order_type = raw.get("orderType") or raw.get("type") or ""
        trigger = raw.get("triggerPrice") or raw.get("stopPrice")
        algo_id = raw.get("algoId") or raw.get("orderId")
        if not algo_id:
            return None
        return {
            "orderId": algo_id,
            "algoId": algo_id,
            "isAlgoOrder": True,
            "type": order_type,
            "stopPrice": trigger,
            "triggerPrice": trigger,
            "closePosition": raw.get("closePosition"),
            "side": raw.get("side"),
            "origQty": raw.get("quantity") or raw.get("origQty") or "0",
            "quantity": raw.get("quantity") or raw.get("origQty") or "0",
            "reduceOnly": raw.get("reduceOnly"),
            "status": raw.get("algoStatus") or raw.get("status"),
            "positionSide": raw.get("positionSide"),
        }

    def get_open_algo_orders(self, symbol="ETHUSDT"):
        """币安 2025+ 条件单（含 closePosition 硬止损）在 Algo 通道"""
        try:
            rows = self._futures_signed_request(
                "get", "openAlgoOrders", {"symbol": symbol},
            )
            if not isinstance(rows, list):
                return []
            out = []
            for row in rows:
                norm = self._normalize_algo_order(row)
                if norm:
                    out.append(norm)
            return out
        except Exception as e:
            logger.warning(f"[Algo挂单查询] {symbol}: {e}")
            return []

    def get_open_orders(self, symbol="ETHUSDT", include_algo=True):
        try:
            orders = list(self.client.futures_get_open_orders(symbol=symbol) or [])
        except Exception as e:
            logger.error(f"[获取挂单失败] {symbol}: {e}")
            orders = []
        if not include_algo:
            return orders
        algo_orders = self.get_open_algo_orders(symbol)
        if not algo_orders:
            return orders
        seen = {str(o.get("orderId")) for o in orders if o.get("orderId")}
        merged = list(orders)
        for ao in algo_orders:
            aid = str(ao.get("algoId") or ao.get("orderId") or "")
            if aid and aid not in seen:
                merged.append(ao)
                seen.add(aid)
        if algo_orders:
            logger.debug(
                f"[挂单合并] {symbol} 普通 {len(orders)} + Algo {len(algo_orders)} "
                f"→ 合计 {len(merged)}"
            )
        return merged

    def _load_symbol_filters(self, symbol="ETHUSDT"):
        if symbol in self._symbol_filters:
            return self._symbol_filters[symbol]
        try:
            info = self.client.futures_exchange_info()
            for s in info.get("symbols", []):
                if s.get("symbol") == symbol:
                    self._symbol_filters[symbol] = s
                    return s
        except Exception as e:
            logger.warning(f"[合约规格] 获取失败 {symbol}: {e}")
        return {}

    def format_quantity(self, qty, symbol="ETHUSDT"):
        sym = self._load_symbol_filters(symbol)
        step = 0.001
        for f in sym.get("filters", []):
            if f.get("filterType") == "LOT_SIZE":
                step = float(f.get("stepSize", step))
                break
        q = float(qty)
        if step > 0:
            q = round(round(q / step) * step, 8)
        return q

    def format_price(self, price, symbol="ETHUSDT"):
        sym = self._load_symbol_filters(symbol)
        tick = 0.01
        for f in sym.get("filters", []):
            if f.get("filterType") == "PRICE_FILTER":
                tick = float(f.get("tickSize", tick))
                break
        p = float(price)
        if tick > 0:
            p = round(round(p / tick) * tick, 8)
        return f"{p:.2f}" if tick <= 0.01 else str(p)

    def set_leverage(self, symbol="ETHUSDT", leverage=15):
        """设置指定交易对的杠杆倍数"""
        try:
            result = self.client.futures_change_leverage(symbol=symbol, leverage=leverage)
            logger.info(f"[设置杠杆成功] {symbol} → {leverage}x")
            return result
        except Exception as e:
            logger.error(f"[设置杠杆失败] {symbol} → {leverage}x: {e}")
            return None

    def _set_ws_price(self, symbol, price):
        with self._price_lock:
            self._price_cache[symbol] = price
            self._price_cache_ts[symbol] = time.time()

    def _get_ws_price(self, symbol, max_age=30.0):
        with self._price_lock:
            px = self._price_cache.get(symbol)
            ts = self._price_cache_ts.get(symbol, 0.0)
        if px and (time.time() - ts) <= max_age:
            return px
        return None

    def start_public_price_ws(self, symbol="ETHUSDT"):
        """订阅 markPrice@1s — 雷达用 WS 推价，避免 REST 轮询限频"""
        if self._pub_ws_running and self._pub_ws_symbol == symbol:
            return
        self._pub_ws_symbol = symbol
        if not self._pub_ws_running:
            self._pub_ws_running = True
            threading.Thread(
                target=self._public_price_ws_loop, args=(symbol,), daemon=True,
            ).start()
            logger.info(f"📡 币安公开 WS 启动: {symbol}@markPrice@1s")

    def _public_price_ws_loop(self, symbol):
        try:
            import websocket
        except ImportError:
            logger.warning("未安装 websocket-client，雷达将回退 REST 慢速兜底")
            self._pub_ws_running = False
            return

        stream = f"{symbol.lower()}@markPrice@1s"
        url = f"{WS_MARKET_BASE}/{stream}"

        def on_message(ws, message):
            try:
                data = json.loads(message)
                if isinstance(data, dict) and "data" in data:
                    data = data["data"]
                px = float(data.get("p") or data.get("markPrice") or 0)
                if px > 0:
                    self._set_ws_price(symbol, px)
            except Exception as e:
                logger.debug(f"WS 行情解析: {e}")

        def on_error(ws, error):
            logger.warning(f"币安公开 WS 错误: {error}")

        def on_close(ws, code, msg):
            logger.warning(f"币安公开 WS 断开: {code} {msg}")

        while self._pub_ws_running:
            try:
                ws = websocket.WebSocketApp(
                    url, on_message=on_message, on_error=on_error, on_close=on_close,
                )
                ws.run_forever(ping_interval=180, ping_timeout=30)
            except Exception as e:
                logger.error(f"币安公开 WS 异常: {e}")
            if self._pub_ws_running:
                time.sleep(3)

    def get_current_price(self, symbol="ETHUSDT", prefer_ws=True):
        """优先 WS 缓存；REST 仅作兜底且限频（有 WS 时 ≥30s 一次）"""
        if prefer_ws:
            ws_px = self._get_ws_price(symbol)
            if ws_px:
                return ws_px
        now = time.time()
        min_gap = self._rest_price_min_interval if self._pub_ws_running else 2
        cached = self._get_ws_price(symbol, max_age=min_gap)
        if cached:
            return cached
        if now - self._last_rest_price_fetch < min_gap:
            stale = self._get_ws_price(symbol, max_age=120)
            return stale or 0.0
        try:
            self._last_rest_price_fetch = now
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            price = float(ticker["price"])
            if price > 0:
                self._set_ws_price(symbol, price)
            return price
        except Exception as e:
            logger.error(f"[查询价格失败] {symbol}: {e}")
            stale = self._get_ws_price(symbol, max_age=120)
            return stale or 0.0

    def get_futures_account_summary(self, asset="USDT"):
        """合约账户概览：用于本金锚点，禁止用 depleted available 算档位额度"""
        try:
            account = self.client.futures_account()
            out = {
                "wallet_balance": 0.0,
                "cross_wallet_balance": 0.0,
                "margin_balance": 0.0,
                "available_balance": 0.0,
                "total_margin_balance": float(account.get("totalMarginBalance", 0) or 0),
                "total_wallet_balance": float(account.get("totalWalletBalance", 0) or 0),
            }
            for a in account.get("assets", []):
                if a.get("asset") != asset:
                    continue
                out["wallet_balance"] = float(a.get("walletBalance", 0) or 0)
                out["cross_wallet_balance"] = float(a.get("crossWalletBalance", 0) or 0)
                out["margin_balance"] = float(a.get("marginBalance", 0) or 0)
                out["available_balance"] = float(a.get("availableBalance", 0) or 0)
                break
            return out
        except Exception as e:
            logger.error(f"[账户概览失败] {e}")
            return {}

    def get_principal_wallet_balance(self, asset="USDT"):
        """
        USDT 合约本金余额（walletBalance）— 唯一合法的档位额度基数。
        禁止用 available / marginBalance / 浮盈放大权益。
        """
        summary = self.get_futures_account_summary(asset)
        for key in ("wallet_balance", "cross_wallet_balance", "total_wallet_balance"):
            val = float(summary.get(key, 0) or 0)
            if val > 0:
                return val
        return 0.0

    def get_cap_equity_balance(self, asset="USDT"):
        """档位额度基数 = 本金 walletBalance（兼容旧名）"""
        return self.get_principal_wallet_balance(asset)

    def get_sizing_balance(self, asset="USDT"):
        """本金口径（walletBalance），用于 regime 仓位预算"""
        return self.get_principal_wallet_balance(asset)

    def get_available_balance(self, asset="USDT"):
        try:
            account = self.client.futures_account()
            for a in account.get("assets", []):
                if a.get("asset") == asset:
                    margin_bal = float(a.get("marginBalance", 0.0))
                    if margin_bal > 0:
                        return margin_bal
                    return float(a.get("availableBalance", 0.0))
            return 0.0
        except Exception as e:
            logger.error(f"[查询余额失败] {e}")
            return 0.0

    def get_position(self, symbol="ETHUSDT"):
        try:
            positions = self.client.futures_position_information(symbol=symbol)
            return positions[0] if positions else None
        except Exception as e:
            logger.error(f"[查询持仓失败] {symbol}: {e}")
            return None

    def place_market_order(self, side, quantity, symbol="ETHUSDT", reduce_only=False):
        qty = self.format_quantity(quantity, symbol)
        if qty <= 0:
            logger.error(f"[市价单跳过] 数量无效 {quantity}")
            return None
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {
                "symbol": symbol, "side": binance_side, "type": "MARKET", "quantity": qty,
            }
            if reduce_only:
                params["reduceOnly"] = True
            order = self.client.futures_create_order(**params)
            tag = "平仓" if reduce_only else "开仓"
            logger.info(f"[市价{tag}成功] {side} {qty} {symbol}")
            return order
        except Exception as e:
            tag = "平仓" if reduce_only else "开仓"
            logger.error(f"[市价{tag}失败] {side} {qty} {symbol}: {e}")
            return None

    def place_limit_order(self, side, quantity, price, symbol="ETHUSDT", reduce_only=True):
        qty = self.format_quantity(quantity, symbol)
        px_str = self.format_price(price, symbol)
        if qty <= 0:
            logger.error(f"[限价单跳过] 数量无效 {quantity}")
            return None
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {
                "symbol": symbol, "side": binance_side, "type": "LIMIT",
                "timeInForce": "GTC", "quantity": qty, "price": px_str,
            }
            if reduce_only:
                params["reduceOnly"] = True
            order = self.client.futures_create_order(**params)
            logger.info(f"[限价单成功] {side} {qty} @ {px_str} orderId={order.get('orderId', '')}")
            return order
        except Exception as e:
            logger.error(f"[限价单失败] {side} {qty} @ {px_str}: {e}")
            return None

    def place_algo_stop_market_order(self, side, stop_price, symbol="ETHUSDT", close_position=True):
        """Algo 通道 STOP_MARKET（closePosition 全平硬止损）"""
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": binance_side,
                "type": "STOP_MARKET",
                "triggerPrice": self.format_price(stop_price, symbol),
            }
            if close_position:
                params["closePosition"] = "true"
            order = self._futures_signed_request("post", "algoOrder", params)
            logger.info(
                f"[Algo止损成功] {side} closePosition Stop @ {stop_price} "
                f"algoId={order.get('algoId', '') if isinstance(order, dict) else '?'}"
            )
            if isinstance(order, dict):
                order.setdefault("isAlgoOrder", True)
            return order
        except Exception as e:
            logger.error(f"[Algo止损失败] {side} Stop @ {stop_price}: {e}")
            return None

    def place_stop_market_order(self, side, stop_price, symbol="ETHUSDT", quantity=None):
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {
                "symbol": symbol, "side": binance_side, "type": "STOP_MARKET",
                "stopPrice": self.format_price(stop_price, symbol),
            }
            if quantity is not None:
                qty = self.format_quantity(quantity, symbol)
                if qty <= 0:
                    logger.error(f"[止损单跳过] 数量无效 {quantity}")
                    return None
                params["quantity"] = qty
                params["reduceOnly"] = True
            else:
                params["closePosition"] = "true"
            order = self.client.futures_create_order(**params)
            tag = f"{quantity} " if quantity is not None else "全仓 "
            logger.info(f"[止损单成功] {side} {tag}Stop @ {stop_price}")
            return order
        except Exception as e:
            if quantity is None and self._is_algo_switch_error(e):
                logger.info(
                    f"[止损单] 普通通道不可用({e}) → 切换 Algo closePosition @ {stop_price}"
                )
                return self.place_algo_stop_market_order(
                    side, stop_price, symbol=symbol, close_position=True,
                )
            logger.error(f"[止损单失败] {side} Stop @ {stop_price}: {e}")
            return None

    def place_stop_limit_order(self, side, quantity, stop_price, limit_price=None,
                               symbol="ETHUSDT", reduce_only=True):
        """STOP 限价止损：触发价 stopPrice，挂单价 price（reduceOnly 分批保护）"""
        qty = self.format_quantity(quantity, symbol)
        if qty <= 0:
            logger.error(f"[限价止损跳过] 数量无效 {quantity}")
            return None
        stop_str = self.format_price(stop_price, symbol)
        if limit_price is None:
            limit_price = stop_price * (0.9995 if side.upper() in ("SELL", "SHORT") else 1.0005)
        px_str = self.format_price(limit_price, symbol)
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {
                "symbol": symbol, "side": binance_side, "type": "STOP",
                "timeInForce": "GTC", "quantity": qty,
                "price": px_str, "stopPrice": stop_str,
            }
            if reduce_only:
                params["reduceOnly"] = True
            order = self.client.futures_create_order(**params)
            logger.info(f"[限价止损成功] {side} {qty} stop@{stop_str} limit@{px_str}")
            return order
        except Exception as e:
            logger.error(f"[限价止损失败] {side} {qty} stop@{stop_price}: {e}")
            return None

    def cancel_algo_order(self, symbol="ETHUSDT", algo_id=None):
        if not algo_id:
            return None
        try:
            res = self._futures_signed_request(
                "delete", "algoOrder", {"symbol": symbol, "algoId": int(algo_id)},
            )
            logger.info(f"[Algo撤单成功] {symbol} algoId={algo_id}")
            return res
        except Exception as e:
            logger.error(f"[Algo撤单失败] {symbol} algoId={algo_id}: {e}")
            return None

    def cancel_order(self, symbol="ETHUSDT", order_id=None, order=None):
        if order and isinstance(order, dict):
            if order.get("isAlgoOrder") or order.get("algoId"):
                return self.cancel_algo_order(
                    symbol, order.get("algoId") or order.get("orderId"),
                )
            order_id = order.get("orderId") or order_id
        if not order_id:
            return None
        try:
            res = self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
            logger.info(f"[撤单成功] {symbol} orderId={order_id}")
            return res
        except Exception as e:
            err = str(e)
            if "-2011" in err or "Unknown order" in err or "Order does not exist" in err:
                return self.cancel_algo_order(symbol, order_id)
            logger.error(f"[撤单失败] {symbol} orderId={order_id}: {e}")
            return None

    def cancel_all_open_orders(self, symbol="ETHUSDT"):
        try:
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info(f"[撤单成功] {symbol} 全部普通挂单已撤销")
        except Exception as e:
            logger.error(f"[撤单失败] {symbol} 普通挂单: {e}")
        try:
            self._futures_signed_request("delete", "algoOpenOrders", {"symbol": symbol})
            logger.info(f"[撤单成功] {symbol} 全部 Algo 条件单已撤销")
        except Exception as e:
            logger.warning(f"[撤单] {symbol} Algo 条件单: {e}")

    def close_all_positions(self, symbol="ETHUSDT"):
        try:
            pos = self.get_position(symbol)
            if not pos: return None
            pos_amt = float(pos.get("positionAmt", 0))
            if pos_amt == 0: return None

            side = "SELL" if pos_amt > 0 else "BUY"
            order = self.client.futures_create_order(
                symbol=symbol, side=side, type="MARKET", quantity=abs(pos_amt), reduceOnly=True
            )
            logger.info(f"[市价平仓成功] {symbol}")
            return order
        except Exception as e:
            logger.error(f"[市价平仓失败] {symbol}: {e}")
            return None

    def fetch_atr_14(self, symbol="ETHUSDT", interval="15m", period=14):
        """REST K 线计算 ATR(14)，失败时回退公开接口。"""
        try:
            from webhook_parser import compute_atr_from_klines
            klines = self.client.futures_klines(
                symbol=symbol, interval=interval, limit=period + 20,
            )
            atr = compute_atr_from_klines(klines, period)
            if atr > 0:
                return atr
        except Exception as e:
            logger.warning(f"[ATR] {symbol} REST 计算失败: {e}")
        from webhook_parser import fetch_eth_atr_14_public
        return fetch_eth_atr_14_public(period)


binance_client = BinanceClient()

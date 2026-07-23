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
BINANCE_CLIENT_VERSION = "v13.45.3-dup-guard"
WS_MARKET_BASE = "wss://fstream.binance.com/market/ws"
WS_MARKET_COMBINED = "wss://fstream.binance.com/stream"
WS_PRIVATE_BASE = "wss://fstream.binance.com/ws"

# REST 持仓查询失败哨兵：禁止被上层当成「空仓」
POSITION_QUERY_FAILED = {"_query_failed": True, "positionAmt": None, "entryPrice": None}


class OrdersQueryFailedList(list):
    """空 list 子类：for-loop 安全空转，但 is_orders_query_failed=True。"""
    __slots__ = ()

    @property
    def _orders_query_failed(self):
        return True


# 挂单查询失败哨兵（可迭代空列表，禁止当成「真·零挂单」）
ORDERS_QUERY_FAILED = OrdersQueryFailedList()


def is_position_query_failed(pos):
    """仅当显式 QUERY_FAILED 哨兵时为 True；禁止把 MagicMock/普通持仓误判。"""
    return isinstance(pos, dict) and pos.get("_query_failed") is True


def is_orders_query_failed(orders):
    """挂单查询失败 → True；上层必须 fail-closed，禁止补挂限价/止损。"""
    if orders is None:
        return True
    if isinstance(orders, dict) and orders.get("_orders_query_failed") is True:
        return True
    return getattr(orders, "_orders_query_failed", False) is True


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
        self._pub_ws_symbols = set()
        self._pub_ws_symbol = None  # 兼容旧字段：最近一次请求的主符号
        self._pub_ws_lock = threading.Lock()
        self._pub_ws_restart = False
        self._rest_price_min_interval = 30
        self._last_rest_price_fetch = 0.0  # 兼容旧字段
        self._last_rest_price_fetch_by_sym = {}
        # markPrice tick 回调：symbol → callable(symbol, price)
        self._price_tick_cbs = {}
        # 私有 User Data Stream：持仓 / 订单实时同步
        self._ud_ws_running = False
        self._ud_ws_symbol = None
        self._listen_key = None
        self._ud_event_cb = None
        self._ud_event_cbs = {}
        self._pos_cache = {}
        self._pos_cache_ts = {}
        self._pos_lock = threading.Lock()
        # 全账户持仓合并缓存：双雷达共用一次 REST，避免每 symbol 各打一次
        self._all_pos_rows = {}
        self._all_pos_ts = 0.0
        self._all_pos_ttl = 1.0
        self._last_order_event_ts = 0.0
        logger.info(f"🟢 Binance Client {BINANCE_CLIENT_VERSION} 已加载")

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
        """币安 2025+ 条件单（含 closePosition 硬止损）在 Algo 通道。
        失败返回 ORDERS_QUERY_FAILED（勿当空列表）。
        """
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
            return ORDERS_QUERY_FAILED

    def get_open_orders(self, symbol="ETHUSDT", include_algo=True):
        """
        成功返回 list；REST 失败返回 ORDERS_QUERY_FAILED。
        铁律：查询失败 ≠ 盘口无单；上层禁止据此补挂限价/止损。
        """
        try:
            orders = list(self.client.futures_get_open_orders(symbol=symbol) or [])
        except Exception as e:
            logger.error(f"[获取挂单失败] {symbol}: {e}")
            return ORDERS_QUERY_FAILED
        if not include_algo:
            return orders
        algo_orders = self.get_open_algo_orders(symbol)
        if is_orders_query_failed(algo_orders):
            # 普通单已拿到；Algo 失败时仍返回普通单
            logger.warning(
                f"[挂单合并] {symbol} Algo 查询失败 → 仅用普通挂单 "
                f"({len(orders)} 笔)；补挂前须再核实"
            )
            return orders
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

    def _iter_open_orders(self, symbol="ETHUSDT", include_algo=True):
        """供 for-loop：查询失败时 yield 空并让调用方先检查 is_orders_query_failed。"""
        orders = self.get_open_orders(symbol, include_algo=include_algo)
        if is_orders_query_failed(orders):
            return orders
        return orders

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

    def set_leverage(self, symbol="ETHUSDT", leverage=None):
        """设置杠杆：必须显式传入 TV leverage，禁止回退固定 25x。"""
        try:
            lev = int(float(leverage or 0))
        except (TypeError, ValueError):
            lev = 0
        if lev <= 0:
            logger.error(
                f"[设置杠杆拒绝] {symbol}: 缺少 TV leverage（禁止固定 EXCHANGE_LEVERAGE 回退）"
            )
            return None
        try:
            result = self.client.futures_change_leverage(symbol=symbol, leverage=lev)
            logger.info(f"[设置杠杆成功] {symbol} → {lev}x (TV)")
            return result
        except Exception as e:
            logger.error(f"[设置杠杆失败] {symbol} → {lev}x: {e}")
            return None

    def _set_ws_price(self, symbol, price):
        with self._price_lock:
            self._price_cache[symbol] = price
            self._price_cache_ts[symbol] = time.time()
        cb = self._price_tick_cbs.get(str(symbol or "").upper())
        if cb:
            try:
                cb(symbol, price)
            except Exception as e:
                logger.debug(f"price tick cb: {e}")

    def _get_ws_price(self, symbol, max_age=30.0):
        with self._price_lock:
            px = self._price_cache.get(symbol)
            ts = self._price_cache_ts.get(symbol, 0.0)
        if px and (time.time() - ts) <= max_age:
            return px
        return None

    def register_price_tick_callback(self, symbol, callback):
        """雷达：markPrice@1s 最快盯价 → 接近/达激活线脉冲哨兵交棒。"""
        sym = str(symbol or "ETHUSDT").upper()
        if callable(callback):
            self._price_tick_cbs[sym] = callback

    def start_public_price_ws(self, symbol="ETHUSDT", on_tick=None):
        """订阅 markPrice@1s；支持多品种合并流（ETH+XAU）。"""
        symbol = str(symbol or "ETHUSDT").upper()
        if on_tick:
            self.register_price_tick_callback(symbol, on_tick)
        with self._pub_ws_lock:
            self._pub_ws_symbol = symbol
            if symbol in self._pub_ws_symbols and self._pub_ws_running:
                return
            self._pub_ws_symbols.add(symbol)
            need_start = not self._pub_ws_running
            if self._pub_ws_running:
                self._pub_ws_restart = True
        if need_start:
            self._pub_ws_running = True
            threading.Thread(
                target=self._public_price_ws_loop, daemon=True, name="binance-pub-ws",
            ).start()
            logger.info(f"📡 币安公开 WS 启动: {sorted(self._pub_ws_symbols)}")
        else:
            logger.info(f"📡 币安公开 WS 增订: {symbol} → {sorted(self._pub_ws_symbols)}")

    def _public_price_ws_loop(self):
        try:
            import websocket
        except ImportError:
            logger.warning("未安装 websocket-client，雷达将回退 REST 慢速兜底")
            self._pub_ws_running = False
            return

        def on_message(ws, message):
            try:
                data = json.loads(message)
                # combined: {"stream":"...","data":{...}}
                if isinstance(data, dict) and "data" in data:
                    payload = data["data"]
                else:
                    payload = data
                if not isinstance(payload, dict):
                    return
                sym = str(payload.get("s") or "").upper()
                px = float(payload.get("p") or payload.get("markPrice") or 0)
                if sym and px > 0:
                    self._set_ws_price(sym, px)
            except Exception as e:
                logger.debug(f"WS 行情解析: {e}")

        def on_error(ws, error):
            logger.warning(f"币安公开 WS 错误: {error}")

        def on_close(ws, code, msg):
            logger.warning(f"币安公开 WS 断开: {code} {msg}")

        backoff = 1.0
        while self._pub_ws_running:
            with self._pub_ws_lock:
                symbols = sorted(self._pub_ws_symbols) or ["ETHUSDT"]
                self._pub_ws_restart = False
            try:
                if len(symbols) == 1:
                    url = f"{WS_MARKET_BASE}/{symbols[0].lower()}@markPrice@1s"
                else:
                    streams = "/".join(f"{s.lower()}@markPrice@1s" for s in symbols)
                    url = f"{WS_MARKET_COMBINED}?streams={streams}"
                ws = websocket.WebSocketApp(
                    url, on_message=on_message, on_error=on_error, on_close=on_close,
                )
                # 允许增订品种时打断重连
                def _run():
                    ws.run_forever(ping_interval=180, ping_timeout=30)

                t = threading.Thread(target=_run, daemon=True)
                t.start()
                connected_ok = False
                while t.is_alive() and self._pub_ws_running:
                    connected_ok = True
                    if self._pub_ws_restart:
                        try:
                            ws.close()
                        except Exception:
                            pass
                        break
                    time.sleep(0.5)
                t.join(timeout=5)
                if connected_ok and self._pub_ws_running and not self._pub_ws_restart:
                    backoff = 1.0  # 正常连过再断 → 重置退避
            except Exception as e:
                logger.error(f"币安公开 WS 异常: {e}")
            if self._pub_ws_running:
                logger.warning(f"币安公开 WS 重连等待 {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2.0, 60.0)

    def _create_listen_key(self):
        try:
            if hasattr(self.client, "futures_stream_get_listen_key"):
                key = self.client.futures_stream_get_listen_key()
            else:
                key = self._futures_signed_request("post", "listenKey", {})
            if isinstance(key, dict):
                key = key.get("listenKey") or key.get("listen_key")
            key = str(key or "").strip()
            return key or None
        except Exception as e:
            logger.error(f"[listenKey创建失败] {e}")
            return None

    def _keepalive_listen_key(self):
        key = self._listen_key
        if not key:
            return False
        try:
            if hasattr(self.client, "futures_stream_keepalive"):
                self.client.futures_stream_keepalive(listenKey=key)
            else:
                self._futures_signed_request("put", "listenKey", {"listenKey": key})
            return True
        except Exception as e:
            logger.warning(f"[listenKey续期失败] {e}")
            return False

    def _set_pos_cache(self, symbol, position_amt, entry_price):
        with self._pos_lock:
            self._pos_cache[symbol] = {
                "symbol": symbol,
                "positionAmt": float(position_amt or 0),
                "entryPrice": float(entry_price or 0),
            }
            self._pos_cache_ts[symbol] = time.time()

    def _get_pos_cache(self, symbol, max_age=8.0):
        with self._pos_lock:
            row = self._pos_cache.get(symbol)
            ts = self._pos_cache_ts.get(symbol, 0.0)
        if row and (time.time() - ts) <= max_age:
            return dict(row)
        return None

    def start_user_data_ws(self, symbol="ETHUSDT", on_event=None):
        """合约 User Data Stream：多品种回调注册，持仓/订单推送对齐实盘。"""
        symbol = str(symbol or "ETHUSDT").upper()
        self._ud_ws_symbol = symbol
        if on_event is not None:
            self._ud_event_cbs[symbol] = on_event
            self._ud_event_cb = on_event  # 兼容单品种
        if self._ud_ws_running:
            return
        self._ud_ws_running = True
        threading.Thread(
            target=self._user_data_ws_loop, daemon=True,
            name="binance-ud-ws",
        ).start()
        logger.info(f"📡 币安私有 WS 启动: User Data Stream ({symbol})")

    def _user_data_ws_loop(self):
        try:
            import websocket
        except ImportError:
            logger.warning("未安装 websocket-client，用户流不可用")
            self._ud_ws_running = False
            return

        last_keepalive = 0.0

        def on_message(ws, message):
            try:
                data = json.loads(message)
                et = str(data.get("e") or "")
                if et == "ACCOUNT_UPDATE":
                    for p in (data.get("a") or {}).get("P") or []:
                        sym = str(p.get("s") or "").upper()
                        if not sym:
                            continue
                        self._set_pos_cache(
                            sym,
                            p.get("pa") or p.get("positionAmt"),
                            p.get("ep") or p.get("entryPrice"),
                        )
                elif et == "ORDER_TRADE_UPDATE":
                    self._last_order_event_ts = time.time()
                    o = data.get("o") or {}
                    sym = str(o.get("s") or "").upper()
                    pa = o.get("pa")
                    if sym and pa is not None:
                        self._set_pos_cache(
                            sym, pa,
                            o.get("ap") or o.get("avgPrice") or 0,
                        )
                elif et == "listenKeyExpired":
                    logger.warning("listenKey 已过期，准备重建")
                    self._listen_key = None
                    try:
                        ws.close()
                    except Exception:
                        pass
                cbs = list(self._ud_event_cbs.values()) or (
                    [self._ud_event_cb] if self._ud_event_cb else []
                )
                for cb in cbs:
                    if not cb or not et:
                        continue
                    try:
                        cb(et, data)
                    except Exception as cb_e:
                        logger.debug(f"UD WS 回调: {cb_e}")
            except Exception as e:
                logger.debug(f"UD WS 解析: {e}")

        def on_error(ws, error):
            logger.warning(f"币安私有 WS 错误: {error}")

        def on_close(ws, code, msg):
            logger.warning(f"币安私有 WS 断开: {code} {msg}")

        backoff = 1.0
        while self._ud_ws_running:
            key = self._listen_key or self._create_listen_key()
            if not key:
                time.sleep(min(backoff, 5))
                backoff = min(backoff * 2.0, 60.0)
                continue
            self._listen_key = key
            url = f"{WS_PRIVATE_BASE}/{key}"
            try:
                ws = websocket.WebSocketApp(
                    url, on_message=on_message, on_error=on_error, on_close=on_close,
                )
                last_keepalive = time.time()

                def _ping():
                    nonlocal last_keepalive
                    while self._ud_ws_running and self._listen_key == key:
                        time.sleep(20)
                        if time.time() - last_keepalive >= 25 * 60:
                            if self._keepalive_listen_key():
                                last_keepalive = time.time()
                            else:
                                self._listen_key = None
                                try:
                                    ws.close()
                                except Exception:
                                    pass
                                break

                threading.Thread(target=_ping, daemon=True).start()
                ws.run_forever(ping_interval=180, ping_timeout=30)
                backoff = 1.0  # 曾连上后断开 → 下次从 1s 起退避
            except Exception as e:
                logger.error(f"币安私有 WS 异常: {e}")
            if self._ud_ws_running:
                logger.warning(f"币安私有 WS 重连等待 {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2.0, 60.0)

    def get_current_price(self, symbol="ETHUSDT", prefer_ws=True):
        """优先 WS 缓存；REST 仅作兜底且按 symbol 限频（有 WS 时 ≥30s 一次）"""
        symbol = str(symbol or "ETHUSDT").upper()
        if prefer_ws:
            ws_px = self._get_ws_price(symbol)
            if ws_px:
                return ws_px
        now = time.time()
        min_gap = self._rest_price_min_interval if self._pub_ws_running else 2
        cached = self._get_ws_price(symbol, max_age=min_gap)
        if cached:
            return cached
        last = float(self._last_rest_price_fetch_by_sym.get(symbol) or 0)
        if last > 0 and (now - last) < min_gap:
            stale = self._get_ws_price(symbol, max_age=120)
            return stale or 0.0
        try:
            self._last_rest_price_fetch_by_sym[symbol] = now
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

    def get_total_equity(self, asset="USDT"):
        """
        账户总权益（marginBalance / totalMarginBalance）— 档位 sizing 与 13x 硬顶基数。
        含未实现盈亏；禁止用 availableBalance（可用余额）。
        """
        summary = self.get_futures_account_summary(asset)
        for key in ("margin_balance", "total_margin_balance", "wallet_balance"):
            val = float(summary.get(key, 0) or 0)
            if val > 0:
                return val
        return 0.0

    def get_principal_wallet_balance(self, asset="USDT"):
        """兼容别名 → get_total_equity（清单口径：总权益非可用余额）"""
        return self.get_total_equity(asset)

    def get_all_usdt_position_notionals(self):
        """
        账户全部 USDT 永续名义敞口（|qty|×mark）。
        用于双品种 Σnotional ≤ equity×13 硬顶。
        返回 {symbol: notional, ...} 与 total。
        """
        out = {}
        total = 0.0
        try:
            rows = self.client.futures_position_information()
        except Exception as e:
            logger.error(f"[全仓名义查询失败] {e}")
            return out, 0.0
        for p in rows or []:
            try:
                amt = abs(float(p.get("positionAmt") or 0))
            except (TypeError, ValueError):
                continue
            if amt <= 0:
                continue
            sym = str(p.get("symbol") or "").upper()
            try:
                mark = float(
                    p.get("markPrice")
                    or p.get("entryPrice")
                    or 0
                )
            except (TypeError, ValueError):
                mark = 0.0
            if mark <= 0:
                mark = float(self.get_current_price(sym) or 0)
            notion = amt * mark
            if notion <= 0:
                continue
            out[sym] = round(notion, 2)
            total += notion
        return out, round(total, 2)

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

    def _refresh_all_positions(self, force=False):
        """一次拉取全部 USDT 永续持仓，写入 per-symbol 缓存。"""
        now = time.time()
        with self._pos_lock:
            if (
                not force
                and self._all_pos_ts > 0
                and (now - self._all_pos_ts) < float(self._all_pos_ttl)
            ):
                return dict(self._all_pos_rows)
        try:
            rows = self.client.futures_position_information() or []
        except Exception as e:
            logger.error(f"[合并持仓查询失败] {e}")
            with self._pos_lock:
                if self._all_pos_rows and (now - self._all_pos_ts) < 60.0:
                    return dict(self._all_pos_rows)
            return None
        by_sym = {}
        for p in rows:
            try:
                sym = str(p.get("symbol") or "").upper()
                if not sym:
                    continue
                by_sym[sym] = p
                self._set_pos_cache(
                    sym, p.get("positionAmt"), p.get("entryPrice"),
                )
            except Exception:
                continue
        with self._pos_lock:
            self._all_pos_rows = by_sym
            self._all_pos_ts = time.time()
        return by_sym

    def get_position(self, symbol="ETHUSDT", prefer_ws=True):
        """
        返回币安持仓 dict，或 None（确认无仓）。
        REST 失败且无可用缓存时返回 POSITION_QUERY_FAILED，禁止上层当空仓清账本。
        双雷达：优先短 TTL 合并查询，减少 IP 权重。
        """
        sym = str(symbol or "").upper()
        if prefer_ws:
            cached = self._get_pos_cache(sym, max_age=8.0)
            if cached is not None:
                return cached
        # 合并查询（1s TTL）：ETH/XAU 哨兵共享同一次 REST
        all_rows = self._refresh_all_positions(force=False)
        if all_rows is None:
            stale = self._get_pos_cache(sym, max_age=60.0)
            if stale is not None:
                logger.warning(
                    f"[查询持仓失败] {sym}: 回退≤60s缓存，禁止当空仓"
                )
                return stale
            logger.error(
                f"[查询持仓失败] {sym}: 无可用缓存 → 返回 QUERY_FAILED "
                f"（上层必须保留账本/跳过空仓判定）"
            )
            return dict(POSITION_QUERY_FAILED)
        pos = all_rows.get(sym)
        if not pos:
            # 交易所返回里无该 symbol → 确认无仓
            return None
        try:
            amt = abs(float(pos.get("positionAmt") or 0))
        except (TypeError, ValueError):
            amt = 0.0
        if amt <= 0:
            return None
        return pos

    def get_recent_user_trades(self, symbol="ETHUSDT", limit=50):
        """最近用户成交（核对 TP 限价成交 vs 手工减仓）"""
        try:
            limit = max(1, min(int(limit or 50), 100))
            rows = self.client.futures_account_trades(symbol=symbol, limit=limit)
            return list(rows or [])
        except Exception as e:
            logger.warning(f"[成交历史] {symbol}: {e}")
            return []

    def find_protective_stop_prices(self, symbol="ETHUSDT"):
        """
        盘口已挂 STOP / STOP_MARKET（含 Algo）的触发价列表。
        查询失败返回 None（禁止当成 [] 去补挂）。
        """
        orders = self.get_open_orders(symbol, include_algo=True)
        if is_orders_query_failed(orders):
            return None
        out = []
        for o in orders or []:
            order_type = str(o.get("type") or o.get("orderType") or "").upper()
            if order_type not in ("STOP", "STOP_MARKET"):
                continue
            for key in ("stopPrice", "triggerPrice", "activatePrice"):
                val = o.get(key)
                if val is None or str(val).strip() in ("", "0"):
                    continue
                try:
                    px = round(float(val), 2)
                except (TypeError, ValueError):
                    continue
                if px > 0:
                    out.append(px)
                break
        return out

    def _existing_same_limit(self, symbol, side, price, quantity=None, tol=0.02):
        """同向同价已有 reduceOnly LIMIT → 返回该单，避免重复挂。"""
        orders = self.get_open_orders(symbol, include_algo=False)
        if is_orders_query_failed(orders):
            return ORDERS_QUERY_FAILED
        want_side = "BUY" if str(side).upper() in ("BUY", "LONG") else "SELL"
        want_px = round(float(price or 0), 2)
        for o in orders or []:
            if str(o.get("type") or "").upper() != "LIMIT":
                continue
            if str(o.get("side") or "").upper() != want_side:
                continue
            try:
                opx = round(float(o.get("price") or 0), 2)
            except (TypeError, ValueError):
                continue
            if abs(opx - want_px) <= tol:
                return o
        return None

    def _existing_same_stop(self, symbol, side, stop_price, tol=0.05):
        """同向同触发价已有 STOP → 返回该单。"""
        orders = self.get_open_orders(symbol, include_algo=True)
        if is_orders_query_failed(orders):
            return ORDERS_QUERY_FAILED
        want_side = "BUY" if str(side).upper() in ("BUY", "LONG") else "SELL"
        want_px = round(float(stop_price or 0), 2)
        for o in orders or []:
            ot = str(o.get("type") or o.get("orderType") or "").upper()
            if ot not in ("STOP", "STOP_MARKET"):
                continue
            if str(o.get("side") or "").upper() != want_side:
                continue
            px = None
            for key in ("stopPrice", "triggerPrice", "activatePrice"):
                val = o.get(key)
                if val is None or str(val).strip() in ("", "0"):
                    continue
                try:
                    px = round(float(val), 2)
                except (TypeError, ValueError):
                    continue
                break
            if px is not None and abs(px - want_px) <= tol:
                return o
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
        # 防重复：查单失败拒挂；同价已有 LIMIT 则复用
        exist = self._existing_same_limit(symbol, side, float(px_str), quantity=qty)
        if is_orders_query_failed(exist):
            logger.error(
                f"[限价单拒挂] {symbol} 挂单查询失败 → 禁止盲补 "
                f"{side} {qty} @ {px_str}"
            )
            return None
        if exist:
            logger.warning(
                f"[限价单去重] {symbol} 已有同价 LIMIT "
                f"id={exist.get('orderId')} @ {px_str} → 跳过重复挂单"
            )
            return exist
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

    def place_algo_stop_market_order(self, side, stop_price, symbol="ETHUSDT",
                                     close_position=True, quantity=None):
        """Algo 通道 STOP_MARKET：优先 quantity+reduceOnly；否则 closePosition。"""
        try:
            binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
            params = {
                "algoType": "CONDITIONAL",
                "symbol": symbol,
                "side": binance_side,
                "type": "STOP_MARKET",
                "triggerPrice": self.format_price(stop_price, symbol),
            }
            if quantity is not None:
                qty = self.format_quantity(quantity, symbol)
                if qty <= 0:
                    logger.error(f"[Algo止损跳过] 数量无效 {quantity}")
                    return None
                params["quantity"] = qty
                params["reduceOnly"] = "true"
            elif close_position:
                params["closePosition"] = "true"
            order = self._futures_signed_request("post", "algoOrder", params)
            tag = f"qty={quantity}" if quantity is not None else "closePosition"
            logger.info(
                f"[Algo止损成功] {side} {tag} Stop @ {stop_price} "
                f"algoId={order.get('algoId', '') if isinstance(order, dict) else '?'}"
            )
            if isinstance(order, dict):
                order.setdefault("isAlgoOrder", True)
            return order
        except Exception as e:
            logger.error(f"[Algo止损失败] {side} Stop @ {stop_price}: {e}")
            return None

    def place_stop_market_order(self, side, stop_price, symbol="ETHUSDT", quantity=None):
        # 防重复：查单失败拒挂；同触发价已有 STOP 则复用
        exist = self._existing_same_stop(symbol, side, stop_price)
        if is_orders_query_failed(exist):
            logger.error(
                f"[止损单拒挂] {symbol} 挂单查询失败 → 禁止盲补 "
                f"{side} Stop @ {stop_price}"
            )
            return None
        if exist:
            logger.warning(
                f"[止损单去重] {symbol} 已有同价 STOP "
                f"id={exist.get('orderId') or exist.get('algoId')} @ {stop_price} "
                f"→ 跳过重复挂单"
            )
            return exist
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
            if self._is_algo_switch_error(e):
                logger.info(
                    f"[止损单] 普通通道不可用({e}) → 切换 Algo @ {stop_price}"
                )
                return self.place_algo_stop_market_order(
                    side, stop_price, symbol=symbol,
                    close_position=(quantity is None),
                    quantity=quantity,
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

    def fetch_klines(self, symbol="ETHUSDT", interval="30m", limit=220):
        """期货 K 线原始行（行情引擎拉 30m 合成 90m）。"""
        return self.client.futures_klines(
            symbol=symbol, interval=interval, limit=int(limit or 220),
        )

    def fetch_atr_14(self, symbol="ETHUSDT", interval="30m", period=14):
        """
        兼容旧调用 → 走行情引擎（30m 合成 90m + Wilder ATR）。
        interval 参数忽略（固定 90m 合成）。
        """
        try:
            from market_engine import get_market_engine
            eng = get_market_engine(
                symbol,
                fetch_klines=lambda s, iv, lim: self.fetch_klines(s, iv, lim),
            )
            atr, _adx = eng.refresh(force=False)
            if atr > 0:
                return atr
        except Exception as e:
            logger.warning(f"[ATR] {symbol} 行情引擎失败: {e}")
        return 0.0

    def fetch_atr_adx(self, symbol="ETHUSDT", force=False):
        """返回 (atr, adx)，VPS 自主计算。"""
        from market_engine import get_market_engine
        eng = get_market_engine(
            symbol,
            fetch_klines=lambda s, iv, lim: self.fetch_klines(s, iv, lim),
        )
        return eng.refresh(force=bool(force))


binance_client = BinanceClient()

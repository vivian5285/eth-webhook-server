#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CoinW 交易所 API 客户端 - v16.22.1-coinw-init
复刻币安单系统架构，适配 CoinW API

关键差异 vs 币安：
- 方向: BUY/SELL -> long/short
- Symbol: ETHUSDT -> ETH
- 止损止盈: TPSL接口实现
- 幂等键: thirdOrderId
- WebSocket: wss://ws.futurescw.com/perpum
"""

import os
import time
import json
import hmac
import hashlib
import base64
import logging
import math
import threading
from typing import Any, Dict, List, Optional, Callable
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

logger = logging.getLogger(__name__)
COINW_CLIENT_VERSION = "v16.22.1-coinw-init"

# ==================== 常量配置 ====================
BASE_URL = "https://api.coinw.com"
WS_URL = "wss://ws.futurescw.com/perpum"

# REST限流
REST_MIN_INTERVAL_SEC = float(os.getenv("REST_MIN_INTERVAL_SEC", "2.0"))
REST_GLOBAL_MIN_INTERVAL_SEC = float(os.getenv("REST_GLOBAL_MIN_INTERVAL_SEC", "1.5"))
IP_RATE_LIMIT_BACKOFF_SEC = float(os.getenv("IP_RATE_LIMIT_BACKOFF_SEC", "900.0"))

# 缓存TTL
OPEN_ORDERS_CACHE_TTL_SEC = float(os.getenv("OPEN_ORDERS_CACHE_TTL_SEC", "45.0"))
ACCOUNT_SUMMARY_CACHE_TTL_SEC = float(os.getenv("ACCOUNT_SUMMARY_CACHE_TTL_SEC", "60.0"))
POSITION_CACHE_TTL_SEC = float(os.getenv("POSITION_CACHE_TTL_SEC", "8.0"))

# 重试配置
TRADE_RETRY_DELAYS_SEC = (0.0, 1.0, 2.0, 4.0, 8.0)


# ==================== 异常类 ====================
class IpRateLimitedError(RuntimeError):
    """IP限流冷却中"""
    def __init__(self, remaining_sec=0.0):
        self.remaining_sec = float(remaining_sec or 0)
        super().__init__(f"ip_rate_limited remaining={self.remaining_sec:.1f}s")


class OrderQueryFailedList(list):
    """挂单查询失败哨兵（可迭代空列表，禁止当空单处理）"""
    __slots__ = ()

    @property
    def _orders_query_failed(self):
        return True


# 挂单查询失败哨兵
ORDERS_QUERY_FAILED = OrderQueryFailedList()

# 持仓查询失败哨兵
POSITION_QUERY_FAILED = {"_query_failed": True, "positionAmt": None, "entryPrice": None}


# ==================== 工具函数 ====================
def is_orders_query_failed(orders) -> bool:
    """挂单查询失败 -> True"""
    if orders is None:
        return True
    if isinstance(orders, dict) and orders.get("_orders_query_failed") is True:
        return True
    return getattr(orders, "_orders_query_failed", False) is True


def is_position_query_failed(pos) -> bool:
    """持仓查询失败 -> True"""
    return isinstance(pos, dict) and pos.get("_query_failed") is True


def _f(v, default=0.0) -> float:
    """安全转float"""
    try:
        return float(v if v is not None else default)
    except (TypeError, ValueError):
        return float(default)


# ==================== CoinW API客户端 ====================
class CoinWClient:
    def __init__(self):
        self.api_key = os.getenv("COINW_API_KEY")
        self.secret_key = os.getenv("COINW_API_SECRET")

        if not self.api_key or not self.secret_key:
            logger.error("缺少 CoinW API密钥！")

        # 缓存
        self._price_cache: Dict[str, float] = {}
        self._price_cache_ts: Dict[str, float] = {}
        self._price_lock = threading.Lock()

        self._pos_cache: Dict[str, Dict] = {}
        self._pos_cache_ts: Dict[str, float] = {}
        self._pos_lock = threading.Lock()

        self._all_pos_rows: Dict[str, Dict] = {}
        self._all_pos_ts: float = 0.0

        self._account_summary_cache: Dict = {}
        self._account_summary_ts: float = 0.0

        # 挂单缓存
        self._open_orders_cache: Dict[str, tuple] = {}
        self._open_orders_cache_lock = threading.Lock()

        # 限流
        self._rest_last_by_sym: Dict[str, float] = {}
        self._rest_last_global: float = 0.0
        self._rest_throttle_lock = threading.Lock()

        # IP限流
        self._ip_rate_limit_until: float = 0.0
        self._ip_rate_limit_lock = threading.Lock()

        # 回调钩子
        self._rate_limit_hooks: List[Callable] = []
        self._order_reject_hooks: List[Callable] = []

        # 幂等标签记录
        self._recent_limit_place: Dict = {}
        self._place_dedupe_lock = threading.Lock()

        # WebSocket
        self._ws_running = False
        self._ws_thread: Optional[threading.Thread] = None
        self._price_tick_cbs: Dict[str, Callable] = {}
        self._pos_change_cbs: List[Callable] = []

        # 监控模式
        self._monitor_only_syms: set = set()

        logger.info(f"CoinW Client {COINW_CLIENT_VERSION} 已加载")

    # ==================== 签名认证 ====================
    def _sign(self, timestamp: str, method: str, endpoint: str, params: dict = None) -> str:
        """CoinW HMAC-SHA256签名"""
        params = params or {}
        if method.upper() == "GET":
            query_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)
            encoded_params = f"{timestamp}{method}{endpoint}?{query_params}" if query_params else f"{timestamp}{method}{endpoint}"
        else:
            encoded_params = f"{timestamp}{method}{endpoint}{json.dumps(params)}"

        signature = base64.b64encode(
            hmac.new(self.secret_key.encode("utf-8"), encoded_params.encode("utf-8"), hashlib.sha256).digest()
        ).decode("US-ASCII")
        return signature

    def _request(self, method: str, endpoint: str, params: dict = None) -> dict:
        """发送API请求"""
        if params is None:
            params = {}
        timestamp = str(int(time.time() * 1000))
        request_url = f"{BASE_URL}{endpoint}"

        signature = self._sign(timestamp, method, endpoint, params)

        headers = {
            "sign": signature,
            "api_key": self.api_key,
            "timestamp": timestamp,
        }

        try:
            import requests
            if method.upper() == "GET":
                resp = requests.get(request_url, params=params, headers=headers, timeout=10)
            else:
                headers["Content-type"] = "application/json"
                resp = requests.request(method.upper(), request_url, data=json.dumps(params), headers=headers, timeout=10)

            result = resp.json()
            # 检查错误码
            if result.get("code") != 0 and result.get("code") != "0":
                err_msg = result.get("msg", "未知错误")
                # 检查限流
                if "-1003" in str(err_msg) or "too_many" in str(err_msg).lower():
                    self._note_api_error(Exception(str(err_msg)))
                # 检查拒单
                if "insufficient" in str(err_msg).lower() or "-2019" in str(err_msg):
                    self._note_order_reject(Exception(str(err_msg)))
            return result
        except Exception as e:
            logger.error(f"CoinW API请求失败: {e}")
            return {"code": -1, "msg": f"网络异常: {str(e)}"}

    # ==================== 限流控制 ====================
    def _throttle_rest(self, symbol: str = "", *, kind: str = "rest", force: bool = False):
        """REST限流"""
        self._raise_if_ip_rate_limited(symbol)

        sym = str(symbol or "_GLOBAL").upper()
        gap = float(REST_MIN_INTERVAL_SEC)
        g_gap = float(REST_GLOBAL_MIN_INTERVAL_SEC)

        with self._rest_throttle_lock:
            now = time.time()
            last_sym = float(self._rest_last_by_sym.get(sym, 0))
            last_g = float(self._rest_last_global or 0)
            wait = max(0.0, gap - (now - last_sym), g_gap - (now - last_g))
            if wait > 0:
                time.sleep(wait)
            now2 = time.time()
            self._rest_last_by_sym[sym] = now2
            self._rest_last_global = now2

    def _raise_if_ip_rate_limited(self, symbol: str = ""):
        """IP限流检查"""
        rem = self.ip_rate_limit_remaining()
        if rem <= 0:
            return
        logger.warning(f"IP限流中 {symbol or '_'}: 剩余 {rem:.0f}s，禁止REST")
        raise IpRateLimitedError(rem)

    def ip_rate_limit_remaining(self) -> float:
        """IP限流剩余时间"""
        with self._ip_rate_limit_lock:
            return max(0.0, self._ip_rate_limit_until - time.time())

    def mark_ip_rate_limited(self, seconds: float = None):
        """标记IP限流"""
        sec = float(seconds if seconds is not None else IP_RATE_LIMIT_BACKOFF_SEC)
        until = time.time() + max(30.0, sec)
        with self._ip_rate_limit_lock:
            self._ip_rate_limit_until = max(self._ip_rate_limit_until, until)

        # 触发钩子
        for h in list(self._rate_limit_hooks):
            try:
                h(str(symbol or ""), f"ip_rate_limit {sec}s")
            except Exception:
                pass

        logger.error(f"IP限流冷却至 {time.strftime('%H:%M:%S', time.localtime(self._ip_rate_limit_until))}")

    def register_rate_limit_hook(self, cb: Callable):
        """注册限流回调"""
        if callable(cb) and cb not in self._rate_limit_hooks:
            self._rate_limit_hooks.append(cb)

    def _note_api_error(self, err: Exception):
        """记录API错误"""
        text = str(err)
        if "-1003" in text or "too_many" in text.lower():
            self.mark_ip_rate_limited()
            for h in list(self._rate_limit_hooks):
                try:
                    h("_GLOBAL", text)
                except Exception:
                    pass

    def _note_order_reject(self, err: Exception):
        """记录拒单"""
        for h in list(self._order_reject_hooks):
            try:
                h("", str(err))
            except Exception:
                pass

    def register_order_reject_hook(self, cb: Callable):
        """注册拒单回调"""
        if callable(cb) and cb not in self._order_reject_hooks:
            self._order_reject_hooks.append(cb)

    # ==================== 缓存管理 ====================
    def invalidate_open_orders_cache(self, symbol: str = ""):
        """失效挂单缓存"""
        sym = str(symbol or "").upper()
        with self._open_orders_cache_lock:
            if sym:
                self._open_orders_cache.pop(sym, None)
            else:
                self._open_orders_cache.clear()

    def _set_open_orders_cache(self, symbol: str, orders):
        """设置挂单缓存"""
        if is_orders_query_failed(orders):
            return
        sym = str(symbol or "").upper()
        with self._open_orders_cache_lock:
            self._open_orders_cache[sym] = (time.time(), list(orders or []))

    def _get_open_orders_cached(self, symbol: str, max_age: float = None) -> Optional[List]:
        """获取挂单缓存"""
        ttl = float(max_age or OPEN_ORDERS_CACHE_TTL_SEC)
        sym = str(symbol or "").upper()
        with self._open_orders_cache_lock:
            row = self._open_orders_cache.get(sym)
        if not row:
            return None
        ts, orders = row
        if (time.time() - float(ts)) > ttl:
            return None
        return list(orders or [])

    def _set_pos_cache(self, symbol: str, pos_amt: float, entry_price: float):
        """设置持仓缓存"""
        with self._pos_lock:
            self._pos_cache[symbol] = {
                "symbol": symbol,
                "positionAmt": pos_amt,
                "entryPrice": entry_price,
            }
            self._pos_cache_ts[symbol] = time.time()

    def _get_pos_cache(self, symbol: str, max_age: float = 8.0) -> Optional[Dict]:
        """获取持仓缓存"""
        with self._pos_lock:
            row = self._pos_cache.get(symbol)
            ts = self._pos_cache_ts.get(symbol, 0.0)
        if row and (time.time() - ts) <= max_age:
            return dict(row)
        return None

    def invalidate_pos_cache(self, symbol: str = ""):
        """失效持仓缓存"""
        sym = str(symbol or "").upper()
        with self._pos_lock:
            if sym:
                self._pos_cache.pop(sym, None)
            else:
                self._pos_cache.clear()

    # ==================== 监控模式 ====================
    def set_monitor_only(self, symbol: str, enabled: bool = True):
        """设置监控模式"""
        sym = str(symbol or "").upper()
        if not sym:
            return
        if enabled:
            self._monitor_only_syms.add(sym)
        else:
            self._monitor_only_syms.discard(sym)

    def is_monitor_only(self, symbol: str = "") -> bool:
        """检查监控模式"""
        sym = str(symbol or "").upper()
        return sym in self._monitor_only_syms

    # ==================== 核心API ====================

    def get_ticker(self, instrument: str = "ETH") -> float:
        """获取当前价格"""
        res = self._request("GET", "/v1/perpumPublic/ticker", {"instrument": instrument})
        try:
            data = res.get("data", [])
            if isinstance(data, list) and len(data) > 0:
                price = float(data[0].get("last_price", 0))
                with self._price_lock:
                    self._price_cache[instrument] = price
                    self._price_cache_ts[instrument] = time.time()
                return price
            return 0.0
        except (TypeError, ValueError):
            return 0.0

    def get_current_price(self, instrument: str = "ETH", prefer_ws: bool = True) -> float:
        """获取当前价格（优先WS缓存）"""
        if prefer_ws:
            with self._price_lock:
                px = self._price_cache.get(instrument)
                ts = self._price_cache_ts.get(instrument, 0)
            if px and (time.time() - ts) <= 30:
                return px

        return self.get_ticker(instrument)

    # CoinW /v1/perpumPublic/klines 的 granularity 是枚举码，不是分钟/秒。
    # 实测映射（2026-08-30，2026-09-06 补 1m=0）：分钟 -> code
    _KLINE_GRAN = {
        1: 0, 3: 7, 5: 1, 15: 2, 30: 8, 60: 3, 120: 10,
        240: 4, 360: 11, 480: 12, 1440: 5, 10080: 6,
    }

    def get_klines(self, instrument: str = "ETH", interval_min: int = 30,
                   limit: int = 300) -> List[list]:
        """
        拉合约 K 线（公开接口，无需鉴权）。
        interval_min ∈ {3,5,15,30,60,120,240,360,480,1440,10080}。
        返回 [[open_ms, open, high, low, close, volume], ...]，时间升序，
        最后一根可能是未收盘的成型 K 线。失败返回 []。
        """
        code = self._KLINE_GRAN.get(int(interval_min or 0))
        if code is None:
            logger.warning(f"get_klines 不支持的周期 {interval_min}m")
            return []
        try:
            self._throttle_rest(str(instrument or "ETH"), kind="rest_probe")
            res = self._request("GET", "/v1/perpumPublic/klines", {
                "currencyCode": str(instrument or "ETH").upper(),
                "granularity": code,
                "limit": int(limit or 300),
            })
        except Exception as e:
            logger.warning(f"get_klines 异常 {instrument} {interval_min}m: {e}")
            return []
        data = res.get("data")
        if res.get("code") != 0 or not isinstance(data, list):
            logger.warning(
                f"get_klines 失败 {instrument} {interval_min}m: "
                f"code={res.get('code')} msg={res.get('msg')}"
            )
            return []
        out = []
        for row in data:
            try:
                out.append([
                    int(row[0]), float(row[1]), float(row[2]),
                    float(row[3]), float(row[4]), float(row[5]),
                ])
            except (TypeError, ValueError, IndexError):
                continue
        out.sort(key=lambda r: r[0])
        return out

    def get_position_history(self, instrument: str = "ETH", limit: int = 50) -> List[dict]:
        """
        已平仓历史（/v1/perpum/positions/history）。用于竞赛面板给 TV 实盘腿计分。
        每行含 avgOpenPrice / avgClosePrice / direction / netProfit / fee /
        tradeStartDate(ms) / openId / liquidateBy / status。失败返回 []。
        """
        try:
            self._throttle_rest(str(instrument or "ETH"))
            res = self._request("GET", "/v1/perpum/positions/history", {
                "instrument": str(instrument or "ETH").upper(),
                "limit": int(limit or 50),
            })
        except Exception as e:
            logger.warning(f"get_position_history 异常: {e}")
            return []
        data = res.get("data")
        if res.get("code") != 0 or not isinstance(data, dict):
            return []
        rows = data.get("rows")
        return list(rows) if isinstance(rows, list) else []

    def register_price_tick_callback(self, symbol: str, callback: Callable):
        """注册价格回调"""
        sym = str(symbol or "").upper()
        if callable(callback):
            self._price_tick_cbs[sym] = callback

    def _notify_price_tick(self, symbol: str, price: float):
        """触发价格回调"""
        cb = self._price_tick_cbs.get(str(symbol or "").upper())
        if cb:
            try:
                cb(symbol, price)
            except Exception as e:
                logger.debug(f"price tick cb: {e}")

    # ==================== 账户与持仓 ====================

    def get_available_balance(self) -> float:
        """获取可用余额"""
        res = self._request("GET", "/v1/perpum/account/available")
        try:
            return float(res.get("data", {}).get("value", 0))
        except:
            return 0.0

    def get_futures_usdt_balance(self) -> float:
        """SNDK sizing base: exchange USDT assets, not transferable balance."""
        if self.ip_rate_limit_remaining() > 0:
            return 0.0
        try:
            res = self._request("GET", "/v1/perpum/account/getUserAssets")
            if res.get("code") != 0:
                return 0.0
            value = float((res.get("data") or {}).get("availableUsdt"))
            return value if math.isfinite(value) and value > 0 else 0.0
        except Exception as exc:
            logger.error("读取CoinW合约USDT余额失败，拒绝新增SNDK仓位: %s", exc)
            return 0.0

    def get_principal_equity(self) -> float:
        """2026-10-04新增：开仓sizing用的"本金权益"，对齐币安marginBalance口径
        (钱包余额+未实现盈亏，已被其它持仓占用的保证金仍算在内)。
        原来CoinW sizing用get_available_balance(可用余额，已扣掉其它持仓占用的
        保证金)——SNDK持仓占了~130保证金时，ZEC多单只按~217/272开(应为~374)。
        getUserAssets: availableUsdt=钱包余额(不含浮盈、不扣保证金)，crossUnPnl=浮盈。
        查询失败/限流返回0.0，由调用方决定拒单还是退回可用余额。"""
        if self.ip_rate_limit_remaining() > 0:
            return 0.0
        try:
            res = self._request("GET", "/v1/perpum/account/getUserAssets")
            if res.get("code") != 0:
                return 0.0
            d = res.get("data") or {}
            wallet = float(d.get("availableUsdt"))
            if not math.isfinite(wallet) or wallet <= 0:
                return 0.0
            try:
                upnl = float(d.get("crossUnPnl") or 0.0)
            except (TypeError, ValueError):
                upnl = 0.0
            if not math.isfinite(upnl):
                upnl = 0.0
            return max(wallet + upnl, 0.0)
        except Exception as exc:
            logger.error("读取CoinW本金权益失败: %s", exc)
            return 0.0

    def get_account_summary(self) -> Dict:
        """获取账户概览"""
        now = time.time()
        ttl = float(ACCOUNT_SUMMARY_CACHE_TTL_SEC)

        cached = self._account_summary_cache
        ts = self._account_summary_ts
        if cached and (now - ts) < ttl:
            return dict(cached)

        if self.ip_rate_limit_remaining() > 0:
            if cached and (now - ts) < 600:
                return dict(cached)
            return {}

        try:
            balance = self.get_available_balance()
            out = {
                "wallet_balance": balance,
                "available_balance": balance,
                "margin_balance": balance,
            }
            self._account_summary_cache = dict(out)
            self._account_summary_ts = time.time()
            return out
        except Exception as e:
            logger.error(f"账户概览失败: {e}")
            return cached if cached else {}

    def get_total_equity(self) -> float:
        """获取总权益"""
        summary = self.get_account_summary()
        for key in ("margin_balance", "wallet_balance", "available_balance"):
            val = float(summary.get(key, 0) or 0)
            if val > 0:
                return val
        return 0.0

    def get_symbol_leverage(self, symbol: str, default: float = 5.0) -> float:
        """
        读取该品种最近一次真实生效的杠杆（CoinW /v1/perpum/positions 返回的
        "leverage" 字段，用户可在APP自行修改）。CoinW下单接口(/v1/perpum/order)
        强制要求携带leverage参数，且"同一品种已有仓位/挂单时不允许用不同杠杆
        再下单"——如果我们硬编码固定值，等于每次下单都可能悄悄把交易所杠杆
        改回硬编码值，冲掉用户在APP上的手动设置。
        这里优先用_all_pos_rows缓存（get_position/get_all_positions已有的
        持仓行），缓存缺失时（品种从未记录过，例如服务刚重启、内存清零）
        兜底发一次REST查真实持仓，避免"重启后又用旧默认值下单"重演币安那次
        的bug；查不到任何历史（品种真正第一次开仓）才用default——CoinW没有
        独立的"查询品种默认杠杆"接口，第一次下单只能先落一个值，取仓位公式
        本身依赖的5倍作为兜底（2026-08-08改：此前误写成20，跟用户在APP上
        对从未交易过的品种手动设置的杠杆不一致）。
        """
        sym = str(symbol or "").upper()
        try:
            row = self._all_pos_rows.get(sym)
            if not row:
                row = self.get_position(sym, prefer_ws=False, force_rest=True)
            if row:
                lev = float(row.get("leverage") or 0)
                if lev > 0:
                    return lev
        except Exception as e:
            logger.debug(f"[{sym}] 读真实杠杆失败，回退默认值: {e}")
        return float(default or 5.0)

    def get_position(self, instrument: str = "ETH", prefer_ws: bool = True, force_rest: bool = False) -> Optional[Dict]:
        """获取持仓"""
        sym = str(instrument or "ETH").upper()

        # 冷却期禁用REST
        if self.ip_rate_limit_remaining() > 0:
            force_rest = False
            prefer_ws = True

        # WS缓存优先
        if prefer_ws and not force_rest:
            cached = self._get_pos_cache(sym, max_age=POSITION_CACHE_TTL_SEC)
            if cached is not None:
                return cached

        # REST查询
        if self.ip_rate_limit_remaining() > 0:
            stale = self._get_pos_cache(sym, max_age=300)
            if stale is not None:
                return stale
            return dict(POSITION_QUERY_FAILED)

        try:
            self._throttle_rest(sym)
            res = self._request("GET", "/v1/perpum/positions", {"instrument": sym})
            data = res.get("data", [])

            if not isinstance(data, list):
                return None

            for pos in data:
                size = _f(pos.get("quantity", 0))
                if size > 0:
                    entry = _f(pos.get("openPrice", 0))
                    self._set_pos_cache(sym, size, entry)
                    self._all_pos_rows[sym] = pos
                    return pos

            # 无持仓
            self._set_pos_cache(sym, 0, 0)
            return None

        except Exception as e:
            self._note_api_error(e)
            logger.error(f"查询持仓失败 {sym}: {e}")
            return dict(POSITION_QUERY_FAILED)

    def get_all_positions(self, force: bool = False) -> Dict[str, Dict]:
        """获取全部持仓"""
        now = time.time()
        ttl = 90.0 if not force else 8.0

        with self._pos_lock:
            if self._all_pos_ts > 0 and (now - self._all_pos_ts) < ttl:
                return dict(self._all_pos_rows)

        try:
            self._throttle_rest("_POS_ALL")
            res = self._request("GET", "/v1/perpum/positions", {})
            data = res.get("data", [])

            by_sym = {}
            if isinstance(data, list):
                for pos in data:
                    sym = str(pos.get("instrument", "")).upper()
                    if sym:
                        by_sym[sym] = pos
                        size = _f(pos.get("quantity", 0))
                        entry = _f(pos.get("openPrice", 0))
                        self._set_pos_cache(sym, size, entry)

            with self._pos_lock:
                self._all_pos_rows = by_sym
                self._all_pos_ts = time.time()
            return by_sym

        except Exception as e:
            self._note_api_error(e)
            logger.error(f"查询全部持仓失败: {e}")
            with self._pos_lock:
                if self._all_pos_rows and (now - self._all_pos_ts) < 60:
                    return dict(self._all_pos_rows)
            return {}

    # ==================== 订单操作 ====================

    def get_open_orders(self, instrument: str = "ETH", position_type: str = "plan", prefer_cache: bool = True) -> List:
        """获取未成交订单"""
        sym = str(instrument or "ETH").upper()
        ptype = str(position_type or "plan")

        # 冷却期
        if self.ip_rate_limit_remaining() > 0:
            cached = self._get_open_orders_cached(sym, max_age=300)
            if cached is not None:
                return cached
            return ORDERS_QUERY_FAILED

        # 缓存
        if prefer_cache:
            cached = self._get_open_orders_cached(sym)
            if cached is not None:
                return cached

        try:
            self._throttle_rest(sym, kind="rest_probe")
            res = self._request("GET", "/v1/perpum/orders/open", {
                "instrument": sym,
                "positionType": ptype
            })

            data = res.get("data")
            if data is None:
                self._set_open_orders_cache(sym, [])
                return []

            # 兼容不同响应格式
            if isinstance(data, list):
                orders = data
            elif isinstance(data, dict):
                orders = data.get("rows", []) or data.get("list", []) or data.get("data", [])
            else:
                orders = []

            self._set_open_orders_cache(sym, orders)
            return list(orders)

        except Exception as e:
            self._note_api_error(e)
            logger.error(f"查询挂单失败 {sym}: {e}")
            return ORDERS_QUERY_FAILED

    def _normalize_direction(self, side: str) -> str:
        """标准化方向"""
        s = str(side).upper()
        if s in ("LONG", "BUY"):
            return "long"
        return "short"

    def _format_quantity(self, qty: float, instrument: str = "ETH") -> str:
        """格式化数量"""
        return str(round(qty, 4))

    def _format_price(self, price: float, instrument: str = "ETH") -> str:
        """格式化价格。2026-09-22修复：旧版对所有品种固定round(price,2)，
        对过去只交易的高价品种(BNB/XAU/MU等都>=1)碰巧蒙对，但heikin_ashi_
        live.py新增的1000PEPE(≈0.0049)/DOGE(≈0.0998)这类低价品种同样会被
        硬截成2位小数——0.0048845直接壓成0.00(止损价归零，set_sl_tp必炸)。
        跟同一天binance_client.py的format_price同类bug(commit 7d77e8e)
        是同一个问题。CoinW没有公开的tick_size查询接口，只能按价格量级
        动态选小数位数；price>=1时行为完全不变(2位小数)，覆盖当前所有
        已实盘品种(XAU/BNB/XPD/SNDK/OPENAI/MU全部>=1)，零回归风险。"""
        p = float(price)
        if p >= 1:
            return f"{p:.2f}"
        elif p >= 0.01:
            return f"{p:.6f}"
        else:
            return f"{p:.8f}"

    def _with_trade_retry(self, symbol: str, op_name: str, fn: Callable, **kwargs):
        """带重试的交易操作"""
        sym = str(symbol or "").upper()
        if self.is_monitor_only(sym):
            logger.error(f"监控模式拒绝 {op_name} {sym}")
            return None

        delays = tuple(TRADE_RETRY_DELAYS_SEC)
        last_err = None

        for i, delay in enumerate(delays):
            if delay > 0:
                time.sleep(delay)

            try:
                return fn()
            except Exception as e:
                last_err = e
                self._note_api_error(e)

                # 非瞬时错误直接抛出
                text = str(e).lower()
                if "-1003" not in str(e) and "too_many" not in text:
                    if "insufficient" in text or "-2019" in str(e):
                        self._note_order_reject(e)
                    raise

                logger.warning(f"{sym} {op_name} 重试 {i+1}/{len(delays)}: {e}")

        logger.error(f"{sym} {op_name} 重试{len(delays)}次仍失败: {last_err}")
        return None

    def place_market_order(self, side: str, quantity: float, instrument: str = "ETH",
                          reduce_only: bool = False, leverage: Optional[float] = None) -> Optional[Dict]:
        """市价开仓/平仓。
        2026-09-22新增可选leverage参数：默认None时行为完全不变(继续读
        get_symbol_leverage，尊重用户APP手动设置)——position_supervisor_
        coinw.py等既有调用方不传这个参数，零回归风险。heikin_ashi_live.py
        这类独立引擎需要给"从未交易过的新品种"精确指定自己算好的杠杆
        (仓位权重跟名义敞口挂钩，杠杆只影响保证金占用，两者独立)，
        显式传入时优先用调用方给的值，不再去查历史/默认5.0。"""
        sym = str(instrument or "ETH").upper()
        qty = self._format_quantity(quantity)
        direction = self._normalize_direction(side)

        if self.is_monitor_only(sym):
            logger.error(f"监控模式拒绝市价单 {sym}")
            return None

        def _do():
            self._throttle_rest(sym)
            # 用户要求（2026-08-08）：与币安单系统对齐——系统永不再主动改交易所
            # 杠杆，完全尊重用户在CoinW APP上手动设置的真实杠杆。CoinW下单接口
            # 强制要求leverage字段，这里回填真实生效值而非硬编码，避免下单时
            # 悄悄把交易所杠杆冲回固定值。下单量本身仍由defense_profiles固定
            # 5倍公式计算，与此处无关。
            lev = leverage if leverage else self.get_symbol_leverage(sym)
            params = {
                "instrument": sym,
                "direction": direction,
                "leverage": str(int(lev)),
                # CoinW quantityUnit: 0=计价货币(USDT) 1=张数 2=标的货币。
                # 全系统的qty(defense_profiles算出的仓位、TP1/TP2按比例拆分、
                # _format_quantity按4位小数格式化)从头到尾都是ETH等标的货币
                # 数量，不是USDT名义值，必须用2，否则交易所会把0.0104当成
                # 0.0104 USDT去下单，远低于最小名义价值，直接被拒。
                "quantityUnit": "2",
                "quantity": qty,
                "positionModel": "1",
                "positionType": "execute",
            }
            if reduce_only:
                # 平仓时需要持仓ID
                pos = self.get_position(sym)
                if pos and pos.get("id"):
                    params["id"] = str(pos.get("id"))
                    params["closeRate"] = "1.0"

            res = self._request("POST", "/v1/perpum/order", params)
            logger.info(f"市价{'平仓' if reduce_only else '开仓'}: {direction} {qty} {sym} -> {res}")
            return res

        try:
            result = self._with_trade_retry(sym, "market", _do)
            if result and result.get("code") == 0:
                if reduce_only:
                    self.invalidate_pos_cache(sym)
                return result
            if result:
                logger.error(f"市价单被拒 {sym}: code={result.get('code')} msg={result.get('msg')}")
            return None
        except Exception as e:
            logger.error(f"市价单失败 {sym}: {e}")
            return None

    def place_limit_order(self, side: str, quantity: float, price: float,
                         instrument: str = "ETH", reduce_only: bool = True,
                         client_order_id: str = None,
                         leverage: Optional[float] = None) -> Optional[Dict]:
        """限价挂单。
        2026-09-23新增可选leverage参数，跟place_market_order 2026-09-22
        那次同一个理由/同一套写法：默认None时行为完全不变(继续读
        get_symbol_leverage)，独立引擎(vwap_mean_reversion_live.py改用
        限价单降滑点后)需要给"从未交易过的新品种"精确指定自己算好的
        杠杆，不能依赖get_symbol_leverage查到的历史/默认值。"""
        sym = str(instrument or "ETH").upper()
        qty = self._format_quantity(quantity)
        px = self._format_price(price)
        direction = self._normalize_direction(side)
        coid = str(client_order_id or "")[:50].strip() or None

        # 同价去重
        want_px = round(float(px), 2)
        key = (sym, direction, want_px, coid or "")

        with self._place_dedupe_lock:
            cached = self._recent_limit_place.get(key)
            if cached and (time.time() - float(cached[0])) < 120:
                logger.warning(f"限价单去重 {sym} {direction}@{px} tag={coid}")
                return cached[1]

        # 查询现有挂单
        orders = self.get_open_orders(sym)
        if is_orders_query_failed(orders):
            if not coid:
                logger.error(f"限价单查单失败且无标签拒挂 {sym}")
                return None
            logger.warning(f"限价单查单失败但有标签 {coid}，依赖幂等性直接下单")

        # 检查同价单
        if not is_orders_query_failed(orders):
            for o in orders:
                try:
                    opx = round(float(o.get("openPrice", 0) or o.get("orderPrice", 0)), 2)
                except (TypeError, ValueError):
                    continue
                if abs(opx - want_px) <= 0.02:
                    logger.warning(f"限价单同价复用 {sym} @{px} id={o.get('id')}")
                    return o

        # 硬上限检查
        if not is_orders_query_failed(orders):
            if len(orders) >= 5:
                logger.error(f"限价单熔断 {sym}: {len(orders)}>=5")
                return None

        if self.is_monitor_only(sym):
            logger.error(f"监控模式拒绝限价单 {sym}")
            return None

        def _do():
            self._throttle_rest(sym)
            # 同上：回填真实杠杆而非硬编码，且与开仓单保持一致——TP等限价单
            # 多为reduce_only平掉现有仓位，若杠杆跟当前仓位真实杠杆不一致，
            # CoinW可能直接拒单。显式传入leverage时优先用调用方给的值。
            lev = leverage if leverage else self.get_symbol_leverage(sym)
            params = {
                "instrument": sym,
                "direction": direction,
                "leverage": str(int(lev)),
                # 同market订单：qty是ETH等标的货币数量，必须用quantityUnit=2，
                # 不能用0(计价货币USDT)，否则TP限价单量会被交易所解读成极小的
                # USDT名义值。
                "quantityUnit": "2",
                "quantity": qty,
                "positionModel": "1",
                "positionType": "plan",
                "openPrice": px,
            }
            if coid:
                params["thirdOrderId"] = coid

            res = self._request("POST", "/v1/perpum/order", params)
            logger.info(f"限价单: {direction} {qty} @{px} tag={coid} -> {res}")
            return res

        try:
            result = self._with_trade_retry(sym, "limit", _do)
            if result and result.get("code") == 0:
                with self._place_dedupe_lock:
                    self._recent_limit_place[key] = (time.time(), result)
                self.invalidate_open_orders_cache(sym)
                return result
            if result:
                logger.error(f"限价单被拒 {sym}: code={result.get('code')} msg={result.get('msg')}")
            return None
        except Exception as e:
            logger.error(f"限价单失败 {sym}: {e}")
            return None

    def set_sl_tp(self, position_id: str, instrument: str = "ETH",
                  stop_loss_price: float = None, stop_profit_price: float = None,
                  stop_from: int = 2, price_type: int = 3) -> Optional[Dict]:
        """设置止损止盈"""
        sym = str(instrument or "ETH").upper()

        if self.is_monitor_only(sym):
            logger.error(f"监控模式拒绝SL/TP设置 {sym}")
            return None

        params = {
            "id": str(position_id),
            "instrument": sym,
            "stopFrom": str(stop_from),
            "priceType": str(price_type),
        }

        if stop_loss_price is not None:
            params["stopLossPrice"] = self._format_price(stop_loss_price)
        if stop_profit_price is not None:
            params["stopProfitPrice"] = self._format_price(stop_profit_price)

        def _do():
            self._throttle_rest(sym)
            res = self._request("POST", "/v1/perpum/TPSL", params)
            return res

        try:
            result = self._with_trade_retry(sym, "set_sl_tp", _do)
            if result and result.get("code") == 0:
                logger.info(f"SL/TP设置: {sym} SL={stop_loss_price} TP={stop_profit_price}")
                return result
            return None
        except Exception as e:
            logger.error(f"SL/TP设置失败 {sym}: {e}")
            return None

    def set_batch_sl_tp(self, position_id: str, instrument: str = "ETH",
                        stop_loss_price: float = None, stop_profit_price: float = None,
                        close_piece: float = None, stop_type: int = 1,
                        stop_from: int = 2, price_type: int = 3) -> Optional[Dict]:
        """批量设置止损止盈（支持分批止盈）"""
        sym = str(instrument or "ETH").upper()

        if self.is_monitor_only(sym):
            logger.error(f"监控模式拒绝批量SL/TP {sym}")
            return None

        params = {
            "id": str(position_id),
            "instrument": sym,
            "stopType": str(stop_type),
            "stopFrom": str(stop_from),
            "priceType": str(price_type),
        }

        if stop_loss_price is not None:
            params["stopLossPrice"] = self._format_price(stop_loss_price)
        if stop_profit_price is not None:
            params["stopProfitPrice"] = self._format_price(stop_profit_price)
        if close_piece is not None:
            params["closePiece"] = str(close_piece)

        def _do():
            self._throttle_rest(sym)
            res = self._request("POST", "/v1/perpum/addTpsl", params)
            return res

        try:
            result = self._with_trade_retry(sym, "batch_sl_tp", _do)
            if result and result.get("code") == 0:
                logger.info(f"批量SL/TP: {sym} SL={stop_loss_price} TP={stop_profit_price} piece={close_piece}")
                return result
            return None
        except Exception as e:
            logger.error(f"批量SL/TP失败 {sym}: {e}")
            return None

    def get_tp_sl_info(self, position_id: str, stop_from: int = 2) -> List[Dict]:
        """
        查询某持仓当前挂着的SL/TP记录(/v1/perpum/TPSL GET)，用于判断分批止盈
        是否真的成交——响应里的triggerStatus(0未触发/1已触发/2已取消)是交易所
        原生给出的成交状态，不需要像币安那样靠"价到+限价消失"去猜测成交
        (CoinW这个接口直接给结论)。stop_from=2对应"execute"(市价开仓形成的
        持仓)，跟本系统的开仓方式一致。
        注意：带上instrument参数会导致签名校验失败(2026-08-08实测，原因
        不明，可能是这个接口的签名对参数集合有特殊要求)，所以这里不传。
        """
        try:
            res = self._request("GET", "/v1/perpum/TPSL", {
                "openId": str(position_id),
                "stopFrom": str(stop_from),
            })
            if res and res.get("code") == 0:
                data = res.get("data")
                return list(data) if isinstance(data, list) else []
            return []
        except Exception as e:
            logger.debug(f"查SL/TP信息失败 openId={position_id}: {e}")
            return []

    def cancel_order(self, order_id: str, instrument: str = "ETH") -> bool:
        """撤单"""
        sym = str(instrument or "ETH").upper()

        def _do():
            self._throttle_rest(sym)
            res = self._request("DELETE", "/v1/perpum/order", {"id": str(order_id)})
            return res

        try:
            result = self._with_trade_retry(sym, "cancel", _do)
            if result and result.get("code") == 0:
                self.invalidate_open_orders_cache(sym)
                logger.info(f"撤单成功: {sym} id={order_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"撤单失败 {sym}: {e}")
            return False

    def cancel_all_orders(self, instrument: str = "ETH") -> int:
        """撤销全部挂单"""
        sym = str(instrument or "ETH").upper()
        cancelled = 0

        for ptype in ["plan", "execute"]:
            orders = self.get_open_orders(sym, position_type=ptype)
            if is_orders_query_failed(orders) or not orders:
                continue

            for order in orders:
                order_id = order.get("id")
                if order_id:
                    if self.cancel_order(order_id, sym):
                        cancelled += 1

        return cancelled

    def close_all_positions(self, instrument: str = "ETH") -> bool:
        """市价全平仓"""
        sym = str(instrument or "ETH").upper()

        pos = self.get_position(sym, force_rest=True)
        if not pos:
            logger.info(f"全平 {sym}: 已无持仓")
            return True

        pos_id = pos.get("id")
        if not pos_id:
            logger.error(f"全平 {sym}: 缺少持仓ID")
            return False

        def _do():
            self._throttle_rest(sym)
            params = {
                "id": str(pos_id),
                "closeRate": "1.0"
            }
            res = self._request("DELETE", "/v1/perpum/positions", params)
            return res

        try:
            result = self._with_trade_retry(sym, "close_all", _do)
            if result and result.get("code") == 0:
                self.invalidate_pos_cache(sym)
                self.invalidate_open_orders_cache(sym)
                logger.info(f"全平成功: {sym}")
                return True
            return False
        except Exception as e:
            logger.error(f"全平失败 {sym}: {e}")
            return False

    def close_position_by_id(self, position_id: str, instrument: str = "ETH",
                             close_rate: float = 1.0,
                             order_price: Optional[float] = None) -> bool:
        """按持仓ID平仓。
        2026-09-23新增可选order_price参数：官方文档(/v1/perpum/positions
        DELETE，"Close a Position")确认这个平仓接口本身就支持指定平仓价——
        positionType传"plan"+orderPrice传目标价，就是按持仓ID(不是裸方向
        单，不会像place_limit_order那样在CoinW"同一品种允许同时持多空"的
        模式下被误判成开一笔新反向仓位)的限价平仓；不传时(None)保持原有
        行为完全不变(positionType默认"execute"市价平仓)。vwap_mean_
        reversion_live.py用这个给出场也挂上限价，降低CoinW滑点成本。"""
        sym = str(instrument or "ETH").upper()

        def _do():
            self._throttle_rest(sym)
            params = {
                "id": str(position_id),
                "closeRate": str(close_rate)
            }
            if order_price is not None:
                params["positionType"] = "plan"
                params["orderPrice"] = self._format_price(float(order_price), sym)
            res = self._request("DELETE", "/v1/perpum/positions", params)
            return res

        try:
            result = self._with_trade_retry(sym, "close_by_id", _do)
            if result and result.get("code") == 0:
                self.invalidate_pos_cache(sym)
                logger.info(f"按ID平仓: {sym} id={position_id}" + (f" @{order_price}(限价)" if order_price is not None else "(市价)"))
                return True
            return False
        except Exception as e:
            logger.error(f"按ID平仓失败 {sym}: {e}")
            return False

    # ==================== WebSocket ====================

    def start_websocket(self):
        """启动WebSocket连接"""
        if self._ws_running:
            return

        self._ws_running = True
        self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._ws_thread.start()
        logger.info("CoinW WebSocket启动")

    def _ws_loop(self):
        """WebSocket循环"""
        try:
            import websocket
        except ImportError:
            logger.warning("未安装websocket-client")
            self._ws_running = False
            return

        backoff = 1.0

        while self._ws_running:
            try:
                ws = websocket.WebSocketApp(
                    WS_URL,
                    on_message=self._on_ws_message,
                    on_error=self._on_ws_error,
                    on_close=self._on_ws_close,
                )

                # 鉴权
                timestamp = str(int(time.time() * 1000))
                sign = self._sign(timestamp, "GET", "/v1/perpum/ws", {})

                # 发送鉴权
                auth_msg = json.dumps({
                    "event": "login",
                    "params": {
                        "api_key": self.api_key,
                        "sign": sign,
                        "timestamp": timestamp,
                    }
                })

                ws.on_open = lambda ws: ws.send(auth_msg)
                ws.run_forever(ping_interval=30, ping_timeout=10)
                backoff = 1.0

            except Exception as e:
                logger.error(f"WebSocket异常: {e}")

            if self._ws_running:
                logger.warning(f"WebSocket重连等待 {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _on_ws_message(self, ws, message):
        """WebSocket消息处理"""
        try:
            data = json.loads(message)

            # 订单/持仓推送
            if data.get("type") == "position":
                self._handle_position_update(data.get("data", {}))
            elif data.get("type") == "position_change":
                self._handle_position_change(data.get("data", {}))
            elif data.get("type") == "order":
                self._handle_order_update(data.get("data", {}))
            elif "ticker" in str(data):
                # 行情数据
                sym = data.get("instrument", data.get("s", ""))
                price = data.get("last_price", data.get("p", 0))
                if sym and price:
                    with self._price_lock:
                        self._price_cache[sym] = float(price)
                        self._price_cache_ts[sym] = time.time()
                    self._notify_price_tick(sym, float(price))

        except Exception as e:
            logger.debug(f"WS消息解析: {e}")

    def _on_ws_error(self, ws, error):
        """WebSocket错误"""
        logger.warning(f"WebSocket错误: {error}")

    def _on_ws_close(self, ws, code, msg):
        """WebSocket关闭"""
        logger.warning(f"WebSocket关闭: {code} {msg}")

    def _handle_position_update(self, data: Dict):
        """处理持仓更新"""
        sym = str(data.get("instrument", "")).upper()
        if not sym:
            return

        size = _f(data.get("quantity", 0))
        entry = _f(data.get("openPrice", 0))
        self._set_pos_cache(sym, size, entry)

    def _handle_position_change(self, data: Dict):
        """处理持仓变更"""
        sym = str(data.get("instrument", "")).upper()
        if not sym:
            return

        # 触发持仓变更回调
        for cb in self._pos_change_cbs:
            try:
                cb(data)
            except Exception as e:
                logger.debug(f"pos_change cb: {e}")

        # 更新缓存
        size = _f(data.get("quantity", 0))
        entry = _f(data.get("openPrice", 0))
        self._set_pos_cache(sym, size, entry)

    def _handle_order_update(self, data: Dict):
        """处理订单更新"""
        self.invalidate_open_orders_cache(data.get("instrument", ""))

    def register_position_change_callback(self, cb: Callable):
        """注册持仓变更回调"""
        if callable(cb) and cb not in self._pos_change_cbs:
            self._pos_change_cbs.append(cb)

    def stop_websocket(self):
        """停止WebSocket"""
        self._ws_running = False


# ==================== 单例 ====================
coinw_client = CoinWClient()

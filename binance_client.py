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
BINANCE_CLIENT_VERSION = "v16.10.0-probe-trade-budget"
# v16.6.2：绝对封死同 IP 2400/min —— 单品种/全局间隔大幅拉大
REST_MIN_INTERVAL_SEC = float(os.getenv("REST_MIN_INTERVAL_SEC", "2.0"))
# 全账户/全品种合计 REST 硬下限（ETH+XAU 共享同一 IP 配额）
REST_GLOBAL_MIN_INTERVAL_SEC = float(os.getenv("REST_GLOBAL_MIN_INTERVAL_SEC", "1.5"))
# IP 级 -1003 后强制全局 REST 冷却（秒）——冷却期内禁止再打 REST
IP_RATE_LIMIT_BACKOFF_SEC = float(os.getenv("IP_RATE_LIMIT_BACKOFF_SEC", "900.0"))
# 挂单 REST 缓存 TTL（秒）；place/cancel 主动失效
OPEN_ORDERS_CACHE_TTL_SEC = float(os.getenv("OPEN_ORDERS_CACHE_TTL_SEC", "45.0"))
# 账户概览缓存（秒）
ACCOUNT_SUMMARY_CACHE_TTL_SEC = float(os.getenv("ACCOUNT_SUMMARY_CACHE_TTL_SEC", "60.0"))


class IpRateLimitedError(RuntimeError):
    """IP 仍在 -1003 冷却窗内：禁止再打 REST，上层必须 fail-closed / 用缓存。"""

    def __init__(self, remaining_sec=0.0):
        self.remaining_sec = float(remaining_sec or 0)
        super().__init__(f"ip_rate_limited remaining={self.remaining_sec:.1f}s")
# 规格 12.2：首次立即重试，之后 1/2/4/8s，最多 5 次重试
TRADE_RETRY_DELAYS_SEC = (0.0, 1.0, 2.0, 4.0, 8.0)
API_PROBE_INTERVAL_SEC = 30.0
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
        # v16.6.2：持仓核对 REST 更稀；WS 优先，合并缓存拉长
        self._all_pos_ttl = float(os.getenv("ALL_POS_TTL_SEC", "90.0"))
        self._all_pos_force_ttl = float(os.getenv("ALL_POS_FORCE_TTL_SEC", "8.0"))
        self._account_summary_cache = {}
        self._account_summary_ts = 0.0
        self._last_order_event_ts = 0.0
        # 查单失败时的进程内同价锁：仅复用刚挂成功的缓存，禁止盲补首挂
        self._recent_limit_place = {}
        self._recent_stop_place = {}
        self._place_dedupe_lock = threading.Lock()
        self._rest_min_interval = float(REST_MIN_INTERVAL_SEC)
        self._rest_global_min_interval = float(REST_GLOBAL_MIN_INTERVAL_SEC)
        self._rest_last_by_sym = {}
        self._rest_last_global = 0.0
        self._rest_throttle_lock = threading.Lock()
        self._rate_limit_hooks = []
        self._order_reject_hooks = []
        self._api_unavailable_hooks = []
        self._ip_rate_limit_until = 0.0
        self._ip_rate_limit_lock = threading.Lock()
        self._rate_limit_hook_last_fire_ts = 0.0
        self._open_orders_cache = {}  # symbol -> (ts, orders_list)
        self._open_orders_cache_lock = threading.Lock()
        self._recent_local_cancels = {}
        self._local_cancel_lock = threading.Lock()
        self._monitor_only_syms = set()
        self._monitor_only_lock = threading.Lock()
        self._trade_retry_locks = {}
        self._trade_retry_lock_guard = threading.Lock()
        self._cred_lock = threading.Lock()
        logger.info(f"🟢 Binance Client {BINANCE_CLIENT_VERSION} 已加载")

    def rebind_credentials(self, api_key, api_secret, force=False):
        """热切换 API Key/Secret（Console 多档案）。有仓切换由上层门禁。"""
        key = str(api_key or "").strip()
        secret = str(api_secret or "").strip()
        if not key or not secret:
            logger.error("[rebind] 拒绝：空密钥")
            return False
        with getattr(self, "_cred_lock", threading.Lock()):
            same = (key == str(self.api_key or "")) and (secret == str(self.api_secret or ""))
            if same and not force:
                return True
            try:
                self.client = Client(key, secret)
                self.api_key = key
                self.api_secret = secret
                # 清缓存，避免旧账户挂单/持仓串读
                with self._pos_lock:
                    self._pos_cache.clear()
                    self._pos_cache_ts.clear()
                    self._all_pos_rows = {}
                    self._all_pos_ts = 0.0
                self._open_orders_cache = {}
                self._symbol_filters = {}
                logger.warning(
                    f"🔁 [rebind] Binance API 已切换 key={key[:4]}…{key[-4:]}"
                )
                return True
            except Exception as e:
                logger.error(f"[rebind] 失败: {e}")
                return False

    def register_rate_limit_hook(self, cb):
        """限流回调：cb(symbol, err_text)。supervisor 用其暂停品种。"""
        if callable(cb) and cb not in self._rate_limit_hooks:
            self._rate_limit_hooks.append(cb)

    def mark_ip_rate_limited(self, seconds=None):
        """IP 共享配额撞墙 → 全局 REST 冷却窗口。"""
        sec = float(seconds if seconds is not None else IP_RATE_LIMIT_BACKOFF_SEC)
        until = time.time() + max(30.0, sec)
        if not hasattr(self, "_ip_rate_limit_lock"):
            self._ip_rate_limit_lock = threading.Lock()
            self._ip_rate_limit_until = 0.0
        with self._ip_rate_limit_lock:
            self._ip_rate_limit_until = max(
                float(getattr(self, "_ip_rate_limit_until", 0) or 0), until
            )
        # 账号级节流阀同步进入强制静默（ETH/XAU 共用）
        try:
            from api_throttle import get_throttle
            get_throttle("binance").enter_silence(sec, reason="ip_rate_limit")
        except Exception:
            pass
        logger.error(
            f"🧊 [IP限流] REST 全局冷却至 "
            f"{time.strftime('%H:%M:%S', time.localtime(self._ip_rate_limit_until))} "
            f"(+{sec:.0f}s)"
        )

    def ip_rate_limit_remaining(self):
        until = float(getattr(self, "_ip_rate_limit_until", 0) or 0)
        return max(0.0, until - time.time())

    def _raise_if_ip_rate_limited(self, symbol=""):
        """
        铁律（v16.4.6）：冷却期内禁止再打 REST。
        旧逻辑只 sleep(5s) 后继续请求 → 冷却窗内反复 -1003 → 告警轰炸。
        """
        rem = self.ip_rate_limit_remaining()
        if rem <= 0:
            return
        logger.warning(
            f"🧊 [IP限流] {symbol or '_'} 拒绝 REST "
            f"(冷却剩余 {rem:.0f}s，禁止打交易所)"
        )
        raise IpRateLimitedError(rem)

    def invalidate_open_orders_cache(self, symbol=""):
        """下单/撤单后失效挂单缓存。"""
        sym = str(symbol or "").upper()
        with getattr(self, "_open_orders_cache_lock", threading.Lock()):
            cache = getattr(self, "_open_orders_cache", None)
            if cache is None:
                self._open_orders_cache = {}
                return
            if sym:
                cache.pop(sym, None)
            else:
                cache.clear()

    def _get_open_orders_cached(self, symbol, max_age=None):
        sym = str(symbol or "").upper()
        ttl = float(
            max_age
            if max_age is not None
            else OPEN_ORDERS_CACHE_TTL_SEC
        )
        with getattr(self, "_open_orders_cache_lock", threading.Lock()):
            row = (getattr(self, "_open_orders_cache", {}) or {}).get(sym)
        if not row:
            return None
        ts, orders = row
        if (time.time() - float(ts or 0)) > ttl:
            return None
        # 返回浅拷贝，避免调用方改坏缓存
        return list(orders or [])

    # ------------------------------------------------------------------ #
    #  轻量级挂单状态检查：不花探针预算，用于防御挂单判断                  #
    # ------------------------------------------------------------------ #
    def defensive_orders_look_ok(self, symbol, max_age=60.0):
        """
        判断当前是否可以进行防御性下单（TP/止损），不耗探针预算。

        返回 (ok, reason):
          ok=True  → 可以正常下单
          ok=False → 查过本地状态，reason 说明原因

        策略：
        1. 缓存新鲜（≤max_age）且非空 → ok=True（上次已知盘口干净）
        2. 缓存新鲜但为空 → ok=True（已知无单，可以挂）
        3. 缓存过期或不存在：
           a. 有本地近期 limit_place 记录（≤120s）→ ok=True（防御单刚挂出去）
           b. 无任何本地记录 → False，"冷启动·无缓存无记录"
        4. 冷却/节流阀拒绝 → False，"冷却中"

        注意：本方法不查 REST，永远不触发节流阀。
        """
        sym = str(symbol or "").upper()
        now = time.time()

        # 1) 缓存新鲜
        cached = self._get_open_orders_cached(sym, max_age=max_age)
        if cached is not None:
            # 有缓存且新鲜 → 信任它（即使为空也是"已知空"）
            return True, "cache_hit"

        # 2) 缓存过期/不存在，检查本地近期防御单记录
        recent = getattr(self, "_recent_limit_place", {}) or {}
        for k, (ts, _) in list(recent.items()):
            if k[0].upper() == sym and (now - float(ts)) < 120.0:
                return True, "recent_limit"

        # 3) 无缓存也无近期记录：冷启动
        return False, "cold_start"

    def _set_open_orders_cache(self, symbol, orders):
        if is_orders_query_failed(orders):
            return
        sym = str(symbol or "").upper()
        with getattr(self, "_open_orders_cache_lock", threading.Lock()):
            if not hasattr(self, "_open_orders_cache"):
                self._open_orders_cache = {}
            self._open_orders_cache[sym] = (time.time(), list(orders or []))

    def register_order_reject_hook(self, cb):
        """开仓/关键挂单被拒（保证金等）回调：cb(symbol, err_text)。不自动重试。"""
        if not hasattr(self, "_order_reject_hooks"):
            self._order_reject_hooks = []
        if callable(cb) and cb not in self._order_reject_hooks:
            self._order_reject_hooks.append(cb)

    def register_api_unavailable_hook(self, cb):
        """规格 12.2：交易 REST 5 次退避失败 → cb(symbol, err_text) 进入仅监控。"""
        if not hasattr(self, "_api_unavailable_hooks"):
            self._api_unavailable_hooks = []
        if callable(cb) and cb not in self._api_unavailable_hooks:
            self._api_unavailable_hooks.append(cb)

    def set_monitor_only(self, symbol, enabled=True):
        """仅监控：拒绝下单/改撤，只读与 WS 价格仍可用。"""
        if not hasattr(self, "_monitor_only_syms"):
            self._monitor_only_syms = set()
            self._monitor_only_lock = threading.Lock()
        sym = str(symbol or "").upper()
        if not sym:
            return
        with self._monitor_only_lock:
            if enabled:
                self._monitor_only_syms.add(sym)
            else:
                self._monitor_only_syms.discard(sym)

    def is_monitor_only(self, symbol=""):
        if not hasattr(self, "_monitor_only_syms"):
            return False
        sym = str(symbol or "").upper()
        with getattr(self, "_monitor_only_lock", threading.Lock()):
            return sym in self._monitor_only_syms

    @staticmethod
    def _is_transient_api_error(err):
        """网络超时 / 5xx / 连接中断 → 可指数退避；拒单与限流不算 transient。"""
        text = str(err or "")
        low = text.lower()
        if (
            "-1003" in text
            or "too_many_requests" in low
            or "banned until" in low
            or "-2019" in text
            or "-2018" in text
            or "-4164" in text
            or "insufficient" in low
            or "margin is insufficient" in low
        ):
            return False
        markers = (
            "timeout", "timed out", "time out",
            "connection", "connecterror", "connectionreset",
            "remotedisconnected", "temporarily unavailable",
            "502", "503", "504", "500",
            "-1001", "internal error", "read timed out",
            "broken pipe", "network", "ssl",
        )
        return any(m in low or m in text for m in markers)

    def _trade_lock_for(self, symbol):
        if not hasattr(self, "_trade_retry_lock_guard"):
            self._trade_retry_lock_guard = threading.Lock()
            self._trade_retry_locks = {}
        sym = str(symbol or "_GLOBAL").upper()
        with self._trade_retry_lock_guard:
            lk = self._trade_retry_locks.get(sym)
            if lk is None:
                lk = threading.Lock()
                self._trade_retry_locks[sym] = lk
            return lk

    def _fire_api_unavailable(self, symbol, err):
        if not hasattr(self, "_api_unavailable_hooks"):
            self._api_unavailable_hooks = []
        text = str(err or "")[:400]
        for h in list(self._api_unavailable_hooks):
            try:
                h(str(symbol or ""), text)
            except Exception:
                pass

    def _with_trade_retry(self, symbol, op_name, fn, *, reduce_only=False):
        """
        规格 12.2：交易类 REST 指数退避重试。
        首次失败后按 0/1/2/4/8s 再试最多 5 次；全失败 → 仅监控钩子。
        重试期间同品种串行，禁止并发下单/改撤。

        修复（v16.9.2）：-1003 IP 限流时，先标记本地冷却再进入重试循环，
        而非直接 raise 让冷却窗口白白空转、TP 挂单丢失。
        """
        sym = str(symbol or "").upper()
        if self.is_monitor_only(sym):
            logger.error(f"[仅监控] 拒绝 {op_name} {sym}")
            return None
        delays = tuple(TRADE_RETRY_DELAYS_SEC)
        lk = self._trade_lock_for(sym)
        # -1003 冷却标志：首次命中时记录，贯穿本次重试循环
        ip_rate_limited_flag = False
        with lk:
            if self.is_monitor_only(sym):
                logger.error(f"[仅监控] 拒绝 {op_name} {sym}")
                return None
            last_err = None
            # 第 1 次尝试
            try:
                return fn()
            except Exception as e:
                last_err = e
                # 标记本地冷却（确保冷却窗口已激活），但不 raise
                # 让重试循环继续，在冷却期 sleep 中等待
                if self._note_api_error(e, sym):
                    ip_rate_limited_flag = True
                if (not reduce_only) and self._note_order_reject(e, sym, reduce_only=False):
                    raise
                if not self._is_transient_api_error(e):
                    raise
                logger.warning(
                    f"[API退避] {sym} {op_name} 首次失败(transient): {e}"
                    + (" [已激活本地冷却·进入重试循环]" if ip_rate_limited_flag else "")
                )
            for i, delay in enumerate(delays):
                if self.is_monitor_only(sym):
                    logger.error(f"[仅监控] 中止重试 {op_name} {sym}")
                    return None
                # IP 冷却中：等待冷却窗口到期后再尝试，避免冷却窗口空转
                if ip_rate_limited_flag:
                    ip_rem = self.ip_rate_limit_remaining()
                    if ip_rem > 0:
                        wait = min(ip_rem, delay if delay > 0 else 2.0)
                        logger.warning(
                            f"[API退避] {sym} {op_name} IP冷却中 "
                            f"(剩余 {ip_rem:.0f}s) → 等待 {wait:.1f}s 后重试"
                        )
                        time.sleep(wait)
                elif delay and delay > 0:
                    time.sleep(float(delay))
                try:
                    out = fn()
                    if ip_rate_limited_flag:
                        logger.info(
                            f"[API退避] {sym} {op_name} 在IP冷却期重试成功 "
                            f"(delay={delay:.1f}s)"
                        )
                    return out
                except Exception as e:
                    last_err = e
                    # 再次命中 -1003：刷新本地冷却，继续循环等待
                    if self._note_api_error(e, sym):
                        ip_rate_limited_flag = True
                    if (not reduce_only) and self._note_order_reject(
                        e, sym, reduce_only=False
                    ):
                        raise
                    if not self._is_transient_api_error(e):
                        raise
                    logger.warning(
                        f"[API退避] {sym} {op_name} 重试 {i + 1}/{len(delays)} "
                        f"delay={delay:.1f}s 失败: {e}"
                    )
            logger.error(
                f"[API不可用] {sym} {op_name} 已重试{len(delays)}次仍失败 → 仅监控 | "
                f"{last_err}"
            )
            self._fire_api_unavailable(sym, last_err)
            return None

    def _mark_local_cancel(self, symbol, order_id):
        """记录本地主动撤单，供 WS 区分交易所单方面取消（规格 12.3）。"""
        if not hasattr(self, "_recent_local_cancels"):
            self._recent_local_cancels = {}
            self._local_cancel_lock = threading.Lock()
        oid = str(order_id or "").strip()
        if not oid:
            return
        key = (str(symbol or "").upper(), oid)
        with self._local_cancel_lock:
            now = time.time()
            self._recent_local_cancels[key] = now
            # 清理 2 分钟外的记录
            cut = now - 120.0
            dead = [k for k, t in self._recent_local_cancels.items() if float(t) < cut]
            for k in dead:
                self._recent_local_cancels.pop(k, None)
        try:
            self.invalidate_open_orders_cache(symbol)
        except Exception:
            pass

    def was_local_cancel(self, symbol, order_id, window_sec=90.0):
        if not hasattr(self, "_recent_local_cancels"):
            return False
        oid = str(order_id or "").strip()
        if not oid:
            return False
        key = (str(symbol or "").upper(), oid)
        with getattr(self, "_local_cancel_lock", threading.Lock()):
            ts = self._recent_local_cancels.get(key)
        if ts is None:
            return False
        return (time.time() - float(ts)) <= float(window_sec)

    def _throttle_rest(self, symbol="", *, kind="rest", force=False):
        """单品种 + 全账户 REST 间隔硬下限；IP 冷却/账号节流阀拒绝时不打交易所。"""
        self._raise_if_ip_rate_limited(symbol)
        # 账号级预算阀（ETH/XAU 共用）；静默或超预算 → 拒绝
        try:
            from api_throttle import get_throttle
            ok, detail = get_throttle("binance").acquire(
                kind or "rest", force=bool(force), symbol=str(symbol or ""),
            )
            if not ok:
                rem = 0.0
                if str(detail).startswith("silence:"):
                    try:
                        rem = float(str(detail).split(":", 1)[1].rstrip("s"))
                    except Exception:
                        rem = self.ip_rate_limit_remaining()
                logger.warning(
                    f"🧊 [节流阀] {symbol or '_'} 拒绝 REST ({detail})"
                )
                raise IpRateLimitedError(rem or 1.0)
        except IpRateLimitedError:
            raise
        except Exception as e:
            logger.debug(f"api_throttle skip: {e}")
        if not hasattr(self, "_rest_throttle_lock"):
            self._rest_throttle_lock = threading.Lock()
            self._rest_last_by_sym = {}
            self._rest_last_global = 0.0
            self._rest_min_interval = float(REST_MIN_INTERVAL_SEC)
            self._rest_global_min_interval = float(REST_GLOBAL_MIN_INTERVAL_SEC)
        if not hasattr(self, "_rate_limit_hooks"):
            self._rate_limit_hooks = []
        sym = str(symbol or "_GLOBAL").upper()
        gap = float(getattr(self, "_rest_min_interval", REST_MIN_INTERVAL_SEC) or 2.0)
        g_gap = float(
            getattr(self, "_rest_global_min_interval", REST_GLOBAL_MIN_INTERVAL_SEC)
            or 1.5
        )
        with self._rest_throttle_lock:
            now = time.time()
            last_sym = float(self._rest_last_by_sym.get(sym) or 0)
            last_g = float(getattr(self, "_rest_last_global", 0) or 0)
            wait = max(0.0, gap - (now - last_sym), g_gap - (now - last_g))
            if wait > 0:
                time.sleep(wait)
            now2 = time.time()
            self._rest_last_by_sym[sym] = now2
            self._rest_last_global = now2

    def _note_api_error(self, err, symbol=""):
        """检测 -1003 / TOO_MANY_REQUESTS → 通知钩子暂停品种（全局去重）。"""
        if not hasattr(self, "_rate_limit_hooks"):
            self._rate_limit_hooks = []
        text = str(err or "")
        low = text.lower()
        if (
            "-1003" in text
            or "too_many_requests" in low
            or "banned until" in low
            or "way too much request" in low
        ):
            try:
                self.mark_ip_rate_limited()
            except Exception:
                pass
            # 钩子全局去重：同一冷却窗只广播一次，杜绝 TG/钉钉轰炸
            now = time.time()
            last_fire = float(
                getattr(self, "_rate_limit_hook_last_fire_ts", 0) or 0
            )
            if (now - last_fire) < 120.0:
                return True
            self._rate_limit_hook_last_fire_ts = now
            for h in list(self._rate_limit_hooks):
                try:
                    h(str(symbol or ""), text)
                except Exception:
                    pass
            # 同 IP 多品种：广播 _GLOBAL，使 ETH/XAU 一并暂停
            sym_u = str(symbol or "").upper()
            if sym_u not in ("", "_GLOBAL"):
                for h in list(self._rate_limit_hooks):
                    try:
                        h("_GLOBAL", text)
                    except Exception:
                        pass
            return True
        return False

    def _note_order_reject(self, err, symbol="", *, reduce_only=False):
        """规格 12.1：保证金不足等拒单 → 钩子暂停，禁止自动重试。"""
        if reduce_only:
            return False
        if not hasattr(self, "_order_reject_hooks"):
            self._order_reject_hooks = []
        text = str(err or "")
        low = text.lower()
        is_reject = (
            "-2019" in text
            or "-2018" in text
            or "-4164" in text
            or "insufficient" in low
            or "margin is insufficient" in low
            or "notional" in low and "filter" in low
        )
        if not is_reject:
            return False
        for h in list(self._order_reject_hooks):
            try:
                h(str(symbol or ""), text)
            except Exception:
                pass
        return True

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
        symbol = str(params.get("symbol") or "")
        self._throttle_rest(symbol)
        try:
            return self.client._request_futures_api(
                method.lower(), path, signed=True, data=params,
            )
        except Exception as e:
            self._note_api_error(e, symbol)
            raise

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
        if self.ip_rate_limit_remaining() > 0:
            return ORDERS_QUERY_FAILED
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
        except IpRateLimitedError:
            return ORDERS_QUERY_FAILED
        except Exception as e:
            logger.warning(f"[Algo挂单查询] {symbol}: {e}")
            return ORDERS_QUERY_FAILED

    def get_open_orders(self, symbol="ETHUSDT", include_algo=True, prefer_cache=True):
        """
        成功返回 list；REST 失败返回 ORDERS_QUERY_FAILED。
        铁律：查询失败 ≠ 盘口无单；上层禁止据此补挂限价/止损。
        v16.6.2：冷却期零 REST；长缓存优先；Algo 二次查询受同一冷却门禁。
        """
        symbol = str(symbol or "ETHUSDT").upper()
        # 冷却期：绝不进入 REST（连 throttle sleep 都不走）
        # 铁律：限流/冷却下「空缓存」≠ 盘口无单 → 必须 ORDERS_QUERY_FAILED，
        # 禁止上层当成 0 挂单去核武撤挂 / 狂补（今日实盘：回退缓存 0 笔 → 裸奔）。
        if self.ip_rate_limit_remaining() > 0:
            cached = self._get_open_orders_cached(symbol, max_age=300.0)
            if cached is not None and len(cached) > 0:
                logger.warning(
                    f"[获取挂单] {symbol}: IP冷却 → 仅用非空缓存 ({len(cached)} 笔)"
                )
                return cached
            logger.warning(
                f"[获取挂单] {symbol}: IP冷却且缓存空/无 → ORDERS_QUERY_FAILED"
            )
            return ORDERS_QUERY_FAILED
        if prefer_cache:
            cached = self._get_open_orders_cached(symbol)
            if cached is not None:
                return cached
        try:
            self._throttle_rest(symbol, kind="rest_probe")
        except IpRateLimitedError:
            cached = self._get_open_orders_cached(symbol, max_age=300.0)
            if cached is not None and len(cached) > 0:
                logger.warning(
                    f"[获取挂单] {symbol}: 节流阀 → 回退非空缓存 ({len(cached)} 笔)"
                )
                return cached
            logger.warning(
                f"[获取挂单] {symbol}: 节流阀且缓存空/无 → ORDERS_QUERY_FAILED"
            )
            return ORDERS_QUERY_FAILED
        try:
            orders = list(self.client.futures_get_open_orders(symbol=symbol) or [])
        except Exception as e:
            self._note_api_error(e, symbol)
            logger.error(f"[获取挂单失败] {symbol}: {e}")
            cached = self._get_open_orders_cached(symbol, max_age=300.0)
            if cached is not None and len(cached) > 0:
                return cached
            return ORDERS_QUERY_FAILED
        if not include_algo:
            self._set_open_orders_cache(symbol, orders)
            return orders
        # Algo 二次 REST：冷却/预算不足则只返回普通单
        if self.ip_rate_limit_remaining() > 0:
            self._set_open_orders_cache(symbol, orders)
            return orders
        algo_orders = self.get_open_algo_orders(symbol)
        if is_orders_query_failed(algo_orders):
            # 普通单已拿到；Algo 失败时仍返回普通单
            logger.warning(
                f"[挂单合并] {symbol} Algo 查询失败 → 仅用普通挂单 "
                f"({len(orders)} 笔)；补挂前须再核实"
            )
            self._set_open_orders_cache(symbol, orders)
            return orders
        if not algo_orders:
            self._set_open_orders_cache(symbol, orders)
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
        self._set_open_orders_cache(symbol, merged)
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
        # IP 冷却期：绝不再打 ticker REST
        if self.ip_rate_limit_remaining() > 0:
            stale = self._get_ws_price(symbol, max_age=300)
            return stale or 0.0
        last = float(self._last_rest_price_fetch_by_sym.get(symbol) or 0)
        if last > 0 and (now - last) < min_gap:
            stale = self._get_ws_price(symbol, max_age=120)
            return stale or 0.0
        try:
            self._throttle_rest(symbol)
            self._last_rest_price_fetch_by_sym[symbol] = time.time()
            self._last_rest_price_fetch = time.time()
            ticker = self.client.futures_symbol_ticker(symbol=symbol)
            price = float(ticker["price"])
            if price > 0:
                self._set_ws_price(symbol, price)
            return price
        except IpRateLimitedError:
            stale = self._get_ws_price(symbol, max_age=300)
            return stale or 0.0
        except Exception as e:
            self._note_api_error(e, symbol)
            logger.error(f"[查询价格失败] {symbol}: {e}")
            stale = self._get_ws_price(symbol, max_age=120)
            return stale or 0.0

    def get_futures_account_summary(self, asset="USDT"):
        """合约账户概览：用于本金锚点，禁止用 depleted available 算档位额度"""
        asset = str(asset or "USDT")
        now = time.time()
        ttl = float(ACCOUNT_SUMMARY_CACHE_TTL_SEC)
        cached = getattr(self, "_account_summary_cache", {}) or {}
        ts = float(getattr(self, "_account_summary_ts", 0) or 0)
        if cached and (now - ts) < ttl:
            return dict(cached)
        if self.ip_rate_limit_remaining() > 0:
            if cached and (now - ts) < 600.0:
                return dict(cached)
            return {}
        try:
            self._throttle_rest("_ACCOUNT", kind="rest_probe")
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
            self._account_summary_cache = dict(out)
            self._account_summary_ts = time.time()
            return out
        except IpRateLimitedError:
            if cached and (now - ts) < 600.0:
                return dict(cached)
            return {}
        except Exception as e:
            self._note_api_error(e, "")
            logger.error(f"[账户概览失败] {e}")
            if cached and (now - ts) < 600.0:
                return dict(cached)
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
            rows_map = self._refresh_all_positions(force=False)
            if rows_map is None:
                return out, 0.0
            rows = list(rows_map.values())
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
            summary = self.get_futures_account_summary(asset)
            margin_bal = float(summary.get("margin_balance", 0) or 0)
            if margin_bal > 0:
                return margin_bal
            return float(summary.get("available_balance", 0) or 0)
        except Exception as e:
            logger.error(f"[查询余额失败] {e}")
            return 0.0

    def _refresh_all_positions(self, force=False):
        """一次拉取全部 USDT 永续持仓，写入 per-symbol 缓存。"""
        now = time.time()
        ttl = float(
            getattr(self, "_all_pos_force_ttl", 1.0)
            if force
            else getattr(self, "_all_pos_ttl", 30.0)
        )
        with self._pos_lock:
            if (
                self._all_pos_ts > 0
                and (now - self._all_pos_ts) < ttl
            ):
                return dict(self._all_pos_rows)
        try:
            self._throttle_rest("_POS_ALL")
            rows = self.client.futures_position_information() or []
        except IpRateLimitedError:
            with self._pos_lock:
                if self._all_pos_rows and (now - self._all_pos_ts) < 180.0:
                    logger.warning(
                        "[合并持仓] IP限流 → 回退持仓缓存 "
                        f"(age={now - self._all_pos_ts:.0f}s)"
                    )
                    return dict(self._all_pos_rows)
            return None
        except Exception as e:
            logger.error(f"[合并持仓查询失败] {e}")
            try:
                self._note_api_error(e, symbol="")
            except Exception:
                pass
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

    def get_position(self, symbol="ETHUSDT", prefer_ws=True, force_rest=False):
        """
        返回币安持仓 dict，或 None（确认无仓）。
        REST 失败且无可用缓存时返回 POSITION_QUERY_FAILED，禁止上层当空仓清账本。
        双雷达：WS 优先；常规 REST 合并缓存；force_rest 用于平仓/恢复。

        修复（v16.9.2）：force_rest=True 时也必须通过 IP 冷却门禁，
        避免强制 REST 在冷却期内触发 -1003 加剧限流。
        """
        sym = str(symbol or "").upper()
        # 冷却期：force_rest 降级为缓存/WS，禁止再打仓位 REST
        # 修复（v16.9.2）：即使 force_rest=True，冷却期也必须拒绝
        # 避免「平仓 force_rest → 打 REST → 命中 -1003 → 冷却窗口更久」
        if self.ip_rate_limit_remaining() > 0:
            logger.warning(
                f"[查询持仓] {sym}: IP冷却中 → 拒绝 REST (force_rest={force_rest})"
            )
            force_rest = False
            prefer_ws = True
        if prefer_ws and not force_rest:
            # UD-WS 健康时允许更长缓存；否则短窗口尽快回退 REST
            max_age = 45.0 if getattr(self, "_ud_ws_running", False) else 12.0
            cached = self._get_pos_cache(sym, max_age=max_age)
            if cached is not None:
                return cached
        if self.ip_rate_limit_remaining() > 0:
            stale = self._get_pos_cache(sym, max_age=300.0)
            if stale is not None:
                return stale
            with self._pos_lock:
                if self._all_pos_rows and (time.time() - self._all_pos_ts) < 300.0:
                    pos = self._all_pos_rows.get(sym)
                    if not pos:
                        return None
                    try:
                        amt = abs(float(pos.get("positionAmt") or 0))
                    except (TypeError, ValueError):
                        amt = 0.0
                    return None if amt <= 0 else pos
            return dict(POSITION_QUERY_FAILED)
        # 合并查询：哨兵共享；force_rest 走短强制窗口
        all_rows = self._refresh_all_positions(force=bool(force_rest or not prefer_ws))
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
            self._throttle_rest(symbol, kind="rest_probe")
            limit = max(1, min(int(limit or 50), 100))
            rows = self.client.futures_account_trades(symbol=symbol, limit=limit)
            return list(rows or [])
        except IpRateLimitedError:
            logger.warning(f"[成交历史] {symbol}: 节流阀/IP冷却拒绝")
            return []
        except Exception as e:
            logger.warning(f"[成交历史] {symbol}: {e}")
            return []

    def fetch_income_history(self, start_time_ms=None, end_time_ms=None, limit=1000):
        """
        已实现盈亏历史（走 _futures_signed_request 含节流阀/冷却门禁）。
        修复（v16.9.2）：根治 console_api 直接调 client.futures_income_history
        绕过节流阀导致 -1003 的问题。
        """
        try:
            self._throttle_rest("_INCOME", kind="rest_probe")
        except IpRateLimitedError:
            logger.warning("[收入历史] 节流/IP冷却 → 跳过 REST")
            return []
        params = {"incomeType": "REALIZED_PNL", "limit": min(int(limit or 1000), 1000)}
        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)
        try:
            return self._futures_signed_request("get", "income", params) or []
        except IpRateLimitedError:
            logger.warning("[收入历史] IP冷却拒绝")
            return []
        except Exception as e:
            self._note_api_error(e, "_INCOME")
            logger.warning(f"[收入历史] {e}")
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
            # 节流/REST失败：本地记录兜底，防止误报"无单"导致防御单叠挂
            ok, reason = self.defensive_orders_look_ok(symbol, max_age=60.0)
            if ok:
                # 有本地近期记录，保守认为「可能已有」，禁止叠挂
                return {}  # 空 dict（非哨兵），告诉上层"别新挂"但不触发 fail-closed
            # 无本地记录 + REST失败 = 冷启动 fail-closed
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

        def _do():
            self._throttle_rest(symbol)
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

        try:
            return self._with_trade_retry(
                symbol, "market", _do, reduce_only=bool(reduce_only),
            )
        except Exception as e:
            tag = "平仓" if reduce_only else "开仓"
            logger.error(f"[市价{tag}失败] {side} {qty} {symbol}: {e}")
            return None

    def place_limit_order(self, side, quantity, price, symbol="ETHUSDT",
                          reduce_only=True, client_order_id=None):
        """
        限价挂单。client_order_id → newClientOrderId（订单标签幂等）。
        查单失败 fail-closed；同价/同标签已存在则复用，禁止狂挂。
        """
        qty = self.format_quantity(quantity, symbol)
        px_str = self.format_price(price, symbol)
        if qty <= 0:
            logger.error(f"[限价单跳过] 数量无效 {quantity}")
            return None
        want_side = "BUY" if str(side).upper() in ("BUY", "LONG") else "SELL"
        want_px = round(float(px_str), 2)
        coid = str(client_order_id or "").strip()[:36] or None
        key = (symbol, want_side, want_px, coid or "")
        legacy_key = (symbol, want_side, want_px)  # 兼容旧缓存键

        def _cache_hit():
            with self._place_dedupe_lock:
                for k in (key, legacy_key):
                    cached = self._recent_limit_place.get(k)
                    if cached and (time.time() - float(cached[0])) < 120.0:
                        return cached[1]
            return None

        # 本地短窗：同标签/同价 120s 内已挂 → 复用
        hit = _cache_hit()
        if hit is not None:
            logger.warning(
                f"[限价单去重] {symbol} 本地120s内已挂 "
                f"{want_side}@{px_str} tag={coid or '-'} → 复用"
            )
            return hit
        # 合并查询：只调一次 get_open_orders，复用结果
        # - _existing_same_limit 用它判断是否有同价单
        # - 硬上限检查复用同一次结果统计 LIMIT 数量和 coid 匹配
        book = self.get_open_orders(symbol, include_algo=False)
        # _existing_same_limit 的逻辑内联，避免再调一次 REST
        exist = None
        if is_orders_query_failed(book):
            # 节流/REST失败：本地记录兜底，防止误报"无单"导致防御单叠挂
            ok_local, _ = self.defensive_orders_look_ok(symbol, max_age=60.0)
            if ok_local:
                exist = {}  # 空 dict（非哨兵），告诉上层"别新挂"但不 fail-closed
            # else: 维持 None = 冷启动 fail-closed
        else:
            want_side_check = "BUY" if str(side).upper() in ("BUY", "LONG") else "SELL"
            want_px_check = round(float(px_str), 2)
            for o in book or []:
                if str(o.get("type") or "").upper() != "LIMIT":
                    continue
                if str(o.get("side") or "").upper() != want_side_check:
                    continue
                try:
                    opx = round(float(o.get("price") or 0), 2)
                except (TypeError, ValueError):
                    continue
                if abs(opx - want_px_check) <= 0.02:
                    exist = o
                    break

        if exist is not None and is_orders_query_failed(exist):
            hit = _cache_hit()
            if hit is not None:
                logger.warning(
                    f"[限价单去重] {symbol} 查单失败但本地 120s 内已挂同价 "
                    f"@ {px_str} → 跳过叠单"
                )
                return hit
            # 节流/REST失败且无本地缓存：
            # 有 client_order_id → 依赖幂等性直接发单（防死锁）
            # 无 client_order_id → fail-closed
            if not coid:
                logger.error(
                    f"[限价单] {symbol} 查单失败且无本地记录/标签 → fail-closed 拒挂 "
                    f"{side} {qty} @ {px_str}（防盲补叠单）"
                )
                return None
            logger.warning(
                f"[限价单] {symbol} 查单失败但带幂等标签 {coid} → "
                f"直接发单（依赖newClientOrderId防重）"
            )
            # 跳过 exist 检查，直接发单（book 已拉过，不再重复）
            try:
                order = self.client.futures_create_order(
                    symbol=symbol,
                    side=want_side,
                    type="LIMIT",
                    timeInForce="GTC",
                    quantity=qty,
                    price=px_str,
                    reduceOnly=reduce_only,
                    newClientOrderId=coid,
                )
                logger.info(
                    f"[限价单成功] {side} {qty} @ {px_str} "
                    f"orderId={order.get('orderId', '')} tag={coid}"
                )
                with self._place_dedupe_lock:
                    self._recent_limit_place[key] = (time.time(), order)
                try:
                    self.invalidate_open_orders_cache(symbol)
                except Exception:
                    pass
                return order
            except Exception as e:
                logger.error(f"[限价单失败] {side} {qty} @ {px_str} tag={coid}: {e}")
                return None

        if exist and exist.get("orderId"):
            logger.warning(
                f"[限价单去重] {symbol} 已有同价 LIMIT "
                f"id={exist.get('orderId')} @ {px_str} → 跳过重复挂单"
            )
            return exist

        # 硬上限：复用已拉取的 book，不再单独调 REST
        if not is_orders_query_failed(book):
            if coid:
                for o in (book or []):
                    if not isinstance(o, dict):
                        continue
                    if str(o.get("clientOrderId") or "") == coid:
                        logger.warning(
                            f"[限价单去重] {symbol} 同标签已存在 "
                            f"clientOrderId={coid} id={o.get('orderId')} → 复用"
                        )
                        with self._place_dedupe_lock:
                            self._recent_limit_place[key] = (time.time(), o)
                        return o
            lim_n = sum(
                1 for o in (book or [])
                if str(o.get("type") or o.get("orderType") or "").upper() == "LIMIT"
            )
            all_n = len(book or [])
            # v15.9.1：硬上限 5（规格：未成交挂单总数不得超过 5）
            if all_n >= 5 or lim_n >= 5:
                logger.error(
                    f"[限价单熔断] {symbol} 挂单总数={all_n} LIMIT={lim_n}≥5 "
                    f"→ 禁止再挂（防击穿；请先净场）"
                )
                return None
        elif coid:
            logger.error(
                f"[限价单] {symbol} 查单失败且带标签 {coid} → fail-closed 拒挂"
            )
            return None
        if self.is_monitor_only(symbol):
            logger.error(f"[仅监控] 拒绝 limit {symbol}")
            return None

        def _do_limit():
            self._throttle_rest(symbol)
            params = {
                "symbol": symbol, "side": want_side, "type": "LIMIT",
                "timeInForce": "GTC", "quantity": qty, "price": px_str,
            }
            if reduce_only:
                params["reduceOnly"] = True
            if coid:
                params["newClientOrderId"] = coid
            order = self.client.futures_create_order(**params)
            logger.info(
                f"[限价单成功] {side} {qty} @ {px_str} "
                f"orderId={order.get('orderId', '')} tag={coid or '-'}"
            )
            with self._place_dedupe_lock:
                self._recent_limit_place[key] = (time.time(), order)
            try:
                self.invalidate_open_orders_cache(symbol)
            except Exception:
                pass
            return order

        try:
            return self._with_trade_retry(
                symbol, "limit", _do_limit, reduce_only=bool(reduce_only),
            )
        except Exception as e:
            logger.error(f"[限价单失败] {side} {qty} @ {px_str} tag={coid or '-'}: {e}")
            return None

    def place_algo_stop_market_order(self, side, stop_price, symbol="ETHUSDT",
                                     close_position=True, quantity=None,
                                     client_order_id=None):
        """Algo 通道 STOP_MARKET：优先 quantity+reduceOnly；否则 closePosition。"""
        binance_side = "BUY" if side.upper() in ["BUY", "LONG"] else "SELL"
        params = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": binance_side,
            "type": "STOP_MARKET",
            "triggerPrice": self.format_price(stop_price, symbol),
        }
        coid = str(client_order_id or "").strip()[:36] or None
        if coid:
            params["clientAlgoId"] = coid
        if quantity is not None:
            qty = self.format_quantity(quantity, symbol)
            if qty <= 0:
                logger.error(f"[Algo止损跳过] 数量无效 {quantity}")
                return None
            params["quantity"] = qty
            params["reduceOnly"] = "true"
        elif close_position:
            params["closePosition"] = "true"

        def _do():
            order = self._futures_signed_request("post", "algoOrder", params)
            tag = f"qty={quantity}" if quantity is not None else "closePosition"
            logger.info(
                f"[Algo止损成功] {side} {tag} Stop @ {stop_price} "
                f"algoId={order.get('algoId', '') if isinstance(order, dict) else '?'}"
            )
            if isinstance(order, dict):
                order.setdefault("isAlgoOrder", True)
            return order

        try:
            return self._with_trade_retry(
                symbol, "algo_stop", _do, reduce_only=True,
            )
        except Exception as e:
            logger.error(f"[Algo止损失败] {side} Stop @ {stop_price}: {e}")
            return None

    def place_stop_market_order(self, side, stop_price, symbol="ETHUSDT",
                                quantity=None, client_order_id=None):
        want_side = "BUY" if str(side).upper() in ("BUY", "LONG") else "SELL"
        want_px = round(float(stop_price or 0), 2)
        coid = str(client_order_id or "").strip()[:36] or None
        key = (symbol, want_side, want_px, coid or "")
        # 防重复：同触发价已有 STOP 则复用。
        # 仅 ORDERS_QUERY_FAILED 哨兵 → fail-closed（None=无同价，可挂）
        exist = self._existing_same_stop(symbol, side, stop_price)
        if exist is not None and is_orders_query_failed(exist):
            with self._place_dedupe_lock:
                cached = self._recent_stop_place.get(key)
                if cached is None:
                    cached = self._recent_stop_place.get((symbol, want_side, want_px))
                if cached and (time.time() - float(cached[0])) < 120.0:
                    logger.warning(
                        f"[止损单去重] {symbol} 查单失败但本地 120s 内已挂同价 "
                        f"Stop @ {stop_price} → 跳过叠单"
                    )
                    return cached[1]
            logger.error(
                f"[止损单] {symbol} 挂单查询失败 → fail-closed 禁止挂单 "
                f"{side} Stop @ {stop_price}（防盲补叠单）"
            )
            return None
        if exist:
            logger.warning(
                f"[止损单去重] {symbol} 已有同价 STOP "
                f"id={exist.get('orderId') or exist.get('algoId')} @ {stop_price} "
                f"→ 跳过重复挂单"
            )
            return exist
        if self.is_monitor_only(symbol):
            logger.error(f"[仅监控] 拒绝 stop {symbol}")
            return None

        def _do_stop():
            self._throttle_rest(symbol)
            params = {
                "symbol": symbol, "side": want_side, "type": "STOP_MARKET",
                "stopPrice": self.format_price(stop_price, symbol),
            }
            if coid:
                params["newClientOrderId"] = coid
            if quantity is not None:
                qty = self.format_quantity(quantity, symbol)
                if qty <= 0:
                    raise ValueError(f"invalid stop qty {quantity}")
                params["quantity"] = qty
                params["reduceOnly"] = True
            else:
                params["closePosition"] = "true"
            order = self.client.futures_create_order(**params)
            tag = f"{quantity} " if quantity is not None else "全仓 "
            logger.info(f"[止损单成功] {side} {tag}Stop @ {stop_price} tag={coid or '-'}")
            with self._place_dedupe_lock:
                self._recent_stop_place[key] = (time.time(), order)
            try:
                self.invalidate_open_orders_cache(symbol)
            except Exception:
                pass
            return order

        try:
            return self._with_trade_retry(
                symbol, "stop", _do_stop, reduce_only=True,
            )
        except Exception as e:
            if self._is_algo_switch_error(e):
                logger.info(
                    f"[止损单] 普通通道不可用({e}) → 切换 Algo @ {stop_price}"
                )
                order = self.place_algo_stop_market_order(
                    side, stop_price, symbol=symbol,
                    close_position=(quantity is None),
                    quantity=quantity,
                    client_order_id=coid,
                )
                if order:
                    with self._place_dedupe_lock:
                        self._recent_stop_place[key] = (time.time(), order)
                return order
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

        def _do():
            res = self._futures_signed_request(
                "delete", "algoOrder", {"symbol": symbol, "algoId": int(algo_id)},
            )
            self._mark_local_cancel(symbol, algo_id)
            logger.info(f"[Algo撤单成功] {symbol} algoId={algo_id}")
            return res

        try:
            return self._with_trade_retry(
                symbol, "cancel_algo", _do, reduce_only=True,
            )
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

        def _do():
            self._throttle_rest(symbol)
            res = self.client.futures_cancel_order(symbol=symbol, orderId=order_id)
            self._mark_local_cancel(symbol, order_id)
            logger.info(f"[撤单成功] {symbol} orderId={order_id}")
            return res

        try:
            return self._with_trade_retry(
                symbol, "cancel", _do, reduce_only=True,
            )
        except Exception as e:
            err = str(e)
            if "-2011" in err or "Unknown order" in err or "Order does not exist" in err:
                return self.cancel_algo_order(symbol, order_id)
            logger.error(f"[撤单失败] {symbol} orderId={order_id}: {e}")
            return None

    def cancel_all_open_orders(self, symbol="ETHUSDT"):
        if self.is_monitor_only(symbol):
            logger.error(f"[仅监控] 拒绝 cancel_all {symbol}")
            return

        def _do_plain():
            self._throttle_rest(symbol)
            self.client.futures_cancel_all_open_orders(symbol=symbol)
            logger.info(f"[撤单成功] {symbol} 全部普通挂单已撤销")
            return True

        def _do_algo():
            self._futures_signed_request(
                "delete", "algoOpenOrders", {"symbol": symbol},
            )
            logger.info(f"[撤单成功] {symbol} 全部 Algo 条件单已撤销")
            return True

        try:
            self._with_trade_retry(symbol, "cancel_all", _do_plain, reduce_only=True)
        except Exception as e:
            logger.error(f"[撤单失败] {symbol} 普通挂单: {e}")
        try:
            self._with_trade_retry(symbol, "cancel_all_algo", _do_algo, reduce_only=True)
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
        try:
            self._throttle_rest(symbol, kind="rest_probe")
            return self.client.futures_klines(
                symbol=symbol, interval=interval, limit=int(limit or 220),
            )
        except IpRateLimitedError:
            logger.warning(f"[K线] {symbol}: 节流/冷却 → 跳过 REST")
            return []
        except Exception as e:
            self._note_api_error(e, symbol)
            logger.warning(f"[K线] {symbol}: {e}")
            return []

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


# 单测可设 BINANCE_SKIP_BOOTSTRAP=1，避免本机构造 Client 卡在网络 ping
if str(os.getenv("BINANCE_SKIP_BOOTSTRAP", "")).strip() in ("1", "true", "TRUE"):
    binance_client = None  # type: ignore
else:
    binance_client = BinanceClient()

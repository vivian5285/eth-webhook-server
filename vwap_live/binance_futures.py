#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""币安 USDT-M 永续签名下单 client——只做这个项目要用到的几个端点，不是
通用 SDK。刻意保持最小面积：市价开仓 + 市价平仓 + 止损单(STOP_MARKET，
下单后立刻挂到交易所，进程挂了止损照样在)。

⚠️ 每一个会动钱/仓位的方法都先检查 cfg.is_armed——两道闸门(key 存在 +
LIVE_TRADING=true)任一没打开，直接抛 NotArmedError，不发任何签名请求。
这样即使调用方哪里漏了判断，这一层也兜底不会误下单。

⚠️ 2026-09-12：如果这个 key 是从某个已经在跑的真实账户(比如 binanceC)
复制来的，那个账户很可能开着"双向持仓模式"(Hedge Mode——本仓库同一批
真实账户历史上就修过 Hedge Mode 相关的坑，见 project_mario_account_
config_20260822)。双向模式下单必须带 positionSide，而且**不能**同时带
reduceOnly(会被交易所拒单)；单向模式相反，只能用 reduceOnly、不能带
positionSide。两种模式的下单参数完全不同，写死一种会在另一种账户设置下
100%被拒单——所以这里启动时先查一次真实持仓模式(get_position_mode，
只读端点，不需要 LIVE_TRADING，只要给了 key 就查)，缓存下来，后面所有
下单调用都按查到的模式组装参数，不猜。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "vwap-live/1.0"}
_filters_cache: dict = {}
_hedge_mode_cache: Optional[bool] = None
_isolated_done: set = set()


class NotArmedError(Exception):
    """两道真金闸门没同时打开时，任何下单/撤单调用都会抛这个——调用方
    不应该 catch 它当成"下单失败重试"，这是刻意的硬停。"""


def _sign(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def _request(cfg, method: str, path: str, params: dict, signed: bool, timeout=15):
    p = dict(params or {})
    if signed:
        p["timestamp"] = int(time.time() * 1000)
        p["recvWindow"] = 5000
    query = urllib.parse.urlencode(p, doseq=True)
    if signed:
        query += f"&signature={_sign(cfg.binance_api_secret, query)}"
    url = f"{cfg.binance_base_url}{path}"
    headers = dict(_UA)
    if signed:
        headers["X-MBX-APIKEY"] = cfg.binance_api_key
    if method == "GET":
        full = f"{url}?{query}" if query else url
        req = urllib.request.Request(full, headers=headers, method="GET")
    else:
        req = urllib.request.Request(f"{url}?{query}" if query else url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        e.body = body  # HTTPError 的响应体只能读一次，存成属性给调用方复用，别再 e.read()
        logger.error("binance %s %s -> HTTP %s: %s", method, path, e.code, body[:300])
        raise
    except Exception as e:
        logger.error("binance %s %s -> %s", method, path, e)
        raise


def _require_armed(cfg):
    if not cfg.is_armed:
        raise NotArmedError(
            f"未武装(api_key_present={cfg.api_key_present}, live_trading={cfg.live_trading})，拒绝下单/撤单")


def get_position_mode(cfg) -> bool:
    """返回 True=双向持仓(Hedge Mode) / False=单向持仓(One-way)。只读
    端点，不需要 LIVE_TRADING，只要有 key 就查——下单前必须先知道这个，
    观察模式下也该在日志里报出来，让人在武装前就能看到这个账户是哪种
    模式。带进程内缓存(模式在运行中途基本不会变，变了也需要重启服务
    生效，不做运行中动态刷新)。"""
    global _hedge_mode_cache
    if _hedge_mode_cache is not None:
        return _hedge_mode_cache
    if not cfg.api_key_present:
        return False
    r = _request(cfg, "GET", "/fapi/v1/positionSide/dual", {}, signed=True)
    _hedge_mode_cache = bool(r.get("dualSidePosition"))
    logger.info("持仓模式查询结果: %s", "双向(Hedge Mode)" if _hedge_mode_cache else "单向(One-way)")
    return _hedge_mode_cache


def get_symbol_filters(cfg, symbol: str) -> dict:
    """LOT_SIZE(stepSize/minQty) + MIN_NOTIONAL + PRICE_FILTER(tickSize)。
    公开端点，不需要武装也能查(下单前必须先知道精度，观察模式也用得到)。"""
    if symbol in _filters_cache:
        return _filters_cache[symbol]
    info = _request(cfg, "GET", "/fapi/v1/exchangeInfo", {}, signed=False)
    for s in info.get("symbols", []):
        if s.get("symbol") == symbol:
            f = {"stepSize": 0.001, "minQty": 0.0, "minNotional": 5.0, "tickSize": 0.01}
            for flt in s.get("filters", []):
                t = flt.get("filterType")
                if t == "LOT_SIZE":
                    f["stepSize"] = float(flt["stepSize"])
                    f["minQty"] = float(flt["minQty"])
                elif t == "MIN_NOTIONAL":
                    f["minNotional"] = float(flt.get("notional", flt.get("minNotional", 5.0)))
                elif t == "PRICE_FILTER":
                    f["tickSize"] = float(flt["tickSize"])
            _filters_cache[symbol] = f
            return f
    raise ValueError(f"未在 exchangeInfo 找到品种 {symbol}")


def _round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    n = round(value / step)
    return float(f"{n * step:.10f}".rstrip("0").rstrip("."))


def quantity_for_usd(cfg, symbol: str, price: float, usd: float) -> float:
    f = get_symbol_filters(cfg, symbol)
    raw_qty = (usd * cfg.leverage) / price
    qty = _round_step(raw_qty, f["stepSize"])
    return max(qty, 0.0)


def get_balance_usdt(cfg) -> Optional[float]:
    """账户 USDT 余额——只读，只要有 key 就能查，不需要 LIVE_TRADING(算
    仓位大小要用实时权益，观察模式下预览"如果现在开会是多大"也用得到，
    不该被那道闸门卡住)。"""
    if not cfg.api_key_present:
        return None
    rows = _request(cfg, "GET", "/fapi/v2/balance", {}, signed=True)
    for r in rows or []:
        if r.get("asset") == "USDT":
            return float(r.get("availableBalance") or 0.0)
    return None


def get_total_notional(cfg) -> float:
    """账户**当前全部持仓**(不限于 vwap_live 自己开的，只要这个账户上有
    仓位就算)的名义价值绝对值总和——组合层面总敞口校验用。只读，不需要
    LIVE_TRADING。查询不带 symbol 参数=返回账户全部品种。"""
    if not cfg.api_key_present:
        return 0.0
    rows = _request(cfg, "GET", "/fapi/v2/positionRisk", {}, signed=True)
    total = 0.0
    for r in rows or []:
        amt = float(r.get("positionAmt") or 0.0)
        if abs(amt) <= 0:
            continue
        notional = r.get("notional")
        if notional is not None:
            total += abs(float(notional))
        else:
            total += abs(amt) * float(r.get("markPrice") or r.get("entryPrice") or 0.0)
    return total


def get_position(cfg, symbol: str) -> Optional[dict]:
    """真实持仓查询——启动时/每轮对账用，不需要 live_trading，只要有 key
    就能查(观察模式下也该知道这个账户上是否已经有仓，避免误判)。双向
    模式下同一品种理论上能同时有 LONG+SHORT 两条腿，这里只取第一条非零
    的——vwap_live 本身只做单向单腿，多腿场景留给人工核实。"""
    if not cfg.api_key_present:
        return None
    rows = _request(cfg, "GET", "/fapi/v2/positionRisk", {"symbol": symbol}, signed=True)
    for r in rows or []:
        amt = float(r.get("positionAmt") or 0.0)
        if abs(amt) > 0:
            return {"symbol": symbol, "side": "LONG" if amt > 0 else "SHORT",
                    "qty": abs(amt), "entry_price": float(r.get("entryPrice") or 0.0)}
    return None


def set_leverage(cfg, symbol: str) -> None:
    _require_armed(cfg)
    _request(cfg, "POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(cfg.leverage)}, signed=True)


def ensure_isolated_margin(cfg, symbol: str) -> None:
    """把这个品种的保证金模式设成 ISOLATED（逐仓）——**这是跟"复用现有
    真实账户"这个决定配套的关键隔离层**：币安的保证金模式是按品种设置
    的，不是按"哪个程序下的单"分的。如果这个品种是全仓(Cross)，vwap_
    live 自己这一笔的浮亏会占用同一账户里其它仓位(包括账户本身雷达
    引擎在别的品种上的仓位)共享的保证金池，反过来也一样——极端情况下
    一边爆仓的压力会传导给另一边。逐仓把每笔仓位的风险锁死在自己这一
    份保证金里，不管这个账户上还同时跑着别的什么程序。只在还没设过
    (且没有持仓/挂单，币安规定持仓期间不能切换)的品种上调用一次，带
    进程内缓存不重复调用；已经是 ISOLATED 的话币安会返回 -4046，当作
    正常情况忽略。"""
    _require_armed(cfg)
    if symbol in _isolated_done:
        return
    try:
        _request(cfg, "POST", "/fapi/v1/marginType", {"symbol": symbol, "marginType": "ISOLATED"}, signed=True)
    except urllib.error.HTTPError as e:
        if "-4046" not in getattr(e, "body", ""):  # -4046="No need to change margin type"，已是逐仓，不是错误
            raise
    _isolated_done.add(symbol)


def open_market(cfg, symbol: str, position_side: str, qty: float) -> dict:
    """开仓：position_side 是要开的仓位方向(LONG/SHORT)。双向模式必须带
    positionSide、不能带 reduceOnly；单向模式相反。"""
    _require_armed(cfg)
    params = {"symbol": symbol, "side": "BUY" if position_side == "LONG" else "SELL",
              "type": "MARKET", "quantity": qty}
    if get_position_mode(cfg):
        params["positionSide"] = position_side
    return _request(cfg, "POST", "/fapi/v1/order", params, signed=True)


def close_market(cfg, symbol: str, position_side: str, qty: float) -> dict:
    """平仓：position_side 是**现有持仓**的方向(不是下单方向)。挂单方向
    永远跟持仓方向相反——平多用 SELL，平空用 BUY。"""
    _require_armed(cfg)
    params = {"symbol": symbol, "side": "SELL" if position_side == "LONG" else "BUY",
              "type": "MARKET", "quantity": qty}
    if get_position_mode(cfg):
        params["positionSide"] = position_side
    else:
        params["reduceOnly"] = "true"
    return _request(cfg, "POST", "/fapi/v1/order", params, signed=True)


def _is_algo_switch_error(e: Exception) -> bool:
    """2026-09-12 实盘发现：这个账户(binanceC)的条件单(含 closePosition
    硬止损)已经被币安切到独立的 Algo 通道，走 /fapi/v1/order 下
    STOP_MARKET 会被拒(-4120 "Order type not supported for this endpoint.
    Please use the Algo Order API endpoints instead.")。跟生产 TV 引擎
    (binance_client.py::_is_algo_switch_error)遇到的是同一个账户级迁移，
    判断逻辑照抄那边、已验证有效。"""
    body = getattr(e, "body", "") or str(e)
    return "-4120" in body or "STOP_ORDER_SWITCH_ALGO" in body


def place_algo_stop_market(cfg, symbol: str, position_side: str, stop_price: float) -> dict:
    """Algo 通道止损单——2026-09-12 起这是这个账户唯一能成功的止损下单
    路径。参数跟普通 STOP_MARKET 端点不同：触发价字段是 triggerPrice
    (不是 stopPrice)，且必须带 algoType=CONDITIONAL；照抄生产 TV 引擎
    binance_client.py::place_algo_stop_market_order 已验证的参数组合。"""
    _require_armed(cfg)
    f = get_symbol_filters(cfg, symbol)
    sp = _round_step(stop_price, f["tickSize"])
    params = {"algoType": "CONDITIONAL", "symbol": symbol,
              "side": "SELL" if position_side == "LONG" else "BUY",
              "type": "STOP_MARKET", "triggerPrice": sp, "closePosition": "true"}
    if get_position_mode(cfg):
        params["positionSide"] = position_side
    r = _request(cfg, "POST", "/fapi/v1/algoOrder", params, signed=True)
    r["is_algo_order"] = True
    return r


def place_stop_market(cfg, symbol: str, position_side: str, stop_price: float) -> dict:
    """止损单：position_side 是现有持仓方向。用 closePosition=true(按交易
    所此刻实际持仓全平，不依赖我们自己算的 qty 是否精确匹配)。双向模式
    下 closePosition + positionSide 是币安文档里明确支持的组合；单向模式
    不带 positionSide。两种模式都不需要额外带 reduceOnly(closePosition
    本身已经隐含只减仓)。

    先试普通端点，遇到 -4120(这个账户的条件单已切到 Algo 通道)自动降级
    走 place_algo_stop_market 重试一次——跟生产 TV 引擎的降级逻辑一致。"""
    _require_armed(cfg)
    f = get_symbol_filters(cfg, symbol)
    sp = _round_step(stop_price, f["tickSize"])
    params = {"symbol": symbol, "side": "SELL" if position_side == "LONG" else "BUY",
              "type": "STOP_MARKET", "stopPrice": sp, "closePosition": "true"}
    if get_position_mode(cfg):
        params["positionSide"] = position_side
    try:
        return _request(cfg, "POST", "/fapi/v1/order", params, signed=True)
    except Exception as e:
        if _is_algo_switch_error(e):
            logger.info("%s 普通止损通道不可用(-4120)，切换 Algo 通道重试", symbol)
            return place_algo_stop_market(cfg, symbol, position_side, stop_price)
        raise


def place_algo_take_profit_market(cfg, symbol: str, position_side: str, target_price: float) -> dict:
    """Algo 通道止盈单——止盈的触发方向必须在"有利"一侧(多单目标价必须
    高于现价、空单目标价必须低于现价)，跟止损方向相反，币安会校验，
    方向反了直接拒单，不需要我们自己再判断一次。参数结构、降级逻辑
    跟 place_algo_stop_market 完全对称。"""
    _require_armed(cfg)
    f = get_symbol_filters(cfg, symbol)
    tp = _round_step(target_price, f["tickSize"])
    params = {"algoType": "CONDITIONAL", "symbol": symbol,
              "side": "SELL" if position_side == "LONG" else "BUY",
              "type": "TAKE_PROFIT_MARKET", "triggerPrice": tp, "closePosition": "true"}
    if get_position_mode(cfg):
        params["positionSide"] = position_side
    r = _request(cfg, "POST", "/fapi/v1/algoOrder", params, signed=True)
    r["is_algo_order"] = True
    return r


def place_take_profit_market(cfg, symbol: str, position_side: str, target_price: float) -> dict:
    """止盈单——2026-09-12 新增，跟止损对称的交易所侧安全网：正常情况下
    均值回归的离场判断(价格回归VWAP±exit_band)由主循环每轮动态算，比
    这里的固定触发价更准(VWAP 会随时间移动)；这张单只是万一进程/VPS
    整个挂掉时的兜底，不追求跟主循环逻辑完全等价，只保证"就算没人
    盯着，回到目标价附近也会自动落袋"。同样先试普通端点、遇 -4120
    降级 Algo 通道。"""
    _require_armed(cfg)
    f = get_symbol_filters(cfg, symbol)
    tp = _round_step(target_price, f["tickSize"])
    params = {"symbol": symbol, "side": "SELL" if position_side == "LONG" else "BUY",
              "type": "TAKE_PROFIT_MARKET", "stopPrice": tp, "closePosition": "true"}
    if get_position_mode(cfg):
        params["positionSide"] = position_side
    try:
        return _request(cfg, "POST", "/fapi/v1/order", params, signed=True)
    except Exception as e:
        if _is_algo_switch_error(e):
            logger.info("%s 普通止盈通道不可用(-4120)，切换 Algo 通道重试", symbol)
            return place_algo_take_profit_market(cfg, symbol, position_side, target_price)
        raise


def get_open_algo_orders(cfg, symbol: str) -> list:
    _require_armed(cfg)
    r = _request(cfg, "GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol}, signed=True)
    return r if isinstance(r, list) else (r.get("orders") or [])


def cancel_algo_order(cfg, symbol: str, algo_id) -> dict:
    _require_armed(cfg)
    return _request(cfg, "DELETE", "/fapi/v1/algoOrder", {"symbol": symbol, "algoId": int(algo_id)}, signed=True)


def cancel_all_open_orders(cfg, symbol: str) -> None:
    """撤销该品种全部挂单——既要撤普通挂单(/fapi/v1/allOpenOrders 一把
    清空)，也要撤 Algo 通道的条件单(2026-09-12起止损单大多走这条通道，
    不撤的话平仓后会留一个指向空仓位的僵尸止损单，下次开仓前触发
    -4130/-4509 之类噪音)。Algo 挂单没有批量撤销端点，逐个撤。"""
    _require_armed(cfg)
    _request(cfg, "DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol}, signed=True)
    try:
        for o in get_open_algo_orders(cfg, symbol):
            algo_id = o.get("algoId") or o.get("orderId")
            if algo_id:
                cancel_algo_order(cfg, symbol, algo_id)
    except Exception as e:
        logger.warning("%s 撤销 Algo 挂单失败(可能本来就没有): %s", symbol, e)

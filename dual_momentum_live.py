#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dual_momentum 实盘执行引擎 - 2026-09-22

宝贝拍板：擂台系统(187.53.133.188:8878)的dual_momentum策略跑了412笔
纸面交易(22天窗口)，胜率56.3%/收益+39.1%，前后半段对比确认edge没有
衰减——拿去B(妈妈)/E(MARIO客户)两个账户实盘验证，品种收窄到已验证
有正贡献的子集(ENA/ZEC/UNI/HYPE/SNDK/BCH核心6个 + SOL/DOGE/1000PEPE
观察品种)。

架构：完全独立于position_supervisor_binance.py的TV信号pipeline——不走
webhook/active_binance_symbols()白名单，直连binance_client(client-层)
下单+挂止损止盈，自己维护一份精简本地状态，不会被雷达/哨兵/综合硬
止损的任何维护逻辑碰到。这么设计是因为B/E两账户当前都是
SMART_HARD_STOP_ENABLED=1(综合硬止损模式)，会完全无视payload自带的
stop_loss/atr、叠加雷达自己的跟涨/保本逻辑，让实盘行为偏离已经验证
过412笔的回测逻辑(固定2.5×ATR止损+纯排名/动量反转离场，不含雷达)。
完全隔离才能保证"验证的是什么，实盘跑的就是什么"。

仓位公式直接复用webhook_parser.compute_fixed_order_qty(principal×0.20×
5×tier权重)，tier权重用DUAL_MOMENTUM_TIER_WEIGHT(见下方常量顶部注释
——2026-09-23宝贝拍板"适当提高"，从B系统当前tier=1真实值0.1225调到
0.175，不是照抄擂台回测用的0.245，取的是B系统tier=2/强档现成的
权重，单笔约占账户总权益17.5%名义仓位)。

只读账户余额/持仓，只对TRADED_SYMBOLS这个白名单动手，绝不碰白名单外
任何品种——跟这次会话反复验证过的"非本引擎开的仓位绝不自动接管"是
同一条铁律(见_reconcile_on_start)。

跑法：
  常驻:  venv/bin/python dual_momentum_live.py
  单轮:  venv/bin/python dual_momentum_live.py --once   (验证用，跑一轮立刻退出)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any, Dict, Optional

from binance_client import binance_client
from webhook_parser import (
    compute_fixed_order_qty,
    FIXED_RISK_PCT,
    FIXED_LEVERAGE,
)
from strategy_engine.klines import get_bars
from strategy_engine.strategies.dual_momentum import generate_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] DualMomentum: %(message)s",
)
logger = logging.getLogger(__name__)

# ==================== 品种配置 ====================
# 2026-09-22：跟strategy_engine/comparison_roster.py::_ALL_SYMBOLS(擂台
# dual_momentum实际跑的完整篮子)手动同步的一份快照——故意不直接import
# 那个私有变量，避免擂台系统未来任何改动静默改变这个实盘引擎的排名
# 基准。真实钱要的是"验证的是什么，实盘跑的就是什么"，不要隐式耦合。
# 如果以后要跟着擂台篮子调整，手动同步改这里，不要改成动态import。
RANKING_UNIVERSE = [
    "ETHUSDT", "BNBUSDT", "ZECUSDT", "BCHUSDT", "XMRUSDT", "XRPUSDT",
    "SOLUSDT", "LINKUSDT", "UNIUSDT", "BTCUSDT", "XLMUSDT", "DOGEUSDT",
    "1000PEPEUSDT", "HYPEUSDT", "ENAUSDT", "XAUUSDT", "PAXGUSDT",
    "SNDKUSDT", "OPENAIUSDT", "ANTHROPICUSDT", "GSUSDT", "MUUSDT",
    "LITEUSDT", "TSLAUSDT", "METAUSDT", "SKHYNIXUSDT", "ASMLUSDT",
]

# 2026-09-22：宝贝拍板实盘只交易这个子集(擂台数据验证过有正贡献的
# 加密原生高波动品种)——核心6个 + 3个观察品种，其余18个品种只参与
# 排名计算，永远不会真实开仓。这几个品种大多不在symbol_config.py的
# BINANCE_SYMBOL_META登记表里——不需要，本引擎完全绕开那套注册机制，
# format_quantity/format_price直接查交易所exchangeInfo算精度。
TRADED_SYMBOLS = [
    "ENAUSDT", "ZECUSDT", "UNIUSDT", "HYPEUSDT", "SNDKUSDT", "BCHUSDT",
    "SOLUSDT", "DOGEUSDT", "1000PEPEUSDT",
]

TIMEFRAME = "4h"
LOOKBACK_BARS = 20
DUAL_MOMENTUM_TIER = 1  # dual_momentum.py::generate_signal固定"tier":1(中)
LEG_RATIOS = (0.10, 0.20, 0.70)  # TP1/TP2/TP3分批比例，本仓库既有惯例
EXCHANGE_LEVERAGE = 5  # 真实交易所杠杆，跟FIXED_LEVERAGE(仓位公式里的杠杆假设)对齐

# 2026-09-23：宝贝核实后发现——擂台自己纸面验证那418笔(+39.85%)用的
# 是strategy_engine/position_sizing.py::TIER_NOTIONAL_MULT
# ={0:0.14,1:0.245,2:0.35}，比webhook_parser.py当前真实值
# {0:0.07,1:0.1225,2:0.175}整整大2倍(那份sizing.py是2026-08-29抄的，
# 之后B系统自己的tier权重经过几轮下调，擂台那份从没跟着改)——2026-
# 09-22首次部署直接借用了B系统"当前真实值"(0.1225)，仓位只有回测
# 验证过的一半。宝贝拍板"适当提高"，不是照抄擂台的0.245(账户已经
# 占用72%左右，翻倍会在多品种同时触发时不够保证金)，也不是继续用
# 保守的0.1225——选0.175：币安B系统tier表里本来就有的"强档"权重(不是
# 凭空发明的新数字)，介于两者中间，理由是"品种已经收窄到擂台数据里
# 验证过有正贡献的子集，比原始更宽泛的27品种/更保守的tier=1权重更值
# 得给一点confidence"。只影响新开的仓位，已经开的12笔(B账户6笔+E
# 账户6笔)不做回溯调整。
DUAL_MOMENTUM_TIER_WEIGHT = 0.175

TICK_INTERVAL_SEC = 300  # 5分钟一轮，跟擂台系统自己的tick间隔一致
KLINES_LIMIT = LOOKBACK_BARS + 5  # 排名只需要lookback_bars+1根，多拉几根兜底

STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dual_momentum_live_state.json"
)


# ==================== 状态持久化 ====================

def _load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"状态文件读取失败，视为空状态启动: {e}")
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


# ==================== 排名计算 ====================

def _compute_universe_returns() -> Dict[str, float]:
    """跟strategy_engine/multi_strategy_runner.py::_compute_universe_returns
    同一套公式：close[-1]/close[-1-lookback_bars]-1，逐品种独立拉K线，
    单个品种拉取失败只跳过它自己，不整体失败(篮子少一两个品种排名依然
    有意义，总比整轮不算强)。"""
    out: Dict[str, float] = {}
    for sym in RANKING_UNIVERSE:
        try:
            bars = get_bars(sym, TIMEFRAME, limit=KLINES_LIMIT)
        except Exception as e:
            logger.warning(f"[{sym}] 拉K线失败，跳过本轮排名: {e}")
            continue
        if not bars or len(bars) < LOOKBACK_BARS + 1:
            continue
        c_now = float(bars[-1]["c"])
        c_then = float(bars[-1 - LOOKBACK_BARS]["c"])
        if c_then > 0:
            out[sym] = c_now / c_then - 1.0
    return out


# ==================== 下单相关 ====================

def _min_notional(symbol: str) -> float:
    """format_quantity只管LOT_SIZE，不check MIN_NOTIONAL——低价品种
    (DOGE/1000PEPE)小仓位容易踩到这条线，这里单独查一遍交易所过滤器。"""
    info = binance_client._load_symbol_filters(symbol)
    for f in info.get("filters", []):
        if f.get("filterType") in ("MIN_NOTIONAL", "NOTIONAL"):
            val = f.get("notional") or f.get("minNotional")
            if val:
                return float(val)
    return 0.0


def _calc_qty(symbol: str, price: float, stop_loss: float) -> float:
    """按币安B系统自己的下单权重公式(compute_fixed_order_qty)算——
    principal用get_total_equity(总权益含浮盈，不是availableBalance，
    跟本仓库既定用法一致)，leverage参数里直接把dual_momentum固定的
    tier=1权重(0.1225)乘进去，一次调用得到最终qty，不用额外二次tier
    缩放。"""
    equity = binance_client.get_total_equity("USDT")
    if equity <= 0:
        logger.error(f"[{symbol}] 权益查询失败或为0，跳过本次开仓")
        return 0.0
    effective_leverage = FIXED_LEVERAGE * DUAL_MOMENTUM_TIER_WEIGHT
    qty_raw, meta = compute_fixed_order_qty(
        principal=equity, price=price,
        margin_pct=FIXED_RISK_PCT, leverage=effective_leverage,
        stop_loss=stop_loss,
    )
    qty = binance_client.format_quantity(qty_raw, symbol)
    logger.info(
        f"[{symbol}] 仓位计算 equity={equity:.2f} qty={qty} "
        f"notional≈{qty * price:.2f} binding={meta.get('binding')}"
    )
    if qty <= 0:
        return 0.0
    min_notional = _min_notional(symbol)
    if min_notional and qty * price < min_notional:
        logger.warning(
            f"[{symbol}] 算出notional={qty * price:.2f} 小于交易所"
            f"MIN_NOTIONAL={min_notional}，放弃这次开仓"
        )
        return 0.0
    return qty


def _place_stop(symbol: str, close_side: str, stop_price: float) -> Optional[str]:
    order = binance_client.place_stop_market_order(
        close_side, stop_price, symbol=symbol, quantity=None,
        client_order_id=f"DMLsl{int(time.time()) % 100000000}",
    )
    if not order:
        return None
    return str(order.get("orderId") or order.get("algoId") or "") or None


def _open_position(symbol: str, signal: Dict[str, Any], state: Dict[str, Any]) -> None:
    side = signal["action"]  # LONG / SHORT
    price = float(signal["price"])
    atr = float(signal["atr"])
    stop_loss = float(signal["stop_loss"])

    qty = _calc_qty(symbol, price, stop_loss)
    if qty <= 0:
        return

    lev_result = binance_client.set_leverage(symbol, leverage=EXCHANGE_LEVERAGE)
    if lev_result is None:
        logger.error(f"[{symbol}] 设置杠杆失败，放弃这次开仓(避免用未知杠杆下单)")
        return

    order = binance_client.place_market_order(side, qty, symbol=symbol, reduce_only=False)
    if not order:
        logger.error(f"[{symbol}] 市价开仓失败: {signal}")
        return

    # 2026-09-22实盘复现两次(SNDK/BCH，B账户+E账户各中一次)：
    # signal["price"]是上一根已收盘4h K线的收盘价，不是这一刻的真实
    # 成交价——这几个品种从信号算出来到市价单真正成交这几秒之间，真实
    # 价格已经涨了6~20%(tokenized股票代币本身波动就大)，用陈旧价算出
    # 来的止损/止盈直接被交易所拒("-2021 立即触发")。第一次修复时想当
    # 然地读order.get("avgPrice")，结果发现币安市价单的REST同步响应
    # 里avgPrice经常是"0"(真实成交均价要过一小会儿才会在订单状态里
    # 反映出来，是币安期货API的已知行为，不是本仓库的问题)——那次修复
    # 完全没生效，等于白改。改成下单成功后直接查一次真实持仓
    # (futures_position_information的entryPrice)，带短暂重试，这才是
    # 真正拿得到成交价的地方。查不到时才退回signal的原始价，不整体
    # 失败。
    fill_price = 0.0
    for _ in range(5):
        try:
            positions = binance_client.client.futures_position_information(symbol=symbol)
            for p in positions:
                fill_price = abs(float(p.get("entryPrice") or 0))
        except Exception:
            fill_price = 0.0
        if fill_price > 0:
            break
        time.sleep(1.0)
    if fill_price <= 0:
        logger.warning(f"[{symbol}] 查真实成交价失败，退回用信号价(可能不精确)")
        fill_price = price
    direction = 1.0 if side == "LONG" else -1.0
    if abs(fill_price - price) / price > 0.001:
        logger.warning(
            f"[{symbol}] 成交价{fill_price}偏离信号价{price}"
            f"({(fill_price / price - 1) * 100:+.2f}%)，止损/止盈按真实成交价重锚"
        )
    stop_loss = round(fill_price - direction * atr * 2.5, 8)
    tp_prices = [
        round(fill_price + direction * atr * mult, 8) for mult in (1.2, 2.2, 3.5)
    ] if signal.get("tp1") else [None, None, None]

    # 成交后立刻落盘(entry_pending_sl)，防止下面挂止损止盈中途崩溃时
    # 变成"没有本地记录的裸仓"——重启核对时会因为"本地无记录+交易所
    # 有仓位"报错跳过、绝不当成真裸仓自动接管，见_reconcile_on_start。
    state[symbol] = {
        "side": side,
        "entry_price": fill_price,
        "qty": qty,
        "atr_at_entry": atr,
        "stop_loss": stop_loss,
        "sl_order_id": None,
        "tp_prices": tp_prices,
        "tp_order_ids": [None, None, None],
        "last_acted_bar_time": signal.get("bar_time"),
        "status": "entry_pending_sl",
    }
    _save_state(state)
    logger.info(f"🚀 [{symbol}] 开仓成交 {side} qty={qty} @{fill_price} | {signal.get('reason')}")

    close_side = "SHORT" if side == "LONG" else "LONG"
    # place_stop_market_order内部自己会把LONG/SHORT转成BUY/SELL，但下面
    # 直接调futures_create_order是裸API调用，必须自己先转成BUY/SELL——
    # 2026-09-22实盘复现：ENA/UNI两笔的TP挂单最初直接传了"SHORT"/"LONG"
    # 进去，交易所返回-1117 Invalid side，止损挂上了但止盈完全没挂上。
    close_side_api = "SELL" if side == "LONG" else "BUY"

    sl_id = _place_stop(symbol, close_side, stop_loss)
    if sl_id:
        state[symbol]["sl_order_id"] = sl_id
        state[symbol]["status"] = "open"
        _save_state(state)
        logger.info(f"🛡️ [{symbol}] 止损已挂 @{stop_loss}")
    else:
        logger.error(
            f"🚨 [{symbol}] 止损挂单失败！仓位当前无保护，需要人工立刻核查"
            f"并补挂 stop@{stop_loss}"
        )

    tp_ids = []
    for i, (ratio, tp_px) in enumerate(zip(LEG_RATIOS, state[symbol]["tp_prices"])):
        if not tp_px:
            tp_ids.append(None)
            continue
        leg_qty = binance_client.format_quantity(qty * ratio, symbol)
        if leg_qty <= 0:
            tp_ids.append(None)
            continue
        try:
            tp_order = binance_client.client.futures_create_order(
                symbol=symbol, side=close_side_api, type="TAKE_PROFIT_MARKET",
                stopPrice=binance_client.format_price(float(tp_px), symbol),
                quantity=leg_qty, reduceOnly=True, workingType="CONTRACT_PRICE",
            )
            tp_ids.append(str(tp_order.get("orderId") or "") or None)
            logger.info(f"🎯 [{symbol}] TP{i + 1}已挂 @{tp_px} qty={leg_qty}")
        except Exception as e:
            logger.error(f"[{symbol}] TP{i + 1}挂单失败 @{tp_px}: {e}")
            tp_ids.append(None)
    state[symbol]["tp_order_ids"] = tp_ids
    _save_state(state)


def _close_position(symbol: str, signal: Dict[str, Any], state: Dict[str, Any]) -> None:
    rec = state.get(symbol) or {}
    state[symbol] = {**rec, "status": "closing"}
    _save_state(state)

    # 1) 先撤掉已知的止损/止盈挂单(容忍"订单不存在")。2026-09-22实盘
    # 核实：STOP_MARKET(closePosition=true)和TAKE_PROFIT_MARKET(哪怕
    # 显式quantity+reduceOnly)在这个交易所/API版本下**都**被算成"算法
    # 订单"(futures_get_open_orders原生端点根本查不到，标准
    # futures_cancel_order直接返回-2011 Unknown order)——止损这条坑
    # 2026-09-20在MU上踩过一次，这次连止盈也一起踩了，直接实测确认后
    # 两者统一用cancel_algo_order，不再区分。
    all_oids = [rec.get("sl_order_id")] + list(rec.get("tp_order_ids") or [])
    for oid in all_oids:
        if not oid:
            continue
        try:
            binance_client.cancel_algo_order(symbol=symbol, algo_id=int(oid))
        except Exception:
            pass

    # 2) 查真实剩余仓位(不信本地qty——止盈可能已经部分成交)
    try:
        positions = binance_client.client.futures_position_information(symbol=symbol)
        live_amt = 0.0
        for p in positions:
            live_amt = float(p.get("positionAmt") or 0)
    except Exception as e:
        logger.error(f"[{symbol}] 离场前查真实仓位失败: {e}，暂不平仓，下一轮重试")
        return

    if live_amt == 0:
        logger.info(f"[{symbol}] 查到仓位已经是0(可能刚被止损/止盈打平)，直接清状态")
        state.pop(symbol, None)
        _save_state(state)
        return

    close_side = "SELL" if live_amt > 0 else "BUY"
    qty = binance_client.format_quantity(abs(live_amt), symbol)
    order = binance_client.place_market_order(close_side, qty, symbol=symbol, reduce_only=True)
    if order:
        logger.info(f"✅ [{symbol}] 离场平仓成交 qty={qty} | {signal.get('reason')}")
        state.pop(symbol, None)
        _save_state(state)
    else:
        logger.error(f"🚨 [{symbol}] 离场市价平仓失败！需要人工立刻核查")


# ==================== 启动核对 ====================

def _reconcile_on_start(state: Dict[str, Any]) -> Dict[str, Any]:
    """本地状态 vs 交易所真实持仓核对——铁律：本地没记录、交易所却有
    仓位的品种，绝不自动接管(可能是宝贝自己手工开的，或者其它来源)，
    只报错跳过，等人工确认。这条规矩跟这次会话反复验证过的"非本引擎
    开的仓位绝不碰"完全一致。"""
    for symbol in TRADED_SYMBOLS:
        rec = state.get(symbol)
        try:
            positions = binance_client.client.futures_position_information(symbol=symbol)
            live_amt = 0.0
            for p in positions:
                live_amt = float(p.get("positionAmt") or 0)
        except Exception as e:
            logger.error(f"[{symbol}] 启动核对查仓位失败: {e}，本轮跳过这个品种")
            continue

        if rec and live_amt == 0:
            logger.warning(
                f"[{symbol}] 本地记录有仓，交易所已空仓(大概率已被止损/止盈打平)，"
                f"清本地状态"
            )
            state.pop(symbol, None)
        elif not rec and live_amt != 0:
            logger.error(
                f"🚨 [{symbol}] 交易所有仓位({live_amt})但本地无记录——不自动接管！"
                f"可能是宝贝自己手工开的或者其它来源，人工确认后再处理，"
                f"本引擎这个品种先跳过管理"
            )
        elif rec and live_amt != 0 and rec.get("status") == "entry_pending_sl":
            logger.error(
                f"🚨 [{symbol}] 检测到上次崩溃发生在'已开仓、止损还没挂上'这个窗口！"
                f"紧急补挂止损@{rec.get('stop_loss')}"
            )
            close_side = "SHORT" if rec.get("side") == "LONG" else "LONG"
            sl_id = _place_stop(symbol, close_side, float(rec["stop_loss"]))
            if sl_id:
                rec["sl_order_id"] = sl_id
                rec["status"] = "open"
                logger.info(f"🛡️ [{symbol}] 紧急止损补挂成功 @{rec['stop_loss']}")
            else:
                logger.error(f"🚨🚨 [{symbol}] 紧急止损补挂仍然失败！需要人工立刻介入")
    _save_state(state)
    return state


# ==================== 主循环 ====================

def _tick_symbol(symbol: str, universe_returns: Dict[str, float], state: Dict[str, Any]) -> None:
    try:
        bars = get_bars(symbol, TIMEFRAME, limit=KLINES_LIMIT)
    except Exception as e:
        logger.warning(f"[{symbol}] 拉K线失败: {e}")
        return
    if not bars:
        return

    rec = state.get(symbol)
    position = {"side": rec["side"]} if rec else None

    params = {"symbol": symbol, "universe_returns": universe_returns}
    signal = generate_signal({"base": bars}, params=params, position=position)
    if not signal:
        return

    # 同一根4h K线已经动作过就跳过——顺带给"刚开仓当根K线内立刻反向翻
    # 转"这种边界噪声一道缓冲，等下一根K线收盘再确认要不要离场。
    bar_time = signal.get("bar_time")
    if rec and bar_time is not None and rec.get("last_acted_bar_time") == bar_time:
        return

    action = signal.get("action")
    if action in ("LONG", "SHORT") and not rec:
        _open_position(symbol, signal, state)
    elif action == "CLOSE_QUICK_EXIT" and rec:
        _close_position(symbol, signal, state)
    else:
        logger.debug(f"[{symbol}] 信号被跳过(action={action} 已有仓位={bool(rec)})")


def run_once() -> None:
    state = _load_state()
    state = _reconcile_on_start(state)

    universe_returns = _compute_universe_returns()
    if len(universe_returns) < 6:
        logger.warning(f"本轮排名品种数不足({len(universe_returns)})，跳过这一轮")
        return
    logger.info(f"本轮动量排名就绪(共{len(universe_returns)}/{len(RANKING_UNIVERSE)}个品种参与)")

    for symbol in TRADED_SYMBOLS:
        try:
            _tick_symbol(symbol, universe_returns, state)
        except Exception as e:
            logger.error(f"[{symbol}] 本轮处理异常: {e}", exc_info=True)


def main() -> None:
    once = "--once" in sys.argv
    logger.info(f"dual_momentum实盘引擎启动 | 目标品种={TRADED_SYMBOLS} | once={once}")
    if once:
        run_once()
        return
    while True:
        try:
            run_once()
        except Exception as e:
            logger.error(f"主循环异常(不退出，下一轮重试): {e}", exc_info=True)
        time.sleep(TICK_INTERVAL_SEC)


if __name__ == "__main__":
    main()

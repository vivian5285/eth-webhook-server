#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNDK 91分钟双均线(EMA7/30)全自动实盘引擎 - 2026-09-23

宝贝拍板：SNDK不再接TV，交易逻辑完全搬到VPS本地。按宝贝发来的真实Pine
策略源码("EMA7 & EMA30 纯裸K微结构突破")逐条核对实现，周期91分钟
(宝贝确认TradingView回测用的91分钟K线，跟radar_reentry_mixin.py::
DUAL_MA_EXIT_INTERVAL_MIN里SNDKUSDT登记的91分钟互相印证)。

2026-09-23重构：所有不碰账户/网络的纯信号/风控逻辑(参数常量+EMA/ATR/
ADX判定+止损状态机)已经拆到sndk_dual_ma_strategy.py——回测脚本
(sndk_dual_ma_backtest.py)需要测的是这同一套逻辑，不能自己另外抄一遍
容易悄悄走样，"验证的是什么，实盘跑的就是什么"。本文件现在只剩：拉
实时数据、真实下单、状态持久化到本地json、钉钉告警、主循环节奏。

数据层：91分钟不是币安任何原生K线周期的整数倍，用仓库里已经在生产
环境跑着的"任意周期"通用合成器strategy_engine/klines.py::get_bars
(dual_momentum_live.py、DUAL_MA_EXIT都在用)，91分钟会自动退化到1分钟
K线合成，内置分页/UTC epoch对齐。

指标精度：每次重算指标用的K线深度~1000根(get_bars内置分页，SNDK这类
新上市代币历史不够时自动退化用能拿到的全部)，只在91分钟bucket边界真正
跨越时才重算(纯本地时间戳判断)，比计划书要求的"每日UTC0点校准"更频繁，
天然覆盖。20秒tick循环只做两件轻量的事：查现价(ticker)+用缓存的ATR/
ADX/结构位跑止损状态机。

刻意不在交易所挂止损条件单——纯VPS内存状态机盯盘，这是防插针机制本身
的要求(挂单没法区分插针秒回和真突破)，代价是VPS进程若挂了、仓位在
恢复前没有交易所侧保护网，用独立钉钉告警部分缓解(复用binance-gateway/
gateway.py同一份WATCHDOG_DINGTALK_*环境变量)。

跑法：
  常驻:  venv/bin/python sndk_dual_ma_live.py
  单轮:  venv/bin/python sndk_dual_ma_live.py --once
  干跑:  venv/bin/python sndk_dual_ma_live.py --dry-run  (只读打印指标/信号，不下单)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from binance_client import binance_client
from market_engine import wilder_atr, wilder_adx, bucket_open_ms
from strategy_engine.klines import get_bars as _sk_get_bars

import sndk_dual_ma_strategy as strat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] SndkDualMA: %(message)s",
)
logger = logging.getLogger(__name__)

SYMBOL = strat.SYMBOL
PERIOD_MS = strat.PERIOD_MS
PERIOD_STR = strat.PERIOD_STR
ATR_PERIOD = strat.ATR_PERIOD
ADX_PERIOD = strat.ADX_PERIOD
STRUCT_LOOKBACK = strat.STRUCT_LOOKBACK
DEEP_BARS_TARGET = strat.DEEP_BARS_TARGET
MIN_BARS_NEEDED = strat.MIN_BARS_NEEDED
EXCHANGE_LEVERAGE = strat.EXCHANGE_LEVERAGE
EQUITY_USAGE_PCT = strat.EQUITY_USAGE_PCT

TICK_INTERVAL_SEC = 20  # 本地盯盘轮询间隔(只查现价，便宜)；防插针窗口是分钟级，20秒足够及时
RECONCILE_EVERY_N_TICKS = 15  # ≈5分钟一次核对真实持仓，防止本地状态跟交易所长期漂移

STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "sndk_dual_ma_live_state.json"
)

# ==================== 钉钉告警 ====================
# 独立小实现(不import主引擎的dingtalk.py，那个模块是给position_supervisor
# 自己的消息格式设计的)，复用binance-gateway/gateway.py同一份环境变量，
# 推到同一个钉钉群，宝贝不用额外配置。
DINGTALK_WEBHOOK = os.getenv("WATCHDOG_DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("WATCHDOG_DINGTALK_SECRET", "")


def _dingtalk_signed_url() -> str:
    if not DINGTALK_WEBHOOK:
        return ""
    if not DINGTALK_SECRET:
        return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    string_to_sign = f"{ts}\n{DINGTALK_SECRET}"
    hmac_code = hmac.new(
        DINGTALK_SECRET.encode("utf-8"), string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    sep = "&" if "?" in DINGTALK_WEBHOOK else "?"
    return f"{DINGTALK_WEBHOOK}{sep}timestamp={ts}&sign={sign}"


def _alert(text: str) -> None:
    url = _dingtalk_signed_url()
    if not url:
        logger.warning(f"[钉钉] 未配置webhook，跳过: {text[:80]}")
        return
    payload = json.dumps({
        "msgtype": "text",
        "text": {"content": f"【SNDK双均线实盘】{text}"},
        "at": {"isAtAll": False},
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except Exception as e:
        logger.warning(f"[钉钉] 发送失败: {e}")


# ==================== 状态持久化 ====================

def _blank_state() -> Dict[str, Any]:
    return {
        "position": None,
        "last_bar_time": None,
        "cached_atr": 0.0,
        "cached_adx": 0.0,
        "cached_struct_low": None,
        "cached_struct_high": None,
    }


def _load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return _blank_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return _blank_state()
            for k, v in _blank_state().items():
                data.setdefault(k, v)
            return data
    except Exception as e:
        logger.error(f"状态文件读取失败，视为空状态启动: {e}")
        return _blank_state()


def _save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


# ==================== K线(strategy_engine通用任意周期合成器，只在91m bucket边界触发) ====================

def _fetch_bars(target_bars: int = DEEP_BARS_TARGET) -> List[list]:
    """用strategy_engine.klines.get_bars合成91分钟bar——公开行情端点，
    不需要API Key，内置分页+"退化到能整除目标周期的最粗原生周期"逻辑
    (91分钟没有能整除它的原生周期，会自动退化到1分钟K线合成)。转换成
    [open_time, o, h, l, c, v]的list行格式，兼容market_engine.wilder_atr/
    wilder_adx等既有只读list-index函数。SNDK这类较新品种交易所历史可能
    不足target_bars根，拉不满时用能拿到的全部，不当失败。"""
    dict_bars = _sk_get_bars(SYMBOL, PERIOD_STR, limit=target_bars)
    return [[b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in dict_bars]


def _live_price() -> float:
    try:
        t = binance_client.client.futures_symbol_ticker(symbol=SYMBOL)
        return float(t["price"])
    except Exception as e:
        logger.warning(f"取现价失败: {e}")
        return 0.0


# ==================== 下单 ====================

def _calc_qty(price: float) -> float:
    """本金等值(1倍不加杠杆)：qty = 账户权益×98% / 现价。"""
    equity = binance_client.get_total_equity("USDT")
    if equity <= 0 or price <= 0:
        logger.error(f"权益或现价异常 equity={equity} price={price}，跳过开仓")
        return 0.0
    return binance_client.format_quantity(equity * EQUITY_USAGE_PCT / price, SYMBOL)


def _open_position(action: str, price: float, bar_time: int, atr_hint: float,
                    state: Dict[str, Any]) -> None:
    side = "LONG" if action == "LONG" else "SHORT"
    qty = _calc_qty(price)
    if qty <= 0:
        logger.warning(f"算出qty<=0，放弃开仓 side={side} price={price}")
        return

    lev_result = binance_client.set_leverage(SYMBOL, leverage=EXCHANGE_LEVERAGE)
    if lev_result is None:
        logger.error("设置1倍杠杆失败，放弃开仓(避免用未知杠杆下单)")
        return

    order = binance_client.place_market_order(side, qty, symbol=SYMBOL, reduce_only=False)
    if not order:
        logger.error(f"市价开仓失败 side={side} qty={qty}")
        _alert(f"⚠️ 开仓下单失败 {side} qty={qty}，请人工核查")
        return

    # avgPrice在市价单REST同步响应里经常是0(币安期货API已知行为，跟
    # dual_momentum_live.py 2026-09-22踩过的同一个坑)，改成下单后查真实
    # 持仓entryPrice，带短暂重试。
    fill_price = 0.0
    for _ in range(5):
        try:
            positions = binance_client.client.futures_position_information(symbol=SYMBOL)
            for p in positions:
                fill_price = abs(float(p.get("entryPrice") or 0))
        except Exception:
            fill_price = 0.0
        if fill_price > 0:
            break
        time.sleep(1.0)
    if fill_price <= 0:
        logger.warning("查真实成交价失败，退回用信号价(可能不精确)")
        fill_price = price

    atr = atr_hint if atr_hint and atr_hint > 0 else fill_price * 0.005
    pos = strat.new_position(side, fill_price, atr, bar_time, time.time())
    pos["qty"] = qty
    state["position"] = pos
    _save_state(state)
    logger.info(f"🚀 开仓成交 {side} qty={qty} @{fill_price} 硬止损={pos['hard_stop']:.6f} ATR={atr:.6f}")
    _alert(f"🚀 开仓 {side} SNDKUSDT qty={qty} @{fill_price:.4f} 硬止损{pos['hard_stop']:.4f}")


def _close_position(reason: str, state: Dict[str, Any]) -> bool:
    """市价平仓当前全部真实持仓(不信本地qty)。成功清状态返回True。"""
    try:
        positions = binance_client.client.futures_position_information(symbol=SYMBOL)
        live_amt = 0.0
        for p in positions:
            live_amt = float(p.get("positionAmt") or 0)
    except Exception as e:
        logger.error(f"平仓前查真实仓位失败: {e}，本轮跳过重试")
        return False

    if live_amt == 0:
        logger.info("查到仓位已是0，直接清状态")
        state["position"] = None
        _save_state(state)
        return True

    close_side = "SELL" if live_amt > 0 else "BUY"
    qty = binance_client.format_quantity(abs(live_amt), SYMBOL)
    order = binance_client.place_market_order(close_side, qty, symbol=SYMBOL, reduce_only=True)
    if order:
        logger.info(f"✅ 平仓成交 qty={qty} 原因={reason}")
        _alert(f"✅ 平仓 SNDKUSDT qty={qty} 原因={reason}")
        state["position"] = None
        _save_state(state)
        return True
    logger.error(f"🚨 市价平仓失败！原因={reason}，需要人工立刻核查")
    _alert(f"🚨 市价平仓失败！原因={reason}，需要人工立刻核查交易所")
    return False


# ==================== 启动核对 ====================

def _reconcile(state: Dict[str, Any]) -> Dict[str, Any]:
    """本地状态 vs 交易所真实持仓核对——铁律：本地没记录、交易所却有
    仓位，绝不自动接管(可能是宝贝自己手工开的，或其它来源)，只报错跳过，
    等人工确认。"""
    try:
        positions = binance_client.client.futures_position_information(symbol=SYMBOL)
        live_amt = 0.0
        for p in positions:
            live_amt = float(p.get("positionAmt") or 0)
    except Exception as e:
        logger.error(f"核对查仓位失败: {e}，本轮先跳过")
        return state

    pos = state.get("position")
    if pos and live_amt == 0:
        logger.warning("本地记录有仓，交易所已空仓(大概率已被本引擎自己打平)，清本地状态")
        state["position"] = None
        _save_state(state)
    elif not pos and live_amt != 0:
        logger.error(
            f"🚨 交易所有仓位({live_amt})但本地无记录——不自动接管！"
            f"可能是手工开的或其它来源，人工确认后处理，本引擎先不管这个仓位"
        )
        _alert(f"🚨 SNDKUSDT交易所有仓位({live_amt})但本引擎本地无记录，未接管，请人工核查")
    return state


# ==================== 主循环 ====================

def _should_refresh_indicators(state: Dict[str, Any], now_ms: int) -> bool:
    """纯本地时间戳判断是否跨过了一个新的91m bucket边界，不额外打API
    去"看一眼"有没有新bar——91m bar固定在UTC epoch整91分钟边界收盘，
    可以直接算。"""
    cur_bucket = bucket_open_ms(now_ms, PERIOD_MS)
    last_bar = state.get("last_bar_time")
    if last_bar is None:
        return True
    return cur_bucket > last_bar


def _refresh_indicators_and_act(state: Dict[str, Any]) -> Dict[str, Any]:
    bars = _fetch_bars()
    if len(bars) < MIN_BARS_NEEDED:
        logger.warning(f"K线不足({len(bars)}/{MIN_BARS_NEEDED})，本轮跳过指标刷新")
        return state

    bar_time = int(bars[-1][0])
    atr_now = wilder_atr(bars, ATR_PERIOD)
    adx_now = wilder_adx(bars, ADX_PERIOD)
    struct_bars = bars[-STRUCT_LOOKBACK:] if len(bars) >= STRUCT_LOOKBACK else bars
    state["cached_atr"] = atr_now
    state["cached_adx"] = adx_now
    state["cached_struct_low"] = min(float(b[3]) for b in struct_bars)
    state["cached_struct_high"] = max(float(b[2]) for b in struct_bars)

    if state.get("last_bar_time") != bar_time:
        pos = state.get("position")
        if pos:
            sig = strat.exit_signal(bars, pos["side"])
            if sig:
                action = sig["action"]
                if action == "CLOSE_ONLY":
                    _close_position("双均线跌破/站上(未满足反手条件)，纯平仓", state)
                elif action in ("REVERSE_LONG", "REVERSE_SHORT"):
                    ok = _close_position("反手信号，先平旧仓", state)
                    if ok:
                        new_action = "LONG" if action == "REVERSE_LONG" else "SHORT"
                        _open_position(new_action, sig["price"], sig["bar_time"], sig.get("atr", atr_now), state)
        else:
            sig = strat.entry_signal(bars)
            if sig:
                _open_position(sig["action"], sig["price"], sig["bar_time"], sig.get("atr", atr_now), state)
        state["last_bar_time"] = bar_time

    _save_state(state)
    return state


def run_once(state: Dict[str, Any]) -> Dict[str, Any]:
    now_ms = int(time.time() * 1000)

    if _should_refresh_indicators(state, now_ms):
        state = _refresh_indicators_and_act(state)

    price = _live_price()
    pos = state.get("position")
    if pos and price > 0:
        atr_now = float(state.get("cached_atr") or pos.get("atr_at_entry", 0.0))
        adx_now = float(state.get("cached_adx") or 0.0)
        struct_low = state.get("cached_struct_low")
        struct_high = state.get("cached_struct_high")
        should_close, reason = strat.evaluate_protective_stop(
            pos, price, atr_now, adx_now, struct_low, struct_high, time.time(),
        )
        state["position"] = pos
        _save_state(state)
        if should_close:
            logger.info(f"🛑 触发止损平仓: {reason}")
            _close_position(reason, state)

    return state


def dry_run_check() -> None:
    """只读体检：拉真实深度K线，把EMA7/30/ATR14/ADX14/入场出场判定都
    打出来，完全不下单、不碰状态文件——改动策略参数后应该先跑这个人工
    核对数字，再放开实盘循环。"""
    bars = _fetch_bars()
    logger.info(f"[干跑] 拉到{len(bars)}根已闭合91m bar(需要至少{MIN_BARS_NEEDED}根，目标{DEEP_BARS_TARGET}根)")
    if len(bars) < MIN_BARS_NEEDED:
        logger.warning("[干跑] bar数不够，指标不可信，先别启用实盘")
        return
    closes = [float(b[4]) for b in bars]
    cur = bars[-1]
    price = _live_price()
    atr = wilder_atr(bars, ATR_PERIOD)
    adx = wilder_adx(bars, ADX_PERIOD)
    ema_fast_now = strat.ema_last(closes, strat.FAST_LEN)
    ema_fast_prev = strat.ema_last(closes[:-1], strat.FAST_LEN)
    ema_slow_now = strat.ema_last(closes, strat.SLOW_LEN)
    logger.info(
        f"[干跑] 最新已收盘91m bar open={float(cur[1]):.4f} close={float(cur[4]):.4f} "
        f"现价={price:.4f} ATR14={atr:.6f} ADX14={adx:.2f}"
    )
    logger.info(
        f"[干跑] EMA7now={ema_fast_now:.4f} EMA7prev={ema_fast_prev:.4f} "
        f"(斜率{'向上' if ema_fast_now > ema_fast_prev else '向下' if ema_fast_now < ema_fast_prev else '持平'}) "
        f"EMA30now={ema_slow_now:.4f}"
    )
    entry = strat.entry_signal(bars)
    logger.info(f"[干跑] 当前空仓假设下的入场信号={entry}")
    for side in ("LONG", "SHORT"):
        exit_sig = strat.exit_signal(bars, side)
        logger.info(f"[干跑] 若持有{side}仓位，出场/反手信号={exit_sig}")


def main() -> None:
    once = "--once" in sys.argv
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logger.info(f"SNDK双均线引擎【干跑模式，不下单】| 品种={SYMBOL} 周期={strat.PERIOD_MIN}min")
        dry_run_check()
        return
    logger.info(f"SNDK双均线实盘引擎启动 | 品种={SYMBOL} 周期={strat.PERIOD_MIN}min once={once}")
    state = _load_state()
    state = _reconcile(state)
    _save_state(state)

    if once:
        run_once(state)
        return

    tick = 0
    while True:
        try:
            state = run_once(state)
        except Exception as e:
            logger.error(f"主循环异常(不退出，下一轮重试): {e}", exc_info=True)
        tick += 1
        if tick % RECONCILE_EVERY_N_TICKS == 0:
            try:
                state = _reconcile(state)
                _save_state(state)
            except Exception as e:
                logger.error(f"定期核对异常: {e}", exc_info=True)
        time.sleep(TICK_INTERVAL_SEC)


if __name__ == "__main__":
    main()

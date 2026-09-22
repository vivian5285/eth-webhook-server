#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNDK 90分钟双均线(EMA7/25)全自动实盘引擎 - 2026-09-23

宝贝拍板：SNDK不再接TV，交易逻辑完全搬到VPS本地——用币安自己的30m K线
现场合成90分钟bar（跟行情引擎market_engine.py同一套UTC epoch对齐算法，
跟TradingView 90分钟图口径一致），本地判断EMA7/25双均线+5根K线突破
进出场，硬止损/保本/ADX自适应吊灯追踪止损全部本地状态机维护，不依赖
任何TV webhook。根据宝贝提供的DeepSeek计划书原样实现。

架构：跟dual_momentum_live.py同一种"完全独立于position_supervisor_
binance.py"的隔离设计——不走webhook/白名单，直连binance_client(client层)
下单，自己维护精简本地状态，只对SNDKUSDT动手。

关键设计取舍——本引擎故意不在交易所挂任何止损/止盈条件单
(place_stop_market_order)，止损完全是VPS内存里的状态机在盯盘触发市价
平仓。这是计划书"防插针"设计本身的要求：交易所端挂的止损单一碰最高/
最低价就无条件成交，没法区分"插针秒回"和"真突破"；只有VPS自己在内存
里维护一个"击穿多久未收回"的计时器，才能做这个区分。代价是：VPS进程
如果挂了，仓位在进程恢复前没有任何交易所侧的保护网——这是本设计明确
接受的取舍，不是疏忽。为部分缓解这个风险：加了独立于position_
supervisor通知体系的钉钉告警(开仓/平仓/止损触发/严重异常都推)，复用
binance-gateway/gateway.py同一份WATCHDOG_DINGTALK_*环境变量、同一个群。

杠杆：交易所杠杆直接设成1倍——跟仓位公式"本金等值、不加任何杠杆"完全
对齐，不是"仓位公式内部按1倍算、交易所却挂着别的杠杆"这种隐藏敞口。

跑法：
  常驻:  venv/bin/python sndk_dual_ma_live.py
  单轮:  venv/bin/python sndk_dual_ma_live.py --once   (验证用，跑一轮立刻退出)
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
from typing import Any, Dict, List, Optional, Tuple

from binance_client import binance_client
from market_engine import merge_30m_to_period, wilder_atr, wilder_adx
from dual_ma_trend import dual_ma_trend_ok

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] SndkDualMA: %(message)s",
)
logger = logging.getLogger(__name__)

# ==================== 策略参数(照DeepSeek计划书原样落地) ====================
SYMBOL = "SNDKUSDT"
PERIOD_MIN = 90
PERIOD_MS = PERIOD_MIN * 60 * 1000
FAST_LEN = 7
SLOW_LEN = 25
STRUCT_LOOKBACK = 20
BREAKOUT_LOOKBACK = 5
ATR_PERIOD = 14
ADX_PERIOD = 14

INITIAL_STOP_ATR_MULT = 2.5
BREAKEVEN_TRIGGER_ATR = 1.0
BREAKEVEN_BUFFER_PCT = 0.0005  # 0.05%，覆盖手续费
TRAIL_TRIGGER_ATR = 1.5
TRAIL_MULT_STRONG = 3.5   # ADX > 30
TRAIL_MULT_WEAK = 1.5     # ADX < 20
TRAIL_MULT_NORMAL = 2.5   # 其余
ADX_STRONG_BOUND = 30.0
ADX_WEAK_BOUND = 20.0
STRUCT_BUFFER_ATR = 0.2

SPIKE_FORCE_CLOSE_SEC = 15 * 60  # 硬止损击穿持续这么久仍未收回 → 强制平仓

EXCHANGE_LEVERAGE = 1  # 交易所真实杠杆锁1倍，等同于现货满仓

RAW_KLINES_LIMIT = 240  # 240根30m ≈ 80根90m，覆盖EMA25/ADX14/struct20warmup有富余
MIN_BARS_NEEDED = max(SLOW_LEN, STRUCT_LOOKBACK, ADX_PERIOD * 2 + 2) + BREAKOUT_LOOKBACK

TICK_INTERVAL_SEC = 20  # 本地盯盘轮询间隔；硬止损防插针窗口是分钟级(5/15分钟)，20秒足够及时
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
    return {"position": None, "last_bar_time": None}


def _load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return _blank_state()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return _blank_state()
            data.setdefault("position", None)
            data.setdefault("last_bar_time", None)
            return data
    except Exception as e:
        logger.error(f"状态文件读取失败，视为空状态启动: {e}")
        return _blank_state()


def _save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


# ==================== K线 ====================

def _fetch_90m_bars() -> List[list]:
    """30m原始K线现场合成90m bar，UTC epoch对齐，跟market_engine.py
    (行情引擎)、跟TradingView 90分钟图同一套锚点算法。只返回已完整
    闭合的bucket，最后一根forming中的30m蜡烛天然不会凑齐3根被合成。"""
    raw_30m = binance_client.fetch_klines(SYMBOL, interval="30m", limit=RAW_KLINES_LIMIT)
    if not raw_30m:
        return []
    return merge_30m_to_period(raw_30m, PERIOD_MS)


def _live_price() -> float:
    try:
        t = binance_client.client.futures_symbol_ticker(symbol=SYMBOL)
        return float(t["price"])
    except Exception as e:
        logger.warning(f"取现价失败: {e}")
        return 0.0


# ==================== 信号判定(90分钟收盘时评估) ====================

def _entry_signal(bars: List[list]) -> Optional[Dict[str, Any]]:
    """最新已收盘90m bar是否满足开多/开空条件。None=无信号。"""
    if len(bars) < MIN_BARS_NEEDED:
        return None
    cur = bars[-1]
    o, c = float(cur[1]), float(cur[4])
    prior5 = bars[-(BREAKOUT_LOOKBACK + 1):-1]
    prior5_high = max(float(b[2]) for b in prior5)
    prior5_low = min(float(b[3]) for b in prior5)

    is_bull = c > o
    is_bear = c < o
    ema_long_ok, _ = dual_ma_trend_ok("LONG", bars, FAST_LEN, SLOW_LEN, "EMA")
    ema_short_ok, _ = dual_ma_trend_ok("SHORT", bars, FAST_LEN, SLOW_LEN, "EMA")

    if is_bull and ema_long_ok and c > prior5_high:
        return {"action": "LONG", "price": c, "bar_time": int(cur[0])}
    if is_bear and ema_short_ok and c < prior5_low:
        return {"action": "SHORT", "price": c, "bar_time": int(cur[0])}
    return None


def _exit_signal(bars: List[list], side: str) -> Optional[Dict[str, Any]]:
    """持仓方向在最新90m收盘时是否该"纯平仓"或"反手"。跟_entry_signal
    共用同一份bars，避免两次取不同数据判断不一致。"""
    if len(bars) < MIN_BARS_NEEDED:
        return None
    cur = bars[-1]
    o, c = float(cur[1]), float(cur[4])
    prior5 = bars[-(BREAKOUT_LOOKBACK + 1):-1]
    prior5_high = max(float(b[2]) for b in prior5)
    prior5_low = min(float(b[3]) for b in prior5)
    is_bull = c > o
    is_bear = c < o

    if side == "LONG":
        ema_short_ok, _ = dual_ma_trend_ok("SHORT", bars, FAST_LEN, SLOW_LEN, "EMA")
        if not ema_short_ok:
            return None  # 双均线都没跌破，继续持仓
        if is_bear and c < prior5_low:
            return {"action": "REVERSE_SHORT", "price": c, "bar_time": int(cur[0])}
        return {"action": "CLOSE_ONLY", "price": c, "bar_time": int(cur[0])}
    else:
        ema_long_ok, _ = dual_ma_trend_ok("LONG", bars, FAST_LEN, SLOW_LEN, "EMA")
        if not ema_long_ok:
            return None
        if is_bull and c > prior5_high:
            return {"action": "REVERSE_LONG", "price": c, "bar_time": int(cur[0])}
        return {"action": "CLOSE_ONLY", "price": c, "bar_time": int(cur[0])}


# ==================== 下单 ====================

def _calc_qty(price: float) -> float:
    """本金等值(1倍不加杠杆)：qty = 账户权益 / 现价——直接对齐宝贝"永远
    满仓、不加任何杠杆"的原话，不复用TV白名单品种那套tier×杠杆仓位
    公式，避免混入隐藏杠杆倍数。"""
    equity = binance_client.get_total_equity("USDT")
    if equity <= 0 or price <= 0:
        logger.error(f"权益或现价异常 equity={equity} price={price}，跳过开仓")
        return 0.0
    return binance_client.format_quantity(equity / price, SYMBOL)


def _open_position(action: str, price: float, bar_time: int, bars: List[list],
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

    atr = wilder_atr(bars, ATR_PERIOD)
    if atr <= 0:
        logger.error("开仓时ATR算不出来，用现价0.5%粗估兜底，避免止损缺失")
        atr = fill_price * 0.005

    direction = 1.0 if side == "LONG" else -1.0
    hard_stop = fill_price - direction * INITIAL_STOP_ATR_MULT * atr

    state["position"] = {
        "side": side,
        "entry_price": fill_price,
        "qty": qty,
        "atr_at_entry": atr,
        "entry_bar_time": bar_time,
        "hard_stop": hard_stop,
        "extreme_price": fill_price,
        "breakeven_active": False,
        "trail_active": False,
        "current_stop": hard_stop,
        "spike_breach_start_ts": None,
        "opened_at": time.time(),
    }
    _save_state(state)
    logger.info(f"🚀 开仓成交 {side} qty={qty} @{fill_price} 硬止损={hard_stop:.6f} ATR={atr:.6f}")
    _alert(f"🚀 开仓 {side} SNDKUSDT qty={qty} @{fill_price:.4f} 硬止损{hard_stop:.4f}")


def _close_position(reason: str, state: Dict[str, Any]) -> bool:
    """市价平仓当前全部真实持仓(不信本地qty，止损可能已经先手动/其它
    途径动过)。成功清状态返回True。"""
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


# ==================== 止损状态机(每个tick评估，intrabar) ====================

def _evaluate_protective_stop(pos: Dict[str, Any], price: float, atr_now: float,
                               adx_now: float, bars: List[list], now_ts: float) -> Tuple[bool, str]:
    """返回(是否该市价平仓, 原因)。硬止损阶段做防插针(持续击穿超15分钟
    才真平)；保本/追踪阶段一碰即触发(计划书原文只在"初始硬止损"小节
    描述了防插针等待，后两档都是"立刻市价全平")。"""
    side = pos["side"]
    direction = 1.0 if side == "LONG" else -1.0
    entry = pos["entry_price"]
    atr0 = pos.get("atr_at_entry") or atr_now

    if side == "LONG":
        pos["extreme_price"] = max(pos.get("extreme_price", entry), price)
    else:
        pos["extreme_price"] = min(pos.get("extreme_price", entry), price)

    profit_atr = direction * (price - entry) / atr0 if atr0 > 0 else 0.0

    if not pos.get("breakeven_active") and profit_atr >= BREAKEVEN_TRIGGER_ATR:
        pos["breakeven_active"] = True
        logger.info(f"🛡️ 浮盈达到{BREAKEVEN_TRIGGER_ATR}倍ATR，止损移动到保本位")

    if not pos.get("trail_active") and profit_atr >= TRAIL_TRIGGER_ATR:
        pos["trail_active"] = True
        logger.info(f"📈 浮盈达到{TRAIL_TRIGGER_ATR}倍ATR，启动ADX自适应吊灯追踪止损")

    candidates = [pos["hard_stop"]]

    if pos.get("breakeven_active"):
        be = entry * (1 + BREAKEVEN_BUFFER_PCT) if side == "LONG" else entry * (1 - BREAKEVEN_BUFFER_PCT)
        candidates.append(be)

    if pos.get("trail_active") and atr_now > 0:
        if adx_now > ADX_STRONG_BOUND:
            mult = TRAIL_MULT_STRONG
        elif adx_now < ADX_WEAK_BOUND:
            mult = TRAIL_MULT_WEAK
        else:
            mult = TRAIL_MULT_NORMAL
        chandelier = pos["extreme_price"] - direction * mult * atr_now

        struct_bars = bars[-STRUCT_LOOKBACK:] if len(bars) >= STRUCT_LOOKBACK else bars
        if struct_bars:
            if side == "LONG":
                struct_level = min(float(b[3]) for b in struct_bars) - STRUCT_BUFFER_ATR * atr_now
                trail_final = max(chandelier, struct_level)
            else:
                struct_level = max(float(b[2]) for b in struct_bars) + STRUCT_BUFFER_ATR * atr_now
                trail_final = min(chandelier, struct_level)
            candidates.append(trail_final)
        else:
            candidates.append(chandelier)

    active_stop = max(candidates) if side == "LONG" else min(candidates)
    pos["current_stop"] = active_stop

    breached = (price <= active_stop) if side == "LONG" else (price >= active_stop)

    if not breached:
        pos["spike_breach_start_ts"] = None
        return False, ""

    if pos.get("breakeven_active") or pos.get("trail_active"):
        return True, ("移动追踪止损触发" if pos.get("trail_active") else "保本止损触发")

    # 仍在初始硬止损阶段：防插针状态机。持续击穿超15分钟未收回才真止损；
    # 期间只要有一个tick价格收回(breached=False)，上面就把计时器清零，
    # 天然实现"5分钟内收回就取消"的效果，不需要额外单独维护一个5分钟计时器。
    if pos.get("spike_breach_start_ts") is None:
        pos["spike_breach_start_ts"] = now_ts
        logger.info(f"⚡ 价格击穿硬止损{active_stop:.6f}，开始15分钟观察窗口(防插针)")
        return False, ""
    elapsed = now_ts - pos["spike_breach_start_ts"]
    if elapsed >= SPIKE_FORCE_CLOSE_SEC:
        return True, f"硬止损击穿持续{elapsed / 60:.1f}分钟未收回"
    return False, ""


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

def run_once(state: Dict[str, Any]) -> Dict[str, Any]:
    price = _live_price()
    pos = state.get("position")
    bars = _fetch_90m_bars()

    if pos and price > 0 and bars:
        atr_now = wilder_atr(bars, ATR_PERIOD) or pos.get("atr_at_entry", 0.0)
        adx_now = wilder_adx(bars, ADX_PERIOD)
        should_close, reason = _evaluate_protective_stop(
            pos, price, atr_now, adx_now, bars, time.time(),
        )
        state["position"] = pos
        _save_state(state)
        if should_close:
            logger.info(f"🛑 触发止损平仓: {reason}")
            _close_position(reason, state)
            pos = None

    if not bars:
        return state
    bar_time = int(bars[-1][0])
    if state.get("last_bar_time") == bar_time:
        return state  # 这根90m bar已经评估过信号，等下一根收盘

    pos = state.get("position")
    if pos:
        sig = _exit_signal(bars, pos["side"])
        if sig:
            action = sig["action"]
            if action == "CLOSE_ONLY":
                _close_position("双均线跌破/站上(未满足反手突破条件)，纯平仓", state)
            elif action in ("REVERSE_LONG", "REVERSE_SHORT"):
                ok = _close_position("反手信号，先平旧仓", state)
                if ok:
                    new_action = "LONG" if action == "REVERSE_LONG" else "SHORT"
                    _open_position(new_action, sig["price"], sig["bar_time"], bars, state)
    else:
        sig = _entry_signal(bars)
        if sig:
            _open_position(sig["action"], sig["price"], sig["bar_time"], bars, state)

    state["last_bar_time"] = bar_time
    _save_state(state)
    return state


def dry_run_check() -> None:
    """只读体检：拉真实K线，把EMA7/25/ATR14/ADX14/入场出场判定都打出来，
    完全不下单、不碰状态文件——部署后先跑这个，人工核对数字再放开实盘
    循环，不要没验证过指标计算就让它直接摸真实资金。"""
    bars = _fetch_90m_bars()
    logger.info(f"[干跑] 拉到{len(bars)}根已闭合90m bar(需要至少{MIN_BARS_NEEDED}根)")
    if len(bars) < MIN_BARS_NEEDED:
        logger.warning("[干跑] bar数不够，指标不可信，先别启用实盘")
        return
    cur = bars[-1]
    price = _live_price()
    atr = wilder_atr(bars, ATR_PERIOD)
    adx = wilder_adx(bars, ADX_PERIOD)
    ema_long_ok, meta_long = dual_ma_trend_ok("LONG", bars, FAST_LEN, SLOW_LEN, "EMA")
    ema_short_ok, meta_short = dual_ma_trend_ok("SHORT", bars, FAST_LEN, SLOW_LEN, "EMA")
    logger.info(
        f"[干跑] 最新已收盘90m bar open={float(cur[1]):.4f} close={float(cur[4]):.4f} "
        f"现价={price:.4f} ATR14={atr:.6f} ADX14={adx:.2f}"
    )
    logger.info(f"[干跑] EMA7/25(多头视角)={meta_long} 多头条件满足={ema_long_ok}")
    logger.info(f"[干跑] EMA7/25(空头视角)={meta_short} 空头条件满足={ema_short_ok}")
    entry = _entry_signal(bars)
    logger.info(f"[干跑] 当前空仓假设下的入场信号={entry}")
    for side in ("LONG", "SHORT"):
        exit_sig = _exit_signal(bars, side)
        logger.info(f"[干跑] 若持有{side}仓位，出场/反手信号={exit_sig}")


def main() -> None:
    once = "--once" in sys.argv
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logger.info(f"SNDK双均线引擎【干跑模式，不下单】| 品种={SYMBOL} 周期={PERIOD_MIN}min")
        dry_run_check()
        return
    logger.info(f"SNDK双均线实盘引擎启动 | 品种={SYMBOL} 周期={PERIOD_MIN}min once={once}")
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SNDK 90分钟双均线(EMA7/30)全自动实盘引擎 - 2026-09-23

宝贝拍板：SNDK不再接TV，交易逻辑完全搬到VPS本地。2026-09-23按宝贝发来
的真实Pine策略源码("EMA7 & EMA30 纯裸K微结构突破")逐条核对重写——
第一版曾照着文字版计划书写成EMA25/无实体过滤/无斜率确认，跟这份能
回测出+234%的真实源码有三处出入，这次全部对齐：

1. 慢线是 EMA30，不是EMA25(文字计划书写错了，源码才是权威)。
2. 开仓多一条"实体够大"过滤：|close-open| >= 0.2×ATR(bodyMulti)，
   十字星/小实体的假突破不算数。
3. 开仓多一条"快线自身斜率"确认：emaFastUp=EMA7本身在上升(比上一根
   90m bar的EMA7高)，emaFastDown同理——单纯"现价站上EMA7"不够，EMA7
   自己也得在朝这个方向走。

数据层：币安30m原始K线现场合成90m bar(3根30m=1根90m，UTC epoch对齐，
跟TradingView 90分钟图同一套锚点)。数学上跟"18根5m合成"完全等价——
OHLCV的开高低收量在同一个对齐窗口内跟用多细的子K线合成无关(High取
子K线里的最大值、Low取最小值、Open取第一根、Close取最后一根、Volume
求和，这几个运算对"3根30m"和"18根5m"给出的结果逐位相同)，只是30m
拉取的K线条数少得多，REST开销小很多，这里保留用30m，不是抄近路。

指标精度：宝贝要求"VPS算出来的EMA7/EMA30/ATR14跟TradingView误差<0.1%"。
EMA/ATR/ADX都是递归指标，warmup的历史越深，起始种子的影响衰减得越
干净、跟TV(近乎无限历史)的差距越小。这版把每次重算指标用的K线深度
从80根90m大幅拉到~1000根(现场分页拉取30m K线，SNDK这类新上市代币
历史不够1000根时自动退化用能拿到的全部)，同时把"多久重算一次"从
"每个20秒tick都重算"改成"只在90分钟bucket边界真正跨越时才重算一次"
(纯本地时间戳判断，不额外多打API)——这样重算频率(每90分钟一次)本身
就比计划书要求的"每日UTC0点校准一次"更频繁，天然覆盖了那条要求，不用
另外再起一个每日定时校准任务。20秒tick循环只做两件轻量的事：查现价
(ticker，便宜)+用上一次bucket边界重算出的缓存ATR/ADX/结构位跑止损
状态机，不会每20秒都去重新拉一遍上千根K线。

风控三层(跟计划书完全一致，这次没有改动)：2.5×ATR初始硬止损+防插针
状态机(持续击穿15分钟未收回才真止损)；浮盈1.0×ATR保本上移(entry+
0.05%)；浮盈1.5×ATR启动ADX自适应吊灯追踪止损(ADX>30→3.5×ATR /
<20→1.5×ATR / 其余2.5×ATR)，叠加20根结构位(-0.2×ATR)兜底，两者取
更靠近现价者。刻意不在交易所挂止损条件单——纯VPS内存状态机盯盘，这是
防插针机制本身的要求(挂单没法区分插针秒回和真突破)，代价是VPS进程
若挂了、仓位在恢复前没有交易所侧保护网，用独立钉钉告警部分缓解(复用
binance-gateway/gateway.py同一份WATCHDOG_DINGTALK_*环境变量)。

仓位：账户权益×98%÷现价(留2%手续费缓冲，源码这版明确要求)，交易所
杠杆锁1倍。

跟dual_momentum_live.py同一种完全独立于position_supervisor_binance.py
的隔离架构，只对SNDKUSDT动手，启动核对铁律"本地无记录的仓位绝不自动
接管"。

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
from typing import Any, Dict, List, Optional, Tuple

from binance_client import binance_client
from market_engine import merge_30m_to_period, wilder_atr, wilder_adx, bucket_open_ms

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] SndkDualMA: %(message)s",
)
logger = logging.getLogger(__name__)

# ==================== 策略参数(照Pine源码"EMA7 & EMA30 纯裸K微结构突破"逐条对齐) ====================
SYMBOL = "SNDKUSDT"
PERIOD_MIN = 90
PERIOD_MS = PERIOD_MIN * 60 * 1000
FAST_LEN = 7
SLOW_LEN = 30  # 2026-09-23修正：源码是EMA30，不是文字计划书写的EMA25
BREAKOUT_LOOKBACK = 5      # breakoutBars
BODY_MIN_ATR_MULT = 0.2    # bodyMulti：|close-open| >= 此倍数×ATR才算有效实体
STRUCT_LOOKBACK = 20
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

EXCHANGE_LEVERAGE = 1     # 交易所真实杠杆锁1倍，等同于现货满仓
EQUITY_USAGE_PCT = 0.98   # 2026-09-23：账户权益98%开仓，留2%手续费缓冲(源码要求)

DEEP_BARS_TARGET = 1000   # 目标90m bar深度，供EMA/ATR/ADX warmup(误差<0.1%要求)
RAW_PER_CALL = 1500       # 币安futures_klines单次limit上限
MIN_BARS_NEEDED = max(SLOW_LEN, STRUCT_LOOKBACK, ADX_PERIOD * 2 + 2) + BREAKOUT_LOOKBACK

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


# ==================== K线(深度分页拉取，只在90m bucket边界触发) ====================

def _fetch_90m_bars(target_bars: int = DEEP_BARS_TARGET) -> List[list]:
    """分页拉取30m原始K线现场合成90m bar，目标覆盖target_bars根已闭合
    90m bar(默认1000根，供EMA30/ADX14 warmup达到<0.1%精度要求)。SNDK这
    类新上市代币交易所历史可能不足，拉不满时用能拿到的全部，不当失败。"""
    needed_30m = target_bars * 3 + 60
    all_raw: List[list] = []
    end_time: Optional[int] = None
    attempts = 0
    while len(all_raw) < needed_30m and attempts < 6:
        attempts += 1
        try:
            kwargs: Dict[str, Any] = {"symbol": SYMBOL, "interval": "30m", "limit": RAW_PER_CALL}
            if end_time is not None:
                kwargs["endTime"] = end_time
            batch = binance_client.client.futures_klines(**kwargs)
        except Exception as e:
            logger.warning(f"深度K线拉取失败(第{attempts}批): {e}")
            break
        if not batch:
            break
        all_raw = batch + all_raw
        end_time = int(batch[0][0]) - 1
        if len(batch) < RAW_PER_CALL:
            break  # 已经拉到交易所最早的数据，没有更多了
    return merge_30m_to_period(all_raw, PERIOD_MS)


def _live_price() -> float:
    try:
        t = binance_client.client.futures_symbol_ticker(symbol=SYMBOL)
        return float(t["price"])
    except Exception as e:
        logger.warning(f"取现价失败: {e}")
        return 0.0


# ==================== EMA(本地连续递归，跟Pine ta.ema同一套SMA种子+递归写法) ====================

def _ema_last(closes: List[float], n: int) -> float:
    """closes末尾对应"当前bar"；传closes[:-1]即可拿到"上一根bar的EMA值"
    (跟Pine里emaFast[1]同一个含义)——因为种子用的是同一批最早的n个值，
    只是递归少走一步，数学上等价于同一条连续EMA序列往回退一格，不是
    从别的起点重新播种。"""
    if len(closes) < n:
        return 0.0
    seed = sum(closes[:n]) / n
    k = 2.0 / (n + 1)
    m = seed
    for v in closes[n:]:
        m = v * k + m * (1.0 - k)
    return m


# ==================== 信号判定(90分钟收盘时评估，逐条对齐Pine源码条件) ====================

def _entry_signal(bars: List[list]) -> Optional[Dict[str, Any]]:
    """最新已收盘90m bar是否满足开多/开空条件。跟Pine源码longCondition/
    shortCondition逐条对齐：快线斜率+阳阴线+站上/跌破双均线+实体过滤+
    突破前5根高低点，六个条件全部满足才算数。None=无信号。"""
    if len(bars) < MIN_BARS_NEEDED:
        return None
    closes = [float(b[4]) for b in bars]
    cur = bars[-1]
    o, c = float(cur[1]), float(cur[4])

    atr_now = wilder_atr(bars, ATR_PERIOD)
    if atr_now <= 0:
        return None

    ema_fast_now = _ema_last(closes, FAST_LEN)
    ema_fast_prev = _ema_last(closes[:-1], FAST_LEN)
    ema_slow_now = _ema_last(closes, SLOW_LEN)

    prior5 = bars[-(BREAKOUT_LOOKBACK + 1):-1]
    prior5_high = max(float(b[2]) for b in prior5)
    prior5_low = min(float(b[3]) for b in prior5)

    body_size = abs(c - o)
    is_body_valid = body_size >= atr_now * BODY_MIN_ATR_MULT
    is_bull = c > o
    is_bear = c < o
    ema_fast_up = ema_fast_now > ema_fast_prev
    ema_fast_down = ema_fast_now < ema_fast_prev

    long_ok = (
        ema_fast_up and is_bull and c > ema_fast_now and c > ema_slow_now
        and is_body_valid and c > prior5_high
    )
    short_ok = (
        ema_fast_down and is_bear and c < ema_fast_now and c < ema_slow_now
        and is_body_valid and c < prior5_low
    )
    if long_ok:
        return {"action": "LONG", "price": c, "bar_time": int(cur[0]), "atr": atr_now}
    if short_ok:
        return {"action": "SHORT", "price": c, "bar_time": int(cur[0]), "atr": atr_now}
    return None


def _exit_signal(bars: List[list], side: str) -> Optional[Dict[str, Any]]:
    """持仓方向在最新90m收盘时是否该"纯平仓"或"反手"。closeLongCondition/
    closeShortCondition跟Pine源码一致：只看是否跌破/站上双均线，不要求
    实体/突破——那两条只在判断"要不要反手"时才需要，走_entry_signal同一
    份完整六条件判定。"""
    if len(bars) < MIN_BARS_NEEDED:
        return None
    closes = [float(b[4]) for b in bars]
    cur = bars[-1]
    c = float(cur[4])
    ema_fast_now = _ema_last(closes, FAST_LEN)
    ema_slow_now = _ema_last(closes, SLOW_LEN)
    entry_sig = _entry_signal(bars)

    if side == "LONG":
        close_cond = c < ema_fast_now and c < ema_slow_now
        if not close_cond:
            return None
        if entry_sig and entry_sig["action"] == "SHORT":
            return {
                "action": "REVERSE_SHORT", "price": c, "bar_time": int(cur[0]),
                "atr": entry_sig["atr"],
            }
        return {"action": "CLOSE_ONLY", "price": c, "bar_time": int(cur[0])}
    else:
        close_cond = c > ema_fast_now and c > ema_slow_now
        if not close_cond:
            return None
        if entry_sig and entry_sig["action"] == "LONG":
            return {
                "action": "REVERSE_LONG", "price": c, "bar_time": int(cur[0]),
                "atr": entry_sig["atr"],
            }
        return {"action": "CLOSE_ONLY", "price": c, "bar_time": int(cur[0])}


# ==================== 下单 ====================

def _calc_qty(price: float) -> float:
    """本金等值(1倍不加杠杆)：qty = 账户权益×98% / 现价——98%是2026-09-23
    源码明确要求的"留2%手续费缓冲"，不是账户全部权益都拿去做名义仓位。"""
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


# ==================== 止损状态机(每个tick评估，intrabar，用缓存指标) ====================

def _evaluate_protective_stop(pos: Dict[str, Any], price: float, atr_now: float,
                               adx_now: float, struct_low: Optional[float],
                               struct_high: Optional[float], now_ts: float) -> Tuple[bool, str]:
    """返回(是否该市价平仓, 原因)。硬止损阶段做防插针(持续击穿超15分钟
    才真平)；保本/追踪阶段一碰即触发(源码只在初始硬止损这层用TV内部
    固定止损做兜底，后两档是VPS这边"立刻市价全平")。"""
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

        if struct_low is not None and struct_high is not None:
            if side == "LONG":
                struct_level = struct_low - STRUCT_BUFFER_ATR * atr_now
                trail_final = max(chandelier, struct_level)
            else:
                struct_level = struct_high + STRUCT_BUFFER_ATR * atr_now
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
    # 天然实现"5分钟内收回就取消"的效果。
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

def _should_refresh_indicators(state: Dict[str, Any], now_ms: int) -> bool:
    """纯本地时间戳判断是否跨过了一个新的90m bucket边界，不额外打API
    去"看一眼"有没有新bar——90m bar固定在UTC epoch整90分钟边界收盘，
    可以直接算。"""
    cur_bucket = bucket_open_ms(now_ms, PERIOD_MS)
    last_bar = state.get("last_bar_time")
    if last_bar is None:
        return True
    return cur_bucket > last_bar


def _refresh_indicators_and_act(state: Dict[str, Any]) -> Dict[str, Any]:
    bars = _fetch_90m_bars()
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
            sig = _exit_signal(bars, pos["side"])
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
            sig = _entry_signal(bars)
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
        should_close, reason = _evaluate_protective_stop(
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
    bars = _fetch_90m_bars()
    logger.info(f"[干跑] 拉到{len(bars)}根已闭合90m bar(需要至少{MIN_BARS_NEEDED}根，目标{DEEP_BARS_TARGET}根)")
    if len(bars) < MIN_BARS_NEEDED:
        logger.warning("[干跑] bar数不够，指标不可信，先别启用实盘")
        return
    closes = [float(b[4]) for b in bars]
    cur = bars[-1]
    price = _live_price()
    atr = wilder_atr(bars, ATR_PERIOD)
    adx = wilder_adx(bars, ADX_PERIOD)
    ema_fast_now = _ema_last(closes, FAST_LEN)
    ema_fast_prev = _ema_last(closes[:-1], FAST_LEN)
    ema_slow_now = _ema_last(closes, SLOW_LEN)
    logger.info(
        f"[干跑] 最新已收盘90m bar open={float(cur[1]):.4f} close={float(cur[4]):.4f} "
        f"现价={price:.4f} ATR14={atr:.6f} ADX14={adx:.2f}"
    )
    logger.info(
        f"[干跑] EMA7now={ema_fast_now:.4f} EMA7prev={ema_fast_prev:.4f} "
        f"(斜率{'向上' if ema_fast_now > ema_fast_prev else '向下' if ema_fast_now < ema_fast_prev else '持平'}) "
        f"EMA30now={ema_slow_now:.4f}"
    )
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

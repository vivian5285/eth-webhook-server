#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
heikin_ashi_trend 实盘执行引擎(币安C账户) - 2026-09-23

宝贝拍板：CoinW实盘验证过的heikin_ashi_trend(擂台138笔纸面交易、38.5%
胜率、盈亏比2.25、最大回撤8.85ATR加权单位——候选里回撤最低、盈亏比
最高的一个)挪到币安C账户跑，CoinW那边腾出来改跑vwap_mean_reversion(15m)。
选heikin_ashi_trend(HA平滑K线+连续同色进场)而不是dual_momentum/
cross_momentum，是因为dual_momentum已经在B/E账户实盘跑着、cross_momentum
跟它本质同一套动量族逻辑，heikin_ashi_trend信号来源完全不同，能拿到真正
独立的alpha。

架构：完全独立于position_supervisor_binance.py的TV pipeline——不走
webhook_parser/active_binance_symbols白名单，直连binance_client(client层)
下单+挂止损，自己维护精简本地状态，不会被雷达/哨兵/综合硬止损碰到。
"验证的是什么，实盘跑的就是什么"——擂台回测是HA自己的2.0×ATR止损+纯
信号离场，不含雷达跟涨/保本这些会让实盘行为偏离回测的逻辑。跟
dual_momentum_live.py/sndk_dual_ma_live.py同一个设计哲学。

信号逻辑(heikin_ashi_strategy.py)是从CoinW那份原样复制来的，逐字节一致，
不做任何参数调整——CoinW版本自己又是从擂台系统strategy_engine/strategies/
heikin_ashi_trend.py原样复制的。三份代码理应永远保持逐字节相同，除非
宝贝明确要求修改。

品种范围：擂台heikin_ashi_trend验证过的全部27个品种——币安C账户当前是
空白账户(SNDK双均线引擎已暂停)，不存在"跟同账户其它系统抢品种"的问题，
不像CoinW那版需要排除position_supervisor_coinw在管的品种。用全部27个
才是对"验证的是什么，实盘跑的就是什么"最忠实的复刻。

仓位公式：照抄CoinW版本、也是擂台strategy_engine/position_sizing.py::
compute_qty的真实公式(risk_capital=equity×20%, notional_cap=
risk_capital×5.0参考杠杆×tier1权重0.245，再跟risk_capital/stop_dist
取更小值)——名义仓位权重要跟138笔纸面回测同源。

杠杆：跟仓位权重是独立的两件事，只决定保证金占用效率，不影响名义敞口/
PnL对价格变动的敏感度。每笔开仓动态算杠杆：在"强平价必须比这笔止损距离
再宽50%缓冲"(用币安该品种真实的maintenance margin ratio，不是CoinW版本
那个全品种通用的猜测值)和"币安该品种leverage bracket第一档上限"之间取
更小值，确保不会出现"交易所先于自己止损强平"的情况。

止损：币安没有"挂在仓位上"的止损类型，用place_stop_market_order挂
STOP_MARKET条件单(reduceOnly)，平仓时要记得撤掉。

跑法：
  常驻:  venv/bin/python heikin_ashi_live.py
  单轮:  venv/bin/python heikin_ashi_live.py --once
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
from typing import Any, Dict, List, Optional

from binance_client import binance_client
from heikin_ashi_strategy import generate_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] HeikinAshiLive: %(message)s",
)
logger = logging.getLogger(__name__)

# ==================== 品种配置 ====================
# 擂台heikin_ashi_trend验证过的全部27个品种，逐一核实过币安USDT-M永续
# 都有对应合约+leverage bracket(2026-09-23查询)。
TRADED_SYMBOLS = [
    "1000PEPEUSDT", "ANTHROPICUSDT", "ASMLUSDT", "BCHUSDT", "BNBUSDT",
    "BTCUSDT", "DOGEUSDT", "ENAUSDT", "ETHUSDT", "GSUSDT", "HYPEUSDT",
    "LINKUSDT", "LITEUSDT", "METAUSDT", "MUUSDT", "OPENAIUSDT", "PAXGUSDT",
    "SKHYNIXUSDT", "SNDKUSDT", "SOLUSDT", "TSLAUSDT", "UNIUSDT", "XAUUSDT",
    "XLMUSDT", "XMRUSDT", "XRPUSDT", "ZECUSDT",
]

TIMEFRAME = "4h"  # 跟擂台heikin_ashi_trend一致
KLINES_LIMIT = 200  # 给ATR/HA序列足够暖机长度，比理论最小值(streak_len+atr_len+10=27)宽裕很多

# 照搬strategy_engine/position_sizing.py::compute_qty的真实公式常量，
# 让实盘名义仓位权重跟纸面模拟同源。
SIZING_RISK_PCT = 0.20
SIZING_REF_LEVERAGE = 5.0  # 只是公式里用来算notional_cap的参考值，不是下单真实杠杆
SIZING_TIER1_MULT = 0.245  # heikin_ashi_trend固定tier=1(中)

# 2026-09-23查询：币安USDT-M永续每个品种leverage bracket第一档
# (initialLeverage, maintMarginRatio)，只在这27个品种范围内用。
EXCHANGE_LEVERAGE_INFO = {
    "1000PEPEUSDT": (75, 0.0065), "ANTHROPICUSDT": (20, 0.025),
    "ASMLUSDT": (20, 0.025), "BCHUSDT": (75, 0.005), "BNBUSDT": (75, 0.005),
    "BTCUSDT": (150, 0.004), "DOGEUSDT": (75, 0.0065), "ENAUSDT": (75, 0.01),
    "ETHUSDT": (150, 0.004), "GSUSDT": (20, 0.025), "HYPEUSDT": (75, 0.01),
    "LINKUSDT": (75, 0.005), "LITEUSDT": (25, 0.02), "METAUSDT": (20, 0.025),
    "MUUSDT": (50, 0.01), "OPENAIUSDT": (20, 0.025), "PAXGUSDT": (75, 0.01),
    "SKHYNIXUSDT": (50, 0.01), "SNDKUSDT": (75, 0.0065), "SOLUSDT": (100, 0.005),
    "TSLAUSDT": (25, 0.02), "UNIUSDT": (75, 0.006), "XAUUSDT": (100, 0.005),
    "XLMUSDT": (75, 0.01), "XMRUSDT": (75, 0.01), "XRPUSDT": (100, 0.005),
    "ZECUSDT": (75, 0.01),
}
FALLBACK_LEVERAGE_INFO = (20, 0.025)  # 万一品种不在表里(理论不该发生)的保守兜底

# 强平价安全垫：要求"强平距离" ≥ SAFETY_MULT × "止损距离"，防止杠杆拉太高
# 导致交易所先于本引擎自己的止损强平仓位。
LIQUIDATION_SAFETY_MULT = 1.5
MIN_LEVERAGE = 3.0

# 2026-09-23新增：组合层面仓位上限，照搬擂台strategy_engine/position_
# sizing.py::clamp_qty_to_portfolio_cap(commit 2c9d287)——那次是宝贝实测
# 从cross_momentum持仓页面抓到"无限子弹"问题：heikin_ashi_trend这类跑满
# 27个品种独立触发的策略，行情一致时会同时开很多笔，全库最严重的策略
# 名义敞口到过净值10.89倍，真实账户扛不住、也会被交易所保证金不足拒单。
# 这个上限只在CoinW/擂台那次审计范围内验证过，币安这份是新移植，同样
# 适用同一个担忧(2026-09-23干跑一次就实测过7/27个品种同时有信号)。按
# "这个引擎账上已经占用了多少名义仓位"把新仓位等比缩小，额度用满就是
# 这笔开不了(等同真实账户保证金不足)，不是主动跳过信号。
MAX_TOTAL_NOTIONAL_MULT = 3.0

TICK_INTERVAL_SEC = 300  # 5分钟一轮，跟擂台/CoinW版本一致

STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "heikin_ashi_live_state.json"
)

# ==================== 钉钉告警 ====================
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
        "text": {"content": f"【HeikinAshi币安实盘】{text}"},
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


# ==================== K线获取 ====================

def _get_bars(symbol: str, limit: int = KLINES_LIMIT) -> List[dict]:
    raw = binance_client.fetch_klines(symbol, interval=TIMEFRAME, limit=limit)
    return [{"t": r[0], "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in raw]


# ==================== 下单相关 ====================

def _calc_qty_and_leverage(symbol: str, price: float, stop_loss: float):
    """qty：照搬position_sizing.py::compute_qty公式。leverage：独立选择，
    只决定保证金效率——在"强平价必须比止损距离再宽50%"和"该品种交易所
    leverage bracket上限"之间取更小值。返回(qty, leverage)，任何一项
    算不出来就返回(0.0, 0.0)。"""
    if price <= 0:
        return 0.0, 0.0
    equity = binance_client.get_total_equity("USDT")
    if equity <= 0:
        logger.error(f"[{symbol}] 权益查询失败或为0，跳过本次开仓")
        return 0.0, 0.0

    risk_capital = equity * SIZING_RISK_PCT
    notional_cap = risk_capital * SIZING_REF_LEVERAGE
    qty = notional_cap / price
    stop_dist = abs(price - stop_loss)
    if stop_dist > 1e-9:
        qty = min(qty, risk_capital / stop_dist)
    qty *= SIZING_TIER1_MULT
    qty = binance_client.format_quantity(qty, symbol)

    stop_dist_frac = stop_dist / price if price > 0 else 0.0
    exch_max_lev, mmr = EXCHANGE_LEVERAGE_INFO.get(symbol, FALLBACK_LEVERAGE_INFO)
    if stop_dist_frac > 1e-9:
        safe_lev = 1.0 / (stop_dist_frac * LIQUIDATION_SAFETY_MULT + mmr)
    else:
        safe_lev = float(exch_max_lev)
    leverage = max(MIN_LEVERAGE, min(float(exch_max_lev), safe_lev))
    leverage = float(int(leverage))  # 交易所要求整数杠杆

    notional = qty * price
    logger.info(
        f"[{symbol}] 仓位计算 equity={equity:.2f} notional≈{notional:.2f}"
        f"({notional / equity * 100:.1f}%权益) qty={qty} "
        f"leverage={leverage:.0f}(强平安全值={safe_lev:.1f} 交易所上限={exch_max_lev}) "
        f"margin≈{notional / leverage:.2f}"
    )
    return qty, leverage


def _clamp_qty_to_portfolio_cap(qty: float, price: float, existing_notional: float, equity: float) -> float:
    if price <= 0 or qty <= 0:
        return 0.0
    cap = equity * MAX_TOTAL_NOTIONAL_MULT
    remaining = cap - existing_notional
    if remaining <= 0:
        return 0.0
    desired = qty * price
    return qty if desired <= remaining else remaining / price


def _open_position(symbol: str, signal: Dict[str, Any], state: Dict[str, Any]) -> None:
    side = signal["action"]  # LONG / SHORT
    price = float(signal["price"])
    atr = float(signal["atr"])

    qty, leverage = _calc_qty_and_leverage(symbol, price, float(signal["stop_loss"]))
    if qty <= 0 or leverage <= 0:
        return

    equity = binance_client.get_total_equity("USDT")
    existing_notional = sum(
        float(r.get("entry_price") or 0) * float(r.get("qty") or 0)
        for r in state.values() if isinstance(r, dict)
    )
    clamped_qty = _clamp_qty_to_portfolio_cap(qty, price, existing_notional, equity)
    clamped_qty = binance_client.format_quantity(clamped_qty, symbol)
    if clamped_qty <= 0:
        logger.info(
            f"⚠️ [{symbol}] 组合名义仓位已达上限(已占用${existing_notional:.2f}/"
            f"净值×{MAX_TOTAL_NOTIONAL_MULT:.0f}=${equity * MAX_TOTAL_NOTIONAL_MULT:.2f})，这笔开不了，跳过"
        )
        return
    if clamped_qty < qty:
        logger.info(f"[{symbol}] 组合额度不足，仓位从{qty}缩小到{clamped_qty}")
    qty = clamped_qty

    lev_result = binance_client.set_leverage(symbol, leverage=leverage)
    if lev_result is None:
        logger.error(f"[{symbol}] 设置杠杆{leverage}x失败，放弃开仓(避免用未知杠杆下单)")
        return

    order = binance_client.place_market_order(side, qty, symbol=symbol, reduce_only=False)
    if not order:
        logger.error(f"[{symbol}] 市价开仓失败: {signal}")
        _alert(f"⚠️ [{symbol}] 开仓下单失败 {side} qty={qty}，请人工核查")
        return

    # avgPrice在市价单REST同步响应里经常是0，改成下单后查真实持仓entryPrice。
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
            f"({(fill_price / price - 1) * 100:+.2f}%)，止损按真实成交价重锚"
        )
    stop_loss = round(fill_price - direction * atr * 2.0, 8)

    state[symbol] = {
        "side": side, "entry_price": fill_price, "qty": qty, "leverage": leverage,
        "atr_at_entry": atr, "stop_loss": stop_loss, "sl_order_id": None,
        "last_acted_bar_time": signal.get("bar_time"), "status": "entry_pending_sl",
    }
    _save_state(state)
    logger.info(f"🚀 [{symbol}] 开仓成交 {side} qty={qty} @{fill_price} lev={leverage}x | {signal.get('reason')}")

    close_side = "SELL" if side == "LONG" else "BUY"
    sl_order = binance_client.place_stop_market_order(
        close_side, stop_loss, symbol=symbol, quantity=None,
        client_order_id=f"HAsl{int(time.time()) % 100000000}",
    )
    if sl_order:
        state[symbol]["sl_order_id"] = str(sl_order.get("orderId") or sl_order.get("algoId") or "") or None
        state[symbol]["status"] = "open"
        _save_state(state)
        logger.info(f"🛡️ [{symbol}] 止损已挂 @{stop_loss}")
    else:
        logger.error(f"🚨 [{symbol}] 止损挂单失败！仓位当前无保护，需要人工立刻核查并补挂 stop@{stop_loss}")
        _alert(f"🚨 [{symbol}] 止损挂单失败！仓位无保护，需要人工立刻核查并补挂 stop@{stop_loss}")

    _alert(f"🚀 开仓 {side} {symbol} qty={qty} @{fill_price:.4f} lev={leverage}x 止损{stop_loss:.4f}\n{signal.get('reason')}")


def _close_position(symbol: str, signal: Dict[str, Any], state: Dict[str, Any]) -> None:
    rec = state.get(symbol) or {}
    state[symbol] = {**rec, "status": "closing"}
    _save_state(state)

    oid = rec.get("sl_order_id")
    if oid:
        try:
            binance_client.cancel_algo_order(symbol=symbol, algo_id=int(oid))
        except Exception:
            try:
                binance_client.client.futures_cancel_order(symbol=symbol, orderId=int(oid))
            except Exception:
                pass

    try:
        positions = binance_client.client.futures_position_information(symbol=symbol)
        live_amt = 0.0
        for p in positions:
            live_amt = float(p.get("positionAmt") or 0)
    except Exception as e:
        logger.error(f"[{symbol}] 离场前查真实仓位失败: {e}，暂不平仓，下一轮重试")
        return

    if live_amt == 0:
        logger.info(f"[{symbol}] 查到仓位已经是0(可能已被止损打平)，直接清状态")
        state.pop(symbol, None)
        _save_state(state)
        return

    close_side = "SELL" if live_amt > 0 else "BUY"
    qty = binance_client.format_quantity(abs(live_amt), symbol)
    order = binance_client.place_market_order(close_side, qty, symbol=symbol, reduce_only=True)
    if order:
        logger.info(f"✅ [{symbol}] 离场平仓成交 qty={qty} | {signal.get('reason')}")
        _alert(f"✅ [{symbol}] 平仓 qty={qty} | {signal.get('reason')}")
        state.pop(symbol, None)
        _save_state(state)
    else:
        logger.error(f"🚨 [{symbol}] 离场市价平仓失败！需要人工立刻核查")
        _alert(f"🚨 [{symbol}] 离场市价平仓失败！需要人工立刻核查")


# ==================== 启动核对 ====================

def _reconcile_on_start(state: Dict[str, Any]) -> Dict[str, Any]:
    """本地状态 vs 交易所真实持仓核对——铁律：本地没记录、交易所却有
    仓位的品种，绝不自动接管，只报错跳过，等人工确认。"""
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
            logger.warning(f"[{symbol}] 本地记录有仓，交易所已空仓(大概率已被止损打平)，清本地状态")
            state.pop(symbol, None)
        elif not rec and live_amt != 0:
            logger.error(
                f"🚨 [{symbol}] 交易所有仓位({live_amt})但本地无记录——不自动接管！"
                f"可能是宝贝自己手工开的或者其它来源，人工确认后再处理，本引擎这个品种先跳过管理"
            )
            _alert(f"🚨 [{symbol}] 交易所有仓位({live_amt})但本地无记录，未接管，请人工核查")
        elif rec and live_amt != 0 and rec.get("status") == "entry_pending_sl":
            logger.error(f"🚨 [{symbol}] 检测到上次崩溃发生在'已开仓、止损还没挂上'窗口！紧急补挂止损@{rec.get('stop_loss')}")
            close_side = "SELL" if rec.get("side") == "LONG" else "BUY"
            sl_order = binance_client.place_stop_market_order(
                close_side, float(rec["stop_loss"]), symbol=symbol, quantity=None,
                client_order_id=f"HAsl{int(time.time()) % 100000000}",
            )
            if sl_order:
                rec["sl_order_id"] = str(sl_order.get("orderId") or "") or None
                rec["status"] = "open"
                logger.info(f"🛡️ [{symbol}] 紧急止损补挂成功 @{rec['stop_loss']}")
            else:
                logger.error(f"🚨🚨 [{symbol}] 紧急止损补挂仍然失败！需要人工立刻介入")
                _alert(f"🚨🚨 [{symbol}] 紧急止损补挂仍然失败！需要人工立刻介入")
    _save_state(state)
    return state


# ==================== 主循环 ====================

def _tick_symbol(symbol: str, state: Dict[str, Any]) -> None:
    bars = _get_bars(symbol)
    if not bars:
        logger.warning(f"[{symbol}] 拉K线失败或为空")
        return

    rec = state.get(symbol)
    position = {"side": rec["side"]} if rec else None

    signal = generate_signal({"base": bars}, position=position)
    if not signal:
        return

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

    for symbol in TRADED_SYMBOLS:
        try:
            _tick_symbol(symbol, state)
        except Exception as e:
            logger.error(f"[{symbol}] 本轮处理异常: {e}", exc_info=True)


def dry_run_check() -> None:
    """只读体检：对全部27个品种拉K线跑一遍信号判定，打印结果，完全不
    下单、不碰状态文件——部署后先跑这个确认没有品种会立刻误开仓，再
    放开实盘循环。"""
    for symbol in TRADED_SYMBOLS:
        try:
            bars = _get_bars(symbol)
        except Exception as e:
            logger.warning(f"[{symbol}] 拉K线异常: {e}")
            continue
        if not bars:
            logger.warning(f"[{symbol}] 拉不到K线")
            continue
        sig = generate_signal({"base": bars}, position=None)
        logger.info(f"[干跑][{symbol}] bars={len(bars)} 当前空仓假设下信号={sig}")


def main() -> None:
    once = "--once" in sys.argv
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logger.info(f"heikin_ashi_trend(币安C)【干跑模式，不下单】| 目标品种数={len(TRADED_SYMBOLS)}")
        dry_run_check()
        return
    logger.info(f"heikin_ashi_trend(币安C)实盘引擎启动 | 目标品种数={len(TRADED_SYMBOLS)} | once={once}")
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

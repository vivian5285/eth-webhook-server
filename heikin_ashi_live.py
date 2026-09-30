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
PnL对价格变动的敏感度。新仓统一使用全仓，并使用币安该品种
leverage bracket第一档上限，用更少保证金承载相同的策略名义仓位。

止损：币安没有"挂在仓位上"的止损类型，用place_stop_market_order挂
STOP_MARKET条件单(reduceOnly)，平仓时要记得撤掉。

跑法：
  常驻:  venv/bin/python heikin_ashi_live.py
  单轮:  venv/bin/python heikin_ashi_live.py --once
"""
from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import hmac
import json
import logging
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

from binance_client import binance_client, is_orders_query_failed
from heikin_ashi_strategy import generate_signal as generate_legacy_ha_signal
from asset_class_combo_strategy import (
    PREVIOUS_STRATEGY_VERSIONS,
    STRATEGY_VERSION,
    combine_entries,
    entry_signals,
    exit_signal as generate_sleeve_exit,
    generate_signal as generate_combo_signal,
    sleeve_config,
)
from strategy_engine import portfolio_guard
from virtual_netting import (
    execution_plan,
    protective_stop,
    risk_rows as virtual_risk_rows,
    side_for_qty,
    sleeve_net_qty,
)

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

TOKENIZED_STOCK_SYMBOLS = {
    "SNDKUSDT", "OPENAIUSDT", "ANTHROPICUSDT", "GSUSDT", "MUUSDT",
    "LITEUSDT", "TSLAUSDT", "METAUSDT", "SKHYNIXUSDT", "ASMLUSDT",
}
GOLD_SYMBOLS = {"XAUUSDT", "PAXGUSDT"}

# 2026-09-26: 当天试过把27个品种拆成B/C/E三个不重叠的9品种子集，跑了几
# 小时后宝贝看到实际效果(有的账户开了SKHYNIX有的没开)明确要求撤销——三个
# 账户还是要用同一份完整品种表，不要有差异。撤销拆分。
# 2026-09-27: 原来这里还留着ZECUSDT整体冻结——宝贝发现擂台ttm_squeeze
# 开了ZEC、实盘没开，查出来是这个"按品种"冻结把ttm_squeeze也一起挡了，
# 跟当初"ZEC本身不是问题品种，只是hma_trend不适合"这个结论对不上。ZEC
# 的排除已经挪到asset_class_combo_strategy.py::HMA_TREND_EXCLUDED_SYMBOLS
# 按sleeve精确排除了，这里不用再整体冻结。
NO_NEW_ENTRY_SYMBOLS = set()

TIMEFRAME = "4h"  # 跟擂台heikin_ashi_trend一致
KLINES_LIMIT = 200  # 给ATR/HA序列足够暖机长度，比理论最小值(streak_len+atr_len+10=27)宽裕很多
TIMEFRAME_MS = 4 * 60 * 60 * 1000

# 2026-09-27: hma_trend/ttm_squeeze/heikin_ashi_trend_ema7_25三个sleeve
# 都是纯"信号反转退出+开仓时定的固定ATR止损"，没有任何止损跟踪——擂台
# 复查真实持仓发现residual_momentum/keltner_channel这类同门派策略里，
# 赢了50%+的仓位止损还钉在开仓时的老位置，"止损风险"因此虚高触发冻结，
# 根因是浮盈没有回收保护，不是风控太保守。这里只加"浮盈到1倍原始风险
# 就把止损一次性上移到保本+覆盖手续费"，不是持续跟踪止损——不影响策略
# 自己"等信号反转才出场"这个核心逻辑(让利润奔跑的空间完全不变)，只是
# 防止反转信号还没触发前的等待期里，一个已经赚了1R的仓位被行情倒灌
# 回真实亏损。只对没有自己止盈梯度的sleeve生效，一次性只收紧不放松，
# 复用protective_stop()本来就有的"只收紧不放松"兜底。
BREAKEVEN_LOCK_R_MULT = float(os.getenv("HA_BREAKEVEN_LOCK_R_MULT", "1.0"))
BREAKEVEN_LOCK_FEE_BUFFER_PCT = 0.0015
BREAKEVEN_LOCK_ELIGIBLE_SLEEVES = {
    "hma_trend", "ttm_squeeze", "heikin_ashi_trend_ema7_25",
    # 2026-09-29补：keltner_channel/mtf_ema_macd_cci前一天接入实盘时漏加
    # 进这份名单，一直在裸跑没有保本锁保护；turtle_breakout是新加的。
    "keltner_channel", "mtf_ema_macd_cci", "turtle_breakout",
    # 2026-09-30新增chanlun_pivot：跟上面这几个一样，加入实盘的当次就
    # 直接补进这份名单，不再重演keltner/mtf那次"裸跑一天"的教训。
    "chanlun_pivot",
}


def _apply_breakeven_lock(
    sleeves: Dict[str, Dict[str, Any]], last_price: float,
) -> bool:
    """浮盈够1R的sleeve止损一次性上移到保本+手续费缓冲，原地修改sleeves。
    返回是否有任何sleeve被改动，供调用方决定要不要触发一次净额重算。"""
    if last_price <= 0:
        return False
    changed = False
    for name, sleeve in sleeves.items():
        if name not in BREAKEVEN_LOCK_ELIGIBLE_SLEEVES:
            continue
        side = str(sleeve.get("side") or "").upper()
        entry = float(sleeve.get("entry_price") or 0.0)
        stop = float(sleeve.get("stop_loss") or 0.0)
        if entry <= 0 or stop <= 0 or side not in ("LONG", "SHORT"):
            continue
        risk = abs(entry - stop)
        if risk <= 0:
            continue
        d = 1.0 if side == "LONG" else -1.0
        r_multiple = d * (last_price - entry) / risk
        if r_multiple < BREAKEVEN_LOCK_R_MULT:
            continue
        locked_stop = entry + d * entry * BREAKEVEN_LOCK_FEE_BUFFER_PCT
        already_locked = (
            stop >= locked_stop - 1e-9 if side == "LONG" else stop <= locked_stop + 1e-9
        )
        if already_locked:
            continue
        sleeve["stop_loss"] = locked_stop
        changed = True
    return changed


# 2026-09-30：利润回吐刹车(giveback brake)——移植自老雷达系统
# (breath_profiles.py/radar_reentry_mixin.py::_maybe_tighten_on_profit_
# giveback，2026-08-31首次校准，当时用ETH/BNB/ZEC/XAU/XMR/PAXG/GS/BCH/
# ASML在各自"真实生产周期"上验证过，ETH/ZEC明确测出负收益、故意排除)。
# 那套是老TV系统专属，这个引擎从没接过。今天(09-30)B/C/E三账户同一天
# 联动止损(ANTHROPIC/BTC/DOGE/SOL/1000PEPE/ENA)，宝贝问"要不要加雷达
# 移动保本止损，但得看量能/趋势强度决定呼吸空间"——查证发现这套机制
# 已经做过、只是躺在没在用的老代码里，而且老校准跟这个引擎没关系(老的
# 是6h/8h/150m等自定义周期验证的，这个引擎统一4H)。用
# scratch_backtest_giveback_live_combo.py在真实4H周期+EMA50中性入场+
# 这个引擎实际的"一次性保本锁"基线上重新回测了今天联动止损的品种+老
# 已验证品种，14个品种里12个明确正收益(多数在收紧档0.6R/45%/60%最优)，
# BTC基本打平(+0.001R，不启用不浪费复杂度)，ANTHROPIC明确负收益
# (-1.0R，比什么都不做还差，排除)。跟breakeven锁一样只朝有利方向
# 棘轮、互不冲突，谁锁得更紧生效谁的；对已经在跑的老仓位也安全——
# initial_risk字段缺失时会用当前止损距离一次性兜底再冻结，不需要
# 重开仓位才能生效。
GIVEBACK_BRAKE_CONFIG: Dict[str, Dict[str, float]] = {
    "DOGEUSDT": {"min_peak_r": 0.4, "trigger_frac": 0.35, "retain_frac": 0.55},
    "XMRUSDT": {"min_peak_r": 0.4, "trigger_frac": 0.35, "retain_frac": 0.55},
    "LINKUSDT": {"min_peak_r": 0.5, "trigger_frac": 0.40, "retain_frac": 0.58},
    "BCHUSDT": {"min_peak_r": 0.5, "trigger_frac": 0.40, "retain_frac": 0.58},
    "SOLUSDT": {"min_peak_r": 0.6, "trigger_frac": 0.45, "retain_frac": 0.60},
    "1000PEPEUSDT": {"min_peak_r": 0.6, "trigger_frac": 0.45, "retain_frac": 0.60},
    "ENAUSDT": {"min_peak_r": 0.6, "trigger_frac": 0.45, "retain_frac": 0.60},
    "UNIUSDT": {"min_peak_r": 0.6, "trigger_frac": 0.45, "retain_frac": 0.60},
    "XLMUSDT": {"min_peak_r": 0.6, "trigger_frac": 0.45, "retain_frac": 0.60},
    "TSLAUSDT": {"min_peak_r": 0.6, "trigger_frac": 0.45, "retain_frac": 0.60},
    "BNBUSDT": {"min_peak_r": 0.6, "trigger_frac": 0.45, "retain_frac": 0.60},
    "ASMLUSDT": {"min_peak_r": 0.6, "trigger_frac": 0.45, "retain_frac": 0.60},
    # BTCUSDT: 回测+0.001R基本打平，故意不启用，省得白增加一层复杂度。
    # ANTHROPICUSDT: 回测-1.008R明确负收益，故意不启用，跟ETH/ZEC同一
    # 类"这个品种深回调后常常还会继续走，提前顶紧止损反而砍自己趋势"。
    # ETHUSDT/ZECUSDT: 沿用老校准结论，不重复验证，故意不启用。
}


def _apply_giveback_brake(sleeves: Dict[str, Dict[str, Any]], last_price: float, symbol: str) -> bool:
    """峰值浮盈回吐到一定比例后，把止损顶到锁住峰值的一部分，只朝有利
    方向棘轮。反应"已经回吐了多少"这个既成事实，不预测未来走势——
    2026-08-31老校准时试过量能+EMA+支撑压力位三合一的"预测型"方案，
    真实回测边际是负的(误伤真突破前的蓄力)，已经否决，不要重新引入。"""
    cfg = GIVEBACK_BRAKE_CONFIG.get(symbol)
    if not cfg or last_price <= 0:
        return False
    changed = False
    for name, sleeve in sleeves.items():
        side = str(sleeve.get("side") or "").upper()
        entry = float(sleeve.get("entry_price") or 0.0)
        stop = float(sleeve.get("stop_loss") or 0.0)
        if entry <= 0 or stop <= 0 or side not in ("LONG", "SHORT"):
            continue
        risk = float(sleeve.get("initial_risk") or 0.0)
        if risk <= 0:
            # 老仓位第一次跑到这条新逻辑，没有initial_risk字段——用当前
            # 止损距离一次性兜底估算，之后写回去冻结住，不再跟着后续被
            # 收紧的stop_loss重新计算(否则分母会越滚越小、刹车越来越敏感)。
            risk = abs(entry - stop)
            if risk <= 0:
                continue
            sleeve["initial_risk"] = risk
            changed = True
        best = float(sleeve.get("best_price") or 0.0) or entry
        best = max(best, last_price) if side == "LONG" else min(best, last_price)
        if abs(best - float(sleeve.get("best_price") or 0.0)) > 1e-12:
            sleeve["best_price"] = best
            changed = True

        peak_profit = (best - entry) if side == "LONG" else (entry - best)
        if peak_profit <= 0 or peak_profit / risk < cfg["min_peak_r"]:
            continue
        current_profit = (last_price - entry) if side == "LONG" else (entry - last_price)
        giveback = peak_profit - current_profit
        if giveback <= 0 or giveback / peak_profit < cfg["trigger_frac"]:
            continue
        retain_profit = peak_profit * cfg["retain_frac"]
        floor_px = entry + retain_profit if side == "LONG" else entry - retain_profit
        improved = floor_px > stop if side == "LONG" else floor_px < stop
        if not improved:
            continue
        sleeve["stop_loss"] = floor_px
        changed = True
    return changed


# 2026-09-30: 改回3.0，恢复给每个sleeve应有的独立预算，但portfolio_guard
# 这次重写过status_for_regime——只在calm/normal/stress三档生效，crisis
# 和drawdown_reduce/crisis这两类"真出事了"的熔断维持绝对值不被放大，
# 方向集中度闸门也改用不受这个值影响的单sleeve基准算。同时保留codex
# 新增的_physical_gross_allows硬闸(读交易所真实仓位，MAX_TOTAL_NOTIONAL_
# MULT=6.6兜底)完全不动，双重保险。
LIVE_SLEEVE_COUNT = 3.0

# 照搬strategy_engine/position_sizing.py::compute_qty的真实公式常量，
# 让实盘名义仓位权重跟纸面模拟同源。
SIZING_RISK_PCT = 0.20
SIZING_REF_LEVERAGE = 5.0  # 只是公式里用来算notional_cap的参考值，不是下单真实杠杆
SIZING_TIER1_MULT = 0.245  # heikin_ashi_trend固定tier=1(中)

# 2026-09-30新增：宝贝发现C账户因为权益较小，chanlun_pivot这类新sleeve
# 分到LINK/BCH等品种上算出来的目标名义金额(如$14-19)低于交易所$20的
# MIN_NOTIONAL门槛，被format_entry_quantity(保守向下取整)直接判成0，
# 白白错过一次本该开仓的机会。只在"确实有信号、只是差一点点够不到最小
# 门槛"时才向上补到交易所最小可下单量，不是无脑放大——目标名义金额低于
# 最小名义金额的MIN_NOTIONAL_BUMP_FLOOR_FRAC时，说明这个品种/权重组合
# 在当前账户权益下本来就不该开这么小的仓，不去补，维持原样跳过。
# 2026-09-30当天二次调整：50%这条线太紧，真实卡住两个案例(E账户BCH
# 反手开仓腿0.03手≈门槛42%、C账户LINK新开仓)都刚好卡在40%上下，被
# 白白跳过——降到30%，能救回这两类"确实不算离谱、只是差一点"的仓位，
# 同时仍然挡住"目标金额只占门槛一小部分"(比如5%-10%)这种明显不该被
# 强行放大的信号强度太弱的情况。
MIN_NOTIONAL_BUMP_FLOOR_FRAC = 0.3

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

# 2026-09-23新增：组合层面仓位上限，照搬擂台strategy_engine/position_
# sizing.py::clamp_qty_to_portfolio_cap(commit 2c9d287)——那次是宝贝实测
# 从cross_momentum持仓页面抓到"无限子弹"问题：heikin_ashi_trend这类跑满
# 27个品种独立触发的策略，行情一致时会同时开很多笔，全库最严重的策略
# 名义敞口到过净值10.89倍，真实账户扛不住、也会被交易所保证金不足拒单。
# 这个上限只在CoinW/擂台那次审计范围内验证过，币安这份是新移植，同样
# 适用同一个担忧(2026-09-23干跑一次就实测过7/27个品种同时有信号)。按
# "这个引擎账上已经占用了多少名义仓位"把新仓位等比缩小，额度用满就是
# 这笔开不了(等同真实账户保证金不足)，不是主动跳过信号。
MAX_TOTAL_NOTIONAL_MULT = 6.6

TICK_INTERVAL_SEC = 300  # 5分钟一轮，跟擂台/CoinW版本一致

# 2026-09-30: 擂台镜像闸门。查出实盘最近7天B/C/E已实现-$50/-$14/-$73，
# 同期擂台同一组sleeve/品种/权重+14.8%——根因是擂台每个sleeve有自己的
# $1000账户和portfolio_guard，预算满(risk_budget_full)或当日亏损冻结时
# 擂台拒绝开仓，这笔就不进擂台成绩；实盘预算独立、照样开了(ENA 09-29
# 08:03、1000PEPE/UNI 09-28 16:00、DOGE 09-28 12:00四笔核实过，全部是
# 擂台拒绝、实盘开仓、止损出局)。擂台排行榜是"被筛过的子集"，实盘没复制
# 这层筛选。改成：新sleeve开仓必须擂台同名策略此刻真有同品种同方向、
# 同一根(或上一根)4h K线开出的仓位才跟；平仓/止损/减仓完全不受影响。
# 擂台读不到(网络/接口异常)一律按"没确认"处理，只挡新开仓(fail-closed)。
ARENA_MIRROR_ENABLED = os.getenv("HA_ARENA_MIRROR", "1") != "0"
ARENA_POSITIONS_URL = os.getenv(
    "HA_ARENA_POSITIONS_URL",
    "http://187.53.133.188:8878/api/roster/compare/{strategy}/positions?status=open&limit=500",
)
# 实盘sleeve名 → 擂台策略名(实盘"heikin_ashi_trend_ema7_25"用的是
# STOCK_HA_PARAMS={}的原版逻辑，对应擂台的heikin_ashi_trend)
ARENA_STRATEGY_ALIAS = {"heikin_ashi_trend_ema7_25": "heikin_ashi_trend"}
ARENA_MIRROR_MAX_LAG_MS = 4 * 60 * 60 * 1000  # 允许擂台晚一根4h K线开出
ARENA_CACHE_TTL_SEC = 60
_arena_cache: Dict[str, Any] = {"ts": 0.0, "index": None}
_arena_skip_logged: set = set()

# 正常信号执行先尝试post-only挂单；硬止损仍由交易所STOP_MARKET负责。
# 总等待时间故意保持短窗，避免为省几bp而错过4h趋势信号。
MAKER_ENTRY_WAIT_SEC = float(os.getenv("HA_MAKER_ENTRY_WAIT_SEC", "12"))
MAKER_EXIT_WAIT_SEC = float(os.getenv("HA_MAKER_EXIT_WAIT_SEC", "18"))
MAKER_REPRICE_ATTEMPTS = max(1, int(os.getenv("HA_MAKER_REPRICE_ATTEMPTS", "2")))
MAKER_POLL_SEC = max(0.5, float(os.getenv("HA_MAKER_POLL_SEC", "1.0")))
ORDER_TERMINAL_STATUSES = {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}
NET_EXECUTION_VERIFY_ATTEMPTS = 5

STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "heikin_ashi_live_state.json"
)
COOLDOWN_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "heikin_ashi_live_cooldowns.json"
)
RISK_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "asset_class_combo_risk_state.json"
)
LOCK_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "heikin_ashi_live.lock"
)
_PROCESS_LOCK = None


def _acquire_process_lock() -> bool:
    """Ensure exactly one live order writer exists for this account directory."""
    global _PROCESS_LOCK
    handle = open(LOCK_FILE, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        logger.error(f"已有实盘引擎持有独占锁 {LOCK_FILE}，本进程拒绝启动")
        return False
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    _PROCESS_LOCK = handle
    return True

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
        "text": {"content": f"【资产组合币安实盘】{text}"},
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


def _load_cooldowns() -> Dict[str, int]:
    if not os.path.exists(COOLDOWN_FILE):
        return {}
    try:
        with open(COOLDOWN_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {str(k): int(v) for k, v in raw.items() if v is not None}
    except Exception as e:
        logger.error(f"离场冷却文件读取失败，保守禁止本轮新开仓: {e}")
        return {symbol: 2**63 - 1 for symbol in TRADED_SYMBOLS}


def _save_cooldowns(cooldowns: Dict[str, int]) -> None:
    tmp = COOLDOWN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cooldowns, f, ensure_ascii=False, indent=2)
    os.replace(tmp, COOLDOWN_FILE)


def _record_exit_bar(cooldowns: Dict[str, int], symbol: str, bar_time: Any) -> None:
    if bar_time is None:
        return
    bar_time = int(bar_time)
    cooldowns[symbol] = max(int(cooldowns.get(symbol, 0)), bar_time)
    _save_cooldowns(cooldowns)


def _latest_closed_bar_open(cutoff_ms: int) -> int:
    return max(0, (int(cutoff_ms) // TIMEFRAME_MS - 1) * TIMEFRAME_MS)


# ==================== K线获取 ====================

def _get_bars(
    symbol: str, limit: int = KLINES_LIMIT, cutoff_ms: Optional[int] = None,
    interval: str = TIMEFRAME,
) -> List[dict]:
    """只返回同一轮扫描开始前已经封口的K线，避免盘中信号和跨4h边界漂移。"""
    cutoff_ms = int(cutoff_ms if cutoff_ms is not None else time.time() * 1000)
    raw = binance_client.fetch_klines(symbol, interval=interval, limit=limit + 2)
    closed = [r for r in raw if len(r) > 6 and int(r[6]) < cutoff_ms]
    return [
        {"t": r[0], "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])}
        for r in closed[-limit:]
    ]


def _asset_class(symbol: str) -> str:
    symbol = str(symbol or "").upper()
    if symbol in TOKENIZED_STOCK_SYMBOLS:
        return "stocks"
    if symbol in GOLD_SYMBOLS:
        return "gold"
    return "crypto"


def _refresh_risk_context(cutoff_ms: int) -> Optional[Dict[str, Any]]:
    equity = binance_client.get_total_equity("USDT")
    if equity <= 0:
        logger.error("账户权益不可读，本轮禁止所有新开仓")
        return None
    today = time.strftime("%Y-%m-%d", time.gmtime())
    raw: Dict[str, Any] = {}
    try:
        with open(RISK_STATE_FILE, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning(f"风险基线文件不可读，按当前权益重新建立: {e}")
    peak = max(float(raw.get("peak_equity") or equity), equity)
    if raw.get("day") == today:
        daily_start = float(raw.get("daily_start_equity") or equity)
    else:
        daily_start = equity
    snapshot = {
        "day": today,
        "peak_equity": peak,
        "daily_start_equity": daily_start,
    }
    tmp = RISK_STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, RISK_STATE_FILE)

    btc_bars = _get_bars("BTCUSDT", limit=100, cutoff_ms=cutoff_ms)
    portfolio_regime = portfolio_guard.classify_regime(btc_bars)
    return {
        "equity": equity,
        "peak_equity": peak,
        "daily_start_equity": daily_start,
        "portfolio_regime": portfolio_regime,
    }


def _live_open_rows(state: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    try:
        positions = binance_client.client.futures_position_information()
    except Exception as e:
        logger.error(f"真实持仓不可读，组合风控fail-closed: {e}")
        return None
    out: List[Dict[str, Any]] = []
    for row in positions or []:
        amount = float(row.get("positionAmt") or 0)
        if abs(amount) < 1e-12:
            continue
        symbol = str(row.get("symbol") or "").upper()
        rec = state.get(symbol) if isinstance(state.get(symbol), dict) else {}
        mark = float(row.get("markPrice") or row.get("entryPrice") or 0)
        out.append({
            "symbol": symbol,
            "side": "LONG" if amount > 0 else "SHORT",
            "entry": mark,
            "qty": abs(amount),
            "stop": float((rec or {}).get("stop_loss") or 0),
        })
    return out


# ==================== 下单相关 ====================

def _calc_qty_and_leverage(
    symbol: str, price: float, stop_loss: float, size_scale: float = 1.0,
):
    """qty：照搬position_sizing.py::compute_qty公式。leverage：独立选择，
    只决定保证金效率，直接使用该品种交易所leverage bracket第一档上限。
    返回(qty, leverage)，任何一项算不出来就返回(0.0, 0.0)。"""
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
    qty *= SIZING_TIER1_MULT * max(0.0, min(1.0, float(size_scale)))
    target_notional = qty * price
    qty = binance_client.format_entry_quantity(qty, symbol, price=price)
    bumped_to_minimum = False
    if target_notional > 0:
        # 2026-09-30二次修复：原来只在qty<=0(format_entry_quantity判定
        # 完全不够$20门槛)时才补——但真实复现过LINKUSDT反复用同一个
        # qty=1.38下单失败：这个1.38是sizing那一刻的价格算出来"刚好"
        # 过了$20门槛(format_entry_quantity判定合法，qty>0)，但从算出
        # 到真的挂单之间价格漂移，实际下单时又跌回$20以下，被交易所拒。
        # 原来的判断只看"这一刻算出来是不是0"，看不到"虽然不是0，但离
        # 门槛太近，扛不住正常漂移"这种情况。改成只要qty低于(带10%缓冲
        # 的)最小可下单量就补齐，不再要求qty必须先是0——min_qty本身已经
        # 内置10%缓冲，这里不用重复留缓冲，直接比较。
        min_qty = binance_client.minimum_entry_quantity(symbol, price=price)
        if (
            min_qty > 0 and 0 <= qty < min_qty
            and target_notional >= min_qty * price * MIN_NOTIONAL_BUMP_FLOOR_FRAC
        ):
            qty = min_qty
            bumped_to_minimum = True

    exch_max_lev, _mmr = EXCHANGE_LEVERAGE_INFO.get(symbol, FALLBACK_LEVERAGE_INFO)
    leverage = float(int(exch_max_lev))  # 交易所要求整数杠杆

    notional = qty * price
    logger.info(
        f"[{symbol}] 仓位计算 equity={equity:.2f} target≈{target_notional:.2f} "
        f"executable≈{notional:.2f}"
        f"({notional / equity * 100:.1f}%权益) qty={qty} "
        f"leverage={leverage:.0f}(交易所第一档上限={exch_max_lev}) "
        f"margin≈{notional / leverage:.2f}"
        + ("[补到交易所最小可下单量]" if bumped_to_minimum else "")
    )
    if qty <= 0:
        logger.info(f"[{symbol}] 目标数量低于交易所最小数量/名义金额，跳过新仓")
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


def _query_order(symbol: str, order_id: Any) -> Optional[Dict[str, Any]]:
    try:
        return binance_client.client.futures_get_order(
            symbol=symbol, orderId=order_id,
        )
    except Exception as e:
        logger.warning(f"[{symbol}] maker查单失败 orderId={order_id}: {e}")
        return None


def _query_order_by_client_id(
    symbol: str, client_order_id: str,
) -> Optional[Dict[str, Any]]:
    if not client_order_id:
        return None
    try:
        return binance_client.client.futures_get_order(
            symbol=symbol, origClientOrderId=client_order_id,
        )
    except Exception as e:
        logger.warning(
            f"[{symbol}] 按幂等标签查单失败 clientOrderId={client_order_id}: {e}"
        )
        return None


def _cancel_maker_and_confirm(
    symbol: str, order_id: Any, latest: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """撤单后必须读到终态；读不到就禁止补市价，防止双倍成交。"""
    canceled = binance_client.cancel_order(symbol=symbol, order_id=order_id)
    if isinstance(canceled, dict):
        latest = canceled
        if str(canceled.get("status") or "").upper() in ORDER_TERMINAL_STATUSES:
            return canceled
    for _ in range(8):
        time.sleep(0.5)
        current = _query_order(symbol, order_id)
        if current is not None:
            latest = current
            if str(current.get("status") or "").upper() in ORDER_TERMINAL_STATUSES:
                return current
    logger.error(
        f"🚨 [{symbol}] maker撤单终态无法确认 orderId={order_id}，"
        "禁止补市价以防重复成交"
    )
    return None


def _maker_then_market(
    side: str, quantity: float, symbol: str, *, reduce_only: bool,
    total_wait_sec: float, intent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Post-only短窗挂单，确认撤单终态后才允许对剩余量补市价。"""
    requested = binance_client.format_quantity(quantity, symbol)
    remaining = requested
    maker_filled = 0.0
    attempt_wait = max(0.05, float(total_wait_sec) / MAKER_REPRICE_ATTEMPTS)

    for attempt in range(MAKER_REPRICE_ATTEMPTS):
        remaining = binance_client.format_quantity(
            max(0.0, requested - maker_filled), symbol,
        )
        if remaining <= 0:
            break
        bid, ask = binance_client.get_best_bid_ask(symbol)
        tick = binance_client.get_tick_size(symbol)
        # 2026-09-26: 摸盘口那一刻的bid/ask到真正挂单之间有网络延迟，
        # 行情稍微一动就会让"挂在对手价上"变成"实际会吃到对手方"，被
        # 交易所按-5022拒单(XMR过去7天占了71/95次，远高于其他品种，
        # 大概率是它盘口天然更薄/跳动更频繁)。让1个tick(相对价格是
        # 万分之几，可忽略)给行情留缓冲，大幅降低这个概率，不改变
        # 撤单重试兜底逻辑本身。
        maker_price = (bid - tick) if str(side).upper() in ("BUY", "LONG") else (ask + tick)
        if maker_price <= 0:
            logger.warning(f"[{symbol}] maker盘口不可读，转入安全市价兜底")
            break

        coid = (
            f"VN{str(intent_id)[:22]}L{attempt}"
            if intent_id
            else (
                f"HA{'X' if reduce_only else 'E'}"
                f"{int(time.time() * 1000) % 1000000000}{attempt}{symbol[:5]}"
            )
        )[:36]
        order = binance_client.place_limit_order(
            side, remaining, maker_price, symbol=symbol,
            reduce_only=reduce_only, client_order_id=coid,
            time_in_force="GTX",
        )
        if not order and intent_id:
            order = _query_order_by_client_id(symbol, coid)
        if not order:
            logger.info(f"[{symbol}] maker第{attempt + 1}次未挂成，重新取价")
            continue

        order_id = order.get("orderId") if isinstance(order, dict) else None
        if not order_id:
            logger.error(f"🚨 [{symbol}] maker响应没有orderId，禁止补市价: {order}")
            return {
                "safe": False, "accepted": False, "maker_filled": maker_filled,
                "market_order": None, "pending_order_id": None,
            }

        latest = order
        deadline = time.monotonic() + attempt_wait
        while time.monotonic() < deadline:
            current = _query_order(symbol, order_id)
            if current is not None:
                latest = current
                if str(current.get("status") or "").upper() in ORDER_TERMINAL_STATUSES:
                    break
            time.sleep(MAKER_POLL_SEC)

        status = str((latest or {}).get("status") or "").upper()
        if status not in ORDER_TERMINAL_STATUSES:
            latest = _cancel_maker_and_confirm(symbol, order_id, latest)
            if latest is None:
                return {
                    "safe": False, "accepted": maker_filled > 0,
                    "maker_filled": maker_filled, "market_order": None,
                    "pending_order_id": order_id,
                }

        filled = float((latest or {}).get("executedQty") or 0)
        maker_filled += min(remaining, max(0.0, filled))
        logger.info(
            f"[{symbol}] maker第{attempt + 1}次终态="
            f"{(latest or {}).get('status')} 成交={filled}/{remaining}"
        )

    remaining = binance_client.format_quantity(
        max(0.0, requested - maker_filled), symbol,
    )
    if remaining <= 0:
        return {
            "safe": True, "accepted": True, "maker_filled": maker_filled,
            "market_order": None, "pending_order_id": None,
        }

    market_coid = f"VN{str(intent_id)[:28]}M"[:36] if intent_id else None
    market_order = binance_client.place_market_order(
        side, remaining, symbol=symbol, reduce_only=reduce_only,
        client_order_id=market_coid,
    )
    if not market_order and market_coid:
        recovered = _query_order_by_client_id(symbol, market_coid)
        if recovered is not None:
            market_order = recovered
    return {
        "safe": True, "accepted": bool(market_order) or maker_filled > 0,
        "maker_filled": maker_filled, "market_order": market_order,
        "pending_order_id": None,
    }


def _open_position(
    symbol: str, signal: Dict[str, Any], state: Dict[str, Any],
    bars: List[dict], risk_context: Optional[Dict[str, Any]],
) -> None:
    side = signal["action"]  # LONG / SHORT
    price = float(signal["price"])
    atr = float(signal["atr"])
    size_scale = float(signal.get("position_scale") or 1.0)

    qty, leverage = _calc_qty_and_leverage(
        symbol, price, float(signal["stop_loss"]), size_scale=size_scale,
    )
    if qty <= 0 or leverage <= 0:
        return

    if risk_context is None:
        logger.error(f"[{symbol}] 组合风险上下文不可读，禁止新开仓")
        return
    open_rows = _portfolio_open_rows(state)
    if open_rows is None:
        return
    decision = portfolio_guard.evaluate_entry(
        symbol=symbol,
        side=side,
        desired_qty=qty,
        price=price,
        stop_price=float(signal["stop_loss"]),
        equity=float(risk_context["equity"]),
        open_rows=open_rows,
        bars=bars,
        peak_equity=float(risk_context["peak_equity"]),
        daily_start_equity=float(risk_context["daily_start_equity"]),
        minimum_regime=str(risk_context["portfolio_regime"]),
        budget_scale=LIVE_SLEEVE_COUNT,
        direction_relative_check=True,
    )
    allowed_qty = binance_client.format_entry_quantity(
        decision.allowed_qty, symbol, price=price,
    )
    logger.info(
        f"[{symbol}] 组合风控 regime={decision.regime} cap={decision.gross_cap_mult:.1f}x "
        f"stopHeat={decision.stop_heat_cap_pct:.1%} cluster={decision.cluster_cap_frac:.0%} "
        f"qty={qty}->{allowed_qty} reason={decision.reason}"
    )
    if allowed_qty <= 0:
        return
    qty = allowed_qty

    # 新仓统一用全仓；已有的逐仓持仓等策略自然离场后，下次开仓再切换。
    margin_result = binance_client.set_margin_type(symbol, margin_type="CROSSED")
    if margin_result is None:
        logger.error(f"[{symbol}] 设置全仓失败，放弃开仓(避免用未知保证金模式下单)")
        return

    lev_result = binance_client.set_leverage(symbol, leverage=leverage)
    if lev_result is None:
        logger.error(f"[{symbol}] 设置杠杆{leverage}x失败，放弃开仓(避免用未知杠杆下单)")
        return

    execution = _maker_then_market(
        side, qty, symbol, reduce_only=False,
        total_wait_sec=MAKER_ENTRY_WAIT_SEC,
    )
    if not execution.get("safe"):
        logger.error(f"🚨 [{symbol}] maker开仓状态不确定，已禁止补市价: {execution}")
        _alert(f"🚨 [{symbol}] maker开仓撤单状态不确定，未补市价，请立刻核查")
        return
    if not execution.get("accepted"):
        logger.error(f"[{symbol}] maker+市价开仓失败: {signal}")
        _alert(f"⚠️ [{symbol}] 开仓下单失败 {side} qty={qty}，请人工核查")
        return

    # avgPrice在市价单REST同步响应里经常是0，改成下单后查真实持仓entryPrice。
    fill_price = 0.0
    live_qty = qty
    for _ in range(5):
        try:
            positions = binance_client.client.futures_position_information(symbol=symbol)
            for p in positions:
                fill_price = abs(float(p.get("entryPrice") or 0))
                amount = abs(float(p.get("positionAmt") or 0))
                if amount > 0:
                    live_qty = amount
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
    sleeve_entries = list(signal.get("sleeve_entries") or [])
    sleeves: Dict[str, Dict[str, Any]] = {}
    total_weight = sum(float(item.get("weight") or 0) for item in sleeve_entries)
    for item in sleeve_entries:
        sleeve_signal = item.get("signal") or {}
        weight = float(item.get("weight") or 0)
        if weight <= 0 or total_weight <= 0:
            continue
        signal_price = float(sleeve_signal.get("price") or price)
        signal_stop = float(sleeve_signal.get("stop_loss") or signal["stop_loss"])
        stop_distance = abs(signal_price - signal_stop)
        sleeve_stop = round(fill_price - direction * stop_distance, 8)
        sleeves[str(item["name"])] = {
            "side": side,
            "qty": binance_client.format_quantity(live_qty * weight / total_weight, symbol),
            "entry_price": fill_price,
            "entry_bar_time": sleeve_signal.get("bar_time"),
            "stop_loss": sleeve_stop,
            "weight": weight,
        }
    if sleeves:
        sleeve_stops = [float(item["stop_loss"]) for item in sleeves.values()]
        stop_loss = max(sleeve_stops) if side == "LONG" else min(sleeve_stops)
    else:
        stop_distance = abs(price - float(signal["stop_loss"]))
        stop_loss = round(fill_price - direction * stop_distance, 8)

    state[symbol] = {
        "side": side, "entry_price": fill_price, "qty": live_qty, "leverage": leverage,
        "atr_at_entry": atr, "stop_loss": stop_loss, "sl_order_id": None,
        "last_acted_bar_time": signal.get("bar_time"), "status": "entry_pending_sl",
        "strategy": STRATEGY_VERSION, "asset_class": _asset_class(symbol),
        "sleeves": sleeves,
    }
    _save_state(state)
    logger.info(f"🚀 [{symbol}] 开仓成交 {side} qty={qty} @{fill_price} lev={leverage}x | {signal.get('reason')}")

    close_side = "SELL" if side == "LONG" else "BUY"
    sl_order = binance_client.place_stop_market_order(
        close_side, stop_loss, symbol=symbol, quantity=None,
        client_order_id=f"HAsl{int(time.time()) % 100000000}",
        working_type="MARK_PRICE", price_protect=False,
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


def _query_live_amount(symbol: str) -> Optional[float]:
    try:
        positions = binance_client.client.futures_position_information(symbol=symbol)
        return sum(float(p.get("positionAmt") or 0) for p in positions)
    except Exception as e:
        logger.error(f"[{symbol}] 查询真实仓位失败: {e}")
        return None


def _physical_gross_allows(
    symbol: str, target_signed_qty: float, cap_mult: float,
    *, expected_current: Optional[float] = None,
) -> bool:
    """Fail closed on new physical exposure using a fresh exchange snapshot."""
    try:
        positions = binance_client.client.futures_position_information()
        rows = []
        symbol_amounts = []
        for position in positions:
            amount = float(position.get("positionAmt") or 0.0)
            if abs(amount) < 1e-12:
                continue
            mark = float(position.get("markPrice") or 0.0)
            if not math.isfinite(mark) or mark <= 0:
                raise ValueError("position mark price unavailable")
            position_symbol = str(position.get("symbol") or "").upper()
            rows.append({
                "symbol": position_symbol,
                "entry": mark,
                "qty": abs(amount),
            })
            if position_symbol == symbol:
                symbol_amounts.append(amount)
        if len(symbol_amounts) > 1:
            raise ValueError("multiple exchange position sides for one-way symbol")
        current = sum(symbol_amounts)
        if expected_current is not None and not _qty_matches(current, expected_current, symbol):
            raise ValueError("exchange position changed during risk check")
        if symbol_amounts:
            mark_price = next(row["entry"] for row in rows if row["symbol"] == symbol)
        else:
            mark_price = float(binance_client.client.futures_mark_price(symbol=symbol)["markPrice"])
        projected = portfolio_guard.projected_account_gross(
            rows, symbol, target_signed_qty, mark_price,
        )
        gross = sum(row["entry"] * row["qty"] for row in rows)
        pure_reduction = target_signed_qty == 0 or (
            current * target_signed_qty > 0
            and abs(target_signed_qty) <= abs(current) + 1e-12
        )
        if pure_reduction and projected <= gross + 1e-8:
            return True
        equity = float(binance_client.get_total_equity("USDT") or 0.0)
        limit = equity * min(MAX_TOTAL_NOTIONAL_MULT, float(cap_mult))
        if equity <= 0 or not math.isfinite(limit):
            raise ValueError("account equity unavailable")
        if projected > limit + 1e-8:
            logger.warning(
                f"[{symbol}] 真实敞口硬闸拒绝增仓 gross={gross:.2f} "
                f"projected={projected:.2f} limit={limit:.2f}"
            )
            return False
        return True
    except Exception as exc:
        logger.error(f"[{symbol}] 真实敞口无法核实，拒绝增加风险: {exc}")
        return False


def _one_way_mode_confirmed() -> bool:
    try:
        row = binance_client.client.futures_get_position_mode()
    except Exception as e:
        logger.error(f"账户持仓模式不可读，fail-closed: {e}")
        return False
    dual = row.get("dualSidePosition") if isinstance(row, dict) else None
    if dual is False or str(dual).strip().lower() == "false":
        return True
    logger.error(f"🚨 账户不是已确认的单向持仓模式: {row}")
    _alert("🚨 账户持仓模式不是已确认的单向模式，组合执行已冻结")
    return False


def _qty_matches(left: float, right: float, symbol: str) -> bool:
    if abs(float(left or 0.0) - float(right or 0.0)) < 1e-12:
        return True
    try:
        return binance_client.format_quantity(
            abs(float(left or 0.0) - float(right or 0.0)), symbol,
        ) <= 0
    except Exception:
        return False


def _state_signed_qty(rec: Dict[str, Any]) -> float:
    qty = abs(float(rec.get("qty") or 0.0))
    side = str(rec.get("side") or "").upper()
    if side == "LONG":
        return qty
    if side == "SHORT":
        return -qty
    return 0.0


def _portfolio_open_rows(
    state: Dict[str, Any],
) -> Optional[List[Dict[str, Any]]]:
    """Risk on virtual gross sleeves, plus any legacy/unmanaged live position."""
    live_rows = _live_open_rows(state)
    if live_rows is None:
        return None
    virtual_symbols = {
        symbol for symbol, rec in state.items()
        if isinstance(rec, dict) and rec.get("strategy") == STRATEGY_VERSION
        and not rec.get("transition")
        and rec.get("status") not in {
            "reconciliation_required", "unprotected_emergency_failed",
        }
    }
    rows = [row for row in live_rows if row.get("symbol") not in virtual_symbols]
    rows.extend(list(virtual_risk_rows(state, strategy_version=STRATEGY_VERSION)))
    return rows


def _migrate_previous_combo_records(
    state: Dict[str, Any], live_amounts: Dict[str, float], cutoff_ms: int,
) -> bool:
    changed = False
    for symbol, rec in list(state.items()):
        if not isinstance(rec, dict):
            continue
        if rec.get("strategy") not in PREVIOUS_STRATEGY_VERSIONS:
            continue
        sleeves = copy.deepcopy(rec.get("sleeves") or {})
        live_amt = float(live_amounts.get(symbol, 0.0))
        virtual_net = sleeve_net_qty(sleeves)
        if sleeves and live_amt and not _qty_matches(virtual_net, live_amt, symbol):
            aligned = [
                item for item in sleeves.values()
                if (str(item.get("side") or "").upper() == "LONG") == (live_amt > 0)
            ]
            if len(aligned) == len(sleeves):
                aligned_total = sum(abs(float(item.get("qty") or 0.0)) for item in aligned)
                if aligned_total > 0:
                    scale = abs(live_amt) / aligned_total
                    for item in aligned:
                        item["qty"] = binance_client.format_quantity(
                            abs(float(item.get("qty") or 0.0)) * scale, symbol,
                        )
                    residual = live_amt - sleeve_net_qty(sleeves)
                    if abs(residual) > 1e-12:
                        largest = max(
                            aligned, key=lambda item: abs(float(item.get("qty") or 0.0))
                        )
                        largest["qty"] = binance_client.format_quantity(
                            max(0.0, abs(float(largest.get("qty") or 0.0)) + (
                                residual if live_amt > 0 else -residual
                            )),
                            symbol,
                        )
                virtual_net = sleeve_net_qty(sleeves)
        if not sleeves or (live_amt and not _qty_matches(virtual_net, live_amt, symbol)):
            logger.error(
                f"[{symbol}] v2账本无法无损迁移 virtual={virtual_net} live={live_amt}，"
                "保持旧版本并冻结自动加仓"
            )
            continue
        rec.update({
            "strategy": STRATEGY_VERSION,
            "virtual_netting": True,
            "sleeves": sleeves,
            "sleeve_cooldowns": rec.get("sleeve_cooldowns") or {},
            "side": side_for_qty(live_amt),
            "qty": abs(live_amt),
            "status": "open" if live_amt else "virtual_flat",
            # Existing v2 sleeves remain live, but no new v3 sleeve may be added
            # from the same already-closed bar that triggered deployment.
            "entry_activation_after_bar": _latest_closed_bar_open(cutoff_ms),
        })
        changed = True
        logger.info(f"[{symbol}] 已从v2无交易迁移到{STRATEGY_VERSION}")
    if changed:
        _save_state(state)
    return changed


def _write_transition(
    symbol: str,
    state: Dict[str, Any],
    candidate_sleeves: Dict[str, Dict[str, Any]],
    candidate_sleeve_cooldowns: Dict[str, int],
    *,
    bar_time: Any,
    reason: str,
    leverage: float,
) -> str:
    rec = copy.deepcopy(state.get(symbol) or {})
    transition_id = uuid.uuid4().hex[:18]
    previous_sleeves = copy.deepcopy(rec.get("sleeves") or {})
    rec.update({
        "strategy": STRATEGY_VERSION,
        "asset_class": _asset_class(symbol),
        "virtual_netting": True,
        "status": "transition_pending",
        "transition": {
            "id": transition_id,
            "created_at": time.time(),
            "bar_time": bar_time,
            "reason": str(reason or "virtual_rebalance"),
            "leverage": float(leverage),
            "previous_sleeves": previous_sleeves,
            "candidate_sleeves": copy.deepcopy(candidate_sleeves),
            "previous_sleeve_cooldowns": copy.deepcopy(
                rec.get("sleeve_cooldowns") or {}
            ),
            "candidate_sleeve_cooldowns": copy.deepcopy(
                candidate_sleeve_cooldowns
            ),
            "previous_net_qty": sleeve_net_qty(previous_sleeves),
            "target_net_qty": sleeve_net_qty(candidate_sleeves),
        },
    })
    state[symbol] = rec
    _save_state(state)
    return transition_id


def _query_until_amount(
    symbol: str, expected: float, attempts: int = NET_EXECUTION_VERIFY_ATTEMPTS,
) -> Optional[float]:
    latest = None
    for attempt in range(max(1, attempts)):
        if attempt:
            time.sleep(0.75)
        latest = _query_live_amount(symbol)
        if latest is not None and _qty_matches(latest, expected, symbol):
            return latest
    return latest


def _execute_net_target(
    symbol: str,
    target_signed_qty: float,
    transition_id: str,
    leverage: float,
    max_gross_mult: float = MAX_TOTAL_NOTIONAL_MULT,
) -> bool:
    """Execute deterministic one-way deltas, verifying exchange truth each leg."""
    for step_no in range(4):
        current = _query_live_amount(symbol)
        if current is None:
            return False
        if _qty_matches(current, target_signed_qty, symbol):
            return True
        plan = execution_plan(current, target_signed_qty)
        if not plan:
            return True
        step = plan[0]
        qty = binance_client.format_quantity(step["qty"], symbol)
        if qty <= 0:
            return _qty_matches(current, target_signed_qty, symbol)
        if not step["reduce_only"]:
            # 2026-09-30真实复现(E账户BCHUSDT反手：+0.092多→-0.03空)：这里的
            # qty是净额执行自己算出来的"开仓腿"步长，不经过_calc_qty_and_
            # leverage那条路(那条路已经补了最小名义金额)，一样会算出低于
            # 交易所$20门槛的量(比如-0.03手BCH≈$9)，被-4164拒单3次后判定
            # "净额执行没有可核实进展"，整个品种被冻结、账户后续每轮都在
            # 重复报同一个错——不是新出现的bug分支，是同一类问题在另一条
            # 代码路径下的复现，补法完全一致：只在"确实要开仓、只是差一点
            # 够不到最小门槛"时才向上补，reduce_only的减仓腿不受影响(交易所
            # 本身对纯减仓单就不检查最小名义金额，见-4164错误信息原文)。
            # 2026-09-30三次修复：第一版这里直接打futures_mark_price这个
            # 独立REST调用，真实复现过它偶尔悄悄失败(exception被吞掉，
            # mark_price退到0.0，整段补量逻辑被跳过而不留任何日志)，
            # 导致LINKUSDT反复用原始的1.38(没补量)下单、反复撞-4164——
            # 换成get_current_price(WS缓存优先，REST限频兜底，取不到也
            # 会退到稍旧的缓存价而不是直接判0)，同时补上失败时的日志，
            # 不再悄悄跳过。
            mark_price = binance_client.get_current_price(symbol)
            if not mark_price or mark_price <= 0:
                logger.warning(f"[{symbol}] 净额执行开仓腿补量检查取不到现价，本次不补量")
            else:
                min_qty = binance_client.minimum_entry_quantity(symbol, price=mark_price)
                if 0 < qty < min_qty and qty >= min_qty * MIN_NOTIONAL_BUMP_FLOOR_FRAC:
                    logger.info(
                        f"[{symbol}] 净额执行开仓腿qty={qty}低于交易所最小名义"
                        f"金额，补到{min_qty}"
                    )
                    qty = min_qty
            if not _physical_gross_allows(
                symbol, target_signed_qty, max_gross_mult,
                expected_current=current,
            ):
                return False
            # 2026-09-26晚复现：SKHYNIXUSDT已有的time_series_momentum遗留仓位
            # 还在场，heikin_ashi_trend这轮又想给同一品种加一条新sleeve——这
            # 步不是"从0开仓"，是"给已有仓位加码"，交易所对已有仓位/挂单的
            # 品种拒绝改保证金模式(code=-4067)，之前这里不分场景一律尝试设
            # CROSSED，设失败就return False，导致净额执行整轮判定失败、状态
            # 被打上reconciliation_required、本轮直接停止扫描其余全部品种——
            # 一个品种卡住能拖死整个账户。只有current==0(真正从空仓开始)才
            # 需要设保证金模式；已有仓位时模式早就定了，不用也不能再设。
            if abs(current) < 1e-12:
                margin_result = binance_client.set_margin_type(symbol, margin_type="CROSSED")
                if margin_result is None:
                    # 2026-09-29真实复现(E账户UNIUSDT，09-28 19:11-19:59
                    # 连续6次撞见，账户空转近50分钟)：上面那条09-26修复
                    # 只排除了"已有持仓"这一种-4067成因，但交易所拒绝改
                    # 保证金模式的真实条件是"该品种当前有持仓**或**有挂单"
                    # ——current==0确认了真的没有持仓，但上一轮遗留的止损/
                    # 算法单可能还没清干净，同样会触发-4067。既然current
                    # 已经确认为0，此刻残留的任何挂单必然是孤儿单，撤掉
                    # 是安全的；撤完重试一次再放弃，不再第一次失败就直接
                    # return False冻结整个账户当轮扫描。
                    binance_client.cancel_all_open_orders(symbol)
                    margin_result = binance_client.set_margin_type(symbol, margin_type="CROSSED")
                if margin_result is None:
                    return False
            if binance_client.set_leverage(symbol, leverage=leverage) is None:
                return False
        intent = f"{transition_id}{step_no}{'R' if step['reduce_only'] else 'O'}"
        execution = _maker_then_market(
            step["side"], qty, symbol,
            reduce_only=bool(step["reduce_only"]),
            total_wait_sec=(
                MAKER_EXIT_WAIT_SEC if step["reduce_only"] else MAKER_ENTRY_WAIT_SEC
            ),
            intent_id=intent,
        )
        expected_after = (
            0.0 if step["kind"] in {"close", "close_for_reverse"}
            else target_signed_qty
        )
        actual = _query_until_amount(symbol, expected_after)
        if actual is not None and _qty_matches(actual, expected_after, symbol):
            continue
        if not execution.get("safe"):
            logger.error(
                f"[{symbol}] 净额执行终态不确定 intent={intent} actual={actual}"
            )
            return False
        if actual is None or _qty_matches(actual, current, symbol):
            logger.error(
                f"[{symbol}] 净额执行没有可核实进展 intent={intent} actual={actual}"
            )
            return False
    final = _query_live_amount(symbol)
    return final is not None and _qty_matches(final, target_signed_qty, symbol)


def _emergency_flatten(symbol: str, reason: str, intent_id: str) -> bool:
    live_amt = _query_live_amount(symbol)
    if live_amt is None:
        return False
    if abs(live_amt) < 1e-12:
        return True
    side = "SELL" if live_amt > 0 else "BUY"
    order = binance_client.place_market_order(
        side,
        abs(live_amt),
        symbol=symbol,
        reduce_only=True,
        emergency=True,
        client_order_id=f"VNE{intent_id}"[:36],
    )
    flat = _query_until_amount(symbol, 0.0)
    ok = flat is not None and _qty_matches(flat, 0.0, symbol)
    logger.error(
        f"🚨 [{symbol}] 紧急平仓 reason={reason} order={bool(order)} confirmed={ok}"
    )
    _alert(f"🚨 [{symbol}] {reason}，已执行紧急平仓，确认归零={ok}")
    return ok


def _cancel_recorded_stop(symbol: str, rec: Dict[str, Any]) -> None:
    oid = rec.get("sl_order_id")
    if not oid:
        return
    try:
        if binance_client.cancel_algo_order(symbol=symbol, algo_id=int(oid)):
            return
    except (TypeError, ValueError):
        pass
    try:
        binance_client.client.futures_cancel_order(symbol=symbol, orderId=int(oid))
    except Exception as e:
        logger.warning(f"[{symbol}] 仓位已平，但旧止损撤单未确认(id={oid}): {e}")


def _finish_close(
    symbol: str, signal: Dict[str, Any], state: Dict[str, Any],
    cooldowns: Dict[str, int], rec: Dict[str, Any],
) -> None:
    _cancel_recorded_stop(symbol, rec)
    _record_exit_bar(cooldowns, symbol, signal.get("bar_time"))
    state.pop(symbol, None)
    _save_state(state)


def _close_position(
    symbol: str, signal: Dict[str, Any], state: Dict[str, Any],
    cooldowns: Dict[str, int],
) -> None:
    rec = state.get(symbol) or {}
    state[symbol] = {**rec, "status": "closing"}
    _save_state(state)

    # 保护单必须保留到确认真实仓位归零。查询或市价平仓失败时仍有硬止损兜底。
    live_amt = _query_live_amount(symbol)
    if live_amt is None:
        logger.error(f"[{symbol}] 离场前仓位不可读，止损保持不动，下一轮重试")
        return

    if live_amt == 0:
        logger.info(f"[{symbol}] 查到仓位已经是0(可能已被止损打平)，直接清状态")
        _finish_close(symbol, signal, state, cooldowns, rec)
        return

    close_side = "SELL" if live_amt > 0 else "BUY"
    qty = binance_client.format_quantity(abs(live_amt), symbol)
    execution = _maker_then_market(
        close_side, qty, symbol, reduce_only=True,
        total_wait_sec=MAKER_EXIT_WAIT_SEC,
    )
    order_accepted = bool(execution.get("accepted"))
    if not execution.get("safe"):
        logger.error(f"🚨 [{symbol}] maker平仓状态不确定，未补市价，保护止损继续保留")
        _alert(f"🚨 [{symbol}] maker平仓撤单状态不确定，保护止损仍在，请核查")

    # 市价单与条件止损可能同时竞争；无论下单响应如何，都以交易所仓位为准。
    confirmed_flat = False
    attempts = 4 if order_accepted else 1
    for attempt in range(attempts):
        if attempt:
            time.sleep(0.75)
        remaining = _query_live_amount(symbol)
        if remaining is not None and abs(remaining) < 1e-12:
            confirmed_flat = True
            break

    if confirmed_flat:
        logger.info(f"✅ [{symbol}] 离场平仓成交 qty={qty} | {signal.get('reason')}")
        _alert(f"✅ [{symbol}] 平仓 qty={qty} | {signal.get('reason')}")
        _finish_close(symbol, signal, state, cooldowns, rec)
    elif order_accepted:
        logger.error(f"🚨 [{symbol}] 平仓已受理但未确认归零，保留止损和本地状态，下一轮复核")
        _alert(f"🚨 [{symbol}] 平仓已受理但未确认归零，保护止损仍保留，请核查")
    else:
        logger.error(f"🚨 [{symbol}] 离场平仓失败，保护止损仍保留")
        _alert(f"🚨 [{symbol}] 离场平仓失败，保护止损仍保留，请人工核查")


# ==================== 启动核对 ====================

def _fetch_live_amounts() -> Optional[Dict[str, float]]:
    try:
        rows = binance_client.client.futures_position_information()
    except Exception as e:
        logger.error(f"全账户持仓快照失败，本轮禁止所有新开仓: {e}")
        return None
    out = {symbol: 0.0 for symbol in TRADED_SYMBOLS}
    for row in rows or []:
        symbol = str(row.get("symbol") or "").upper()
        if symbol in out:
            out[symbol] += float(row.get("positionAmt") or 0)
    return out


def _truthy(value: Any) -> bool:
    return value is True or str(value or "").strip().lower() in ("true", "1", "yes")


def _is_protective_stop(order: Dict[str, Any], live_amt: float) -> bool:
    order_type = str(order.get("type") or order.get("orderType") or "").upper()
    expected_side = "SELL" if live_amt > 0 else "BUY"
    if order_type not in ("STOP", "STOP_MARKET"):
        return False
    if str(order.get("side") or "").upper() != expected_side:
        return False
    if _truthy(order.get("closePosition")):
        return True
    try:
        qty = float(order.get("origQty") or order.get("quantity") or 0)
        return _truthy(order.get("reduceOnly")) and qty >= abs(live_amt) * 0.995
    except (TypeError, ValueError):
        return False


def _protective_order_id(order: Dict[str, Any]) -> str:
    return str(order.get("algoId") or order.get("orderId") or "")


def _protective_trigger(order: Dict[str, Any]) -> float:
    try:
        return float(
            order.get("triggerPrice") or order.get("stopPrice")
            or order.get("activatePrice") or 0
        )
    except (TypeError, ValueError):
        return 0.0


def _price_matches(symbol: str, left: float, right: float) -> bool:
    try:
        return str(binance_client.format_price(left, symbol)) == str(
            binance_client.format_price(right, symbol)
        )
    except Exception:
        return abs(float(left or 0.0) - float(right or 0.0)) <= 1e-12


def _cancel_protective_order(symbol: str, order: Dict[str, Any]) -> bool:
    oid = _protective_order_id(order)
    if not oid:
        return False
    if order.get("isAlgoOrder") or order.get("algoId"):
        return bool(binance_client.cancel_algo_order(symbol=symbol, algo_id=int(oid)))
    return bool(binance_client.cancel_order(symbol=symbol, order_id=int(oid)))


def _audit_protective_stop(
    symbol: str, rec: Dict[str, Any], live_amt: float, state: Dict[str, Any],
) -> None:
    regular = binance_client.get_open_orders(
        symbol, include_algo=False, prefer_cache=False,
    )
    if is_orders_query_failed(regular):
        logger.error(f"[{symbol}] 普通挂单不可读，fail-closed：不盲目补止损")
        return
    algo = binance_client.get_open_algo_orders(symbol)
    if is_orders_query_failed(algo):
        logger.error(f"[{symbol}] Algo止损不可读，fail-closed：不盲目补止损")
        return
    protective = [
        order for order in [*(regular or []), *(algo or [])]
        if _is_protective_stop(order, live_amt)
    ]
    if protective:
        # Keep the closest full-position protection. A confirmed canonical order
        # must exist before any duplicate is removed.
        priced = [order for order in protective if _protective_trigger(order) > 0]
        if priced:
            canonical = (
                max(priced, key=_protective_trigger)
                if live_amt > 0 else min(priced, key=_protective_trigger)
            )
        else:
            canonical = protective[0]
        canonical_id = _protective_order_id(canonical)
        rec["sl_order_id"] = canonical_id or rec.get("sl_order_id")
        actual_stop = _protective_trigger(canonical)
        if actual_stop > 0:
            rec["stop_loss"] = actual_stop
        rec["status"] = "open"
        _save_state(state)

        stale = [
            order for order in protective
            if _protective_order_id(order) != canonical_id
        ]
        for order in stale:
            if _cancel_protective_order(symbol, order):
                logger.warning(
                    f"[{symbol}] 清理重复保护止损 id={_protective_order_id(order)} "
                    f"trigger={_protective_trigger(order)}，保留 id={canonical_id}"
                )
            else:
                logger.error(
                    f"[{symbol}] 唯一止损已确认，但重复止损撤单失败 "
                    f"id={_protective_order_id(order)}"
                )
        return

    stop_loss = float(rec.get("stop_loss") or 0)
    if stop_loss <= 0:
        logger.error(f"🚨 [{symbol}] 持仓没有有效止损价，无法自动补挂")
        _alert(f"🚨 [{symbol}] 持仓没有有效止损价且盘口无保护单，请人工立刻处理")
        return

    logger.error(f"🚨 [{symbol}] 盘口确认没有保护止损，立即补挂@{stop_loss}")
    close_side = "SELL" if live_amt > 0 else "BUY"
    binance_client.invalidate_open_orders_cache(symbol)
    sl_order = binance_client.place_stop_market_order(
        close_side, stop_loss, symbol=symbol, quantity=None,
        client_order_id=f"HAsl{int(time.time()) % 100000000}",
        working_type="MARK_PRICE", price_protect=False,
    )
    if sl_order:
        rec["sl_order_id"] = str(sl_order.get("orderId") or sl_order.get("algoId") or "") or None
        rec["status"] = "open"
        _save_state(state)
        logger.info(f"🛡️ [{symbol}] 缺失止损已补挂 @{stop_loss}")
        _alert(f"🛡️ [{symbol}] 巡检发现止损缺失，已自动补挂 @{stop_loss}")
    else:
        rec["status"] = "entry_pending_sl"
        _save_state(state)
        logger.error(f"🚨🚨 [{symbol}] 止损补挂失败，需要人工立刻介入")
        _alert(f"🚨🚨 [{symbol}] 盘口无保护单且自动补挂失败，请人工立刻介入")


def _all_protective_orders(
    symbol: str, live_amt: float,
) -> Optional[List[Dict[str, Any]]]:
    regular = binance_client.get_open_orders(
        symbol, include_algo=False, prefer_cache=False,
    )
    algo = binance_client.get_open_algo_orders(symbol)
    if is_orders_query_failed(regular) or is_orders_query_failed(algo):
        return None
    return [
        order for order in [*(regular or []), *(algo or [])]
        if _is_protective_stop(order, live_amt)
    ]


def _replace_single_protective_stop(
    symbol: str,
    live_amt: float,
    stop_loss: float,
    rec: Dict[str, Any],
    state: Dict[str, Any],
) -> bool:
    """Replace protection without a naked interval, ending with one stop."""
    if abs(live_amt) < 1e-12 or stop_loss <= 0:
        return False
    expected_side = "SELL" if live_amt > 0 else "BUY"
    orders = _all_protective_orders(symbol, live_amt)
    if orders is None:
        logger.error(f"[{symbol}] 止损盘口不可读，禁止替换")
        return False

    exact = [
        order for order in orders
        if _truthy(order.get("closePosition"))
        and _price_matches(symbol, _protective_trigger(order), stop_loss)
    ]
    if len(orders) == 1 and exact:
        rec.update({
            "sl_order_id": _protective_order_id(exact[0]),
            "stop_loss": _protective_trigger(exact[0]),
            "status": "open",
        })
        _save_state(state)
        return True

    temporary = next((
        order for order in orders
        if not _truthy(order.get("closePosition"))
        and float(order.get("quantity") or order.get("origQty") or 0)
        >= abs(live_amt) * 0.995
        and _price_matches(symbol, _protective_trigger(order), stop_loss)
    ), None)
    if temporary is None:
        temporary = binance_client.place_algo_stop_market_order(
            expected_side,
            stop_loss,
            symbol=symbol,
            close_position=False,
            quantity=abs(live_amt),
            client_order_id=f"HATMP{int(time.time()) % 100000000}",
            working_type="MARK_PRICE",
            price_protect=False,
        )
        if not temporary:
            logger.error(f"[{symbol}] 临时全量止损挂单失败，旧保护保持不动")
            return False

    temporary_id = _protective_order_id(temporary)
    verified = _all_protective_orders(symbol, live_amt)
    if verified is None or not any(
        _protective_order_id(order) == temporary_id for order in verified
    ):
        logger.error(f"[{symbol}] 临时止损无法核实，旧保护保持不动")
        return False

    for order in verified:
        if _truthy(order.get("closePosition")):
            if not _cancel_protective_order(symbol, order):
                logger.error(f"[{symbol}] 旧closePosition止损撤销失败，停止替换")
                return False

    canonical = binance_client.place_algo_stop_market_order(
        expected_side,
        stop_loss,
        symbol=symbol,
        close_position=True,
        quantity=None,
        client_order_id=f"HACAN{int(time.time()) % 100000000}",
        working_type="MARK_PRICE",
        price_protect=False,
    )
    if not canonical:
        rec.update({
            "sl_order_id": temporary_id,
            "stop_loss": stop_loss,
            "status": "open",
        })
        _save_state(state)
        logger.error(f"[{symbol}] 唯一止损替换失败，临时全量止损继续保护")
        return False

    canonical_id = _protective_order_id(canonical)
    final_orders = _all_protective_orders(symbol, live_amt)
    canonical_order = next((
        order for order in (final_orders or [])
        if _protective_order_id(order) == canonical_id
        and _truthy(order.get("closePosition"))
    ), None)
    if canonical_order is None:
        logger.error(f"[{symbol}] 新closePosition止损无法核实，临时止损继续保护")
        return False

    for order in final_orders or []:
        if _protective_order_id(order) != canonical_id:
            _cancel_protective_order(symbol, order)
    rec.update({
        "sl_order_id": canonical_id,
        "stop_loss": _protective_trigger(canonical_order),
        "status": "open",
    })
    _save_state(state)
    logger.info(f"🛡️ [{symbol}] 唯一保护止损已确认 @{rec['stop_loss']}")
    return True


def _cancel_owned_protective_orders(symbol: str) -> bool:
    regular = binance_client.get_open_orders(
        symbol, include_algo=False, prefer_cache=False,
    )
    algo = binance_client.get_open_algo_orders(symbol)
    if is_orders_query_failed(regular) or is_orders_query_failed(algo):
        return False
    ok = True
    for order in [*(regular or []), *(algo or [])]:
        order_type = str(order.get("type") or order.get("orderType") or "").upper()
        if order_type not in ("STOP", "STOP_MARKET"):
            continue
        if not _cancel_protective_order(symbol, order):
            ok = False
    return ok


def _commit_virtual_transition(
    symbol: str,
    state: Dict[str, Any],
    cooldowns: Dict[str, int],
) -> bool:
    rec = copy.deepcopy(state.get(symbol) or {})
    transition = rec.get("transition") or {}
    candidate = copy.deepcopy(transition.get("candidate_sleeves") or {})
    target = sleeve_net_qty(candidate)
    live_amt = _query_live_amount(symbol)
    if live_amt is None or not _qty_matches(live_amt, target, symbol):
        return False

    previous_net = float(
        transition.get("previous_net_qty")
        if transition.get("previous_net_qty") is not None
        else _state_signed_qty(rec)
    )
    previous_stop = float(rec.get("stop_loss") or 0.0)
    bar_time = transition.get("bar_time")
    transition_id = str(transition.get("id") or "unknown")

    rec.pop("transition", None)
    rec.update({
        "strategy": STRATEGY_VERSION,
        "asset_class": _asset_class(symbol),
        "virtual_netting": True,
        "sleeves": candidate,
        "sleeve_cooldowns": copy.deepcopy(
            transition.get("candidate_sleeve_cooldowns") or {}
        ),
        "leverage": float(
            transition.get("leverage")
            or rec.get("leverage")
            or EXCHANGE_LEVERAGE_INFO.get(symbol, FALLBACK_LEVERAGE_INFO)[0]
        ),
        "last_acted_bar_time": bar_time,
        "side": side_for_qty(target),
        "qty": abs(target),
    })

    if _qty_matches(target, 0.0, symbol):
        canceled = _cancel_owned_protective_orders(symbol)
        rec.update({
            "stop_loss": 0.0,
            "sl_order_id": None,
            "status": "virtual_flat" if candidate else "closed",
        })
        if candidate:
            state[symbol] = rec
        else:
            state.pop(symbol, None)
            _record_exit_bar(cooldowns, symbol, bar_time)
        _save_state(state)
        if not canceled:
            logger.error(f"[{symbol}] 已归零但保护单清理未完全确认")
        return True

    desired_stop = protective_stop(
        candidate,
        target,
        previous_stop=previous_stop,
        previous_signed_qty=previous_net,
    )
    if desired_stop <= 0:
        if _emergency_flatten(symbol, "虚拟净仓没有有效保护价", transition_id):
            state.pop(symbol, None)
            _record_exit_bar(cooldowns, symbol, bar_time)
            _save_state(state)
        else:
            rec["status"] = "unprotected_emergency_failed"
            state[symbol] = rec
            _save_state(state)
        return False

    rec.update({
        "stop_loss": desired_stop,
        "status": "entry_pending_sl",
    })
    state[symbol] = rec
    _save_state(state)
    replaced = _replace_single_protective_stop(
        symbol, live_amt, desired_stop, rec, state,
    )
    if replaced:
        rec["status"] = "open"
        state[symbol] = rec
        _save_state(state)
        return True

    protective = _all_protective_orders(symbol, live_amt)
    if protective:
        rec["status"] = "protection_degraded"
        state[symbol] = rec
        _save_state(state)
        _alert(
            f"⚠️ [{symbol}] 净仓已完成且仍有全量保护，但唯一止损规范化失败，请核查"
        )
        return True

    if _emergency_flatten(symbol, "净仓完成后无法建立保护止损", transition_id):
        state.pop(symbol, None)
        _record_exit_bar(cooldowns, symbol, bar_time)
        _save_state(state)
    else:
        rec["status"] = "unprotected_emergency_failed"
        state[symbol] = rec
        _save_state(state)
    return False


def _recover_pending_transition(
    symbol: str,
    rec: Dict[str, Any],
    state: Dict[str, Any],
    cooldowns: Dict[str, int],
) -> bool:
    transition = rec.get("transition") or {}
    if not transition:
        return True
    live_amt = _query_live_amount(symbol)
    if live_amt is None:
        return False
    target = float(transition.get("target_net_qty") or 0.0)
    previous = float(transition.get("previous_net_qty") or 0.0)
    if _qty_matches(live_amt, target, symbol):
        logger.warning(f"[{symbol}] 恢复中断过渡：交易所已到目标仓位，继续提交账本")
        return _commit_virtual_transition(symbol, state, cooldowns)
    if _qty_matches(live_amt, previous, symbol):
        logger.warning(f"[{symbol}] 恢复中断过渡：交易所仍在原仓位，安全回滚意图")
        rec["sleeves"] = copy.deepcopy(transition.get("previous_sleeves") or {})
        rec["sleeve_cooldowns"] = copy.deepcopy(
            transition.get("previous_sleeve_cooldowns") or {}
        )
        rec.pop("transition", None)
        rec.update({
            "side": side_for_qty(live_amt),
            "qty": abs(live_amt),
            "status": "open" if live_amt else "virtual_flat",
        })
        state[symbol] = rec
        _save_state(state)
        return True
    if _qty_matches(live_amt, 0.0, symbol):
        # 2026-09-30真实复现(E账户BCHUSDT反手：previous=0.092多，target=
        # -0.03空，减仓腿成功、开仓腿因为最小名义金额被拒后，交易所真实
        # 仓位停在0——既不等于previous也不等于target，落进下面"无法自动
        # 恢复"分支被冻结，此后每轮都在原地重复报同一条错，账户被动跳过
        # 这个品种，直到有人手动处理)。0是一个可以安全确认的终态(真实
        # 敞口为零，没有方向性风险)，不需要非得等于previous或target才
        # 算"安全"——照抄_reconcile_on_start里"保护止损已使净仓归零"那条
        # 已有的自愈逻辑，清空这个品种的虚拟账本，下一轮凭最新信号重新
        # 判断要不要开仓，不再原地卡死。
        logger.warning(
            f"[{symbol}] 恢复中断过渡：交易所净仓已核实归零(不等于previous"
            f"也不等于target的中间态)，视为安全终态，清空虚拟账本重新开始"
        )
        state.pop(symbol, None)
        _save_state(state)
        return True
    rec["status"] = "reconciliation_required"
    state[symbol] = rec
    _save_state(state)
    age = max(0.0, time.time() - float(transition.get("created_at") or 0.0))
    logger.error(
        f"🚨 [{symbol}] 中断过渡无法自动恢复 live={live_amt} "
        f"previous={previous} target={target} age={age:.0f}s，冻结该品种"
    )
    _alert(
        f"🚨 [{symbol}] 虚拟账本与交易所仓位不一致，已冻结。"
        f"live={live_amt} previous={previous} target={target}"
    )
    return False


def _reconcile_on_start(
    state: Dict[str, Any], cooldowns: Dict[str, int], cutoff_ms: int,
) -> tuple[Dict[str, Any], set[str]]:
    """核对真实持仓并审计保护单。未知仓位整轮禁止交易，绝不加仓。"""
    live_amounts = _fetch_live_amounts()
    if live_amounts is None:
        return state, set(TRADED_SYMBOLS)

    _migrate_previous_combo_records(state, live_amounts, cutoff_ms)

    unmanaged_symbols: set[str] = set()
    for symbol in TRADED_SYMBOLS:
        rec = state.get(symbol)
        live_amt = live_amounts.get(symbol, 0.0)

        if isinstance(rec, dict) and rec.get("strategy") == STRATEGY_VERSION:
            if rec.get("transition"):
                if not _recover_pending_transition(
                    symbol, rec, state, cooldowns,
                ):
                    unmanaged_symbols.add(symbol)
                    continue
                rec = state.get(symbol)
                live_amt = float(_query_live_amount(symbol) or 0.0)
            if not rec:
                continue
            expected = sleeve_net_qty(rec.get("sleeves") or {})
            if not _qty_matches(live_amt, expected, symbol):
                if _qty_matches(live_amt, 0.0, symbol) and not _qty_matches(
                    expected, 0.0, symbol,
                ):
                    logger.warning(
                        f"[{symbol}] 交易所保护止损已使净仓归零，清空该品种全部虚拟子仓"
                    )
                    _record_exit_bar(
                        cooldowns, symbol, _latest_closed_bar_open(cutoff_ms),
                    )
                    state.pop(symbol, None)
                    continue
                unmanaged_symbols.add(symbol)
                rec["status"] = "reconciliation_required"
                state[symbol] = rec
                logger.error(
                    f"🚨 [{symbol}] 净仓核对失败 live={live_amt} virtual={expected}，"
                    "已冻结该品种"
                )
                _alert(
                    f"🚨 [{symbol}] 交易所净仓({live_amt})与虚拟账本({expected})不一致，已冻结"
                )
                continue
            rec.update({
                "side": side_for_qty(live_amt),
                "qty": abs(live_amt),
                "status": "open" if live_amt else "virtual_flat",
            })
            state[symbol] = rec
            if not _qty_matches(live_amt, 0.0, symbol):
                _audit_protective_stop(symbol, rec, live_amt, state)
            continue

        if rec and live_amt == 0:
            logger.warning(f"[{symbol}] 本地记录有仓，交易所已空仓(大概率已被止损打平)，清本地状态")
            _record_exit_bar(cooldowns, symbol, _latest_closed_bar_open(cutoff_ms))
            state.pop(symbol, None)
        elif not rec and live_amt != 0:
            unmanaged_symbols.add(symbol)
            logger.error(
                f"🚨 [{symbol}] 交易所有仓位({live_amt})但本地无记录——不自动接管！"
                f"可能是宝贝自己手工开的或者其它来源，人工确认后再处理，本引擎这个品种先跳过管理"
            )
            _alert(f"🚨 [{symbol}] 交易所有仓位({live_amt})但本地无记录，未接管，请人工核查")
        elif rec and live_amt != 0:
            expected_long = str(rec.get("side") or "").upper() == "LONG"
            if expected_long != (live_amt > 0):
                unmanaged_symbols.add(symbol)
                logger.error(f"🚨 [{symbol}] 本地方向与交易所方向相反，禁止自动管理")
                _alert(f"🚨 [{symbol}] 本地方向与交易所方向相反，已停止该品种自动交易，请核查")
                continue
            _audit_protective_stop(symbol, rec, live_amt, state)
    _save_state(state)
    return state, unmanaged_symbols


# ==================== 主循环 ====================

def _combo_stop(rec: Dict[str, Any]) -> float:
    side = str(rec.get("side") or "").upper()
    sleeves = rec.get("sleeves") or {}
    stops = [
        float(item.get("stop_loss") or 0)
        for item in sleeves.values()
        if str(item.get("side") or "").upper() == side
        and float(item.get("stop_loss") or 0) > 0
    ]
    if not stops:
        return float(rec.get("stop_loss") or 0)
    desired = max(stops) if side == "LONG" else min(stops)
    current = float(rec.get("stop_loss") or 0)
    if current > 0:
        # A sleeve change may tighten protection, never widen it.
        desired = max(desired, current) if side == "LONG" else min(desired, current)
    return desired


def _risk_allowed_qty(
    symbol: str,
    signal: Dict[str, Any],
    desired_qty: float,
    state: Dict[str, Any],
    bars: List[dict],
    risk_context: Optional[Dict[str, Any]],
) -> float:
    if risk_context is None:
        logger.error(f"[{symbol}] 组合风险上下文不可读，禁止增加仓位")
        return 0.0
    open_rows = _portfolio_open_rows(state)
    if open_rows is None:
        return 0.0
    decision = portfolio_guard.evaluate_entry(
        symbol=symbol,
        side=str(signal["action"]),
        desired_qty=desired_qty,
        price=float(signal["price"]),
        stop_price=float(signal["stop_loss"]),
        equity=float(risk_context["equity"]),
        open_rows=open_rows,
        bars=bars,
        peak_equity=float(risk_context["peak_equity"]),
        daily_start_equity=float(risk_context["daily_start_equity"]),
        minimum_regime=str(risk_context["portfolio_regime"]),
        budget_scale=LIVE_SLEEVE_COUNT,
        direction_relative_check=True,
    )
    allowed = binance_client.format_entry_quantity(
        decision.allowed_qty, symbol, price=float(signal["price"]),
    )
    logger.info(
        f"[{symbol}] 子策略增仓风控 regime={decision.regime} "
        f"asset={_asset_class(symbol)} qty={desired_qty}->{allowed} "
        f"reason={decision.reason}"
    )
    return allowed


def _add_combo_sleeves(
    symbol: str,
    entries: List[dict],
    state: Dict[str, Any],
    bars: List[dict],
    risk_context: Optional[Dict[str, Any]],
) -> None:
    rec = state.get(symbol) or {}
    bundle = combine_entries(entries)
    if not bundle:
        return
    side = str(rec.get("side") or "").upper()
    if side and side != str(bundle["action"]).upper():
        logger.info(f"[{symbol}] 反向子策略信号不与现有单向仓位对冲，等待现仓退出")
        return

    qty, leverage = _calc_qty_and_leverage(
        symbol,
        float(bundle["price"]),
        float(bundle["stop_loss"]),
        size_scale=float(bundle.get("position_scale") or 1.0),
    )
    qty = _risk_allowed_qty(symbol, bundle, qty, state, bars, risk_context)
    if qty <= 0:
        return
    before = _query_live_amount(symbol)
    if before is None or abs(before) < 1e-12:
        logger.error(f"[{symbol}] 增仓前真实仓位不可读或已归零，留待下一轮重建")
        return
    if (before > 0) != (bundle["action"] == "LONG"):
        logger.error(f"[{symbol}] 增仓方向与真实仓位冲突，拒绝下单")
        return

    # 2026-09-26晚同一个bug的另一处：这个函数上面已经用precondition保证了
    # before一定非0(给已有仓位加码，不是从0开仓)，改保证金模式在这里永远
    # 会撞到交易所-4067(有仓位/挂单不能改模式)，不是需要视情况判断，是
    # 根本不该在这条路径上调用——直接去掉，模式沿用已有仓位的原有模式。
    if binance_client.set_leverage(symbol, leverage=leverage) is None:
        return
    execution = _maker_then_market(
        bundle["action"], qty, symbol, reduce_only=False,
        total_wait_sec=MAKER_ENTRY_WAIT_SEC,
    )
    if not execution.get("safe") or not execution.get("accepted"):
        logger.error(f"[{symbol}] 子策略增仓失败或终态不确定，保留原仓与原止损")
        return
    after = _query_live_amount(symbol)
    if after is None or abs(after) <= abs(before) + 1e-12:
        logger.error(f"[{symbol}] 增仓响应已受理但真实仓位未增加，不写入子策略状态")
        return

    actual_added = abs(after) - abs(before)
    total_weight = sum(float(item["weight"]) for item in entries)
    direction = 1.0 if bundle["action"] == "LONG" else -1.0
    sleeves = dict(rec.get("sleeves") or {})
    for item in entries:
        sig = item["signal"]
        weight = float(item["weight"])
        distance = abs(float(sig["price"]) - float(sig["stop_loss"]))
        sleeves[item["name"]] = {
            "side": bundle["action"],
            "qty": binance_client.format_quantity(
                actual_added * weight / max(total_weight, 1e-12), symbol,
            ),
            "entry_price": float(sig["price"]),
            "entry_bar_time": sig.get("bar_time"),
            "stop_loss": round(float(sig["price"]) - direction * distance, 8),
            "weight": weight,
        }
    rec.update({
        "side": bundle["action"],
        "qty": abs(after),
        "leverage": leverage,
        "last_acted_bar_time": bundle.get("bar_time"),
        "strategy": STRATEGY_VERSION,
        "asset_class": _asset_class(symbol),
        "sleeves": sleeves,
    })
    state[symbol] = rec
    _save_state(state)
    desired_stop = _combo_stop(rec)
    _replace_single_protective_stop(symbol, after, desired_stop, rec, state)
    logger.info(
        f"🚀 [{symbol}] 子策略加入 {[item['name'] for item in entries]} "
        f"实际增仓={actual_added} 当前仓位={abs(after)}"
    )


def _reduce_combo_sleeves(
    symbol: str,
    exits: List[tuple[str, Dict[str, Any]]],
    state: Dict[str, Any],
    cooldowns: Dict[str, int],
) -> None:
    rec = state.get(symbol) or {}
    sleeves = dict(rec.get("sleeves") or {})
    exiting_names = [name for name, _signal in exits if name in sleeves]
    if not exiting_names:
        return
    remaining_names = [name for name in sleeves if name not in exiting_names]
    signal = exits[0][1]
    if not remaining_names:
        signal = dict(signal)
        signal["reason"] = " + ".join(
            f"{name}: {sig.get('reason')}" for name, sig in exits
        )
        _close_position(symbol, signal, state, cooldowns)
        return

    live_amt = _query_live_amount(symbol)
    if live_amt is None or abs(live_amt) < 1e-12:
        _finish_close(symbol, signal, state, cooldowns, rec)
        return
    close_qty = sum(float(sleeves[name].get("qty") or 0) for name in exiting_names)
    close_qty = binance_client.format_quantity(min(close_qty, abs(live_amt)), symbol)
    if close_qty <= 0:
        return
    close_side = "SELL" if live_amt > 0 else "BUY"
    execution = _maker_then_market(
        close_side, close_qty, symbol, reduce_only=True,
        total_wait_sec=MAKER_EXIT_WAIT_SEC,
    )
    if not execution.get("safe") or not execution.get("accepted"):
        logger.error(f"[{symbol}] 子策略减仓失败或终态不确定，状态与保护单保持不动")
        return
    after = _query_live_amount(symbol)
    if after is None:
        return
    if abs(after) < 1e-12:
        _finish_close(symbol, signal, state, cooldowns, rec)
        return
    if abs(after) >= abs(live_amt) - 1e-12:
        logger.error(f"[{symbol}] 子策略减仓未在真实仓位体现，不修改状态")
        return

    for name in exiting_names:
        sleeves.pop(name, None)
    rec.update({
        "qty": abs(after),
        "sleeves": sleeves,
        "last_acted_bar_time": signal.get("bar_time"),
    })
    state[symbol] = rec
    _save_state(state)
    desired_stop = _combo_stop(rec)
    _replace_single_protective_stop(symbol, after, desired_stop, rec, state)
    logger.info(
        f"✅ [{symbol}] 子策略退出 {exiting_names}，减仓={close_qty} "
        f"剩余={abs(after)} 活跃={list(sleeves)}"
    )


def _normalized_net_qty(
    symbol: str, sleeves: Dict[str, Dict[str, Any]],
) -> float:
    raw = sleeve_net_qty(sleeves)
    if abs(raw) < 1e-12:
        return 0.0
    qty = binance_client.format_quantity(abs(raw), symbol)
    return qty if raw > 0 else -qty


def _rebalance_virtual_sleeves(
    symbol: str,
    candidate_sleeves: Dict[str, Dict[str, Any]],
    state: Dict[str, Any],
    cooldowns: Dict[str, int],
    sleeve_cooldowns: Dict[str, int],
    *,
    bar_time: Any,
    reason: str,
    leverage: float,
    bars: List[dict],
    risk_context: Optional[Dict[str, Any]],
) -> bool:
    candidate = copy.deepcopy(candidate_sleeves)
    raw_target = sleeve_net_qty(candidate)
    normalized_target = _normalized_net_qty(symbol, candidate)
    if not _qty_matches(raw_target, normalized_target, symbol):
        logger.error(
            f"[{symbol}] 虚拟净仓无法按交易所步长表达 raw={raw_target} "
            f"normalized={normalized_target}"
        )
        return False
    current = _query_live_amount(symbol)
    if current is None:
        return False
    opens_risk = normalized_target != 0 and (
        current * normalized_target <= 0
        or abs(normalized_target) > abs(current) + 1e-12
    )
    max_gross_mult = MAX_TOTAL_NOTIONAL_MULT
    if opens_risk:
        if risk_context is None:
            logger.error(f"[{symbol}] 账户风险状态不可读，暂缓净额增仓")
            return True
        regime = portfolio_guard.more_severe_regime(
            portfolio_guard.classify_regime(bars),
            str(risk_context["portfolio_regime"]),
        )
        status = portfolio_guard.status_for_regime(
            regime,
            float(risk_context["equity"]),
            float(risk_context["peak_equity"]),
            float(risk_context["daily_start_equity"]),
        )
        if status.blocked:
            logger.info(f"[{symbol}] 账户熔断期间暂缓净额增仓: {status.reason}")
            return True
        max_gross_mult = min(max_gross_mult, status.gross_cap_mult)
        if not _physical_gross_allows(
            symbol, normalized_target, max_gross_mult,
            expected_current=current,
        ):
            return True
    # 2026-09-30: 审计留痕——之前日志只有"市价开仓成功 SELL xx"，事后查不出
    # 是哪个子策略、什么方向组成的净仓，只能靠回放K线反推。每次净额调整
    # 都把原因+全部子仓(名字/方向/数量)打出来，方向核查有据可查。
    logger.info(
        f"📋 [{symbol}] 子仓调整 原因={reason} 当前净仓={current} 目标净仓={normalized_target} "
        f"子仓={[(n, s.get('side'), s.get('qty')) for n, s in candidate.items()]}"
    )
    transition_id = _write_transition(
        symbol,
        state,
        candidate,
        sleeve_cooldowns,
        bar_time=bar_time,
        reason=reason,
        leverage=leverage,
    )
    rec = state[symbol]
    rec["transition"]["target_net_qty"] = normalized_target
    state[symbol] = rec
    _save_state(state)
    if not _execute_net_target(
        symbol, normalized_target, transition_id, leverage, max_gross_mult,
    ):
        rec = state.get(symbol) or rec
        rec["status"] = "reconciliation_required"
        state[symbol] = rec
        _save_state(state)
        logger.error(f"🚨 [{symbol}] 净额执行未完成，已冻结并保留过渡账本")
        _alert(f"🚨 [{symbol}] 净额执行未完成，已冻结；禁止后续自动交易")
        return False
    return _commit_virtual_transition(symbol, state, cooldowns)


def _new_virtual_sleeve(
    symbol: str,
    item: Dict[str, Any],
    state: Dict[str, Any],
    candidate_sleeves: Dict[str, Dict[str, Any]],
    bars: List[dict],
    risk_context: Optional[Dict[str, Any]],
) -> tuple[Optional[Dict[str, Any]], float]:
    signal = item.get("signal") or {}
    side = str(signal.get("action") or "").upper()
    if side not in ("LONG", "SHORT"):
        return None, 0.0
    qty, leverage = _calc_qty_and_leverage(
        symbol,
        float(signal.get("price") or 0.0),
        float(signal.get("stop_loss") or 0.0),
        size_scale=float(item.get("weight") or 0.0),
    )
    if qty <= 0 or leverage <= 0:
        return None, 0.0
    provisional = copy.deepcopy(state)
    provisional_rec = copy.deepcopy(provisional.get(symbol) or {})
    provisional_rec.update({
        "strategy": STRATEGY_VERSION,
        "sleeves": copy.deepcopy(candidate_sleeves),
    })
    provisional[symbol] = provisional_rec
    qty = _risk_allowed_qty(
        symbol, signal, qty, provisional, bars, risk_context,
    )
    if qty <= 0:
        return None, leverage
    price = float(signal["price"])
    stop_loss = float(signal["stop_loss"])
    return {
        "side": side,
        "qty": qty,
        "entry_price": price,
        "entry_bar_time": signal.get("bar_time"),
        "stop_loss": stop_loss,
        "weight": float(item.get("weight") or 0.0),
        # 2026-09-30利润回吐刹车用：开仓那一刻的止损距离冻结存一份，后续
        # 不管stop_loss被breakeven锁/刹车本身怎么顶紧，giveback_brake算
        # R倍数永远用这个原始值当分母，不会跟着已经收紧过的止损滚动重算
        # (否则风险分母会越算越小，刹车会变得越来越敏感，失控)。
        "initial_risk": abs(price - stop_loss),
        "best_price": price,
    }, leverage


def _arena_strategy_name(sleeve_name: str) -> str:
    return ARENA_STRATEGY_ALIAS.get(sleeve_name, sleeve_name)


def _arena_open_index() -> Optional[Dict[tuple, List[int]]]:
    """{(擂台策略名, 品种, 方向): [entry_bar_time,...]}；任一策略读失败返回None。"""
    now = time.time()
    if _arena_cache["index"] is not None and now - _arena_cache["ts"] < ARENA_CACHE_TTL_SEC:
        return _arena_cache["index"]
    names = sorted({
        _arena_strategy_name(name)
        for asset in ("crypto", "stocks", "gold")
        for name, _ in sleeve_config(asset)
    })
    index: Dict[tuple, List[int]] = {}
    try:
        for name in names:
            url = ARENA_POSITIONS_URL.format(strategy=urllib.parse.quote(name))
            with urllib.request.urlopen(url, timeout=8) as resp:
                payload = json.load(resp)
            if payload.get("status") != "ok":
                raise RuntimeError(f"{name} status={payload.get('status')}")
            for row in payload.get("positions") or []:
                if str(row.get("status") or "open") != "open":
                    continue
                key = (name, str(row.get("symbol") or "").upper(), str(row.get("side") or "").upper())
                index.setdefault(key, []).append(int(row.get("entry_bar_time") or 0))
    except Exception as e:
        logger.warning(f"擂台镜像：读取擂台持仓失败，本轮禁止所有新sleeve开仓: {e}")
        _arena_cache.update({"ts": now, "index": None})
        return None
    _arena_cache.update({"ts": now, "index": index})
    return index


def _arena_confirms_entry(sleeve_name: str, symbol: str, side: str, bar_time: int) -> bool:
    if not ARENA_MIRROR_ENABLED:
        return True
    index = _arena_open_index()
    if index is None:
        return False
    key = (_arena_strategy_name(sleeve_name), symbol.upper(), str(side).upper())
    return any(
        0 <= int(t) - int(bar_time) <= ARENA_MIRROR_MAX_LAG_MS
        or 0 <= int(bar_time) - int(t) <= ARENA_MIRROR_MAX_LAG_MS
        for t in index.get(key, [])
    )


def _process_virtual_combo(
    symbol: str,
    state: Dict[str, Any],
    cooldowns: Dict[str, int],
    bars: List[dict],
    bars_by_tf: Dict[str, List[dict]],
    risk_context: Optional[Dict[str, Any]],
) -> bool:
    rec = copy.deepcopy(state.get(symbol) or {})
    sleeves = copy.deepcopy(rec.get("sleeves") or {})
    sleeve_cooldowns = {
        str(name): int(value)
        for name, value in (rec.get("sleeve_cooldowns") or {}).items()
        if value is not None
    }
    last_close = float(bars[-1]["c"]) if bars else 0.0
    breakeven_changed = _apply_breakeven_lock(sleeves, last_close)
    giveback_changed = _apply_giveback_brake(sleeves, last_close, symbol)
    breakeven_changed = breakeven_changed or giveback_changed

    exits: List[tuple[str, Dict[str, Any]]] = []
    for name, sleeve in sleeves.items():
        signal = generate_sleeve_exit(name, bars_by_tf, sleeve)
        if signal and str(signal.get("action") or "").upper().startswith("CLOSE"):
            exits.append((name, signal))
    if exits:
        candidate = copy.deepcopy(sleeves)
        for name, signal in exits:
            candidate.pop(name, None)
            if signal.get("bar_time") is not None:
                sleeve_cooldowns[name] = int(signal["bar_time"])
        bar_time = max(
            (int(signal.get("bar_time") or 0) for _, signal in exits),
            default=0,
        )
        reason = " + ".join(
            f"{name}: {signal.get('reason')}" for name, signal in exits
        )
        leverage = float(
            rec.get("leverage")
            or EXCHANGE_LEVERAGE_INFO.get(symbol, FALLBACK_LEVERAGE_INFO)[0]
        )
        return _rebalance_virtual_sleeves(
            symbol,
            candidate,
            state,
            cooldowns,
            sleeve_cooldowns,
            bar_time=bar_time,
            reason=reason,
            leverage=leverage,
            bars=bars,
            risk_context=risk_context,
        )

    entries = [
        item for item in entry_signals(bars_by_tf, _asset_class(symbol), symbol=symbol)
        if item["name"] not in sleeves
    ] if symbol not in NO_NEW_ENTRY_SYMBOLS else []
    candidate = copy.deepcopy(sleeves)
    accepted_names: List[str] = []
    leverage = float(
        rec.get("leverage")
        or EXCHANGE_LEVERAGE_INFO.get(symbol, FALLBACK_LEVERAGE_INFO)[0]
    )
    acted_bar = 0
    for item in entries:
        signal = item.get("signal") or {}
        bar_time = int(signal.get("bar_time") or 0)
        if bar_time <= int(cooldowns.get(symbol, 0)):
            continue
        if bar_time <= int(sleeve_cooldowns.get(item["name"], 0)):
            continue
        if bar_time <= int(rec.get("entry_activation_after_bar") or 0):
            continue
        side = str(signal.get("action") or "").upper()
        if not _arena_confirms_entry(item["name"], symbol, side, bar_time):
            skip_key = (symbol, item["name"], bar_time)
            if skip_key not in _arena_skip_logged:
                _arena_skip_logged.add(skip_key)
                logger.info(
                    f"[{symbol}] 擂台镜像：{item['name']} {side} 信号(bar={bar_time}) "
                    f"擂台未开同向仓，本轮不跟(下轮重查)"
                )
            continue
        sleeve, sleeve_leverage = _new_virtual_sleeve(
            symbol, item, state, candidate, bars, risk_context,
        )
        if sleeve is None:
            continue
        candidate[item["name"]] = sleeve
        accepted_names.append(item["name"])
        leverage = max(leverage, sleeve_leverage)
        acted_bar = max(acted_bar, bar_time)
    if not accepted_names:
        if breakeven_changed:
            return _rebalance_virtual_sleeves(
                symbol,
                candidate,
                state,
                cooldowns,
                sleeve_cooldowns,
                bar_time=int(bars[-1]["t"]) if bars else 0,
                reason="breakeven锁盈/利润回吐刹车上移止损",
                leverage=leverage,
                bars=bars,
                risk_context=risk_context,
            )
        return True

    return _rebalance_virtual_sleeves(
        symbol,
        candidate,
        state,
        cooldowns,
        sleeve_cooldowns,
        bar_time=acted_bar,
        reason=f"virtual entries: {', '.join(accepted_names)}",
        leverage=leverage,
        bars=bars,
        risk_context=risk_context,
    )

def _tick_symbol(
    symbol: str, state: Dict[str, Any], cooldowns: Dict[str, int], cutoff_ms: int,
    risk_context: Optional[Dict[str, Any]],
) -> bool:
    bars = _get_bars(symbol, cutoff_ms=cutoff_ms)
    if not bars:
        logger.warning(f"[{symbol}] 拉K线失败或为空")
        return True

    rec = state.get(symbol)
    asset_class = _asset_class(symbol)
    daily = _get_bars(
        symbol, limit=100, cutoff_ms=cutoff_ms, interval="1d",
    )
    bars_by_tf = {"base": bars, "1d": daily}

    if rec and rec.get("strategy") in PREVIOUS_STRATEGY_VERSIONS:
        logger.error(f"[{symbol}] 旧组合账本未能安全迁移，本轮冻结该品种")
        return True

    # Positions from the original standalone HA engine keep their original exit
    # logic. v2 combo positions are migrated in reconciliation without trading.
    if rec and str(rec.get("strategy") or "") != STRATEGY_VERSION:
        signal = generate_legacy_ha_signal(
            {"base": bars}, position={"side": rec["side"]},
        )
        if signal and signal.get("action") == "CLOSE_QUICK_EXIT":
            if rec.get("last_acted_bar_time") != signal.get("bar_time"):
                _close_position(symbol, signal, state, cooldowns)
        return True

    return _process_virtual_combo(
        symbol, state, cooldowns, bars, bars_by_tf, risk_context,
    )


def run_once() -> None:
    cutoff_ms = int(time.time() * 1000)
    state = _load_state()
    cooldowns = _load_cooldowns()
    one_way_confirmed = _one_way_mode_confirmed()
    state, unmanaged_symbols = _reconcile_on_start(state, cooldowns, cutoff_ms)
    if not one_way_confirmed:
        logger.error("账户模式校验未通过：本轮只完成持仓/止损审计，不执行任何交易")
        return
    risk_context = _refresh_risk_context(cutoff_ms)
    if risk_context:
        logger.info(
            f"组合风险状态={risk_context['portfolio_regime']} "
            f"equity={risk_context['equity']:.2f} peak={risk_context['peak_equity']:.2f}"
        )

    for symbol in TRADED_SYMBOLS:
        if symbol in unmanaged_symbols:
            logger.warning(f"[{symbol}] 本轮跳过：真实仓位未被本策略安全接管")
            continue
        try:
            safe_to_continue = _tick_symbol(
                symbol, state, cooldowns, cutoff_ms, risk_context,
            )
            if safe_to_continue is False:
                logger.error(
                    f"[{symbol}] 本轮净额执行未完整收口，停止扫描其余品种"
                )
                break
        except Exception as e:
            logger.error(f"[{symbol}] 本轮处理异常: {e}", exc_info=True)
            if (state.get(symbol) or {}).get("transition"):
                _alert(f"🚨 [{symbol}] 过渡执行异常，已停止本轮后续交易")
                break


def dry_run_check() -> None:
    """只读体检：对全部27个品种拉K线跑一遍信号判定，打印结果，完全不
    下单、不碰状态文件——部署后先跑这个确认没有品种会立刻误开仓，再
    放开实盘循环。"""
    cutoff_ms = int(time.time() * 1000)
    for symbol in TRADED_SYMBOLS:
        try:
            bars = _get_bars(symbol, cutoff_ms=cutoff_ms)
        except Exception as e:
            logger.warning(f"[{symbol}] 拉K线异常: {e}")
            continue
        if not bars:
            logger.warning(f"[{symbol}] 拉不到K线")
            continue
        asset_class = _asset_class(symbol)
        daily = _get_bars(
            symbol, limit=100, cutoff_ms=cutoff_ms, interval="1d",
        )
        entries = entry_signals({"base": bars, "1d": daily}, asset_class)
        sig = combine_entries(entries)
        logger.info(
            f"[干跑][{symbol}] asset={asset_class} bars={len(bars)} "
            f"子策略={[item['name'] for item in entries]} 合并信号={sig}"
        )


def main() -> None:
    once = "--once" in sys.argv
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        logger.info(f"{STRATEGY_VERSION}【干跑模式，不下单】| 目标品种数={len(TRADED_SYMBOLS)}")
        dry_run_check()
        return
    if not _acquire_process_lock():
        raise SystemExit(2)
    logger.info(f"{STRATEGY_VERSION}实盘引擎启动 | 目标品种数={len(TRADED_SYMBOLS)} | once={once}")
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

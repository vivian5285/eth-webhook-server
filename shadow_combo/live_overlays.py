"""Verbatim copy of heikin_ashi_live.py lines 123-262 (breakeven lock + giveback brake),
source sha256 cdb8384e (md5) as deployed on B/C/E 2026-09-30. Do not edit by hand."""
from __future__ import annotations

import os
from typing import Any, Dict

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

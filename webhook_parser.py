#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TradingView webhook parser — Trillion_God v6.5.6 最终版."""
import json
import logging
import math
import re

logger = logging.getLogger(__name__)

TV_STRATEGY_VERSION = "v6.5.6"

# ── RISK20：min(风险/止损距, 名义/价, TV.qty)，无状态纯函数 ───────────────
FIXED_RISK_PCT = 0.20
FIXED_NOTIONAL_MULT = 5.0  # 杠杆倍数：作用在「本金×20%」上，不是全本金
FIXED_MARGIN_PCT = FIXED_RISK_PCT
FIXED_LEVERAGE = 5
EXCHANGE_LEVERAGE = FIXED_LEVERAGE
VPS_MARGIN_LEVERAGE = FIXED_LEVERAGE
SIZING_MODE = "RISK20_NOTIONAL5"
ABSURD_TV_QTY_VS_CAPS = 50.0
# 铁律：名义上限 = 合约本金 × 20% × 5 = 本金 × 1（≈余额1倍）。
# 保证金不足由 supervisor 用 available×20%×5×0.92 再裁。
NOTIONAL_MARGIN_HAIRCUT = 1.0
VPS_RISK_PCT = 0.0
VPS_GLOBAL_SCALE = 1.0
VPS_REGIME_SCALE = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0}
VPS_MARGIN_PCT_BY_REGIME = {}
HARD_NOTIONAL_CAP = 0.0
MAX_RISK_PCT = 50.0
MIN_RISK_PCT = 0.01
MAX_POSITION_SIZE = 9999.0
MIN_QTY_DEFAULT = 0.001
MAX_TOTAL_NOTIONAL_MULT = 13.0
MAX_RISK_PCT_LIMIT = MAX_RISK_PCT
VPS_REGIME_RISK_MULTIPLIERS = VPS_REGIME_SCALE

# 分腿：只挂 TP1+TP2（各30%）；余仓40%由呼吸止损阶段二收网
LEG_TP_RATIOS = [0.30, 0.30, 0.40]
PLACE_TP_LEVELS = 2

# ── 旧雷达常量已废除（实盘走 breath_stop；保留名防旧 import）────────────────
# 旧值：activate=0.85 / step=0.5 / lock=0.3 / tp3_trail=2.0 — 禁止再用于决策
RADAR_ACTIVATE_TP1_FRAC = 0.0  # deleted
RADAR_STEP_ATR = 0.75          # 兼容名 → 对齐 breath STEP_TRIGGER
RADAR_LOCK_ATR = 0.4           # 兼容名 → 对齐 breath STEP_ADVANCE
RADAR_TP1_FLOOR_ATR = 0.5
RADAR_TP2_FLOOR_ATR = 1.5
RADAR_TP3_TRAIL_ATR = 0.0      # deleted（阶段二改呼吸系数）
RADAR_STAGE_COST_BUFFER_PCT = 0.0
ATR_UPDATE_SEC = 300
ORDER_TIMEOUT_SEC = 300
SIGNAL_DEDUP_SEC = 60
ATR_FALLBACK_ETH = 12.0
ATR_FALLBACK_DEFAULT = 30.0

# 已废除：档位相关空壳（防旧 import 崩）
ADD_QTY_RATIO_BY_REGIME = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
MAX_ADD_TIMES_BY_REGIME = {1: 0, 2: 0, 3: 0, 4: 0}
TV_REGIME_TP_RATIOS = {1: list(LEG_TP_RATIOS), 2: list(LEG_TP_RATIOS),
                       3: list(LEG_TP_RATIOS), 4: list(LEG_TP_RATIOS)}
TV_REGIME_TP_MULT = {1: (0.75, 1.4, 2.0), 2: (1.10, 2.0, 2.8),
                     3: (1.30, 2.6, 3.8), 4: (1.55, 3.0, 4.8)}
RADAR_ACTIVATION_RATIO_BY_REGIME = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}
RADAR_TRAIL_STEP_BY_REGIME = {1: RADAR_STEP_ATR, 2: RADAR_STEP_ATR,
                             3: RADAR_STEP_ATR, 4: RADAR_STEP_ATR}
RADAR_BREATH_ATR_BY_REGIME = {1: RADAR_LOCK_ATR, 2: RADAR_LOCK_ATR,
                             3: RADAR_LOCK_ATR, 4: RADAR_LOCK_ATR}
RADAR_ACTIVATION_RATIO = 0.0
RADAR_TP1_REMAINING_PCT = 1.0
RADAR_STAGE1_TP1_RATIO = 0.0
RADAR_STAGE2_TP1_RATIO = 0.0
RADAR_STAGE_ATR_MULT = {}
VPS_HARD_SL_PCT = {}
VPS_HARD_SL_EXTRA_RELAX = 0.0
VPS_HARD_SL_LIMIT_PCT = 0.0
VPS_HARD_SL_M = VPS_HARD_SL_PCT
VPS_REGIME_BREATH_MULT = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0}
VPS_HARD_SL_LIMIT_OFFSET = 0.0

ENTRY_TYPE_OPEN = "OPEN"
ENTRY_TYPE_PYRAMID = "PYRAMID"
ENTRY_TYPE_PROFIT_ADD = "PROFIT_ADD"
VALID_ENTRY_TYPES = frozenset({ENTRY_TYPE_OPEN})

# 最终架构：只认 4 个交易 action + PING 探活
RECONCILE_ACTIONS = frozenset()  # 已废除，保留空集防旧 import
FLATTEN_ACTIONS = frozenset({
    "CLOSE_QUICK_EXIT", "CLOSE_RSI_EXIT",
})
VALID_ACTIONS = frozenset({
    "LONG", "SHORT", "PING",
    "CLOSE_QUICK_EXIT", "CLOSE_RSI_EXIT",
})

ACTION_ALIASES = {
    "BUY": "LONG",
    "SELL": "SHORT",
    "CLOSE_LONG": "CLOSE_QUICK_EXIT",
    "CLOSE_SHORT": "CLOSE_QUICK_EXIT",
    "CLOSE": "CLOSE_QUICK_EXIT",
    "QUICK_EXIT": "CLOSE_QUICK_EXIT",
    "RSI_EXIT": "CLOSE_RSI_EXIT",
    "CLOSE_PROTECT": "CLOSE_QUICK_EXIT",
}

CLOSE_TYPE_TP3 = "tp3"
CLOSE_TYPE_PROTECT = "protect"
CLOSE_TYPE_BREAKEVEN = "breakeven"
CLOSE_TYPE_HARD_SL = "hard_sl"
CLOSE_TYPE_VPS_SHIELD = "vps_shield"
CLOSE_TYPE_GENERIC = "generic"
CLOSE_TYPE_QUICK = "quick_exit"
CLOSE_TYPE_RSI = "rsi_exit"
CLOSE_TYPE_RECONCILE = "reconcile"

CLOSE_TYPE_LABELS = {
    CLOSE_TYPE_TP3: "TP3/追踪止盈",
    CLOSE_TYPE_PROTECT: "反转保护",
    CLOSE_TYPE_BREAKEVEN: "保本/移动止损",
    CLOSE_TYPE_HARD_SL: "硬止损",
    CLOSE_TYPE_VPS_SHIELD: "VPS硬止损",
    CLOSE_TYPE_GENERIC: "常规清场",
    CLOSE_TYPE_QUICK: "反转保护",
    CLOSE_TYPE_RSI: "反转保护(RSI)",
    CLOSE_TYPE_RECONCILE: "TV对账(不下单)",
}

EXIT_SOURCE_RADAR_BE = "radar_be"
EXIT_SOURCE_VPS_HARD_SL = "vps_hard_sl"
EXIT_SOURCE_SL_INITIAL = "sl_initial"
EXIT_SOURCE_SL_BREAKEVEN = "sl_breakeven"
EXIT_SOURCE_TP3 = "tp3"
EXIT_SOURCE_TV_CLOSE = "tv_close"
EXIT_SOURCE_TV_PROTECT = "tv_protect"
EXIT_SOURCE_MANUAL = "manual"
EXIT_SOURCE_UNKNOWN = "unknown"
EXIT_SOURCE_QUICK = "quick_exit"
EXIT_SOURCE_RSI = "rsi_exit"

EXIT_SOURCE_LABELS = {
    EXIT_SOURCE_RADAR_BE: "止损平仓(呼吸止损)",
    EXIT_SOURCE_VPS_HARD_SL: "止损平仓(呼吸止损)",
    EXIT_SOURCE_SL_INITIAL: "止损平仓（阶段一）",
    EXIT_SOURCE_SL_BREAKEVEN: "止损平仓（阶段二/趋势追踪）",
    EXIT_SOURCE_TP3: "TP余仓追踪收网",
    EXIT_SOURCE_TV_CLOSE: "TV主动全平",
    EXIT_SOURCE_TV_PROTECT: "TV反转保护",
    EXIT_SOURCE_MANUAL: "人工/异动清仓",
    EXIT_SOURCE_UNKNOWN: "来源未明",
    EXIT_SOURCE_QUICK: "CLOSE_QUICK_EXIT",
    EXIT_SOURCE_RSI: "CLOSE_RSI_EXIT",
}

RADAR_STAGE_LABELS = {
    0: "呼吸止损·开仓即挂",
    1: "阶段一·阶梯锁本",
    2: "阶段二·ADX追踪",
    3: "TP1底线(0.5ATR)",
    4: "阶梯推进",
    5: "TP2底线(1.5ATR)",
    6: "阶梯推进",
    7: "ADX连续追踪",
}


def is_reconcile_action(action):
    return str(action or "").strip().upper() in RECONCILE_ACTIONS


def is_flatten_action(action):
    a = str(action or "").strip().upper()
    return a in FLATTEN_ACTIONS


def classify_tv_close(action="", reason="", pnl_pct=None):
    action = str(action or "").strip().upper()
    reason = str(reason or "").strip()
    if action in RECONCILE_ACTIONS:
        if action == "CLOSE_TRAIL":
            return CLOSE_TYPE_TP3
        if action == "CLOSE_SL_BREAKEVEN":
            return CLOSE_TYPE_BREAKEVEN
        if action == "CLOSE_SL_INITIAL":
            return CLOSE_TYPE_HARD_SL
        if action == "CLOSE_TP":
            return CLOSE_TYPE_RECONCILE
        return CLOSE_TYPE_RECONCILE
    if action == "CLOSE_QUICK_EXIT":
        return CLOSE_TYPE_QUICK
    if action == "CLOSE_RSI_EXIT":
        return CLOSE_TYPE_RSI
    if "防回吐" in reason or "保本" in reason:
        return CLOSE_TYPE_BREAKEVEN
    if "硬止损" in reason:
        return CLOSE_TYPE_HARD_SL
    return CLOSE_TYPE_GENERIC


def close_type_display_label(close_type, fallback_reason=""):
    label = CLOSE_TYPE_LABELS.get(close_type or CLOSE_TYPE_GENERIC, "常规清场")
    if close_type == CLOSE_TYPE_GENERIC and fallback_reason:
        return fallback_reason[:48]
    return label


def get_regime_tp_ratios(regime=None):
    """固定 30/30/40（不再按档位）。"""
    return list(LEG_TP_RATIOS)


def format_regime_tp_ratios_label(regime=None):
    return "30/30/40(挂TP1+TP2·余仓交阶段二)"


def get_leg_tp_ratios(payload=None):
    """
    默认 30/30/40（缺 qty1/qty2 时回退）。
    有 TV qty1/qty2 时，supervisor._tp_slices_for_initial 按 qty 缩放挂单，不走本比例。
    """
    return list(LEG_TP_RATIOS)


def get_radar_activation_ratio(regime=None):
    """已废除：开仓即呼吸，无激活比例。"""
    return 0.0


def format_radar_activation_ratios_label():
    return (
        "呼吸止损=TV.atr×1.5+0.3缓冲"
        "|阶梯0.75/0.4×呼吸系数"
        "|保本3.0ATR→呼吸追踪"
    )


def get_radar_trail_step(regime=None):
    """兼容：breath 阶梯步长 0.75。"""
    return float(RADAR_STEP_ATR)


def get_radar_breath_atr(regime=None):
    """兼容：breath 跟进 0.4。"""
    return float(RADAR_LOCK_ATR)


def get_vps_hard_sl_params(regime=None):
    return {
        "regime": 0,
        "pct": 0.0,
        "pct_label": "stop_loss",
        "sl_m": 0.0,
        "breath_mult": 1.0,
        "final_mult": 0.0,
        "deprecated": True,
    }


def compute_vps_hard_sl_distance(entry, regime=None, extra_relax=None, atr=None):
    return 0.0


def compute_vps_hard_sl(side, entry, atr=None, regime=None, extra_relax=None):
    return 0.0


def compute_vps_hard_sl_limit_price(side, trigger_px, offset=None):
    trigger_px = float(trigger_px or 0)
    return round(trigger_px, 2) if trigger_px > 0 else 0.0


def format_vps_hard_sl_note(side, entry, atr=None, regime=3, tv_sl_ref=0, extra_relax=None):
    """呼吸止损说明（TV stop_loss 仅参考）。"""
    try:
        from breath_stop import INITIAL_SL_ATR, initial_stop_price
        atr_f = float(atr or 0)
        if atr_f > 0 and entry:
            px = initial_stop_price(side, entry, atr_f)
            return (
                f"呼吸止损 `{px:.2f}` | entry±{INITIAL_SL_ATR}×ATR={atr_f:.2f} "
                f"| closePosition 开仓即追踪"
            )
    except Exception:
        pass
    ref = float(tv_sl_ref or 0)
    if ref > 0:
        return f"呼吸止损待ATR绑定 | TV参考 `{ref:.2f}`(不挂盘)"
    return "呼吸止损待绑定 | 须 webhook atr"


def format_tv_vps_sl_compare(side, entry, atr=None, regime=3, tv_sl_ref=0, extra_relax=None):
    ref = float(tv_sl_ref or 0)
    entry = float(entry or 0)
    if ref <= 0:
        return format_vps_hard_sl_note(side, entry, atr, regime)
    dist = abs(entry - ref) if entry > 0 else 0
    return f"硬止损 `{ref:.2f}` 距入场 {dist:.2f}U · 原值挂单(禁止推宽)"


def _strip_bom(text):
    if not text:
        return ""
    if text.startswith("\ufeff"):
        return text[1:]
    return text


def _to_float(val, default=None):
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _to_int(val, default=None):
    f = _to_float(val, None)
    if f is None:
        return default
    try:
        return int(f)
    except (TypeError, ValueError):
        return default


def get_regime_add_qty_ratio(regime=None):
    return 0.0


def get_regime_max_add_times(regime=None):
    return 0


def resolve_tv_add_qty_ratio(regime=None, tv_qty_ratio=None):
    return 0.0


def normalize_entry_type(val, default=ENTRY_TYPE_OPEN):
    return ENTRY_TYPE_OPEN


def _floor_qty_3dp(qty, min_qty=None):
    min_qty = float(min_qty if min_qty is not None else MIN_QTY_DEFAULT)
    qty = float(qty or 0)
    if qty <= 0:
        return 0.0
    qty = math.floor(qty * 1000.0) / 1000.0
    if qty < min_qty:
        return 0.0
    return qty


def compute_fixed_order_qty(principal, price, qty_step=0.001, min_qty=None,
                            face_value=None, max_position=None,
                            margin_pct=None, leverage=None,
                            stop_loss=None, tv_qty=None, tv_sl=None,
                            tv_price=None, **_kw):
    """
    无状态纯函数（仅开仓时算一次；不读历史仓位）——铁律永远：
      risk_capital = 合约本金 × 0.20          # 风险资金（也作保证金预算）
      notional_cap = risk_capital × 5        # = 本金 × 20% × 5 = 本金 × 1
      qty = min(risk_capital/|price−initialStop|, notional_cap/price, TV.qty′)
    天文 TV.qty（≫ 风险/名义候选）直接忽略，防止 Pine equity 膨胀把上限顶穿。
    TV.stop_loss 只参与 sl_adj，绝不作为盘口止损价。
    """
    principal = float(principal or 0)
    price = float(price or 0)
    tv_px = float(tv_price if tv_price is not None else price or 0)
    risk_pct = float(margin_pct if margin_pct is not None else FIXED_RISK_PCT)
    notional_mult = float(leverage if leverage is not None else FIXED_NOTIONAL_MULT)
    min_qty = float(min_qty if min_qty is not None else MIN_QTY_DEFAULT)
    max_position = float(max_position if max_position is not None else MAX_POSITION_SIZE)
    # VPS 实际止损价（initialStop）
    vps_stop = float(stop_loss or 0)
    # TV 参考止损（仅算调整系数）
    tv_stop = float(tv_sl or 0)
    tv_ref = float(tv_qty) if tv_qty is not None and float(tv_qty or 0) > 0 else None

    meta = {
        "principal": principal,
        "price": price,
        "tv_price": tv_px,
        "margin_pct": risk_pct,
        "risk_pct": risk_pct * 100.0,
        "leverage": notional_mult,
        "notional_mult": notional_mult,
        "sizing_mode": SIZING_MODE,
        "hard_notional_cap": 0.0,
        "qty_ratio": 1.0,
        "stop_loss": vps_stop,
        "initial_stop": vps_stop,
        "tv_sl": tv_stop,
        "tv_qty": tv_ref,
        "tv_qty_ref_only": False,
        "regime": 0,
        "bind": "risk20_x5_equals_1x_equity_tv_sl_adj",
        "formula": (
            "min(risk/vps_dist, (risk×5)/price, TV.qty×(tv_dist/vps_dist))"
            " · notional=equity×20%×5(=1×equity)"
        ),
        "sl_adj": 1.0,
        "tv_sl_adj_skipped": False,
    }
    if principal <= 0 or price <= 0 or risk_pct <= 0 or notional_mult <= 0:
        meta["error"] = "invalid_inputs"
        return 0.0, meta
    if vps_stop <= 0:
        meta["error"] = "missing_stop_loss"
        return 0.0, meta
    if tv_ref is None or tv_ref <= 0:
        meta["error"] = "missing_tv_qty"
        return 0.0, meta

    vps_stop_dist = abs(price - vps_stop)
    if vps_stop_dist <= 1e-12:
        meta["error"] = "zero_stop_dist"
        meta["stop_dist"] = 0.0
        return 0.0, meta

    # 调整系数：把 TV.qty（按 TV 隐含止损距算出）换算到 VPS 止损距下的等效上限
    sl_adj = 1.0
    tv_implied_dist = 0.0
    if tv_stop > 0 and tv_px > 0:
        tv_implied_dist = abs(tv_px - tv_stop)
        if tv_implied_dist > 1e-12:
            sl_adj = tv_implied_dist / vps_stop_dist
        else:
            meta["tv_sl_adj_skipped"] = True
            meta["tv_sl_adj_reason"] = "zero_tv_implied_dist"
    else:
        meta["tv_sl_adj_skipped"] = True
        meta["tv_sl_adj_reason"] = "missing_tv_stop_loss"

    adjusted_tv_qty = tv_ref * sl_adj

    risk_capital = principal * risk_pct
    # 名义 = (本金×20%) × 5倍杠杆 = 本金×1；绝不是本金×5
    notional_cap = risk_capital * notional_mult * NOTIONAL_MARGIN_HAIRCUT
    qty_by_risk = risk_capital / vps_stop_dist
    qty_by_notional = notional_cap / price
    tv_qty_ignored_absurd = False
    cap_ref = max(qty_by_risk, qty_by_notional)
    if cap_ref > 0 and adjusted_tv_qty > cap_ref * ABSURD_TV_QTY_VS_CAPS:
        raw_qty = min(qty_by_risk, qty_by_notional)
        tv_qty_ignored_absurd = True
    else:
        raw_qty = min(qty_by_risk, qty_by_notional, adjusted_tv_qty)

    # 哪个约束生效（便于开仓日志核对）
    if tv_qty_ignored_absurd:
        binding = "risk" if qty_by_risk <= qty_by_notional + 1e-12 else "notional"
    else:
        _cands = (
            ("risk", qty_by_risk),
            ("notional", qty_by_notional),
            ("adjusted_tv_qty", adjusted_tv_qty),
        )
        binding = min(_cands, key=lambda x: x[1])[0]

    meta["risk_capital"] = round(risk_capital, 4)
    meta["notional_cap"] = round(notional_cap, 2)
    meta["nominal_value"] = round(notional_cap, 2)
    meta["notional_margin_haircut"] = NOTIONAL_MARGIN_HAIRCUT
    meta["stop_dist"] = round(vps_stop_dist, 4)
    meta["vps_stop_dist"] = round(vps_stop_dist, 4)
    meta["tv_implied_dist"] = round(tv_implied_dist, 4)
    meta["sl_adj"] = round(sl_adj, 6)
    meta["adjusted_tv_qty"] = round(adjusted_tv_qty, 6)
    meta["tv_qty_ignored_absurd"] = tv_qty_ignored_absurd
    meta["qty_by_risk"] = round(qty_by_risk, 6)
    meta["qty_by_notional"] = round(qty_by_notional, 6)
    meta["binding"] = binding
    meta["margin"] = round(risk_capital, 4)
    meta["notional"] = round(min(raw_qty * price, notional_cap), 2)
    meta["order_amount"] = meta["notional"]
    meta["position_value"] = meta["notional"]
    meta["raw_qty"] = round(raw_qty, 6)

    if face_value and float(face_value) > 0:
        fv = float(face_value)
        qty = max(1, int(math.floor(raw_qty / fv)))
        adj_cap = int(math.floor(adjusted_tv_qty / fv)) if adjusted_tv_qty > 0 else qty
        qty = min(qty, int(max_position), adj_cap)
        meta["base_qty"] = float(qty)
        meta["qty"] = float(qty)
        return float(qty), meta

    qty = _floor_qty_3dp(raw_qty, min_qty=min_qty)
    step = float(qty_step or 0.001)
    if step > 0.001 + 1e-12 and qty > 0:
        qty = math.floor(qty / step) * step
        if qty < min_qty:
            qty = 0.0
    if qty > max_position:
        qty = float(max_position)
    # 最终不超过调整后的 TV 上限（禁止再夹回未调整的原始 TV.qty）
    if adjusted_tv_qty > 0 and qty > adjusted_tv_qty:
        qty = _floor_qty_3dp(adjusted_tv_qty, min_qty=min_qty)
    meta["base_qty"] = float(qty)
    meta["qty"] = float(qty)
    return float(qty), meta


def compute_tv_order_qty(principal, risk_pct=None, leverage=None, qty_ratio=None,
                         price=None, tv_sl=None, qty_step=0.001, min_qty=None,
                         face_value=None, regime=None, max_position=None,
                         stop_loss=None, tv_qty=None, tv_price=None, **_kw):
    """兼容旧名 → RISK20_NOTIONAL5（含 TV 止损距调整系数）。"""
    return compute_fixed_order_qty(
        principal=principal, price=price, qty_step=qty_step, min_qty=min_qty,
        face_value=face_value, max_position=max_position,
        stop_loss=stop_loss,
        tv_sl=tv_sl, tv_qty=tv_qty, tv_price=tv_price,
    )


def compute_vps_open_qty(principal, price, tv_sl=None, regime=None, leverage=None,
                         risk_pct=None, qty_ratio=1.0, qty_step=0.001, min_qty=None,
                         face_value=None, max_position=None, global_scale=None,
                         stop_loss=None, tv_qty=None, tv_price=None, **_kw):
    return compute_fixed_order_qty(
        principal=principal, price=price, qty_step=qty_step, min_qty=min_qty,
        face_value=face_value, max_position=max_position,
        stop_loss=stop_loss if stop_loss is not None else None,
        tv_sl=tv_sl, tv_qty=tv_qty, tv_price=tv_price if tv_price is not None else price,
    )


def check_total_notional_cap(equity, existing_notional, new_notional, mult=None):
    equity = float(equity or 0)
    existing = max(0.0, float(existing_notional or 0))
    new_n = max(0.0, float(new_notional or 0))
    mult = float(mult if mult is not None else MAX_TOTAL_NOTIONAL_MULT)
    cap = equity * mult if equity > 0 else 0.0
    total = existing + new_n
    ok = equity > 0 and total <= cap + 1e-6
    return ok, {
        "equity": round(equity, 2),
        "existing_notional": round(existing, 2),
        "new_notional": round(new_n, 2),
        "total_notional": round(total, 2),
        "cap": round(cap, 2),
        "mult": mult,
        "ok": ok,
    }


def compute_vps_add_qty(**_kw):
    """已废除加仓：恒返回 0。"""
    return 0.0, {"error": "add_disabled", "sizing_mode": SIZING_MODE}


def get_vps_margin_pct(regime=None):
    return FIXED_RISK_PCT * 100.0


def compute_vps_effective_risk(regime=None, global_scale=None):
    return 0.0, {"sizing_mode": SIZING_MODE, "deprecated": True}


def apply_vps_regime_risk(risk_pct, regime=None):
    return 0.0, {"sizing_mode": SIZING_MODE}


def format_vps_sizing_note(meta=None, qty=None, entry_type="OPEN"):
    meta = meta or {}
    principal = float(meta.get("principal") or 0)
    risk_pct = float(meta.get("margin_pct") or FIXED_RISK_PCT)
    mult = float(meta.get("notional_mult") or meta.get("leverage") or FIXED_NOTIONAL_MULT)
    parts = [
        f"风险{risk_pct * 100:.0f}%/止损距",
        f"名义=本金×{risk_pct * 100:.0f}%×{mult:.0f}(=本金×{risk_pct * mult:.0f})",
        f"sizing={SIZING_MODE}",
    ]
    if principal > 0:
        parts.append(f"权益={principal:.0f}U")
    if meta.get("stop_dist"):
        parts.append(f"VPS止损距={float(meta['stop_dist']):.2f}")
    if meta.get("sl_adj") is not None and float(meta.get("sl_adj") or 1) != 1.0:
        parts.append(f"sl_adj={float(meta['sl_adj']):.4f}")
    if meta.get("adjusted_tv_qty"):
        parts.append(f"TV.qty′≤{float(meta['adjusted_tv_qty']):.4f}")
    elif meta.get("tv_qty"):
        parts.append(f"TV.qty≤{float(meta['tv_qty'])}")
    if meta.get("binding"):
        parts.append(f"bind={meta['binding']}")
    if meta.get("notional") or meta.get("order_amount"):
        parts.append(f"名义={float(meta.get('notional') or meta.get('order_amount') or 0):.0f}U")
    if qty is not None and float(qty) > 0:
        parts.append(f"qty={float(qty)}")
    elif meta.get("base_qty"):
        parts.append(f"qty={float(meta['base_qty'])}")
    return " · ".join(parts)


def format_tv_sizing_note(risk_pct=None, leverage=None, qty_ratio=None, principal=None,
                          qty=None, regime=None, final_risk_pct=None, meta=None,
                          entry_type="OPEN"):
    if meta:
        return format_vps_sizing_note(meta, qty=qty, entry_type=entry_type)
    return format_vps_sizing_note(
        {"principal": principal, "leverage": FIXED_NOTIONAL_MULT, "margin_pct": FIXED_RISK_PCT},
        qty=qty,
    )


# ── 旧阶梯雷达（已废除生效路径；仅兼容导入，禁止新代码调用）────────────────

def radar_activation_price(side, entry, tp1):
    """已废除：旧 85% TP1 激活价。开仓即呼吸，无激活门槛 → 返回 0。"""
    return 0.0


def compute_ladder_radar_sl(*_args, **_kwargs):
    """
    【已物理删除】旧阶梯雷达 0.85/0.5/0.3/2.0×ATR。
    生产唯一止损：breath_stop.calculate_breath_stop。
    保留函数名仅防旧 import 崩；调用即失败，禁止实盘决策。
    """
    raise RuntimeError(
        "compute_ladder_radar_sl deleted; use breath_stop.calculate_breath_stop"
    )


def _unwrap_payload(obj):
    if not isinstance(obj, dict):
        return obj
    for key in ("message", "alert", "payload", "data", "body"):
        inner = obj.get(key)
        if isinstance(inner, dict):
            merged = dict(obj)
            merged.update(inner)
            return merged
        if isinstance(inner, str):
            nested = _extract_json_object(inner)
            if isinstance(nested, dict):
                merged = dict(obj)
                merged.update(nested)
                return merged
    return obj


def _extract_json_object(text):
    text = _strip_bom(str(text or "").strip())
    if not text:
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = None
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, str):
        try:
            inner = json.loads(obj)
            if isinstance(inner, dict):
                return inner
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def parse_webhook_request(raw_bytes, content_type="", as_json=None):
    raw_text = ""
    if raw_bytes:
        try:
            raw_text = raw_bytes.decode("utf-8", errors="replace")
        except Exception:
            raw_text = str(raw_bytes)
    data = None
    if isinstance(as_json, dict):
        data = as_json
    elif raw_text:
        data = _extract_json_object(raw_text)
    if data is None:
        raise ValueError("Invalid JSON payload")
    data = _unwrap_payload(data)
    if not isinstance(data, dict):
        raise ValueError("Payload must be a JSON object")
    return raw_text, data


def normalize_tv_payload(data):
    """Normalize Trillion_God v6.5.6 webhook fields."""
    if not isinstance(data, dict):
        raise ValueError("Payload must be a JSON object")

    src = _unwrap_payload(data)
    out = dict(src)

    action_raw = str(
        src.get("action") or src.get("side_action") or src.get("signal")
        or src.get("order") or ""
    ).strip().upper()
    action = ACTION_ALIASES.get(action_raw, action_raw)

    side_raw = str(src.get("side") or src.get("position_side") or "").strip().upper()
    if side_raw in ("BUY", "LONG"):
        side = "LONG"
    elif side_raw in ("SELL", "SHORT"):
        side = "SHORT"
    else:
        side = side_raw if side_raw in ("LONG", "SHORT") else ""
    if action in ("LONG", "SHORT") and not side:
        side = action

    price = _to_float(src.get("price") or src.get("close") or src.get("entry"))
    atr = _to_float(src.get("atr") or src.get("ATR"))
    adx = _to_float(src.get("adx") or src.get("ADX") or src.get("adx_val"))

    tp1 = _to_float(src.get("tp1") or src.get("tv_tp1") or src.get("TP1"))
    tp2 = _to_float(src.get("tp2") or src.get("tv_tp2") or src.get("TP2"))
    tp3 = _to_float(src.get("tp3") or src.get("tv_tp3") or src.get("TP3"))
    stop_loss = _to_float(
        src.get("stop_loss") or src.get("tv_sl") or src.get("stop") or src.get("sl")
    )

    qty = _to_float(src.get("qty"))
    qty1 = _to_float(src.get("qty1"))
    qty2 = _to_float(src.get("qty2"))
    qty3 = _to_float(src.get("qty3"))
    bar_time = src.get("bar_time") or src.get("bar_time_ms") or src.get("barTime") or src.get("time")
    leg = str(src.get("leg") or "").strip()
    bot_id = str(src.get("bot_id") or src.get("botId") or "").strip()
    reason = str(src.get("reason") or src.get("exit_reason") or src.get("comment") or "").strip()
    secret = str(src.get("secret") or src.get("token") or src.get("key") or "").strip()

    try:
        from symbol_config import extract_symbol_from_payload
        ticker_raw = extract_symbol_from_payload(src)
    except Exception:
        ticker_raw = str(src.get("symbol") or src.get("ticker") or src.get("sym") or "").strip()
    if ticker_raw:
        out["ticker"] = ticker_raw
        out["symbol"] = ticker_raw

    out["action"] = action
    out["side"] = side
    if price is not None:
        out["price"] = price
    if atr is not None:
        out["atr"] = atr
    if adx is not None:
        out["adx"] = adx
    if tp1 is not None:
        out["tp1"] = tp1
        out["tv_tp1"] = tp1
    if tp2 is not None:
        out["tp2"] = tp2
        out["tv_tp2"] = tp2
    if tp3 is not None:
        out["tp3"] = tp3
        out["tv_tp3"] = tp3
    if stop_loss is not None and stop_loss > 0:
        out["stop_loss"] = round(stop_loss, 2)
        out["tv_sl"] = round(stop_loss, 2)
    if qty is not None:
        out["qty"] = qty
    if qty1 is not None:
        out["qty1"] = qty1
    if qty2 is not None:
        out["qty2"] = qty2
    if qty3 is not None:
        out["qty3"] = qty3
    if bar_time is not None and bar_time != "":
        try:
            bt = int(float(bar_time))
            if 0 < bt < 10_000_000_000:
                bt *= 1000
            if bt > 0:
                out["bar_time"] = bt
        except (TypeError, ValueError):
            pass
    if leg:
        out["leg"] = leg
    if bot_id:
        out["bot_id"] = bot_id
    if reason:
        out["reason"] = reason
    if secret:
        out["secret"] = secret
        out["token"] = secret

    if action in ("LONG", "SHORT"):
        out["entry_type"] = ENTRY_TYPE_OPEN
        out["leverage"] = float(FIXED_LEVERAGE)
        out["qty_ratio"] = 1.0
        ratios = get_leg_tp_ratios(out)
        out["_leg_ratios"] = ratios

    out["_normalized"] = True
    out["_schema"] = TV_STRATEGY_VERSION
    # 仅白名单：LONG/SHORT/CLOSE_QUICK_EXIT/CLOSE_RSI_EXIT/PING
    # 禁止 CLOSE_TP/CLOSE_TRAIL/CLOSE_SL_* 等旧 action 借 startswith("CLOSE") 混入
    out["_parse_ok"] = bool(action) and action in VALID_ACTIONS
    out["_is_reconcile"] = is_reconcile_action(action)
    out["_is_flatten"] = is_flatten_action(action)
    return out


def compute_atr_from_klines(klines, period=14):
    if not klines or len(klines) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(klines)):
        try:
            high = float(klines[i][2])
            low = float(klines[i][3])
            prev_close = float(klines[i - 1][4])
        except (IndexError, TypeError, ValueError):
            continue
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return 0.0
    return sum(trs[-period:]) / period


def fetch_eth_atr_14_public(period=14, symbol="ETHUSDT"):
    return fetch_symbol_atr_14_public(symbol or "ETHUSDT", period=period)


def fetch_symbol_atr_14_public(symbol="ETHUSDT", period=14):
    sym = str(symbol or "ETHUSDT").upper().replace(".P", "")
    if ":" in sym:
        sym = sym.split(":")[-1]
    try:
        import requests
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": sym, "interval": "15m", "limit": period + 20},
            timeout=8,
        )
        resp.raise_for_status()
        return compute_atr_from_klines(resp.json(), period)
    except Exception as e:
        logger.warning(f"Public {sym} ATR fetch failed: {e}")
        return 0.0


def atr_fallback_for_symbol(symbol="ETHUSDT"):
    sym = str(symbol or "").upper()
    if "ETH" in sym:
        return ATR_FALLBACK_ETH
    return ATR_FALLBACK_DEFAULT


def _field_present(val):
    return val is not None and val != ""


def _has_positive_float(val):
    f = _to_float(val)
    return f is not None and f > 0


def validate_tp_prices_for_side(side, entry, tp_list, min_gap=0.01):
    """校验至少 TP1+TP2 与方向一致；TP3 可选。"""
    side = str(side or "").strip().upper()
    entry = float(entry or 0)
    if side not in ("LONG", "SHORT") or entry <= 0:
        return False
    prices = []
    for t in tp_list or []:
        try:
            p = round(float(t), 2)
        except (TypeError, ValueError):
            p = 0.0
        if p > 0:
            prices.append(p)
    if len(prices) < 2:
        return False
    gap = max(min_gap, entry * 0.0001)
    if side == "LONG":
        ok = all(p > entry + gap for p in prices[:2]) and prices[0] < prices[1]
        if len(prices) >= 3:
            ok = ok and prices[1] < prices[2]
        return ok
    ok = all(p < entry - gap for p in prices[:2]) and prices[0] > prices[1]
    if len(prices) >= 3:
        ok = ok and prices[1] > prices[2]
    return ok


def enrich_entry_tp_prices(action, price, atr, regime=None, payload=None):
    """开仓：已有 tp1/2/3 原样；缺档用 ATR 倍数补全（仅兜底）。"""
    payload = dict(payload or {})
    for i in (1, 2, 3):
        v = _to_float(payload.get(f"tp{i}")) or _to_float(payload.get(f"tv_tp{i}"))
        if v and v > 0:
            payload[f"tp{i}"] = v
            payload[f"tv_tp{i}"] = v
    tps = {i: _to_float(payload.get(f"tv_tp{i}")) for i in (1, 2, 3)}
    if all(tps[i] and tps[i] > 0 for i in (1, 2, 3)):
        payload["_tp_source"] = "tv"
        return payload

    mults = TV_REGIME_TP_MULT[3]
    sign = 1.0 if action == "LONG" else -1.0
    px = float(price or 0)
    a = float(atr or 0) or ATR_FALLBACK_DEFAULT
    if px <= 0:
        return payload
    filled = 0
    for i, mult in enumerate(mults, start=1):
        key = f"tv_tp{i}"
        if not _has_positive_float(payload.get(key)):
            val = round(px + sign * a * mult, 2)
            payload[key] = val
            payload[f"tp{i}"] = val
            filled += 1
    if filled == 3:
        payload["_tp_source"] = "local"
    elif filled > 0:
        payload["_tp_source"] = "tv+local"
    else:
        payload["_tp_source"] = "tv"
    return payload


def enrich_signal_fields(payload, action, fetch_atr=None, fallback_regime=3,
                         fallback_atr=30.0, fallback_price=0.0):
    out = dict(payload or {})
    action = str(action or "").strip().upper()

    if not _has_positive_float(out.get("price")) and fallback_price > 0:
        out["price"] = fallback_price
        out["_price_source"] = "local"
    elif _has_positive_float(out.get("price")):
        out["_price_source"] = "tv"

    is_entry = action in ("LONG", "SHORT")
    is_close = action.startswith("CLOSE")

    if is_entry:
        # 开仓 initial_atr 只认 TV webhook.atr>0；禁止本地/1h/90m 回填冒充
        has_key = ("atr" in out) or ("ATR" in out)
        raw = _to_float(out.get("atr") if "atr" in out else out.get("ATR"))
        if raw is not None and raw > 0:
            out["atr"] = float(raw)
            out["_atr_source"] = "tv"
        else:
            out["atr"] = float(raw or 0.0)  # 0 / 负 / 缺失→0，下游拒开
            out["_atr_source"] = "tv_invalid" if has_key else "missing"
            out["_atr_reject"] = True
        out = enrich_entry_tp_prices(
            action, out.get("price"), out.get("atr") if _has_positive_float(out.get("atr")) else None, None, out,
        )
        sl = _to_float(out.get("stop_loss")) or _to_float(out.get("tv_sl"))
        if sl and sl > 0:
            out["stop_loss"] = round(sl, 2)
            out["tv_sl"] = round(sl, 2)
        out["leverage"] = float(FIXED_LEVERAGE)
    elif is_close:
        # 平仓仅日志用 ATR，允许本地补全
        if not _has_positive_float(out.get("atr")):
            atr = 0.0
            if callable(fetch_atr):
                atr = float(fetch_atr() or 0)
            out["atr"] = atr or float(fallback_atr or ATR_FALLBACK_DEFAULT)
            out["_atr_source"] = "local"
        else:
            out["_atr_source"] = "tv"
    return out


def format_tv_field_sources(data):
    if not data:
        return "TV透传"
    label_map = {"atr": "ATR", "tp": "TP", "price": "价格"}
    source_vals = {"tv", "local", "tv+local"}

    def _tag(src):
        if src == "tv":
            return "TV透传"
        if src == "local":
            return "本地补全"
        if src == "tv+local":
            return "TV+补全"
        return str(src)

    if any(k in data for k in label_map) and all(
        (not data.get(k)) or str(data.get(k)) in source_vals for k in label_map
    ):
        parts = []
        for key, label in label_map.items():
            src = data.get(key)
            if src:
                parts.append(f"{label}={_tag(src)}")
        return " · ".join(parts) if parts else "TV透传"

    parts = []
    for key, label in (("atr", "ATR"), ("price", "价格")):
        src = data.get(f"_{key}_source")
        if src:
            parts.append(f"{label}={_tag(src)}")
    tp_src = data.get("_tp_source")
    if tp_src:
        parts.append(f"TP={_tag(tp_src)}")
    return " · ".join(parts) if parts else "TV透传"


def format_webhook_log(data):
    action = data.get("action", "?")
    parts = [f"TV {TV_STRATEGY_VERSION} → 【{action}】"]
    if data.get("bot_id"):
        parts.append(f"bot={data['bot_id']}")
    if data.get("side"):
        parts.append(f"side={data['side']}")
    if data.get("price"):
        parts.append(f"price={float(data['price']):.2f}")
    if data.get("stop_loss") or data.get("tv_sl"):
        parts.append(f"sl={float(data.get('stop_loss') or data.get('tv_sl')):.2f}")
    if data.get("leg"):
        parts.append(f"leg={data['leg']}")
    if data.get("reason"):
        parts.append(f"reason={str(data['reason'])[:48]}")
    if data.get("atr"):
        parts.append(f"ATR={float(data['atr']):.2f}")
    tps = [data.get(f"tp{i}") or data.get(f"tv_tp{i}") for i in (1, 2, 3)]
    if any(_has_positive_float(t) for t in tps):
        tp_txt = "/".join(
            f"{float(t):.0f}" if _has_positive_float(t) else "-" for t in tps
        )
        parts.append(f"TP={tp_txt}")
    if data.get("_is_reconcile"):
        parts.append("对账(不下单)")
    if data.get("_is_flatten"):
        parts.append("主动全平")
    return " | ".join(parts)

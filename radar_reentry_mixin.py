#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
递进雷达闸门 + 智能限价再入场（混入 PositionSupervisorBinance）。
终极版：5m/3m 极值优于 TV 挂限价；休眠至激活线；硬止损不重入。
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, Optional

from reentry_profiles import (
    STERILE_MAX_RETRY,
    apply_tier_to_breath_profile,
    arm_stop_price,
    compute_reentry_limit_px,
    get_reentry_profile,
    live_breath_zone_values,
    make_catchup_client_order_id,
    make_reentry_client_order_id,
    pick_best_tier_extreme,
    reentry_enabled,
    tier_label,
    tp_amplitude_scale,
)
from smart_reentry_engine import (
    blank_reentry_state,
    bump_after_reentry_fill,
    evaluate_flat_for_reentry,
    init_cycle_on_open,
    max_unfilled_refreshes,
    open_reentry_window,
    plan_reentry_limit,
)

logger = logging.getLogger(__name__)

# 2026-08-19新增：追单确认重入。常规智能重入(_place_reentry_limit)只挂"比TV/
# 上次开仓价更优"的限价单，专治"刚好在原地被抖出去"——但如果止损时已经先吃到
# 一截浮盈(exit_source=radar_be、超出常规重入区间reentry_zone_atr)、随后价格
# 头也不回地继续冲(实盘复现：ETHUSDT 09:39雷达保本止损@1919.54后，13:00-15:00
# 直接冲到2132，TV仍持仓、VPS却因为等不到"更优价格"的回调永远没能追回去)，
# 那张等便宜价的限价单会一直挂空、永远等不到成交。这里加一条并列的"追单"腿：
# 仅tier=2强趋势、仅radar_be退出、仅reentry_zone判定"超出常规区间"时才会武装，
# 在有限的观察窗口内确认EMA站上+动量非噪音+期间没有跌破(破坏)出场价，才用
# 市价追回去；确认不了或超时就放弃，不重复触发、不叠加开仓次数上限。
_CHASE_CONFIRM_WINDOW_SEC = 900.0  # 观察窗口：15分钟，超时未确认就放弃
_CHASE_MOMENTUM_MIN = 0.15  # bar_momentum_score阈值，滤掉横盘噪音

# 2026-08-20新增：TV心跳持仓。TV每根收盘K线独立发一条自己当前的持仓状态
# (方向+开仓价+止损+TP123)，跟开平仓警报完全解耦——目的是补上现有"TV信号
# vs 实盘"比对（watchdog/check.py 的 fetch_last_tv_signals_all）的结构性盲区：
# 那套比对靠本地 journalctl 里有没有留痕，如果TV那条警报根本没送达VPS
# （网络抖动/nginx瞬断/webhook丢包），本地压根没日志可比，看不见"TV发了但
# VPS完全没收到"这种最彻底的漏单。心跳的权威依据是TV自己的持仓状态，不
# 依赖VPS这边有没有留痕，能补上这个洞。
# 2026-08-20再追加：漏单不再只报警——TV方向是对的，只是VPS没跟上，白搭建
# 系统。宽限期一到，VPS自己拉币安K线，用比TV开仓价更优的价格(多周期取
# 极值)挂限价追回；止损按TV自己的止损"空间"(距离)重新锚定到追回的新
# 成交价，不是硬搬TV原始止损价；限价反复挂不上、预算(复用常规重入的
# ~25分钟刷新预算)耗尽后，直接市价追上不犹豫，止损用同一个距离公式锚定
# 实际市价成交价。全套逻辑用独立的catchup_*状态字段，不跟常规重入/追单
# 确认共用任何计数器。见_tv_heartbeat_catchup_tick/_maybe_start_tv_heartbeat_
# catchup/_place_tv_catchup_limit/_escalate_tv_catchup_to_market/
# _finalize_tv_catchup_fill。
TV_HEARTBEAT_STALE_SEC = 4 * 3600.0  # 心跳超过4小时没刷新就不当最新真相用
TV_HEARTBEAT_GAP_GRACE_SEC = 180.0   # 心跳持仓但实盘空仓，持续超过3分钟才判定漏单
TV_HEARTBEAT_CATCHUP_ENABLED = True  # 追回执行总开关：真实下单路径。2026-08-20
                                      # 先关着部署观察一轮idle-patrol全账户无报错，
                                      # 随后XAU实盘复现同款场景(雷达保本止损出局、
                                      # TV仍持有且继续上涨)，确认打开
CATCHUP_MARKET_FALLBACK_SIZE_MULT = 0.7  # 2026-08-21：市价兜底那条腿没拿到
                                          # "比TV更优价格"这层保护(价格已经跑
                                          # 出去才会走到这一步)，风险收益比不如
                                          # 限价优价那条腿，仓位打七折，止损空间
                                          # /距离公式不受影响(只缩qty，止损公式
                                          # 依然按TV止损距离精确锚定)
CATCHUP_MAX_CONCURRENT_PER_ACCOUNT = 2  # 2026-08-21实盘复现：同一账户短时间内
                                         # 曾同时出现5个品种一起进入"等待EMA
                                         # 确认"的观察名单(ETH/BNB/BCH/SKHYNIX/
                                         # OPENAI)——如果好几个凑巧同时确认，会
                                         # 在很短时间内并发挂出好几笔真实订单，
                                         # 敞口叠加、没有账户级上限兜底。这里加
                                         # 一道账户内"同时已武装(挂着真实订单)"
                                         # 的追回周期数量上限，跟单笔固定中档
                                         # 仓位一样走保守克制的路子，不追求一次
                                         # 吃满所有机会。

# 2026-08-20新增：多周期趋势强度确认——ETH那次雷达保本止损出局后TV仍持有，
# 价格继续了一大段单边行情，VPS却没能跟上，根因是现有呼吸空间/雷达跟随
# 距离即使判定"强趋势档"也有固定上限，遇到真正多周期共振的大趋势不够宽。
# 15m/1h/4h/日线四选三确认一致趋势 + 4h RSI超买超卖(78/22)反向刹车，确认
# 后把TP3+跟随系数上限抬高35%——只放宽"退出的耐心"，不放宽"入场的胆子"，
# 不影响开不开仓/要不要重入，只影响持仓中雷达愿不愿意多给一点跟随空间。
# 详见 position_supervisor_binance.py::_refresh_breathing_coefficient。
MEGA_TREND_TIMEFRAMES = ("15m", "1h", "4h", "1d")
MEGA_TREND_ADX_THRESHOLD = 30.0
MEGA_TREND_VOTE_MIN = 3  # 四选三
MEGA_TREND_RSI_LONG_MAX = 78.0
MEGA_TREND_RSI_SHORT_MIN = 22.0
MEGA_TREND_CEILING_MULT = 1.35
MEGA_TREND_REFRESH_SEC = 240.0


class RadarReentryMixin:
    """递进激活 + 限价再入场。依赖宿主的 binance_client / dingtalk / breath 方法。"""

    def _init_reentry_runtime(self):
        blank = blank_reentry_state()
        for k, v in blank.items():
            setattr(self, k, v)
        self._reentry_open_snap = None
        self._reentry_cycle_aborted = False
        self._base_breath_profile = dict(getattr(self, "breath_profile", None) or {})
        self._clear_chase_watch()
        self._init_tv_catchup_runtime()

    def _init_tv_catchup_runtime(self):
        """TV心跳漏单追回：独立状态机初始化，跟reentry_*/_chase_watch_*
        完全分开的一套字段，不共用任何计数器。"""
        self.catchup_active = False
        self.catchup_phase = ""
        self.catchup_side = None
        self.catchup_tv_entry_frozen = 0.0
        self.catchup_stop_distance_frozen = 0.0
        self.catchup_tps_frozen = [0.0, 0.0, 0.0]
        self.catchup_limit_order_id = None
        self.catchup_limit_px = 0.0
        self.catchup_limit_deadline_ts = 0.0
        self.catchup_unfilled_refreshes = 0
        self.catchup_order_tag = None
        self.catchup_started_ts = 0.0
        self.last_hard_sl_exit_ts = 0.0
        # 内存簿记（不落盘，跟_tv_gap_first_seen_ts同款先例）：判断
        # "这次漏单事件是否已经用掉唯一一次追回机会"，重启后允许重新
        # 评估一次(顶多多追一次而不是漏追)，比持久化一个可能过期的
        # dedupe标记更安全。
        self._catchup_episode_side = None
        self._catchup_episode_entry = 0.0
        self._catchup_episode_resolved = False
        self._catchup_stale_give_up_alerted = False
        self._catchup_capacity_blocked_alerted = False

    def _reentry_state_dict(self) -> Dict[str, Any]:
        return {
            "reentry_attempt": int(getattr(self, "reentry_attempt", 0) or 0),
            "radar_tier": int(getattr(self, "radar_tier", 0) or 0),
            "adx_tier": int(getattr(self, "adx_tier", 1) or 1),
            "reentry_window_deadline_ts": float(
                getattr(self, "reentry_window_deadline_ts", 0) or 0
            ),
            "radar_activation_frac": float(
                getattr(self, "radar_activation_frac", 0.0) or 0.0
            ),
            "radar_activation_price": float(
                getattr(self, "radar_activation_price", 0) or 0
            ),
            "radar_activation_adx": float(
                getattr(self, "radar_activation_adx", 0) or 0
            ),
            "radar_activation_sticky": bool(
                getattr(self, "radar_activation_sticky", False)
            ),
            "cycle_tv_price": float(getattr(self, "cycle_tv_price", 0) or 0),
            "cycle_tv_side": getattr(self, "cycle_tv_side", None),
            "cycle_open_atr": float(getattr(self, "cycle_open_atr", 0) or 0),
            "cycle_entry": float(getattr(self, "cycle_entry", 0) or 0),
            "reentry_active": bool(getattr(self, "reentry_active", False)),
            "reentry_limit_order_id": getattr(self, "reentry_limit_order_id", None),
            "reentry_limit_px": float(getattr(self, "reentry_limit_px", 0) or 0),
            "reentry_limit_deadline_ts": float(
                getattr(self, "reentry_limit_deadline_ts", 0) or 0
            ),
            "reentry_unfilled_refreshes": int(
                getattr(self, "reentry_unfilled_refreshes", 0) or 0
            ),
            "reentry_order_tag": getattr(self, "reentry_order_tag", None),
            "reentry_sterile_fail_count": int(
                getattr(self, "reentry_sterile_fail_count", 0) or 0
            ),
            "last_exit_source": str(getattr(self, "last_exit_source", "") or ""),
            "last_exit_px": float(getattr(self, "last_exit_px", 0) or 0),
            "radar_pending_arm": bool(getattr(self, "radar_pending_arm", True)),
        }

    def _load_reentry_state_from_dict(self, s: Dict[str, Any]):
        if not isinstance(s, dict):
            return
        blank = blank_reentry_state()
        for k, default in blank.items():
            if k not in s:
                continue
            val = s.get(k, default)
            if k in (
                "reentry_attempt", "radar_tier", "adx_tier", "reentry_unfilled_refreshes",
                "reentry_sterile_fail_count",
            ):
                setattr(self, k, int(val or 0))
            elif k in (
                "radar_activation_frac", "reentry_window_deadline_ts", "cycle_tv_price", "cycle_open_atr",
                "cycle_entry", "reentry_limit_px", "reentry_limit_deadline_ts",
                "last_exit_px",
            ):
                setattr(self, k, float(val or 0))
            elif k in ("reentry_active", "radar_pending_arm", "radar_activation_sticky"):
                setattr(self, k, bool(val))
            elif k == "reentry_order_tag":
                setattr(self, k, str(val) if val else None)
            else:
                setattr(self, k, val)

    def _tv_catchup_state_dict(self) -> Dict[str, Any]:
        """TV心跳漏单追回：只持久化"挂着真实交易所订单"这部分状态
        （对齐reentry_*的持久化理由），episode去重字段(_catchup_episode_*)
        纯内存，不落盘，见_init_tv_catchup_runtime注释。"""
        return {
            "catchup_active": bool(getattr(self, "catchup_active", False)),
            "catchup_phase": str(getattr(self, "catchup_phase", "") or ""),
            "catchup_side": getattr(self, "catchup_side", None),
            "catchup_tv_entry_frozen": float(
                getattr(self, "catchup_tv_entry_frozen", 0) or 0
            ),
            "catchup_stop_distance_frozen": float(
                getattr(self, "catchup_stop_distance_frozen", 0) or 0
            ),
            "catchup_tps_frozen": list(
                getattr(self, "catchup_tps_frozen", None) or [0.0, 0.0, 0.0]
            ),
            "catchup_limit_order_id": getattr(self, "catchup_limit_order_id", None),
            "catchup_limit_px": float(getattr(self, "catchup_limit_px", 0) or 0),
            "catchup_limit_deadline_ts": float(
                getattr(self, "catchup_limit_deadline_ts", 0) or 0
            ),
            "catchup_unfilled_refreshes": int(
                getattr(self, "catchup_unfilled_refreshes", 0) or 0
            ),
            "catchup_order_tag": getattr(self, "catchup_order_tag", None),
            "catchup_started_ts": float(getattr(self, "catchup_started_ts", 0) or 0),
            "last_hard_sl_exit_ts": float(getattr(self, "last_hard_sl_exit_ts", 0) or 0),
        }

    def _load_tv_catchup_state_from_dict(self, s: Dict[str, Any]):
        if not isinstance(s, dict):
            return
        if "catchup_active" not in s:
            return  # 旧state文件没有这批字段：保持_init_tv_catchup_runtime的默认值
        self.catchup_active = bool(s.get("catchup_active", False))
        self.catchup_phase = str(s.get("catchup_phase", "") or "")
        self.catchup_side = s.get("catchup_side")
        self.catchup_tv_entry_frozen = float(s.get("catchup_tv_entry_frozen", 0) or 0)
        self.catchup_stop_distance_frozen = float(
            s.get("catchup_stop_distance_frozen", 0) or 0
        )
        self.catchup_tps_frozen = list(s.get("catchup_tps_frozen") or [0.0, 0.0, 0.0])
        self.catchup_limit_order_id = s.get("catchup_limit_order_id")
        self.catchup_limit_px = float(s.get("catchup_limit_px", 0) or 0)
        self.catchup_limit_deadline_ts = float(
            s.get("catchup_limit_deadline_ts", 0) or 0
        )
        self.catchup_unfilled_refreshes = int(
            s.get("catchup_unfilled_refreshes", 0) or 0
        )
        self.catchup_order_tag = s.get("catchup_order_tag")
        self.catchup_started_ts = float(s.get("catchup_started_ts", 0) or 0)
        self.last_hard_sl_exit_ts = float(s.get("last_hard_sl_exit_ts", 0) or 0)

    def _clear_reentry_cycle(self, source=""):
        """新 TV / 硬止损 / 周期结束：清再入场与周期字段。"""
        try:
            self._cancel_reentry_limit(reason=source or "清周期")
        except Exception:
            pass
        blank = blank_reentry_state()
        for k, v in blank.items():
            setattr(self, k, v)
        self._reentry_open_snap = None
        self._reentry_cycle_aborted = False
        base = getattr(self, "_base_breath_profile", None)
        if isinstance(base, dict) and base:
            self.breath_profile = dict(base)
        if source:
            logger.info(f"🧹 [{self.symbol}] 再入场周期已清零 | {source}")

    def _apply_tier_breath_overlay(self):
        base = getattr(self, "_base_breath_profile", None) or getattr(
            self, "breath_profile", None
        ) or {}
        if not getattr(self, "_base_breath_profile", None) and base:
            self._base_breath_profile = dict(base)
        attempt = int(
            getattr(self, "radar_tier", 0)
            or getattr(self, "adx_tier", 1)
            or 1
        )
        self.breath_profile = apply_tier_to_breath_profile(
            dict(self._base_breath_profile or base),
            attempt,
            get_reentry_profile(self.symbol),
        )
        # 呼吸空间(TP1-TP2/TP2-TP3)按实时ADX连续微调，不再锁死在开仓时的
        # 离散档位；叠加实时动量做有界微调(见_adx_momentum_t)——同样ADX下
        # 正在加速冲的多给一点空间，横盘磨的收紧一点。ATR 不动，仍全程只
        # 信 TV 锁定值（不重蹈 v16.4.0 VPS拉ATR与TV对不上而弃用的老路，
        # last_adx/last_momentum 本来就在持续更新，不新增数据源）。
        # last_adx 尚未就绪时优雅退回本次离散档位值。
        try:
            b12, b23 = live_breath_zone_values(
                float(getattr(self, "last_adx", 0) or 0),
                get_reentry_profile(self.symbol),
                fallback_tier=attempt,
                momentum=float(getattr(self, "last_momentum", 0) or 0),
            )
            self.breath_profile["breath_tp12"] = b12
            self.breath_profile["breath_tp23"] = b23
        except Exception:
            pass
        # v2.6：呼吸空间（止损离最高点多远）按这笔单自己的TP1距离重新校准，
        # 见 reentry_profiles.tp_amplitude_scale 的docstring——同一品种不同
        # 账户可能接完全不同的TV策略(窄止盈TP1≈1×ATR / 宽止盈TP1≈6-9×ATR)，
        # 死记固定ATR倍数的表格对其中一边必然错位，用这笔单实际TP1距离
        # 相对标定基准(tp1_atr=1.35)做等比例缩放，自动适配当前策略振幅。
        # v2.6.2教训：step_trigger_atr/step_advance_atr（阶梯止损"多久收一次
        # 档"）曾经也套用同一个scale，结果2026-08-11实盘发现宽止盈账户
        # (C账户ZEC scale=1.5)价格已经favorable移动2.25×ATR，阶梯却因为
        # 触发门槛被放宽到2.10×ATR迟迟不收档，浮盈几乎没锁住——阶梯止损是
        # "多久该收紧一档"，跟着的是波动率节奏，不是目标定多远，跟呼吸空间
        # (止损离最高点该留多宽)是两件事，不该共用同一个缩放，已经拆开。
        try:
            tp1 = (self.tv_tps or [0])[0]
            scale = tp_amplitude_scale(
                float(tp1 or 0),
                float(getattr(self, "watched_entry", 0) or 0),
                self._get_locked_initial_atr(),
                float(self.breath_profile.get("tp1_atr") or 1.35),
            )
            self._tp_amplitude_scale = scale
            if abs(scale - 1.0) > 1e-9:
                for k in ("breath_tp12", "breath_tp23"):
                    if k in self.breath_profile:
                        self.breath_profile[k] = round(float(self.breath_profile[k]) * scale, 4)
            # TP3确认过渡区（防"一冲即回"假突破）默认写死1×ATR确认距离，
            # 同样是按窄止盈基准标定的——宽止盈账户TP3远在15×ATR开外时，
            # 1×ATR只占整段距离的6%，确认门槛太松；窄止盈账户TP3才2-3×ATR
            # 时，1×ATR却占了三分之一强，确认门槛又太紧。同一个scale一并
            # 校准，不用等真出现"TP3确认区也错位"才补。
            base_confirm_atr = float(
                self.breath_profile.get("tp3_confirm_atr")
                if self.breath_profile.get("tp3_confirm_atr") is not None
                else 1.0
            )
            self.breath_profile["tp3_confirm_atr"] = round(base_confirm_atr * scale, 4)
        except Exception:
            self._tp_amplitude_scale = 1.0

    def _begin_open_radar_dormant(self, *, side, entry, tv_price, open_atr,
                                  reentry_attempt=None, adx_tier=None, radar_tier=None,
                                  adx=None, activation_ratio=None):
        """开仓后：硬+TP 已挂；雷达休眠至雷达激活线。"""
        attempt = int(
            reentry_attempt if reentry_attempt is not None
            else getattr(self, "reentry_attempt", 0) or 0
        )
        at = int(
            adx_tier if adx_tier is not None else getattr(self, "adx_tier", 1) or 1
        )
        rt = int(
            radar_tier if radar_tier is not None
            else getattr(self, "radar_tier", at) or at
        )
        adx_v = float(
            adx if adx is not None
            else getattr(self, "last_adx", 0)
            or getattr(self, "radar_activation_adx", 0)
            or 25.0
        )
        tps = list(getattr(self, "tv_tps", None) or [])
        tp1_v = float(tps[0] or 0) if tps else 0.0
        tp2_v = float(tps[1] or 0) if len(tps) > 1 else 0.0
        st = init_cycle_on_open(
            side=side,
            tv_price=tv_price,
            entry=entry,
            open_atr=open_atr,
            reentry_attempt=attempt,
            symbol=self.symbol,
            adx_tier=at,
            radar_tier=rt,
            adx=adx_v,
            activation_ratio=activation_ratio,
            tp1=tp1_v,
            tp2=tp2_v,
        )
        for k, v in st.items():
            setattr(self, k, v)
        self.radar_activated = False
        self._radar_handoff_done = False
        self._radar_armed_after_tp1 = False
        self._radar_activation_notified = False
        self._radar_notify_pending = False
        self._clear_chase_watch()
        frac = float(st.get("radar_activation_frac") or 0)
        attempt = int(getattr(self, "reentry_attempt", 0) or 0)
        # v1.0 §5.1：init_cycle_on_open 已内用 radar_gate_price_from_tps(tp1, tp2, attempt)
        # 保证 tp1/tp2 均已传入；若 gate=0（tp1/tp2 缺失）则写死标签供诊断
        gate_px = float(st.get("radar_activation_price") or 0)
        gate_lab = f"绝对价格锚定 {'(重入=TP2)' if attempt >= 1 else '(首次=min(距TP1剩20%,1.5×ATR))'}"
        self._radar_trigger_gate = f"被动雷达·{gate_lab}"
        self._apply_tier_breath_overlay()
        logger.info(
            f"⏳ [{self.symbol}] 雷达休眠至激活 "
            f"mode={gate_lab} attempt={attempt} "
            f"ratio={frac:.0%} gate≈{gate_px:.4f}"
        )

    def _radar_is_dormant(self) -> bool:
        """未激活一律休眠。pending_arm=False（如 TP3 互斥）不得误开雷达改单。"""
        return not bool(getattr(self, "radar_activated", False))

    def _maybe_arm_radar_on_activation(self, live_qty, curr_px, source=""):
        """
        规格 v2.1：价触激活线（或 sticky）：挂雷达 STOP@保本位，开始雷达动态跟随。
        - 首次开仓：价格到达 (TP1+TP2)/2 即武装（TP1是否成交仅记日志不阻塞）
        - 重入开仓：价格到达 TP2 才武装
        - 防御兜底：TP1+TP2 均已成交时直接强制武装（覆盖所有边界）
        """
        if bool(getattr(self, "radar_activated", False)):
            return True
        force = "强制" in str(source or "") or "force" in str(source or "").lower()
        # 主闸：现价触线 / sticky（插针回撤）——不依赖 TP 限价是否成交
        if not force and not self._activation_reached_for_arm(curr_px):
            # TP1+TP2 已成交：视为必须启动，不再卡在价触判定
            consumed = set(getattr(self, "tp_levels_consumed", []) or [])
            if not (1 in consumed and 2 in consumed):
                return False
            force = True
            source = f"{source or 'arm'}·TP12已成交"
        live_qty = float(live_qty or self.watched_qty or 0)
        if live_qty <= 0:
            return False
        entry = float(getattr(self, "watched_entry", 0) or 0)
        side = str(getattr(self, "current_side", "") or "").strip().upper()
        atr = float(
            getattr(self, "open_atr", 0)
            or getattr(self, "cycle_open_atr", 0)
            or getattr(self, "current_atr", 0)
            or 0
        )
        if atr <= 0 and hasattr(self, "_get_locked_initial_atr"):
            try:
                atr = float(self._get_locked_initial_atr() or 0)
            except Exception:
                atr = 0.0
        profile = getattr(self, "breath_profile", None) or {}
        tick = float(profile.get("tick_size") or 0.01)
        fee_pct = profile.get("fee_cover_pct")
        init = float(
            arm_stop_price(
                side, entry, atr,
                tick_size=tick,
                fee_cover_pct=fee_pct,
            ) or 0
        )
        if init <= 0:
            init = float(getattr(self, "initial_stop", 0) or 0)
        if init <= 0:
            init = float(getattr(self, "current_sl", 0) or 0)
        # 硬止损价可作兜底 initial（避免 atr 丢失导致永不武装）
        if init <= 0:
            hard = float(getattr(self, "frozen_hard_sl_px", 0) or getattr(self, "tv_sl", 0) or 0)
            if hard > 0:
                init = hard
                logger.warning(
                    f"⚠️ [{self.symbol}] 武装用硬止损价兜底 initial_stop={init:.2f} | {source}"
                )
        if init <= 0:
            logger.warning(f"⚠️ [{self.symbol}] 达激活线但无 initial_stop | {source}")
            return False
        self.initial_stop = float(init)
        self.current_sl = float(init)
        self.tv_sl = float(init)
        self._apply_tier_breath_overlay()
        self._radar_arming = True
        try:
            ok = self._ensure_radar_sl(init, live_qty=live_qty, for_handoff=True)
        finally:
            self._radar_arming = False
        if not ok:
            logger.warning(
                f"⚠️ [{self.symbol}] 达激活线但雷达 STOP 未挂出 @{init:.2f} | "
                f"{source or '价触'} → 保持休眠重试"
            )
            return False
        self.radar_activated = True
        self.radar_pending_arm = False
        self._radar_handoff_done = True
        self._radar_armed_after_tp1 = True
        frac = float(getattr(self, "radar_activation_frac", 0.0) or 0.0)
        attempt = int(getattr(self, "reentry_attempt", 0) or 0)
        open_kind = "重入开仓" if attempt >= 1 else "首次开仓"
        # 规格 v1.0：绝对价格锚定
        self._radar_trigger_gate = (
            f"雷达已激活·保本起步·{open_kind}·绝对价格锚定 | "
            f"{source or '价触'}"
        )
        self._radar_stage_last = 1
        if not getattr(self, "_radar_arm_ding_sent", False):
            self._radar_notify_pending = True
            try:
                self._report_radar_first_activation(
                    live_qty, curr_px, init, sl_placed=True,
                    trigger_gate=self._radar_trigger_gate,
                )
            except Exception as e:
                logger.debug(f"雷达激活钉钉跳过: {e}")
        self._save_state()
        logger.info(
            f"📡 [{self.symbol}] 雷达已激活·保本起步 @{init:.2f} "
            f"(entry={entry:.2f}) | {self._radar_trigger_gate} | hung=True"
        )
        return True

    def _snapshot_cycle_for_reentry(self) -> Dict[str, Any]:
        consumed = list(getattr(self, "tp_levels_consumed", None) or [])
        tp1_ever = (
            1 in consumed
            or bool(getattr(self, "_tp1_filled_hint", False))
            or bool(getattr(self, "_ws_tp1_fill_hint", False))
        )
        return {
            "side": getattr(self, "current_side", None),
            "entry": float(getattr(self, "watched_entry", 0) or 0),
            "qty": float(
                getattr(self, "initial_qty", 0)
                or getattr(self, "watched_qty", 0)
                or 0
            ),
            "atr": float(
                getattr(self, "cycle_open_atr", 0)
                or getattr(self, "open_atr", 0)
                or getattr(self, "current_atr", 0)
                or 0
            ),
            "tv_price": float(
                getattr(self, "cycle_tv_price", 0)
                or getattr(self, "tv_price", 0)
                or 0
            ),
            "reentry_attempt": int(getattr(self, "reentry_attempt", 0) or 0),
            "radar_activation_frac": float(
                getattr(self, "radar_activation_frac", 0) or 0.0
            ),
            "tv_tps": list(getattr(self, "tv_tps", None) or [0, 0, 0]),
            "frozen_hard_sl_px": float(getattr(self, "frozen_hard_sl_px", 0) or 0),
            "initial_stop": float(getattr(self, "initial_stop", 0) or 0),
            "current_sl": float(getattr(self, "current_sl", 0) or 0),
            "radar_activated": bool(getattr(self, "radar_activated", False)),
            "tp_levels_consumed": consumed,
            "tp1_ever_filled": bool(tp1_ever),
            "adx_tier": int(getattr(self, "adx_tier", 1) or 1),
            "payload": dict(
                (getattr(self, "last_tv_signal", None) or {}).get("payload")
                or getattr(self, "last_tv_signal", None)
                or {}
            ) if isinstance(getattr(self, "last_tv_signal", None), dict) else {},
        }

    def _exit_px_near_hard(self, exit_px: float) -> bool:
        hard = float(getattr(self, "frozen_hard_sl_px", 0) or 0)
        px = float(exit_px or 0)
        if hard <= 0 or px <= 0:
            return False
        if abs(px - hard) <= max(2.5, px * 0.002):
            return True
        # WS 曾捕获 STOP 成交：即使哨兵醒来时 mark 已漂离，仍认硬止损
        hint = getattr(self, "_ws_hard_sl_fill_hint", None) or {}
        try:
            ts = float(hint.get("ts") or 0)
            hpx = float(hint.get("px") or hint.get("stop") or 0)
        except (TypeError, ValueError):
            ts, hpx = 0.0, 0.0
        if ts > 0 and (time.time() - ts) <= 600.0:
            if hpx > 0 and abs(hpx - hard) <= max(3.0, hard * 0.003):
                return True
            if abs(px - hard) <= max(8.0, hard * 0.005):
                return True
        return False

    def _latch_ws_hard_sl_fill(self, fill_px=0.0, stop_px=0.0, source=""):
        """UD-WS STOP 成交闩锁（pause 期间也记，供空仓归因）。"""
        hard = float(getattr(self, "frozen_hard_sl_px", 0) or 0)
        px = float(fill_px or stop_px or 0)
        sp = float(stop_px or 0)
        if hard > 0 and sp > 0 and abs(sp - hard) > max(5.0, hard * 0.01):
            # 明显不是本仓硬止损价（可能是雷达腿）→ 仍记，但标 soft
            pass
        self._ws_hard_sl_fill_hint = {
            "ts": time.time(),
            "px": px or sp or hard,
            "stop": sp or hard,
            "source": str(source or "ws_stop"),
        }
        logger.info(
            f"📌 [{self.symbol}] WS硬止损成交闩锁 "
            f"px={float(self._ws_hard_sl_fill_hint['px']):.2f} "
            f"stop={float(self._ws_hard_sl_fill_hint['stop']):.2f} | {source}"
        )

    def _fetch_reentry_klines(self):
        """拉取 5m / 3m K 线（≥3 根，供 parse_kline_extreme 取最近已收盘根）。"""
        from binance_client import binance_client
        k5, k3 = None, None
        try:
            k5 = binance_client.fetch_klines(self.symbol, interval="5m", limit=3)
        except Exception as e:
            logger.warning(f"[{self.symbol}] 拉5m K线失败: {e}")
        try:
            k3 = binance_client.fetch_klines(self.symbol, interval="3m", limit=3)
        except Exception as e:
            logger.debug(f"[{self.symbol}] 拉3m K线失败: {e}")
        return k5, k3

    def _clear_reentry_order_tag(self, reason=""):
        """仅在成交 / 确认撤销 / TTL 刷新前调用：释放本地标签后才允许新挂。"""
        old = getattr(self, "reentry_order_tag", None)
        self.reentry_order_tag = None
        if old:
            logger.info(
                f"🏷️ [{self.symbol}] 再入订单标签已释放 tag={old} | {reason}"
            )

    def _clear_catchup_order_tag(self, reason=""):
        old = getattr(self, "catchup_order_tag", None)
        self.catchup_order_tag = None
        if old:
            logger.info(f"🏷️ [{self.symbol}] 追回订单标签已释放 tag={old} | {reason}")

    def _cancel_catchup_limit(self, reason=""):
        from binance_client import binance_client

        oid = getattr(self, "catchup_limit_order_id", None)
        tag = getattr(self, "catchup_order_tag", None)
        if oid:
            try:
                binance_client.cancel_order(self.symbol, order_id=oid)
                logger.info(f"🗑️ [{self.symbol}] 撤追回限价 id={oid} tag={tag} | {reason}")
            except Exception as e:
                try:
                    binance_client.cancel_order(self.symbol, order={"orderId": oid})
                except Exception as e2:
                    logger.debug(f"撤追回限价跳过: {e}/{e2}")
        self.catchup_limit_order_id = None
        self.catchup_limit_px = 0.0
        self.catchup_limit_deadline_ts = 0.0
        self._clear_catchup_order_tag(reason=reason or "撤单释放标签")

    def _clear_tv_catchup_cycle(self, source=""):
        """撤掉在飞的追回限价单，重置本轮周期字段（不含episode去重字段——
        见_init_tv_catchup_runtime注释，那批字段只在成交/中止/心跳重新FLAT
        时才应该重置）。任何"仓位归零"的路径都该调用一次，避免上一次
        追回周期留下的挂单/字段污染下一段生命周期。"""
        try:
            self._cancel_catchup_limit(reason=source or "清追回周期")
        except Exception:
            pass
        self.catchup_active = False
        self.catchup_phase = ""
        self.catchup_side = None
        self.catchup_tv_entry_frozen = 0.0
        self.catchup_stop_distance_frozen = 0.0
        self.catchup_tps_frozen = [0.0, 0.0, 0.0]
        self.catchup_unfilled_refreshes = 0
        self.catchup_started_ts = 0.0
        if source:
            logger.info(f"🧹 [{self.symbol}] TV心跳追回周期已清零 | {source}")

    def _reset_tv_catchup_episode(self, source=""):
        """仅重置episode去重簿记——TV心跳重新变FLAT才代表这次漏单事件
        彻底结束，之后任何新的LONG/SHORT心跳都算全新事件。"""
        self._catchup_episode_side = None
        self._catchup_episode_entry = 0.0
        self._catchup_episode_resolved = False

    def _ensure_sterile_for_reentry(self, reason="再入前清场") -> bool:
        """
        仓位归零后挂再入限价前：必须 qty=0 且挂单列表为空。
        最多 STERILE_MAX_RETRY 轮；失败 → 钉钉 + 暂停该品种。

        修复（v16.9.2）：
        - prefer_ws=True 避免强制 REST 打 -1003
        - get_open_orders 前检查 IP 冷却；冷却中跳过盘口扫描
        - 连续 REST 间插入 sleep，避免堆积
        """
        max_n = int(STERILE_MAX_RETRY)
        last_detail = ""
        for i in range(1, max_n + 1):
            try:
                self._purge_all_defense_orders_on_flat(
                    f"{reason}·第{i}轮", max_rounds=6,
                )
            except Exception as e:
                logger.warning(f"[{self.symbol}] 再入清场撤单异常: {e}")
            time.sleep(0.4)  # 修复（v16.9.2）：撤单后等一下再查单
            # 再撤可能残留的开仓向限价（含旧再入单）
            try:
                from binance_client import binance_client, is_orders_query_failed, IpRateLimitedError
                # IP 冷却中：跳过盘口扫描，保守认为已净
                try:
                    if binance_client.ip_rate_limit_remaining() > 0:
                        logger.warning(
                            f"🛡️ [{self.symbol}] {reason} IP冷却中 → 跳过查单，保守通过"
                        )
                        self.reentry_sterile_fail_count = 0
                        return True
                except Exception:
                    pass
                book = binance_client.get_open_orders(self.symbol)
                if is_orders_query_failed(book):
                    last_detail = "挂单=QUERY_FAILED"
                    logger.error(
                        f"🚫 [{self.symbol}] {reason} 查单失败 → 拒挂（fail-closed）"
                    )
                    time.sleep(0.6 * i)
                    continue
                for o in (book or []):
                    if not isinstance(o, dict):
                        continue
                    oid = o.get("orderId") or o.get("algoId")
                    if oid:
                        try:
                            binance_client.cancel_order(
                                self.symbol, order_id=oid,
                            )
                        except Exception:
                            try:
                                binance_client.cancel_order(
                                    self.symbol, order=o,
                                )
                            except Exception:
                                pass
            except Exception as e:
                last_detail = f"撤单异常:{e}"
                time.sleep(0.6 * i)
                continue

            if hasattr(self, "_wait_verify") and hasattr(self, "_verify_sterile_flat"):
                ok = self._wait_verify(
                    self._verify_sterile_flat, retries=6, delay=0.4,
                )
            elif hasattr(self, "_verify_sterile_flat"):
                ok = bool(self._verify_sterile_flat())
            else:
                # 修复（v16.9.2）：prefer_ws=True 避免强制 REST 触发 -1003
                pos = self._get_active_position(prefer_ws=True)
                ok = pos != "QUERY_FAILED" and not (
                    pos and float(pos.get("size") or 0) > 0
                )
            if ok:
                self.reentry_sterile_fail_count = 0
                logger.info(
                    f"🧹 [{self.symbol}] {reason} 无菌通过 | 第{i}/{max_n}轮"
                )
                return True
            last_detail = str(
                getattr(self, "_last_sterile_flat_fail_detail", "") or "无菌未过"
            )
            logger.warning(
                f"⚠️ [{self.symbol}] {reason} 第{i}/{max_n}轮未过 | {last_detail}"
            )
            time.sleep(0.8 * i)

        self.reentry_sterile_fail_count = int(
            getattr(self, "reentry_sterile_fail_count", 0) or 0
        ) + 1
        logger.warning(
            f"[内测-仅告警] [{self.symbol}] {reason} 失败超限({max_n}轮) → 内测模式不暂停 | {last_detail}"
        )
        try:
            import dingtalk
            self._call_dingtalk(
                dingtalk.report_reentry_abandon,
                side=str(getattr(self, "cycle_tv_side", "")),
                reason=f"连续{max_n}轮未净场: {reason} | {last_detail}",
                attempt=int(max_n),
                max_attempts=int(max_n),
                exit_source=str(reason),
                exit_price=0,
                entry_price=0,
                symbol=self.symbol,
                tier_label_str=str(tier_label(int(getattr(self, "adx_tier", 2) or 2))),
            )
        except Exception:
            pass
        return False

    def _maybe_start_smart_limit_reentry(self, snap: Dict[str, Any], meta: Dict[str, Any]):
        """仓位归零且微赚/保本后挂限价再入；硬止损/亏损/超次不挂。"""
        if not reentry_enabled(self.symbol):
            logger.info(f"⏸ [{self.symbol}] 智能再入已关闭(enabled=False)")
            return False
        if getattr(self, "_reentry_cycle_aborted", False):
            return False
        if bool(getattr(self, "reentry_active", False)):
            return False
        # 红色铁律：本地标签未清 → 绝不再挂
        if getattr(self, "reentry_order_tag", None):
            logger.error(
                f"🚫 [{self.symbol}] 本地再入标签仍在 "
                f"tag={self.reentry_order_tag} → 拒启动（防狂挂）"
            )
            return False
        if self.monitoring or float(getattr(self, "watched_qty", 0) or 0) > 0:
            return False
        # 挂单前确认空仓：prefer_ws=True 避免冷却期强制 REST 触发 -1003
        pos = self._get_active_position(prefer_ws=True)
        if pos == "QUERY_FAILED":
            return False
        if pos and float(pos.get("size") or 0) > 0:
            return False

        snap = snap or {}
        meta = meta or {}
        exit_src = str(meta.get("exit_source") or "")
        # 2026-08-20实盘复现(B账户XAU)：snap["side"]/meta["side"]偶发已经是
        # 空的(current_side在这之前的某个环节已经被清掉)，之前武装追单确认
        # 观察窗时直接拿到空side存进去，_check_chase_reentry_confirmation
        # 一看side无效就自我清掉，整个观察窗形同虚设、白武装一次。
        # last_tv_side不在_reset_breath_ledger_on_flat的清空列表里，兜底更可靠。
        side = str(
            snap.get("side") or meta.get("side")
            or getattr(self, "last_tv_side", "") or ""
        ).upper()
        entry = float(snap.get("entry") or meta.get("entry_px") or 0)
        exit_px = float(
            meta.get("live_exit_px")
            or getattr(self, "last_exit_px", 0)
            or 0
        )
        atr = float(snap.get("atr") or 0)
        attempt = int(snap.get("reentry_attempt") or 0)

        if self._exit_px_near_hard(exit_px) or exit_src in ("vps_hard_sl", "hard_sl"):
            self._clear_reentry_cycle(source="硬止损出局·禁止再入")
            return False

        window_ts = float(open_reentry_window(self.symbol))
        self.reentry_window_deadline_ts = window_ts
        ok, why = evaluate_flat_for_reentry(
            exit_source=exit_src,
            side=side,
            entry=entry,
            exit_px=exit_px,
            atr=atr,
            reentry_attempt=attempt,
            symbol=self.symbol,
            window_deadline_ts=window_ts,
            tp1_ever_filled=bool(snap.get("tp1_ever_filled")),
            adx_tier=int(snap.get("adx_tier") if snap.get("adx_tier") is not None else 1),
        )
        if not ok:
            logger.info(
                f"🚫 [{self.symbol}] 不启动再入场: {why} | "
                f"src={exit_src} exit={exit_px:.2f} attempt={attempt} "
                f"tp1_filled={bool(snap.get('tp1_ever_filled'))} "
                f"tier={snap.get('adx_tier')}"
            )
            if why in (
                "hard_sl_no_reentry", "max_reentries", "tv_close_no_reentry",
                "tp1_already_filled",
            ):
                self._clear_reentry_cycle(source=why)
            elif (
                why == "outside_reentry_zone"
                and exit_src == "radar_be"
                and not bool(snap.get("tp1_ever_filled"))
                and attempt < int(get_reentry_profile(self.symbol).get("max_reentries") or 1)
            ):
                # 2026-08-20：不再要求adx_tier==2——开仓那一刻的静态tier快照
                # 不代表后面行情走势，武装观察窗后交给多周期实时确认
                # (_check_chase_reentry_confirmation)重新判断值不值得追，
                # 比一次性tier标签更新鲜也更准（SKHYNIXUSDT实盘复现：弱档
                # 开仓，之前直接被tier_not_strong永久挡死，连这个判断都
                # 走不到）。
                self._arm_chase_reentry_watch(
                    side=side, exit_px=exit_px, atr=atr, attempt=attempt,
                    deadline_ts=window_ts,
                )
            return False

        # 闭环第一步：无菌确认（仓+单皆零）后才允许挂再入限价
        if not self._ensure_sterile_for_reentry(reason="智能再入·仓位归零清场"):
            return False

        self.cycle_tv_side = side
        self.cycle_tv_price = float(snap.get("tv_price") or 0)
        self.cycle_open_atr = atr
        self.cycle_entry = entry
        self.reentry_attempt = attempt
        self.radar_tier = attempt
        # 规格 v1.0：雷达激活采用绝对价格锚定，不再保存 ADX/TP1 距离百分比。
        self.radar_activation_frac = float(snap.get("radar_activation_frac") or 0.0)
        self.last_exit_source = exit_src
        self.last_exit_px = exit_px
        self.reentry_unfilled_refreshes = 0
        self._reentry_open_snap = dict(snap)
        self._reentry_open_snap["exit_source"] = exit_src
        self._reentry_open_snap["exit_px"] = exit_px

        placed = self._place_reentry_limit(side=side, reason="雷达保本·智能再入")
        if not placed:
            logger.warning(f"⚠️ [{self.symbol}] 再入限价挂出失败")
            return False
        try:
            from ops_log import audit as ops_audit
            ops_audit(
                f"{self.symbol} reentry_limit_placed side={side} "
                f"attempt={attempt} limit={float(getattr(self, 'reentry_limit_px', 0) or 0):.4f} "
                f"tag={getattr(self, 'reentry_order_tag', None)} "
                f"exit={exit_src}@{exit_px:.4f} tv={float(getattr(self, 'cycle_tv_price', 0) or 0):.4f}"
            )
        except Exception:
            pass
        try:
            import dingtalk
            self._call_dingtalk(
                dingtalk.report_reentry_attempt,
                side=side,
                qty=qty,
                reentry_price=float(getattr(self, "reentry_limit_px", 0) or 0),
                exit_price=float(exit_px),
                exit_source=str(exit_src),
                regime=int(getattr(self, "adx_tier", 3) or 3),
                tier_label_str=str(tier_label(int(getattr(self, "adx_tier", 2) or 2))),
                attempt=int(attempt),
                max_attempts=int(get_reentry_profile(self.symbol).get("max_reentries") or 1),
                tv_price=float(getattr(self, "cycle_tv_price", 0) or 0),
                entry_price=float(getattr(self, "cycle_tv_price", 0) or 0),
                symbol=self.symbol,
            )
        except Exception:
            pass
        return True

    def _clear_chase_watch(self, reason=""):
        self._chase_watch_active = False
        self._chase_watch_side = None
        self._chase_watch_exit_px = 0.0
        self._chase_watch_atr = 0.0
        self._chase_watch_attempt = 0
        self._chase_watch_deadline_ts = 0.0
        self._chase_watch_armed_ts = 0.0

    def _arm_chase_reentry_watch(self, *, side, exit_px, atr, attempt, deadline_ts=None):
        """武装追单确认观察窗——不下单，只记录状态，交给巡检周期性确认。

        2026-08-20：观察窗口从固定15分钟(_CHASE_CONFIRM_WINDOW_SEC)改为复用
        品种自己的重入窗口(open_reentry_window，调用方已经算好传进来)——
        原始案例(ETH 09:39止损、13:00-15:00才冲起来)本身就横跨几个小时，
        固定15分钟根本盖不住这种"隔了大半天才确认"的真实行情，未传
        deadline_ts时才退回旧的15分钟兜底（防止有遗漏的调用点传漏）。
        """
        if bool(getattr(self, "_chase_watch_active", False)):
            return
        self._chase_watch_active = True
        self._chase_watch_side = str(side or "").upper()
        self._chase_watch_exit_px = float(exit_px or 0)
        self._chase_watch_atr = float(atr or 0)
        self._chase_watch_attempt = int(attempt or 0)
        self._chase_watch_armed_ts = time.time()
        dl = float(deadline_ts or 0)
        self._chase_watch_deadline_ts = dl if dl > time.time() else (
            time.time() + _CHASE_CONFIRM_WINDOW_SEC
        )
        try:
            self._save_state()
        except Exception:
            pass
        window_sec = self._chase_watch_deadline_ts - time.time()
        logger.info(
            f"📡 [{self.symbol}] 武装追单确认窗口 {window_sec:.0f}s | "
            f"side={self._chase_watch_side} exit={self._chase_watch_exit_px:.2f} "
            f"→ 观察多周期EMA+动量，确认继续延续才市价追回"
        )

    def _multi_tf_trend_signal(self, side: str, timeframes, adx_threshold=None) -> Dict[str, Any]:
        """
        多周期趋势一致性评估（纯评估，不下单）。对每个周期各拉K线，判断
        "现价站上快线+快线在慢线上方(方向跟side一致)+动量非噪音"；
        adx_threshold给定时额外要求该周期Wilder ADX超过阈值才算过。
        返回 {tf: {"pass":bool,"close":...,"ema_fast":...,"ema_slow":...,
        "momentum":...,"adx":...,"bars":[...]}, ..., "score": int, "total": int}。
        每个周期的bars顺手带出，调用方(如RSI刹车)能直接复用不用再拉一次。
        """
        result: Dict[str, Any] = {"score": 0, "total": len(timeframes)}
        side = str(side or "").upper()
        if side not in ("LONG", "SHORT"):
            return result
        try:
            from binance_client import binance_client
            from market_engine import ema_series, bar_momentum_score, wilder_adx
        except Exception as e:
            logger.debug(f"[{self.symbol}] 多周期趋势评估依赖导入跳过: {e}")
            return result
        for tf in timeframes:
            entry: Dict[str, Any] = {"pass": False}
            result[tf] = entry
            try:
                bars = binance_client.fetch_klines(self.symbol, interval=tf, limit=60)
            except Exception as e:
                logger.debug(f"[{self.symbol}] 多周期趋势评估取{tf}K线跳过: {e}")
                continue
            if not bars or len(bars) < 31:
                continue
            closes = [float(b[4]) for b in bars]
            ema_f = ema_series(closes, 15)
            ema_s = ema_series(closes, 30)
            if not ema_f or not ema_s:
                continue
            last_close = closes[-1]
            fast_now, slow_now = ema_f[-1], ema_s[-1]
            mom = bar_momentum_score(bars, lookback=3)
            entry.update({
                "close": last_close, "ema_fast": fast_now, "ema_slow": slow_now,
                "momentum": mom, "bars": bars,
            })
            if side == "LONG":
                ok = last_close > fast_now > slow_now and mom >= _CHASE_MOMENTUM_MIN
            else:
                ok = last_close < fast_now < slow_now and mom <= -_CHASE_MOMENTUM_MIN
            if ok and adx_threshold is not None:
                adx = wilder_adx(bars, 14)
                entry["adx"] = adx
                ok = adx >= float(adx_threshold)
            entry["pass"] = bool(ok)
            if ok:
                result["score"] += 1
        return result

    def _multi_tf_trend_confirmed(self, side: str, timeframes=("5m", "15m", "30m")) -> bool:
        """多周期EMA+动量一致确认：每个周期都要"现价站上快线+快线在慢线上方+
        动量非噪音"才算真延续，任一周期没过就整体不通过。

        2026-08-20 SKHYNIX实盘复现：雷达保本止损出局后TV心跳仍显示持仓，
        若只看15m，当时快线(1207.47)仍压着慢线(1201.37)，结构看着"还行"；
        但5m快慢线已经死叉、三个周期现价其实都已跌破快线、动量全部转负——
        单一周期确认会漏判这种"大结构没破但已经在走弱"的情况，误导去追单。
        改成三个周期全部一致才算数，任何一个周期掉链子就不追。

        实现改为调用共享的 _multi_tf_trend_signal(adx_threshold=None)，行为
        保持不变——严格全过(score==total)，不查ADX。
        """
        sig = self._multi_tf_trend_signal(side, timeframes, adx_threshold=None)
        total = int(sig.get("total") or 0)
        score = int(sig.get("score") or 0)
        if total <= 0:
            return False
        confirmed = score == total
        if not confirmed:
            for tf in timeframes:
                e = sig.get(tf) or {}
                if not e.get("pass"):
                    logger.info(
                        f"📉 [{self.symbol}] 多周期确认在{tf}未通过 | "
                        f"close={float(e.get('close') or 0):.4f} "
                        f"ema_fast={float(e.get('ema_fast') or 0):.4f} "
                        f"ema_slow={float(e.get('ema_slow') or 0):.4f} "
                        f"momentum={float(e.get('momentum') or 0):.3f}"
                    )
        return confirmed

    def _maybe_refresh_mega_trend(self):
        """节流刷新"确认大趋势"判定——成本高(4个周期各一次REST)，不能挂在
        每个sentinel tick上，跟_maybe_refresh_atr同款节流模式，240s一次。
        结果写入self._mega_trend_confirmed，供_refresh_breathing_coefficient
        读取放宽TP3+跟随系数上限。"""
        now = time.time()
        last = float(getattr(self, "_mega_trend_last_refresh_ts", 0) or 0)
        if last > 0 and (now - last) < MEGA_TREND_REFRESH_SEC:
            return
        self._mega_trend_last_refresh_ts = now
        side = str(getattr(self, "current_side", "") or "").upper()
        if side not in ("LONG", "SHORT"):
            self._mega_trend_confirmed = False
            return
        sig = self._multi_tf_trend_signal(
            side, MEGA_TREND_TIMEFRAMES, adx_threshold=MEGA_TREND_ADX_THRESHOLD,
        )
        score = int(sig.get("score") or 0)
        confirmed = score >= MEGA_TREND_VOTE_MIN
        if confirmed:
            bars_4h = (sig.get("4h") or {}).get("bars") or []
            try:
                from market_engine import wilder_rsi
                rsi_4h = float(wilder_rsi(bars_4h, 14)) if bars_4h else 0.0
            except Exception as e:
                logger.debug(f"[{self.symbol}] 4h RSI计算跳过: {e}")
                rsi_4h = 0.0
            if side == "LONG" and rsi_4h > MEGA_TREND_RSI_LONG_MAX:
                logger.info(
                    f"🌡️ [{self.symbol}] 多周期趋势够(score={score}/4)但4h RSI="
                    f"{rsi_4h:.1f}>{MEGA_TREND_RSI_LONG_MAX:.0f}疑似超买 → 不放宽"
                )
                confirmed = False
            elif side == "SHORT" and rsi_4h < MEGA_TREND_RSI_SHORT_MIN:
                logger.info(
                    f"🌡️ [{self.symbol}] 多周期趋势够(score={score}/4)但4h RSI="
                    f"{rsi_4h:.1f}<{MEGA_TREND_RSI_SHORT_MIN:.0f}疑似超卖 → 不放宽"
                )
                confirmed = False
        prev = bool(getattr(self, "_mega_trend_confirmed", False))
        self._mega_trend_confirmed = confirmed
        if confirmed and not prev:
            logger.info(
                f"🚀 [{self.symbol}] 确认多周期大趋势(score={score}/4) → "
                f"TP3+跟随系数上限×{MEGA_TREND_CEILING_MULT}"
            )
        elif prev and not confirmed:
            logger.info(f"📉 [{self.symbol}] 大趋势确认解除(score={score}/4)")

    def _check_chase_reentry_confirmation(self):
        """巡检周期性调用：观察窗口内确认真延续（非反转、非噪音）才市价追回。"""
        if not bool(getattr(self, "_chase_watch_active", False)):
            return
        now = time.time()
        deadline = float(getattr(self, "_chase_watch_deadline_ts", 0) or 0)
        if deadline > 0 and now > deadline:
            logger.info(f"⏱️ [{self.symbol}] 追单确认窗口超时，放弃追回")
            self._clear_chase_watch()
            return
        if self.monitoring or float(getattr(self, "watched_qty", 0) or 0) > 0:
            self._clear_chase_watch()
            return
        side = str(getattr(self, "_chase_watch_side", "") or "").upper()
        exit_px = float(getattr(self, "_chase_watch_exit_px", 0) or 0)
        if side not in ("LONG", "SHORT") or exit_px <= 0:
            self._clear_chase_watch()
            return
        try:
            from binance_client import binance_client
        except Exception as e:
            logger.debug(f"[{self.symbol}] 追单确认依赖导入跳过: {e}")
            return
        try:
            bars = binance_client.fetch_klines(self.symbol, interval="15m", limit=60)
        except Exception as e:
            logger.debug(f"[{self.symbol}] 追单确认取K线跳过: {e}")
            return
        if not bars or len(bars) < 31:
            return
        # 观察窗口内一旦跌破(多)/涨破(空)过出场价，说明已经回头，不是"没回头的
        # 真延续"，直接放弃——只看武装之后新收的K线，避免拿到武装前的旧反转。
        # 2026-08-20：armed_ts单独存(不再用deadline反推)——观察窗口改成品种
        # 自己的重入窗口(可能几小时)后，deadline-固定900s会算出一个跟真实
        # 武装时间对不上的时间点，导致这里过滤出空列表、"有没有反转"这道
        # 检查形同虚设。
        armed_ts = float(getattr(self, "_chase_watch_armed_ts", 0) or 0) or (deadline - _CHASE_CONFIRM_WINDOW_SEC)
        watch_bars = [b for b in bars if int(b[0]) >= armed_ts * 1000]
        if not watch_bars:
            watch_bars = bars[-6:]
        if side == "LONG":
            reversed_back = any(float(b[3]) < exit_px for b in watch_bars)
        else:
            reversed_back = any(float(b[2]) > exit_px for b in watch_bars)
        if reversed_back:
            return
        if not self._multi_tf_trend_confirmed(side):
            return
        logger.info(f"📡 [{self.symbol}] 多周期追单确认通过(5m/15m/30m一致) → 市价追回")
        self._execute_chase_reentry(side)

    def _execute_chase_reentry(self, side):
        """确认通过后市价追回；成交后复用常规再入的挂防线逻辑(_on_reentry_limit_filled)。"""
        attempt = int(getattr(self, "_chase_watch_attempt", 0) or 0)
        self._clear_chase_watch()
        if getattr(self, "reentry_order_tag", None) or bool(getattr(self, "reentry_active", False)):
            logger.warning(f"🚫 [{self.symbol}] 追单确认通过但常规再入周期已在进行 → 让位")
            return False
        if not self._ensure_sterile_for_reentry(reason="追单确认·仓位归零清场"):
            return False
        from binance_client import binance_client

        qty = float((getattr(self, "_reentry_open_snap", None) or {}).get("qty") or 0)
        if qty <= 0:
            qty = float(getattr(self, "base_qty", 0) or 0)
        if qty <= 0:
            logger.error(f"🚨 [{self.symbol}] 追单市价无数量，放弃")
            return False
        open_side = "BUY" if side == "LONG" else "SELL"
        order = binance_client.place_market_order(
            open_side, qty, symbol=self.symbol, reduce_only=False,
        )
        if not order:
            logger.warning(f"⚠️ [{self.symbol}] 追单市价下单失败")
            return False
        pos = self._get_active_position(prefer_ws=False)
        if pos == "QUERY_FAILED" or not pos or float(pos.get("size") or 0) <= 0:
            logger.error(f"🚨 [{self.symbol}] 追单市价成交后查仓失败/无仓，人工核对")
            return False
        self.reentry_attempt = attempt
        self.radar_tier = attempt
        try:
            from ops_log import audit as ops_audit
            ops_audit(
                f"{self.symbol} chase_reentry_filled side={side} "
                f"qty={qty} entry={pos.get('entry_price')}"
            )
        except Exception:
            pass
        return bool(self._on_reentry_limit_filled(pos))

    def record_tv_heartbeat(self, data: dict):
        """接收TV心跳持仓，只写状态，不进交易流水线（app.py 的 HEARTBEAT
        分支调用）。绝不下单、绝不改任何交易相关字段，纯粹记录"TV自己觉得
        现在是什么状态"，供 _check_tv_heartbeat_gap 等只读比对使用。"""
        side = str((data or {}).get("tv_side") or "FLAT").strip().upper()
        if side not in ("LONG", "SHORT", "FLAT"):
            side = "FLAT"
        self.tv_heartbeat_side = side
        self.tv_heartbeat_ts = time.time()
        # 收到任意一条新心跳(不管LONG/SHORT/FLAT)都代表"当下不算过期"，
        # 之前那次"观察期结束未执行"的去重标记翻篇，未来这条心跳再次过期
        # 时应该能重新提醒，不会被上一段episode的标记永久压住。同理账户
        # 并发上限的去重标记也一起翻篇——名额之前满、现在可能已经空出来
        # 或者又满了，值得重新评估/重新提醒一次。
        self._catchup_stale_give_up_alerted = False
        self._catchup_capacity_blocked_alerted = False
        if side in ("LONG", "SHORT"):
            def _f(key):
                try:
                    return float((data or {}).get(key) or 0)
                except (TypeError, ValueError):
                    return 0.0
            self.tv_heartbeat_entry = _f("tv_entry")
            self.tv_heartbeat_stop = _f("tv_stop")
            self.tv_heartbeat_tp1 = _f("tv_tp1")
            self.tv_heartbeat_tp2 = _f("tv_tp2")
            self.tv_heartbeat_tp3 = _f("tv_tp3")
        else:
            self.tv_heartbeat_entry = 0.0
            self.tv_heartbeat_stop = 0.0
            self.tv_heartbeat_tp1 = 0.0
            self.tv_heartbeat_tp2 = 0.0
            self.tv_heartbeat_tp3 = 0.0
            self._tv_gap_first_seen_ts = 0.0
            self._tv_gap_alerted = False
            self._reset_tv_catchup_episode(source="心跳转FLAT")
            # TV彻底转FLAT代表这一整段行情翻篇了，中途是否被硬止损扫过
            # 已经不再相关——清零后下一段新持仓周期不会被上一段的硬止损
            # 记录误伤（见_infer_flat_close_meta顶部注释）。
            self.last_hard_sl_exit_ts = 0.0
        # 2026-08-20实盘发现：之前这里不落盘，心跳只活在内存里——当天后续
        # 又部署重启了几次(网格套利/大趋势确认)，每次重启都从磁盘上那份
        # 从未更新过的旧state文件("FLAT")重新加载，把刚收到的"TV仍持仓"
        # 心跳直接冲掉，_tv_gap_first_seen_ts的宽限期计时也跟着重置——
        # 如果真遇到漏单，反复部署重启会让报警窗口一直被打断永远攒不够
        # 3分钟触发。心跳落盘频率不算高(每根收盘K线一次)，直接存不心疼。
        try:
            self._save_state()
        except Exception:
            pass
        logger.debug(
            f"💓 [{self.symbol}] TV心跳 side={side} "
            f"entry={self.tv_heartbeat_entry} stop={self.tv_heartbeat_stop}"
        )

    def _tv_heartbeat_stale_sec(self) -> float:
        """按品种自己的TV周期动态算心跳过期阈值，跟固定4小时下限取更宽的
        那个——2026-08-20修复：TV_HEARTBEAT_STALE_SEC固定4小时，但BCH/XMR
        的TV图表周期是6小时(tv_tf_sec=21600)，每根收盘后到下一根收盘之间
        有近2小时心跳会被固定阈值误判成"太旧不可信"直接跳过，等于这两个
        品种的心跳检测大半时间形同虚设。改成"至少2根K线的时间"跟固定
        下限取更宽的那个，短周期品种不受影响。"""
        try:
            tv_tf_sec = int(get_reentry_profile(self.symbol).get("tv_tf_sec") or 0)
        except Exception:
            tv_tf_sec = 0
        return max(TV_HEARTBEAT_STALE_SEC, tv_tf_sec * 2)

    def _check_tv_heartbeat_gap(self):
        """TV心跳显示持仓、但实盘完全空仓超过宽限期 → 判定漏单，钉钉高优先级
        提醒（目前只报警不自动下单，见模块顶部注释）。由 _run_idle_live_reconcile
        在确认实盘空仓那个分支里调用，天然不会跟正在监控的持仓打架。"""
        hb_side = str(getattr(self, "tv_heartbeat_side", "FLAT") or "FLAT").upper()
        if hb_side not in ("LONG", "SHORT"):
            self._tv_gap_first_seen_ts = 0.0
            self._tv_gap_alerted = False
            return
        hb_ts = float(getattr(self, "tv_heartbeat_ts", 0) or 0)
        now = time.time()
        if hb_ts <= 0 or now - hb_ts > self._tv_heartbeat_stale_sec():
            return  # 心跳太旧不可信，可能TV那边早换了状态但新心跳还没到
        if bool(getattr(self, "trading_paused", False)):
            return
        if (
            bool(getattr(self, "reentry_active", False))
            or bool(getattr(self, "_chase_watch_active", False))
        ):
            return  # 已有重入/追单周期在跑，那本身就是VPS在处理"该有仓位没有"，让位
        first_seen = float(getattr(self, "_tv_gap_first_seen_ts", 0) or 0)
        if first_seen <= 0:
            self._tv_gap_first_seen_ts = now
            return
        if now - first_seen < TV_HEARTBEAT_GAP_GRACE_SEC:
            return
        if bool(getattr(self, "_tv_gap_alerted", False)):
            return  # 这次缺口已经报过，别刷屏；新心跳/仓位变化会自然重置
        self._tv_gap_alerted = True
        logger.warning(
            f"🆘 [{self.symbol}] TV心跳持仓{hb_side}但实盘持续"
            f"{now - first_seen:.0f}s空仓 → 疑似漏单"
        )
        try:
            import dingtalk
            self._dingtalk(
                dingtalk.report_system_alert,
                title=f"[{self.symbol}] TV持仓{hb_side}但VPS完全空仓，疑似漏单",
                detail=(
                    f"TV心跳显示持仓 {hb_side}，开仓价≈{self.tv_heartbeat_entry:.4f}，"
                    f"止损≈{self.tv_heartbeat_stop:.4f}，"
                    f"TP1/2/3≈{self.tv_heartbeat_tp1:.4f}/"
                    f"{self.tv_heartbeat_tp2:.4f}/{self.tv_heartbeat_tp3:.4f}，"
                    f"但{self._tag()}账户实盘已连续{now - first_seen:.0f}秒完全空仓，"
                    f"疑似webhook漏单（未送达/未处理）。VPS正自动尝试用更优价格"
                    f"追回（限价优价→预算耗尽转市价），成交/中止会再发通知，"
                    f"人工也可随时去控制面板核查。"
                ),
                level="紧急",
                suggestion="自动追回进行中，如需人工介入去控制面板核查/手动补开",
                immediate=True,
            )
        except Exception as e:
            logger.debug(f"[{self.symbol}] 漏单钉钉发送跳过: {e}")

    def _tv_heartbeat_catchup_tick(self):
        """由_run_idle_live_reconcile在实盘空仓分支里，紧挨着
        _check_tv_heartbeat_gap()调用——报警和"开始尝试追回"在同一时刻
        触发，复用_check_tv_heartbeat_gap已经在维护的_tv_gap_first_seen_ts
        宽限计时器做触发闸门，不重复造一个计时器。"""
        if not TV_HEARTBEAT_CATCHUP_ENABLED:
            return
        if bool(getattr(self, "catchup_active", False)):
            self._progress_tv_catchup_cycle()
            return
        self._maybe_start_tv_heartbeat_catchup()

    def _maybe_start_tv_heartbeat_catchup(self):
        hb_side = str(getattr(self, "tv_heartbeat_side", "FLAT") or "FLAT").upper()
        if hb_side not in ("LONG", "SHORT"):
            return
        hb_ts = float(getattr(self, "tv_heartbeat_ts", 0) or 0)
        now = time.time()
        if hb_ts <= 0 or now - hb_ts > self._tv_heartbeat_stale_sec():
            self._maybe_notify_catchup_watch_expired()
            return
        if bool(getattr(self, "trading_paused", False)):
            return
        if (
            bool(getattr(self, "reentry_active", False))
            or bool(getattr(self, "_chase_watch_active", False))
        ):
            return  # 自己出场触发的重入/追单确认在跑，让位（跟_check_tv_heartbeat_gap一致）
        first_seen = float(getattr(self, "_tv_gap_first_seen_ts", 0) or 0)
        if first_seen <= 0 or now - first_seen < TV_HEARTBEAT_GAP_GRACE_SEC:
            return

        hb_entry = float(getattr(self, "tv_heartbeat_entry", 0) or 0)
        same_episode = (
            bool(getattr(self, "_catchup_episode_resolved", False))
            and str(getattr(self, "_catchup_episode_side", "")) == hb_side
            and abs(float(getattr(self, "_catchup_episode_entry", 0) or 0) - hb_entry) < 1e-6
        )
        if same_episode:
            return  # 这次漏单事件已经用掉唯一一次追回机会，等心跳重新FLAT再变LONG/SHORT才算新事件

        hb_stop = float(getattr(self, "tv_heartbeat_stop", 0) or 0)
        if hb_entry <= 0 or hb_stop <= 0:
            return

        # 2026-08-21追加(同日修订，收窄排除范围)：TV这一整段持仓期间(自
        # first_seen往前，即心跳最近一次从FLAT转过来之后)，如果我们自己被
        # 真正的永久硬止损(vps_hard_sl，距离=|TV价-TV.SL|×1.15，比TV自己
        # 的止损宽15%)扫过——大概率是TV自己判断失误或行情反打(突发反转)，
        # 不该追回，等于错上加错。只排除这一种止损；雷达自己保本前/后触发
        # 的止损(sl_initial/radar_be/sl_breakeven，距离是我们自己的ATR算的，
        # 不是从TV止损距离推出来的，本质上都是"VPS比TV紧、提前出局但方向
        # 没错"同一类现象)、以及这一轮从未开过仓(最初ETH漏单原型)都不受
        # 影响，正常评估追回。见position_supervisor_binance.py::
        # _infer_flat_close_meta 顶部注释。
        last_hard_sl_ts = float(getattr(self, "last_hard_sl_exit_ts", 0) or 0)
        if last_hard_sl_ts > 0:
            logger.info(
                f"🚫 [{self.symbol}] TV心跳漏单：这段持仓期内曾被永久硬止损出局 "
                f"(exit_ts={last_hard_sl_ts:.0f}) → 不追回，大概率TV判断失误/行情反打"
            )
            return

        # 2026-08-21追加：光有"K线更优价格"还不够，追多周期EMA+动量一致
        # (复用追单确认chase-watch同一套_multi_tf_trend_confirmed，5m/15m/
        # 30m)——没确认就先不启动追回，每次idle-patrol tick都会重新评估，
        # 一旦确认(或心跳过期/TV转FLAT)自然结束等待，不需要额外计时器。
        # 只挡"要不要开始追"这一步，已经武装后的限价刷新/市价兜底沿用
        # 原有节奏，不重复加门槛（市价兜底那步宝贝已经明确要求不要犹豫）。
        if not self._multi_tf_trend_confirmed(hb_side):
            logger.debug(
                f"[{self.symbol}] TV心跳漏单：5m/15m/30m EMA+动量未一致确认{hb_side} "
                f"→ 暂不启动追回，继续观察"
            )
            return

        # 2026-08-21实盘复现：账户内曾同时出现5个品种一起进入观察名单——
        # EMA都通过了不代表可以无限制并发下单，账户级同时"已武装"(挂着
        # 真实订单)的追回周期数量设上限，超了就先让位，等有名额空出来
        # 再武装，不会丢失这次机会(episode不算解决，下一轮tick继续评估)。
        active_siblings = self._count_active_catchup_siblings()
        if active_siblings >= CATCHUP_MAX_CONCURRENT_PER_ACCOUNT:
            if not bool(getattr(self, "_catchup_capacity_blocked_alerted", False)):
                self._catchup_capacity_blocked_alerted = True
                logger.warning(
                    f"🚦 [{self.symbol}] TV心跳追回：多周期EMA已确认{hb_side}，"
                    f"但账户内已有{active_siblings}个品种同时武装追回"
                    f"(上限{CATCHUP_MAX_CONCURRENT_PER_ACCOUNT}) → 先让位，"
                    f"等有名额再挂单"
                )
                try:
                    import dingtalk
                    self._dingtalk(
                        dingtalk.report_system_alert,
                        title=f"TV心跳追回：账户并发上限已满，暂缓 [{self.symbol}]",
                        detail=(
                            f"多周期EMA已确认{hb_side}方向延续，但账户内已有"
                            f"{active_siblings}个品种同时武装追回(上限"
                            f"{CATCHUP_MAX_CONCURRENT_PER_ACCOUNT})，本次先让位，"
                            f"等有名额空出来会自动重新评估，不会丢失机会。"
                        ),
                        level="提示",
                        notify_level=1,
                    )
                except Exception as e:
                    logger.debug(f"[{self.symbol}] 并发上限提醒钉钉跳过: {e}")
            return

        self._catchup_episode_side = hb_side
        self._catchup_episode_entry = hb_entry
        self._catchup_episode_resolved = False
        self.catchup_side = hb_side
        self.catchup_tv_entry_frozen = hb_entry
        self.catchup_stop_distance_frozen = abs(hb_entry - hb_stop)
        self.catchup_tps_frozen = [
            float(getattr(self, "tv_heartbeat_tp1", 0) or 0),
            float(getattr(self, "tv_heartbeat_tp2", 0) or 0),
            float(getattr(self, "tv_heartbeat_tp3", 0) or 0),
        ]
        self.catchup_started_ts = now
        self.catchup_unfilled_refreshes = 0
        try:
            self._save_state()
        except Exception:
            pass

        logger.warning(
            f"🚑 [{self.symbol}] TV心跳漏单 → 启动自动追回 side={hb_side} "
            f"tv_entry={hb_entry:.4f} tv_stop={hb_stop:.4f} "
            f"stop_dist={self.catchup_stop_distance_frozen:.4f}"
        )
        self._place_tv_catchup_limit(reason="漏单追回·首次挂单")

    def _count_active_catchup_siblings(self) -> int:
        """账户内(同一进程SUPERVISORS，一个账户的所有品种军师都在同一个
        gunicorn worker进程里)当前有几个品种正处于已武装的追回周期
        (catchup_active=True，挂着真实交易所订单)，不含自己。局部导入
        避免跟position_supervisor_binance.py的模块级循环引用——
        position_supervisor_binance在模块顶部导入RadarReentryMixin，
        这里只能在函数体内、调用那一刻才反向导入，不能放模块顶部。"""
        try:
            from position_supervisor_binance import SUPERVISORS
        except Exception:
            return 0
        count = 0
        for sym, sup in SUPERVISORS.items():
            if sup is self:
                continue
            if bool(getattr(sup, "catchup_active", False)):
                count += 1
        return count

    def _maybe_notify_catchup_watch_expired(self):
        """2026-08-21追加：心跳漏单曾经报过警(_tv_gap_alerted)、但始终没能
        等到多周期EMA一致确认，心跳自己先过期了——不能就这么默默不再评估。
        跟成交/中止/市价兜底一样补一条收尾钉钉，宝贝不用自己去翻dashboard
        才知道"这次已经不追了"。一次性通知，同一段episode不重复刷屏；
        新心跳一来(record_tv_heartbeat)就会清掉这个去重标记，下一段episode
        该提醒还是会提醒。"""
        if not bool(getattr(self, "_tv_gap_alerted", False)):
            return  # 连"疑似漏单"这条报警都没触发过，谈不上"放弃"
        if bool(getattr(self, "_catchup_stale_give_up_alerted", False)):
            return
        if bool(getattr(self, "catchup_active", False)):
            return  # 已经武装的周期有自己的收尾路径(成交/中止)，不归这里管
        self._catchup_stale_give_up_alerted = True
        logger.warning(
            f"⌛ [{self.symbol}] TV心跳追回观察期结束：心跳已过期，始终未能"
            f"满足追回条件(多周期EMA确认/账户并发名额) → 本次未执行任何追回动作"
        )
        try:
            import dingtalk
            self._dingtalk(
                dingtalk.report_system_alert,
                title=f"TV心跳追回：观察期结束未执行 [{self.symbol}]",
                detail=(
                    "之前提醒过TV持仓但VPS空仓的疑似漏单——观察期内始终未能"
                    "满足追回条件(多周期EMA一致确认延续该方向、和/或账户内"
                    "并发追回名额)，心跳数据现已过期，本次不再追回。若TV"
                    "后续仍持有该方向，下一次新心跳到达会重新评估。"
                ),
                level="提示",
                notify_level=1,
            )
        except Exception as e:
            logger.debug(f"[{self.symbol}] 追回观察期结束钉钉跳过: {e}")

    def _fetch_catchup_klines(self):
        """15m为主档、5m为副档——拉回来的两档各取极值后交给
        pick_best_tier_extreme比较谁对该side更有利。"""
        from binance_client import binance_client
        k15, k5 = None, None
        try:
            k15 = binance_client.fetch_klines(self.symbol, interval="15m", limit=3)
        except Exception as e:
            logger.warning(f"[{self.symbol}] 追回拉15m K线失败: {e}")
        try:
            k5 = binance_client.fetch_klines(self.symbol, interval="5m", limit=3)
        except Exception as e:
            logger.debug(f"[{self.symbol}] 追回拉5m K线失败: {e}")
        return k15, k5

    def _prepare_tv_catchup_sizing(self, side):
        """仓位大小：固定中档(tier=1)，从零安全取值——照抄
        open_grid_position已验证过的写法：显式设tier/side/参考价，
        显式清零tv_suggested_qty/tv_sl_ref（否则会带入上一个TV周期的
        陈旧值），现算一个真实ATR写入_tv_signal_atr（否则
        _resolve_open_atr_with_degrade会静默退化成不带止损距离的
        纯名义下单）。"""
        from binance_client import binance_client

        curr_px = float(
            binance_client.get_current_price(self.symbol)
            or getattr(self, "catchup_tv_entry_frozen", 0)
            or 0
        )
        try:
            from strategy_engine import klines as _sk_klines
            from strategy_engine import indicators as _sk_ind
            bars = _sk_klines.get_bars(self.symbol, "15m", limit=60)
            atr = float(_sk_ind.wilder_atr(bars, 14)) if bars else 0.0
        except Exception as e:
            logger.warning(f"[{self.symbol}] 追回ATR现算失败: {e}")
            atr = 0.0
        self._catchup_fresh_atr = atr
        self._tv_signal_atr = float(atr)
        self.tv_open_tier = 1
        self.last_tv_side = side
        self.tv_price = curr_px
        self.tv_suggested_qty = 0.0
        self.tv_sl_ref = 0.0
        qty, principal, margin_usdt, margin_pct, meta = self._calc_target_open_qty(curr_px)
        self._catchup_qty = float(qty or 0)
        logger.info(
            f"📐 [{self.symbol}] 追回sizing tier=1 qty={self._catchup_qty} "
            f"atr={atr:.4f} meta={meta.get('binding') if isinstance(meta, dict) else meta}"
        )

    def _tv_catchup_precheck_still_valid(self) -> bool:
        """下单前复核：TV心跳是否还是原方向且未过期——不追一个TV自己可能
        已经出场/反转的仓位。限价挂单前、市价兜底前都会调用。"""
        side = str(getattr(self, "catchup_side", "") or "").upper()
        hb_side = str(getattr(self, "tv_heartbeat_side", "FLAT") or "FLAT").upper()
        hb_ts = float(getattr(self, "tv_heartbeat_ts", 0) or 0)
        if hb_side != side or hb_side not in ("LONG", "SHORT"):
            return False
        if hb_ts <= 0 or time.time() - hb_ts > self._tv_heartbeat_stale_sec():
            return False
        return True

    def _place_tv_catchup_limit(self, reason="", is_refresh=False):
        if not self._tv_catchup_precheck_still_valid():
            logger.warning(
                f"🚫 [{self.symbol}] 追回前复核：TV心跳已变化/过期 → 中止本次追回周期"
            )
            self._abort_tv_catchup_cycle(reason="pre_order_stale_or_flipped")
            return False

        side = str(getattr(self, "catchup_side", "") or "").upper()
        from binance_client import binance_client, is_orders_query_failed

        if is_refresh:
            n = int(getattr(self, "catchup_unfilled_refreshes", 0) or 0) + 1
            self.catchup_unfilled_refreshes = n
            cap = max_unfilled_refreshes(self.symbol)
            if n > cap:
                logger.warning(
                    f"🚫 [{self.symbol}] 追回限价连续未成交刷新 {n}>{cap} → 转市价兜底"
                )
                self._cancel_catchup_limit(reason="未成交超限·转市价")
                return bool(self._escalate_tv_catchup_to_market(reason="limit_budget_exhausted"))
            self._cancel_catchup_limit(reason="TTL刷新·先撤旧标签")
        elif getattr(self, "catchup_limit_order_id", None):
            logger.error(f"🚫 [{self.symbol}] 已有追回限价 → 拒挂 | {reason}")
            return False

        pos = self._get_active_position(prefer_ws=True)
        if pos == "QUERY_FAILED":
            return False
        if pos and float(pos.get("size") or 0) > 0:
            logger.warning(f"🚫 [{self.symbol}] 追回挂单前仍有仓 → 中止")
            return False
        if not is_refresh:
            if not self._ensure_sterile_for_reentry(reason="追回挂单前清场"):
                return False
        else:
            if hasattr(self, "_verify_sterile_flat"):
                if not self._wait_verify(self._verify_sterile_flat, retries=5, delay=0.35):
                    logger.error(f"🚫 [{self.symbol}] TTL刷新后无菌未过 → 拒挂新限价")
                    return False

        k15, k5 = self._fetch_catchup_klines()
        lo, hi = pick_best_tier_extreme(side, k15, k5)
        rp = get_reentry_profile(self.symbol)
        lim, src = compute_reentry_limit_px(
            side=side,
            tv_price=float(getattr(self, "catchup_tv_entry_frozen", 0) or 0),
            low5=lo, high5=hi, low3=0.0, high3=0.0,
            tick=float(rp.get("tick_size") or 0.01),
            discount=float(rp.get("limit_discount") or 0.003),
            prev_entry=0.0,
        )
        if lim <= 0:
            logger.warning(f"🚫 [{self.symbol}] 追回限价中止: {src}")
            if src == "not_better_than_tv":
                self._abort_tv_catchup_cycle(reason="not_better_than_tv")
            return False

        if not is_refresh:
            self._prepare_tv_catchup_sizing(side)
        qty = float(getattr(self, "_catchup_qty", 0) or 0)
        if qty <= 0:
            logger.error(f"🚨 [{self.symbol}] 追回限价无数量")
            return False

        ttl = float(rp.get("limit_ttl_sec") or 300)
        deadline_ts = time.time() + ttl
        open_side = "BUY" if side == "LONG" else "SELL"
        tag = make_catchup_client_order_id(self.symbol, side, lim, time.time())
        self.catchup_order_tag = tag
        try:
            self._save_state()
        except Exception:
            pass

        try:
            book = binance_client.get_open_orders(self.symbol)
            if is_orders_query_failed(book):
                logger.error(f"🚫 [{self.symbol}] 追回挂单前查单失败 → 释放标签并拒挂 tag={tag}")
                self._clear_catchup_order_tag(reason="查单失败拒挂")
                return False
            for o in (book or []):
                if not isinstance(o, dict):
                    continue
                if str(o.get("type") or "").upper() != "LIMIT":
                    continue
                if str(o.get("side") or "").upper() != open_side:
                    continue
                try:
                    opx = float(o.get("price") or 0)
                except (TypeError, ValueError):
                    continue
                if abs(opx - lim) <= max(lim * 1e-8, 1e-6):
                    oid = o.get("orderId")
                    self.catchup_active = True
                    self.catchup_phase = "limit"
                    self.catchup_limit_order_id = oid
                    self.catchup_limit_px = lim
                    self.catchup_limit_deadline_ts = deadline_ts
                    coid = str(o.get("clientOrderId") or "") or tag
                    self.catchup_order_tag = coid
                    self._save_state()
                    logger.warning(f"♻️ [{self.symbol}] 追回复用已有同价限价 id={oid} @{lim:.2f} tag={coid}")
                    return True
        except Exception as e:
            logger.error(f"🚫 [{self.symbol}] 追回挂单前查单异常 → 拒挂: {e}")
            self._clear_catchup_order_tag(reason="查单异常拒挂")
            return False

        order = binance_client.place_limit_order(
            open_side, qty, lim, symbol=self.symbol, reduce_only=False,
            client_order_id=tag,
        )
        if not order:
            self._clear_catchup_order_tag(reason="下单失败释放")
            return False
        oid = order.get("orderId") or order.get("algoId")
        self.catchup_active = True
        self.catchup_phase = "limit"
        self.catchup_limit_order_id = oid
        self.catchup_limit_px = lim
        self.catchup_limit_deadline_ts = deadline_ts
        self._save_state()
        logger.info(
            f"📥 [{self.symbol}] 追回限价已挂 {side} {qty} @{lim:.2f} "
            f"src={src} id={oid} tag={tag} | {reason} | refresh={n if is_refresh else 0}"
        )
        return True

    def _progress_tv_catchup_cycle(self):
        if self.monitoring or float(getattr(self, "watched_qty", 0) or 0) > 0:
            return
        phase = str(getattr(self, "catchup_phase", "") or "")
        if phase != "limit":
            return  # market_fallback是同步下单，没有可轮询的中间态
        side = str(getattr(self, "catchup_side", "") or "").upper()
        pos = self._get_active_position(prefer_ws=True)
        if pos == "QUERY_FAILED":
            return
        if pos and float(pos.get("size") or 0) > 0:
            if str(pos.get("side") or "").upper() == side:
                self._finalize_tv_catchup_fill(pos, escalated=False)
            else:
                logger.warning(f"⚠️ [{self.symbol}] 追回期间出现反向仓 → 中止周期")
                self._abort_tv_catchup_cycle(reason="反向仓")
            return
        now = time.time()
        deadline = float(getattr(self, "catchup_limit_deadline_ts", 0) or 0)
        if deadline > 0 and now >= deadline:
            self._place_tv_catchup_limit(reason="TTL刷新", is_refresh=True)

    def _escalate_tv_catchup_to_market(self, reason=""):
        """限价预算耗尽后无条件市价追上——宝贝原话"这种情形不要犹豫了，
        直接市价进单"：心跳持续了一整个预算窗口证明TV还在持有，这本身
        就是确认，不再额外加多周期确认门槛（不同于自己出场后的追单确认
        chase-watch，那是为了验证"自己出场是不是真的判断错了"）。"""
        if not self._tv_catchup_precheck_still_valid():
            logger.warning(f"🚫 [{self.symbol}] 市价追回前复核：TV心跳已变化/过期 → 中止")
            self._abort_tv_catchup_cycle(reason="pre_market_stale_or_flipped")
            return False
        side = str(getattr(self, "catchup_side", "") or "").upper()
        if side not in ("LONG", "SHORT"):
            self._abort_tv_catchup_cycle(reason="bad_side")
            return False
        if not self._ensure_sterile_for_reentry(reason="追回市价·仓位归零清场"):
            return False
        from binance_client import binance_client

        if not getattr(self, "_catchup_qty", 0):
            self._prepare_tv_catchup_sizing(side)
        qty = float(getattr(self, "_catchup_qty", 0) or 0)
        if qty <= 0:
            logger.error(f"🚨 [{self.symbol}] 追回市价无数量，放弃")
            self._abort_tv_catchup_cycle(reason="no_qty")
            return False
        # 市价兜底没拿到"比TV更优价格"这层保护，风险收益比不如限价优价，
        # 仓位打折(只缩qty，止损空间/距离公式不受影响)。折算后按交易所
        # qty_step重新对齐，跟_calc_vps_open_qty里tier缩放同款做法。
        qty_step = float(getattr(self, "qty_step", 0.001) or 0.001)
        min_qty = float(getattr(self, "min_qty", 0.001) or 0.001)
        discounted = qty * CATCHUP_MARKET_FALLBACK_SIZE_MULT
        if qty_step > 0:
            discounted = math.floor(discounted / qty_step) * qty_step
        if discounted < min_qty:
            logger.error(
                f"🚨 [{self.symbol}] 追回市价折算后({discounted})低于最小下单量"
                f"({min_qty})，放弃"
            )
            self._abort_tv_catchup_cycle(reason="market_discount_below_min_qty")
            return False
        logger.info(
            f"📉 [{self.symbol}] 市价兜底仓位打折 ×{CATCHUP_MARKET_FALLBACK_SIZE_MULT} "
            f"{qty}→{discounted}"
        )
        qty = discounted
        open_side = "BUY" if side == "LONG" else "SELL"
        self.catchup_phase = "market_fallback"
        try:
            self._save_state()
        except Exception:
            pass
        order = binance_client.place_market_order(
            open_side, qty, symbol=self.symbol, reduce_only=False,
        )
        if not order:
            logger.warning(f"⚠️ [{self.symbol}] 追回市价下单失败")
            self._abort_tv_catchup_cycle(reason="market_order_failed")
            return False
        pos = self._get_active_position(prefer_ws=False)
        if pos == "QUERY_FAILED" or not pos or float(pos.get("size") or 0) <= 0:
            logger.error(f"🚨 [{self.symbol}] 追回市价成交后查仓失败/无仓，人工核对")
            return False  # 不标记episode已解决——状态存疑，需要人工看，catchup_active留True当红旗
        return bool(self._finalize_tv_catchup_fill(pos, escalated=True))

    def _finalize_tv_catchup_fill(self, pos: Dict[str, Any], escalated: bool) -> bool:
        """追回成交后：变成完全正常的TV持仓，交还雷达/呼吸止损/TP1-2-3
        系统全权接管（不像网格套利那样永久简化）——不复用
        _on_reentry_limit_filled，那深度耦合reentry_attempt递增/
        _reentry_open_snap等只对"自己出场后重入"有意义的语义。"""
        side = str(pos.get("side") or getattr(self, "catchup_side", "") or "").upper()
        entry = float(pos.get("entry_price") or 0)
        qty = float(pos.get("size") or 0)
        if side not in ("LONG", "SHORT") or entry <= 0 or qty <= 0:
            return False

        self.catchup_limit_order_id = None
        self.catchup_limit_px = 0.0
        self.catchup_limit_deadline_ts = 0.0
        self.catchup_active = False
        self.catchup_phase = ""
        self._clear_catchup_order_tag(reason="追回成交释放")
        self._catchup_episode_resolved = True

        self.current_side = side
        self.last_tv_side = side
        self.watched_entry = entry
        self.watched_qty = qty
        self.initial_qty = qty
        self._open_settled_qty = qty
        self.base_qty = qty
        self.position_source = "TV"
        self.monitoring = True

        atr = float(
            getattr(self, "_catchup_fresh_atr", 0) or getattr(self, "_tv_signal_atr", 0) or 0
        )
        self.open_atr = atr
        self.current_atr = atr
        self._tv_signal_atr = atr
        tv_ref_price = float(getattr(self, "catchup_tv_entry_frozen", 0) or entry)
        self.tv_price = tv_ref_price

        tps = list(getattr(self, "catchup_tps_frozen", None) or [0.0, 0.0, 0.0])
        self.tv_tps = self._sanitize_tp_prices(tps) if hasattr(self, "_sanitize_tp_prices") else tps
        try:
            if not self._tp_prices_valid_for_side(side, entry):
                self.tv_tps = [0.0, 0.0, 0.0]
            self._ensure_tp123_prices_from_tv(entry)
        except Exception as e:
            logger.warning(f"[{self.symbol}] 追回成交 TP 重算跳过: {e}")

        # 止损：按TV止损"空间"(距离)重新锚定到实际成交价，两条分支(限价
        # 优价/市价兜底)统一公式，只是fill_px不同——不硬搬TV原始止损绝对价
        distance = float(getattr(self, "catchup_stop_distance_frozen", 0) or 0)
        hard_sl = (entry - distance) if side == "LONG" else (entry + distance)
        self.frozen_hard_sl_px = round(float(hard_sl), 2)
        self.initial_stop = self.frozen_hard_sl_px
        self.current_sl = self.frozen_hard_sl_px
        self.tv_sl = self.frozen_hard_sl_px

        try:
            self._bind_adx_tier_on_open(adx=float(getattr(self, "last_adx", 0) or 25.0), tier=1)
        except Exception as e:
            logger.warning(f"[{self.symbol}] 追回ADX档绑定失败，默认中档: {e}")
            self.adx_tier = 1
            self.radar_tier = 1

        self._begin_open_radar_dormant(
            side=side, entry=entry, tv_price=tv_ref_price, open_atr=atr,
            reentry_attempt=0, adx_tier=1, radar_tier=1,
            adx=float(getattr(self, "last_adx", 0) or 25.0),
        )

        self._save_state()
        self._ensure_price_ws()
        self._ensure_sentinel_running()

        sl_ok = self._ensure_frozen_hard_sl(qty, reason="TV心跳漏单追回·硬止损")
        placed_tp = 0
        try:
            placed_tp = self._place_tp_levels_only(qty, retries=2)
        except Exception as e:
            logger.error(f"[{self.symbol}] 追回TP挂单失败: {e}")
        try:
            self._resolve_atr_scenario_after_open(entry, side, qty)
        except Exception as e:
            logger.warning(f"[{self.symbol}] 追回ATR场景绑定跳过: {e}")
        if float(getattr(self, "frozen_hard_sl_px", 0) or 0) > 0:
            self.current_sl = self.frozen_hard_sl_px
            self.tv_sl = self.frozen_hard_sl_px
        if self._radar_is_dormant():
            self._strip_radar_stop_keep_hard(reason="追回后雷达仍休眠")

        slip = abs(entry - tv_ref_price) if tv_ref_price > 0 else 0.0
        try:
            import dingtalk
            self._dingtalk(
                dingtalk.report_system_alert,
                title=f"TV心跳漏单已自动追回 [{self.symbol}]",
                detail=(
                    f"{side} {qty} @ {entry:.4f}（TV原开仓价{tv_ref_price:.4f}，滑点{slip:.4f}）"
                    f"{'· 已升级市价' if escalated else '· 限价优价成交'}\n"
                    f"硬止损@{self.frozen_hard_sl_px:.4f}（按TV止损空间{distance:.4f}重新锚定）"
                    f" hard挂单={'成功' if sl_ok else '失败需人工核查'}\n"
                    f"TP挂出={placed_tp}档 TP目标={self.tv_tps}"
                ),
                level="紧急",
                immediate=True,
            )
        except Exception:
            pass
        logger.info(
            f"✅ [{self.symbol}] TV心跳漏单追回成交 {side} {qty}@{entry:.2f} "
            f"escalated={escalated} hard@{self.frozen_hard_sl_px:.2f} "
            f"hung={1 if sl_ok else 0} tp_placed={placed_tp}"
        )
        return True

    def _abort_tv_catchup_cycle(self, reason=""):
        self._cancel_catchup_limit(reason=reason)
        self.catchup_active = False
        self.catchup_phase = ""
        self._catchup_episode_resolved = True
        try:
            self._save_state()
        except Exception:
            pass
        logger.warning(f"🛑 [{self.symbol}] TV心跳追回周期已中止 | {reason}")
        try:
            import dingtalk
            self._dingtalk(
                dingtalk.report_system_alert,
                title=f"TV心跳漏单追回已中止 [{self.symbol}]",
                detail=f"side={getattr(self, 'catchup_side', None)} 原因={reason}，请人工核查",
                level="紧急",
                immediate=True,
            )
        except Exception:
            pass

    def _check_tv_heartbeat_direction_mismatch(self):
        """持仓中(live_qty>0)时，心跳显示的方向如果新鲜且跟实盘方向不一致，
        钉钉高优先级提醒（只报警不自动下单，边界跟_check_tv_heartbeat_gap
        一致）。

        2026-08-20新增：这是把心跳最初要解决的"webhook总丢失"盲区，从
        "该有仓位却没有"延伸到"已有仓位但方向被一条漏掉的反转信号改变了"
        这个同类场景——旧的watchdog side_mismatch检查靠比对journalctl里
        成功记录过的TV信号，如果一条反转信号本身就没送达，旧检查看到的
        还是"上一条信号=原方向"，跟实盘对得上，压根不会报警，是同一类
        结构性盲区，心跳(TV自己权威的当下状态，不依赖之前有没有留痕)能
        补上。由_sentinel_loop在持仓期间跟_maybe_refresh_atr同一处定期
        调用，纯内存比较，不用额外拉数据，可以放心跟着tick跑。"""
        side = str(getattr(self, "current_side", "") or "").upper()
        if side not in ("LONG", "SHORT"):
            self._tv_dir_mismatch_alerted = False
            return
        hb_side = str(getattr(self, "tv_heartbeat_side", "FLAT") or "FLAT").upper()
        if hb_side not in ("LONG", "SHORT") or hb_side == side:
            self._tv_dir_mismatch_alerted = False
            return
        hb_ts = float(getattr(self, "tv_heartbeat_ts", 0) or 0)
        now = time.time()
        if hb_ts <= 0 or now - hb_ts > self._tv_heartbeat_stale_sec():
            return
        if bool(getattr(self, "trading_paused", False)):
            return
        if bool(getattr(self, "_tv_dir_mismatch_alerted", False)):
            return  # 这次不一致已经报过，别刷屏；方向一致/心跳更新会自然重置
        self._tv_dir_mismatch_alerted = True
        logger.warning(
            f"🆘 [{self.symbol}] TV心跳方向{hb_side}但实盘持仓{side} → 疑似方向反转信号漏单"
        )
        try:
            import dingtalk
            self._dingtalk(
                dingtalk.report_system_alert,
                title=f"[{self.symbol}] TV心跳方向{hb_side}但实盘持仓{side}，疑似漏单",
                detail=(
                    f"TV心跳显示当前方向{hb_side}，但{self._tag()}账户实盘仍持仓"
                    f"{side}——可能是一条方向反转的TV信号没有送达，请人工核实"
                    f"TV当前真实方向，需要的话手动平仓/反手。"
                ),
                level="紧急",
                suggestion="核实TV当前真实方向，需要的话去控制面板手动平仓/反手",
                immediate=True,
            )
        except Exception as e:
            logger.debug(f"[{self.symbol}] 方向不一致钉钉发送跳过: {e}")

    def _place_reentry_limit(self, side=None, reason="", *, is_refresh=False):
        side = str(side or getattr(self, "cycle_tv_side", "") or "").upper()
        if side not in ("LONG", "SHORT"):
            return False

        # 红色铁律：本地标签未清且非刷新 → 绝对拒挂（即使交易所查单为空）
        # 必须在 import binance_client 之前判断，避免查单失败路径误入下单。
        pending_tag = getattr(self, "reentry_order_tag", None)
        if pending_tag and not is_refresh:
            logger.error(
                f"🚫 [{self.symbol}] 本地订单标签未释放 tag={pending_tag} "
                f"→ 拒挂第二笔（防查不到单狂挂）| {reason}"
            )
            return False

        from binance_client import binance_client, is_orders_query_failed, IpRateLimitedError

        # IP 冷却期：刷新路径最多跑一次，然后退出（避免刷新周期反复打 REST）
        if is_refresh:
            try:
                if binance_client.ip_rate_limit_remaining() > 0:
                    logger.warning(
                        f"🛡️ [{self.symbol}] TTL刷新 IP冷却中 → 退出本次刷新"
                    )
                    return False
            except Exception:
                pass

        if is_refresh:
            n = int(getattr(self, "reentry_unfilled_refreshes", 0) or 0) + 1
            self.reentry_unfilled_refreshes = n
            cap = max_unfilled_refreshes(self.symbol)
            if n > cap:
                logger.warning(
                    f"🚫 [{self.symbol}] 再入限价连续未成交刷新 {n}>{cap} → 终止周期"
                )
                self._cancel_reentry_limit(reason="未成交超限")
                self.reentry_active = False
                # 2026-08-20：限价反复够不着价，可能是趋势太强、价格一直没回踩——
                # 不直接放弃，radar_be退出+TP1没成交(此刻仓位还没成交过，
                # 这个前提在_maybe_start_smart_limit_reentry入口就已验证过，
                # 这里不会变)+重入名额没用完，交给追单确认接手(多周期实时
                # 确认+市价追)，跟今天统一的"入场资格看实时确认，不轻易放弃"
                # 是同一个思路，不留死路。
                try:
                    exit_src = str(getattr(self, "last_exit_source", "") or "")
                    attempt = int(getattr(self, "reentry_attempt", 0) or 0)
                    max_n = int(get_reentry_profile(self.symbol).get("max_reentries") or 1)
                    side_now = str(
                        getattr(self, "cycle_tv_side", "")
                        or getattr(self, "last_tv_side", "") or ""
                    ).upper()
                    exit_px_now = float(getattr(self, "last_exit_px", 0) or 0)
                    if (
                        exit_src == "radar_be"
                        and attempt < max_n
                        and side_now in ("LONG", "SHORT")
                        and exit_px_now > 0
                    ):
                        atr_now = float(
                            getattr(self, "cycle_open_atr", 0)
                            or getattr(self, "open_atr", 0)
                            or 0
                        )
                        deadline = float(
                            getattr(self, "reentry_window_deadline_ts", 0) or 0
                        )
                        self._arm_chase_reentry_watch(
                            side=side_now, exit_px=exit_px_now, atr=atr_now,
                            attempt=attempt, deadline_ts=deadline,
                        )
                except Exception as e:
                    logger.debug(f"[{self.symbol}] 限价超限转追单确认跳过: {e}")
                self._clear_reentry_cycle(source="unfilled_refresh_cap")
                return False
            # TTL：必须先撤旧 + 释放旧标签，才能生成新标签
            self._cancel_reentry_limit(reason="TTL刷新·先撤旧标签")
        elif getattr(self, "reentry_limit_order_id", None):
            # 非刷新却已有 oid：禁止叠挂
            logger.error(
                f"🚫 [{self.symbol}] 已有再入限价 id={self.reentry_limit_order_id} "
                f"→ 拒挂 | {reason}"
            )
            return False

        # 挂单前确认无持仓 + 无菌（刷新路径也再验一次）
        # 修复（v16.9.2）：prefer_ws=True 避免强制 REST 在冷却期触发 -1003
        pos = self._get_active_position(prefer_ws=True)
        if pos == "QUERY_FAILED":
            return False
        if pos and float(pos.get("size") or 0) > 0:
            logger.warning(f"🚫 [{self.symbol}] 再入挂单前仍有仓 → 中止")
            return False
        if not is_refresh:
            # 首次挂单前无菌已在 _maybe_start 做过；此处再验一次轻量
            if hasattr(self, "_verify_sterile_flat") and not self._verify_sterile_flat():
                if not self._ensure_sterile_for_reentry(reason="再入挂单前复检"):
                    return False
        else:
            # 刷新：撤旧后必须确认盘口空（查不到单 → 拒挂，不清标签已在 cancel 清）
            if hasattr(self, "_verify_sterile_flat"):
                if not self._wait_verify(
                    self._verify_sterile_flat, retries=5, delay=0.35,
                ):
                    logger.error(
                        f"🚫 [{self.symbol}] TTL刷新后无菌未过 → 拒挂新限价"
                    )
                    return False

        tv = float(getattr(self, "cycle_tv_price", 0) or 0)
        k5, k3 = self._fetch_reentry_klines()
        plan, why = plan_reentry_limit(
            side=side, tv_price=tv, symbol=self.symbol,
            klines_5m=k5, klines_3m=k3,
        )
        if not plan:
            logger.warning(
                f"🚫 [{self.symbol}] 再入限价中止: {why} | TV@{tv:.2f}"
            )
            if why == "not_better_than_tv":
                self._cancel_reentry_limit(reason="无法优于TV")
                self.reentry_active = False
                self._clear_reentry_cycle(source="not_better_than_tv")
            return False

        qty = float((getattr(self, "_reentry_open_snap", None) or {}).get("qty") or 0)
        if qty <= 0:
            qty = float(getattr(self, "base_qty", 0) or 0)
        if qty <= 0:
            logger.error(f"🚨 [{self.symbol}] 再入限价无数量")
            return False

        lim = float(plan["limit_px"])
        open_side = "BUY" if side == "LONG" else "SELL"
        # 先持久化新标签，再下单：崩溃中途也不会无标签狂挂
        tag = make_reentry_client_order_id(self.symbol, side, lim, time.time())
        self.reentry_order_tag = tag
        try:
            self._save_state()
        except Exception:
            pass

        # 交易所侧再确认：查单失败 → 释放标签并拒挂（绝不盲补）
        try:
            book = binance_client.get_open_orders(self.symbol)
            if is_orders_query_failed(book):
                logger.error(
                    f"🚫 [{self.symbol}] 挂单前查单失败 → 释放标签并拒挂 tag={tag}"
                )
                self._clear_reentry_order_tag(reason="查单失败拒挂")
                return False
            # 同向同价已存在 → 复用，不新挂
            for o in (book or []):
                if not isinstance(o, dict):
                    continue
                if str(o.get("type") or "").upper() != "LIMIT":
                    continue
                if str(o.get("side") or "").upper() != open_side:
                    continue
                try:
                    opx = float(o.get("price") or 0)
                except (TypeError, ValueError):
                    continue
                if abs(opx - lim) <= max(lim * 1e-8, 1e-6):
                    oid = o.get("orderId")
                    self.reentry_active = True
                    self.reentry_limit_order_id = oid
                    self.reentry_limit_px = lim
                    self.reentry_limit_deadline_ts = float(plan["deadline_ts"])
                    # 复用盘口单时，标签对齐其 clientOrderId（若有）
                    coid = str(o.get("clientOrderId") or "") or tag
                    self.reentry_order_tag = coid
                    self._save_state()
                    logger.warning(
                        f"♻️ [{self.symbol}] 复用已有同价再入限价 id={oid} "
                        f"@{lim:.2f} tag={coid}"
                    )
                    return True
        except Exception as e:
            logger.error(f"🚫 [{self.symbol}] 挂单前查单异常 → 拒挂: {e}")
            self._clear_reentry_order_tag(reason="查单异常拒挂")
            return False

        order = binance_client.place_limit_order(
            open_side, qty, lim, symbol=self.symbol, reduce_only=False,
            client_order_id=tag,
        )
        if not order:
            # 下单失败：释放标签，允许后续重试（否则永久卡死）
            self._clear_reentry_order_tag(reason="下单失败释放")
            return False
        oid = order.get("orderId") or order.get("algoId")
        self.reentry_active = True
        self.reentry_limit_order_id = oid
        self.reentry_limit_px = lim
        self.reentry_limit_deadline_ts = float(plan["deadline_ts"])
        self._save_state()
        logger.info(
            f"📥 [{self.symbol}] 再入限价已挂 {side} {qty} @{lim:.2f} "
            f"src={plan.get('source')} id={oid} tag={tag} | {reason} | "
            f"refresh={int(getattr(self, 'reentry_unfilled_refreshes', 0) or 0)}"
        )
        return True

    def _cancel_reentry_limit(self, reason=""):
        from binance_client import binance_client

        oid = getattr(self, "reentry_limit_order_id", None)
        tag = getattr(self, "reentry_order_tag", None)
        if oid:
            try:
                binance_client.cancel_order(self.symbol, order_id=oid)
                logger.info(
                    f"🗑️ [{self.symbol}] 撤再入限价 id={oid} tag={tag} | {reason}"
                )
            except Exception as e:
                try:
                    binance_client.cancel_order(
                        self.symbol, order={"orderId": oid},
                    )
                except Exception as e2:
                    logger.debug(f"撤再入限价跳过: {e}/{e2}")
        self.reentry_limit_order_id = None
        self.reentry_limit_px = 0.0
        self.reentry_limit_deadline_ts = 0.0
        # 撤后必须释放标签，才允许下一周期新标签
        self._clear_reentry_order_tag(reason=reason or "撤单释放标签")

    def _reentry_tick(self):
        """空仓时：TTL 刷新 / 成交检测 / 终止条件。"""
        if not bool(getattr(self, "reentry_active", False)):
            return False
        if self.monitoring or float(getattr(self, "watched_qty", 0) or 0) > 0:
            return False
        from binance_client import binance_client

        side = str(getattr(self, "cycle_tv_side", "") or "").upper()
        pos = self._get_active_position(prefer_ws=True)
        if pos == "QUERY_FAILED":
            return False
        if pos and float(pos.get("size") or 0) > 0:
            if str(pos.get("side") or "").upper() == side:
                return self._on_reentry_limit_filled(pos)
            logger.warning(f"⚠️ [{self.symbol}] 再入期间出现反向仓 → 中止周期")
            self._clear_reentry_cycle(source="再入期反向仓")
            return False

        now = time.time()
        deadline = float(getattr(self, "reentry_limit_deadline_ts", 0) or 0)
        if deadline > 0 and now >= deadline:
            logger.info(f"⏰ [{self.symbol}] 再入限价 TTL 到期 → 按最新5m极值重挂")
            return bool(
                self._place_reentry_limit(
                    side=side, reason="TTL刷新", is_refresh=True,
                )
            )
        return True

    def _on_reentry_limit_filled(self, pos: Dict[str, Any]) -> bool:
        """再入限价成交 → attempt+1，按新成交价挂 hard+TP12，雷达休眠候命。"""
        side = str(pos.get("side") or getattr(self, "cycle_tv_side", "") or "").upper()
        entry = float(pos.get("entry_price") or 0)
        qty = float(pos.get("size") or 0)
        if side not in ("LONG", "SHORT") or entry <= 0 or qty <= 0:
            return False
        prev = int(getattr(self, "reentry_attempt", 0) or 0)
        prev_frac = float(getattr(self, "radar_activation_frac", 0.0) or 0.0)
        bumped = bump_after_reentry_fill(
            prev, prev_frac, self.symbol,
            adx_tier=int(getattr(self, "adx_tier", 1) or 1),
            adx=float(
                getattr(self, "radar_activation_adx", 0)
                or getattr(self, "last_adx", 0)
                or 25.0
            ),
            entry=entry,
            open_atr=float(
                getattr(self, "cycle_open_atr", 0)
                or getattr(self, "open_atr", 0)
                or 0
            ),
            side=side,
            tp1=float((list(getattr(self, "tv_tps", None) or [0])[0]) or 0),
            tp2=float((list(getattr(self, "tv_tps", None) or [0, 0])[1]) or 0),
        )
        # 成交：释放本地标签（允许下次再入周期）
        self.reentry_limit_order_id = None
        self.reentry_limit_px = 0.0
        self.reentry_limit_deadline_ts = 0.0
        self.reentry_active = False
        self._clear_reentry_order_tag(reason="再入成交释放")
        for k, v in bumped.items():
            if k == "tier_coeffs":
                continue
            setattr(self, k, v)

        snap = dict(getattr(self, "_reentry_open_snap", None) or {})
        tv_tps = list(snap.get("tv_tps") or [0, 0, 0])
        atr = float(
            getattr(self, "cycle_open_atr", 0) or snap.get("atr") or 0
        )
        tv_price = float(
            getattr(self, "cycle_tv_price", 0) or snap.get("tv_price") or entry
        )

        self.current_side = side
        self.watched_entry = entry
        self.watched_qty = qty
        self.initial_qty = qty
        self.tv_price = tv_price
        if hasattr(self, "_sanitize_tp_prices"):
            self.tv_tps = self._sanitize_tp_prices(tv_tps)
        else:
            self.tv_tps = tv_tps
        self.open_atr = atr
        self._tv_signal_atr = atr
        self.monitoring = True
        self._apply_tier_breath_overlay()

        # 成交价可能偏离 TV：TP 方向无效则按新成交价重算；硬止损一律按 fill+滑点
        try:
            if hasattr(self, "_ensure_tp123_prices_from_tv"):
                if not self._tp_prices_valid_for_side(side, entry):
                    self.tv_tps = [0.0, 0.0, 0.0]
                self._ensure_tp123_prices_from_tv(entry)
        except Exception as e:
            logger.warning(f"[{self.symbol}] 再入成交 TP 重算跳过: {e}")

        self._begin_open_radar_dormant(
            side=side, entry=entry, tv_price=tv_price, open_atr=atr,
            reentry_attempt=int(bumped["reentry_attempt"]),
        )
        radar_init = 0.0
        try:
            from breath_stop import initial_stop_price
            init = initial_stop_price(
                side, entry, atr, profile=getattr(self, "breath_profile", None),
            )
            if init > 0:
                radar_init = float(init)
                self.initial_stop = radar_init
                self.current_sl = radar_init
                self.tv_sl = radar_init
        except Exception:
            pass

        self._save_state()
        self._ensure_price_ws()
        self._ensure_sentinel_running()
        hard_px = 0.0
        arm_ok = False
        try:
            arm_ok = bool(self._arm_temp_stop_and_tp12(
                qty, entry, side,
                source=f"再入成交·attempt={self.reentry_attempt}",
            ))
            hard_px = float(getattr(self, "frozen_hard_sl_px", 0) or 0)
            self._resolve_atr_scenario_after_open(entry, side, qty)
            # 恢复雷达账本价（arm 会暂用硬止损覆写 initial_stop）
            if radar_init > 0:
                self.initial_stop = radar_init
                self.current_sl = radar_init
                self.tv_sl = radar_init
            if self._radar_is_dormant():
                self._strip_radar_stop_keep_hard(reason="再入后雷达仍休眠")
        except Exception as e:
            logger.error(f"[{self.symbol}] 再入后防线失败: {e}")

        # 成交后检查点：硬止损 + TP12 必须已挂；钉钉实盘核实
        hard_hung = hard_px > 0 and arm_ok
        tp_note = ""
        try:
            tps = list(getattr(self, "tv_tps", None) or [])
            tp_note = (
                f"TP1={float(tps[0] or 0):.2f} TP2={float(tps[1] or 0):.2f}"
                if len(tps) >= 2 else "TP=?"
            )
        except Exception:
            tp_note = "TP=?"
        slip = abs(entry - tv_price) if tv_price > 0 else 0.0
        try:
            import dingtalk
            self._call_dingtalk(
                dingtalk.report_reentry_fill,
                side=str(side),
                qty=float(qty),
                fill_price=float(entry),
                tv_price=float(tv_price),
                entry_price=float(entry),
                hard_sl=float(hard_px),
                hard_sl_hung=bool(hard_hung),
                regime=int(getattr(self, "adx_tier", 3) or 3),
                attempt=int(self.reentry_attempt),
                symbol=self.symbol,
                tp1=float(tps[0]) if len(tps) >= 1 else 0,
                tp2=float(tps[1]) if len(tps) >= 2 else 0,
            )
        except Exception:
            pass
        logger.info(
            f"✅ [{self.symbol}] 再入成交 {side} {qty}@{entry:.2f} "
            f"attempt={self.reentry_attempt} hard@{hard_px:.2f} "
            f"dormant=1 hung={1 if hard_hung else 0}"
        )
        return True

    def _strip_radar_stop_keep_hard(self, reason=""):
        """休眠窗：盘口只留 closePosition 永久硬止损，撤掉其余 STOP（禁双挂）。"""
        try:
            from binance_client import binance_client
            ids = dict(getattr(self, "_defense_order_ids", {}) or {})
            hard_id = str(ids.get("hard_stop") or "").strip()
            rid = str(ids.get("radar_stop") or ids.get("stop") or "").strip()
            cancelled = []
            if rid and rid != hard_id:
                try:
                    binance_client.cancel_order(self.symbol, order_id=rid)
                    cancelled.append(rid)
                except Exception as e:
                    logger.debug(f"撤雷达id失败 {rid}: {e}")
            # 扫盘口：非 closePosition 的 STOP 一律撤（休眠期不应存在）
            try:
                orders = binance_client.get_open_orders(
                    self.symbol, include_algo=True,
                ) or []
            except Exception:
                orders = []
            for o in orders:
                t = str(o.get("type") or o.get("orderType") or "").upper()
                if "STOP" not in t:
                    continue
                cp = str(o.get("closePosition") or "").lower() in ("true", "1")
                if cp:
                    continue
                oid = str(o.get("orderId") or o.get("algoId") or "")
                if not oid or oid == hard_id:
                    continue
                try:
                    binance_client.cancel_order(self.symbol, order_id=oid)
                    cancelled.append(oid)
                except Exception as e:
                    logger.debug(f"撤休眠STOP失败 {oid}: {e}")
            if cancelled:
                ids["radar_stop"] = ""
                ids["stop"] = ""
                self._defense_order_ids = ids
                logger.info(
                    f"🗑️ [{self.symbol}] 已撤休眠期雷达/多余STOP "
                    f"{cancelled} | {reason}"
                )
            try:
                self._clear_pending_tags_for_kind("RADAR", save=False)
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"撤休眠雷达跳过: {e}")
        self.radar_activated = False
        self.radar_pending_arm = True

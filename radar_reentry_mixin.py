#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
递进雷达闸门 + 智能限价再入场（混入 PositionSupervisorBinance）。
终极版：5m/3m 极值优于 TV 挂限价；休眠至激活线；硬止损不重入。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from reentry_profiles import (
    activation_frac_for_attempt,
    apply_tier_to_breath_profile,
    get_reentry_profile,
    reentry_enabled,
    tier_label,
)
from smart_reentry_engine import (
    blank_reentry_state,
    bump_after_reentry_fill,
    evaluate_flat_for_reentry,
    init_cycle_on_open,
    max_unfilled_refreshes,
    plan_reentry_limit,
)

logger = logging.getLogger(__name__)


class RadarReentryMixin:
    """递进激活 + 限价再入场。依赖宿主的 binance_client / dingtalk / breath 方法。"""

    def _init_reentry_runtime(self):
        blank = blank_reentry_state()
        for k, v in blank.items():
            setattr(self, k, v)
        self._reentry_open_snap = None
        self._reentry_cycle_aborted = False
        self._base_breath_profile = dict(getattr(self, "breath_profile", None) or {})

    def _reentry_state_dict(self) -> Dict[str, Any]:
        return {
            "reentry_attempt": int(getattr(self, "reentry_attempt", 0) or 0),
            "radar_tier": int(getattr(self, "radar_tier", 0) or 0),
            "radar_activation_frac": float(
                getattr(self, "radar_activation_frac", 0.50) or 0.50
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
            if k in ("reentry_attempt", "radar_tier", "reentry_unfilled_refreshes"):
                setattr(self, k, int(val or 0))
            elif k in (
                "radar_activation_frac", "cycle_tv_price", "cycle_open_atr",
                "cycle_entry", "reentry_limit_px", "reentry_limit_deadline_ts",
                "last_exit_px",
            ):
                setattr(self, k, float(val or 0))
            elif k in ("reentry_active", "radar_pending_arm"):
                setattr(self, k, bool(val))
            else:
                setattr(self, k, val)

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
            or getattr(self, "reentry_attempt", 0)
            or 0
        )
        self.breath_profile = apply_tier_to_breath_profile(
            dict(self._base_breath_profile or base),
            attempt,
            get_reentry_profile(self.symbol),
        )

    def _begin_open_radar_dormant(self, *, side, entry, tv_price, open_atr,
                                  reentry_attempt=None):
        """开仓后：硬+TP 已挂；雷达休眠至激活线。"""
        attempt = int(
            reentry_attempt if reentry_attempt is not None
            else getattr(self, "reentry_attempt", 0) or 0
        )
        st = init_cycle_on_open(
            side=side,
            tv_price=tv_price,
            entry=entry,
            open_atr=open_atr,
            reentry_attempt=attempt,
            symbol=self.symbol,
        )
        for k, v in st.items():
            setattr(self, k, v)
        self.radar_activated = False
        self._radar_handoff_done = False
        self._radar_armed_after_tp1 = False
        self._radar_activation_notified = False
        self._radar_notify_pending = False
        frac = float(st["radar_activation_frac"])
        self._radar_trigger_gate = (
            f"递进雷达·{int(frac * 100)}%×TP1距·attempt={attempt}"
        )
        self._apply_tier_breath_overlay()
        logger.info(
            f"⏳ [{self.symbol}] 雷达休眠至激活 "
            f"frac={frac:.0%} attempt={attempt} "
            f"gate≈{self._radar_activation_price():.2f}"
        )

    def _radar_is_dormant(self) -> bool:
        if bool(getattr(self, "radar_activated", False)):
            return False
        return bool(getattr(self, "radar_pending_arm", True))

    def _maybe_arm_radar_on_activation(self, live_qty, curr_px, source=""):
        """价触激活线：挂雷达 STOP@initialStop，开始呼吸。"""
        if bool(getattr(self, "radar_activated", False)):
            return True
        if not self._price_reached_radar_activation(curr_px, live_only=True):
            return False
        live_qty = float(live_qty or self.watched_qty or 0)
        if live_qty <= 0:
            return False
        init = float(getattr(self, "initial_stop", 0) or 0)
        if init <= 0:
            init = float(getattr(self, "current_sl", 0) or 0)
        if init <= 0:
            logger.warning(f"⚠️ [{self.symbol}] 达激活线但无 initial_stop | {source}")
            return False
        self.current_sl = float(init)
        self.tv_sl = float(init)
        self._apply_tier_breath_overlay()
        ok = self._ensure_radar_sl(init, live_qty=live_qty, for_handoff=True)
        self.radar_activated = True
        self.radar_pending_arm = False
        self._radar_handoff_done = True
        self._radar_armed_after_tp1 = True
        frac = float(getattr(self, "radar_activation_frac", 0.5) or 0.5)
        self._radar_trigger_gate = (
            f"递进雷达已激活·{int(frac * 100)}%×TP1 | {source or '价触'}"
        )
        self._radar_stage_last = 1
        if not getattr(self, "_radar_activation_notified", False):
            self._radar_notify_pending = True
            try:
                self._report_radar_first_activation(
                    live_qty, curr_px, init, sl_placed=bool(ok),
                    trigger_gate=self._radar_trigger_gate,
                )
            except Exception as e:
                logger.debug(f"雷达激活钉钉跳过: {e}")
            self._radar_activation_notified = True
            self._radar_notify_pending = False
        self._save_state()
        logger.info(
            f"📡 [{self.symbol}] 雷达已激活 @{init:.2f} | "
            f"{self._radar_trigger_gate} | hung={bool(ok)}"
        )
        return True

    def _snapshot_cycle_for_reentry(self) -> Dict[str, Any]:
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
                getattr(self, "radar_activation_frac", 0.5) or 0.5
            ),
            "tv_tps": list(getattr(self, "tv_tps", None) or [0, 0, 0]),
            "frozen_hard_sl_px": float(getattr(self, "frozen_hard_sl_px", 0) or 0),
            "initial_stop": float(getattr(self, "initial_stop", 0) or 0),
            "current_sl": float(getattr(self, "current_sl", 0) or 0),
            "radar_activated": bool(getattr(self, "radar_activated", False)),
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
        return abs(px - hard) <= max(2.5, px * 0.002)

    def _fetch_reentry_klines(self):
        """拉取 5m / 3m 最近一根 K 线。"""
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

    def _maybe_start_smart_limit_reentry(self, snap: Dict[str, Any], meta: Dict[str, Any]):
        """仓位归零且微赚/保本后挂限价再入；硬止损/亏损/超次不挂。"""
        if not reentry_enabled(self.symbol):
            logger.info(f"⏸ [{self.symbol}] 智能再入已关闭(enabled=False)")
            return False
        if getattr(self, "_reentry_cycle_aborted", False):
            return False
        if bool(getattr(self, "reentry_active", False)):
            return False
        if self.monitoring or float(getattr(self, "watched_qty", 0) or 0) > 0:
            return False
        # 挂单前确认空仓
        pos = self._get_active_position(prefer_ws=False)
        if pos == "QUERY_FAILED":
            return False
        if pos and float(pos.get("size") or 0) > 0:
            return False

        snap = snap or {}
        meta = meta or {}
        exit_src = str(meta.get("exit_source") or "")
        side = str(snap.get("side") or meta.get("side") or "").upper()
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

        ok, why = evaluate_flat_for_reentry(
            exit_source=exit_src,
            side=side,
            entry=entry,
            exit_px=exit_px,
            atr=atr,
            reentry_attempt=attempt,
            symbol=self.symbol,
        )
        if not ok:
            logger.info(
                f"🚫 [{self.symbol}] 不启动再入场: {why} | "
                f"src={exit_src} exit={exit_px:.2f} attempt={attempt}"
            )
            if why in ("hard_sl_no_reentry", "max_reentries", "tv_close_no_reentry"):
                self._clear_reentry_cycle(source=why)
            return False

        self.cycle_tv_side = side
        self.cycle_tv_price = float(snap.get("tv_price") or 0)
        self.cycle_open_atr = atr
        self.cycle_entry = entry
        self.reentry_attempt = attempt
        self.radar_tier = attempt
        self.radar_activation_frac = float(
            snap.get("radar_activation_frac")
            or activation_frac_for_attempt(attempt, get_reentry_profile(self.symbol))
        )
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
            import dingtalk
            self._call_dingtalk(
                dingtalk.report_system_alert,
                title=f"智能再入场限价已挂 [{self.symbol}]",
                detail=(
                    f"{side} 档位{tier_label(attempt)}→{tier_label(attempt + 1)} "
                    f"attempt={attempt}/{int(get_reentry_profile(self.symbol).get('max_reentries') or 4)} | "
                    f"limit@{float(self.reentry_limit_px):.2f} | "
                    f"TV@{float(self.cycle_tv_price):.2f} | "
                    f"exit={exit_src}@{exit_px:.2f}"
                ),
                level="提示",
            )
        except Exception:
            pass
        return True

    def _place_reentry_limit(self, side=None, reason="", *, is_refresh=False):
        from binance_client import binance_client

        side = str(side or getattr(self, "cycle_tv_side", "") or "").upper()
        if side not in ("LONG", "SHORT"):
            return False

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
                self._clear_reentry_cycle(source="unfilled_refresh_cap")
                return False

        # 挂单前确认无持仓
        pos = self._get_active_position(prefer_ws=False)
        if pos == "QUERY_FAILED":
            return False
        if pos and float(pos.get("size") or 0) > 0:
            logger.warning(f"🚫 [{self.symbol}] 再入挂单前仍有仓 → 中止")
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

        self._cancel_reentry_limit(reason="刷新前撤旧")
        open_side = "BUY" if side == "LONG" else "SELL"
        lim = float(plan["limit_px"])
        order = binance_client.place_limit_order(
            open_side, qty, lim, symbol=self.symbol, reduce_only=False,
        )
        if not order:
            return False
        oid = order.get("orderId") or order.get("algoId")
        self.reentry_active = True
        self.reentry_limit_order_id = oid
        self.reentry_limit_px = lim
        self.reentry_limit_deadline_ts = float(plan["deadline_ts"])
        self._save_state()
        logger.info(
            f"📥 [{self.symbol}] 再入限价已挂 {side} {qty} @{lim:.2f} "
            f"src={plan.get('source')} id={oid} | {reason} | "
            f"refresh={int(getattr(self, 'reentry_unfilled_refreshes', 0) or 0)}"
        )
        return True

    def _cancel_reentry_limit(self, reason=""):
        from binance_client import binance_client

        oid = getattr(self, "reentry_limit_order_id", None)
        if oid:
            try:
                binance_client.cancel_order(self.symbol, order_id=oid)
                logger.info(
                    f"🗑️ [{self.symbol}] 撤再入限价 id={oid} | {reason}"
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
        """再入限价成交 → attempt+1，按新档休眠雷达，挂 hard+TP。"""
        side = str(pos.get("side") or getattr(self, "cycle_tv_side", "") or "").upper()
        entry = float(pos.get("entry_price") or 0)
        qty = float(pos.get("size") or 0)
        if side not in ("LONG", "SHORT") or entry <= 0 or qty <= 0:
            return False
        prev = int(getattr(self, "reentry_attempt", 0) or 0)
        prev_frac = float(getattr(self, "radar_activation_frac", 0.5) or 0.5)
        bumped = bump_after_reentry_fill(prev, prev_frac, self.symbol)
        self.reentry_limit_order_id = None
        self.reentry_limit_px = 0.0
        self.reentry_limit_deadline_ts = 0.0
        self.reentry_active = False
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
        self._begin_open_radar_dormant(
            side=side, entry=entry, tv_price=tv_price, open_atr=atr,
            reentry_attempt=int(bumped["reentry_attempt"]),
        )
        try:
            from breath_stop import initial_stop_price
            init = initial_stop_price(
                side, entry, atr, profile=getattr(self, "breath_profile", None),
            )
            if init > 0:
                self.initial_stop = float(init)
                self.current_sl = float(init)
                self.tv_sl = float(init)
        except Exception:
            pass

        self._save_state()
        self._ensure_price_ws()
        self._ensure_sentinel_running()
        try:
            self._arm_temp_stop_and_tp12(
                qty, entry, side,
                source=f"再入成交·attempt={self.reentry_attempt}",
            )
            self._resolve_atr_scenario_after_open(entry, side, qty)
            if self._radar_is_dormant():
                self._strip_radar_stop_keep_hard(reason="再入后雷达仍休眠")
        except Exception as e:
            logger.error(f"[{self.symbol}] 再入后防线失败: {e}")
        try:
            import dingtalk
            self._call_dingtalk(
                dingtalk.report_system_alert,
                title=f"智能再入已成交 [{self.symbol}]",
                detail=(
                    f"{side} {qty} @ {entry:.2f} | "
                    f"档位{tier_label(int(self.reentry_attempt))} "
                    f"attempt={self.reentry_attempt} "
                    f"frac={float(self.radar_activation_frac):.0%} | "
                    f"雷达休眠至激活线"
                ),
                level="提示",
            )
        except Exception:
            pass
        logger.info(
            f"✅ [{self.symbol}] 再入成交 {side} {qty}@{entry:.2f} "
            f"attempt={self.reentry_attempt} dormant=1"
        )
        return True

    def _strip_radar_stop_keep_hard(self, reason=""):
        """休眠窗：尽量只留硬止损。"""
        try:
            from binance_client import binance_client
            ids = dict(getattr(self, "_defense_order_ids", {}) or {})
            rid = ids.get("radar_stop") or ids.get("stop")
            hard_id = ids.get("hard_stop")
            if rid and str(rid) != str(hard_id or ""):
                binance_client.cancel_order(self.symbol, order_id=rid)
                ids["radar_stop"] = ""
                ids["stop"] = ""
                self._defense_order_ids = ids
                logger.info(f"🗑️ [{self.symbol}] 已撤休眠期雷达单 | {reason}")
        except Exception as e:
            logger.debug(f"撤休眠雷达跳过: {e}")
        self.radar_activated = False
        self.radar_pending_arm = True

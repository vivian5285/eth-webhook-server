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
    STERILE_MAX_RETRY,
    apply_tier_to_breath_profile,
    arm_stop_price,
    get_reentry_profile,
    live_breath_zone_values,
    make_reentry_client_order_id,
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
                "tp1_already_filled", "tier_not_strong",
            ):
                self._clear_reentry_cycle(source=why)
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

# -*- coding: utf-8 -*-
"""One-shot patch: replace VPS wide SL core with TV hard SL only."""
from pathlib import Path

NEW = r'''    def _shield_stop_price(self, entry=None):
        """实盘硬止损价 = TV tv_sl（严格）。"""
        return self._tv_hard_sl_target(entry) or None

    def _resolve_hard_sl_regime(self):
        """开仓档位锁定（雷达/TP 比例用）；硬止损价本身只认 TV tv_sl。"""
        return int(getattr(self, "open_regime", None) or self.regime or 3)

    def _tv_hard_sl_target(self, entry=None, side=None, regime=None):
        """
        实盘硬止损唯一来源：TV tv_sl（账本）→ 回退 tv_sl_ref。
        禁止再用开仓价×档位% 的 VPS 宽止损。
        """
        # 优先 tv_sl_ref（真 TV）；旧账本 tv_sl 可能是遗留 VPS% 宽价
        px = round(float(getattr(self, "tv_sl_ref", 0) or 0), 2)
        if px <= 0:
            px = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        if px > 0:
            return px
        last = self.last_tv_signal if isinstance(self.last_tv_signal, dict) else {}
        for src in (
            last,
            last.get("payload") if isinstance(last.get("payload"), dict) else {},
            getattr(self, "_pending_open_defense_snap", None) or {},
        ):
            if not isinstance(src, dict):
                continue
            cand = round(self._safe_float(src.get("tv_sl"), 0), 2)
            if cand > 0:
                self.tv_sl = cand
                self.tv_sl_ref = cand
                return cand
        return 0.0

    def _vps_hard_sl_target(self, entry=None, side=None, regime=None):
        """兼容旧名 → 已改为 TV 硬止损。"""
        return self._tv_hard_sl_target(entry, side, regime)

    def _matches_any_vps_regime_stop(self, stop_px, entry=None, side=None):
        """旧 VPS% 档位匹配已废弃；恒 False（不再用 VPS 宽价识别）。"""
        return 0

    def _looks_like_tv_tight_stop(self, stop_px, entry=None, side=None):
        """
        旧逻辑：把 TV 止损当「紧价」禁止挂盘 — 已废除。
        恒返回 False，允许/要求挂 TV 硬止损。
        """
        return False

    def _is_valid_radar_sl(self, sl, entry=None, side=None):
        """雷达保本只能在浮盈侧：LONG > entry，SHORT < entry。"""
        entry = float(entry if entry is not None else (self.watched_entry or 0))
        side = str(side or self.current_side or "").strip().upper()
        sl = round(float(sl or 0), 2)
        if entry <= 0 or sl <= 0 or side not in ("LONG", "SHORT"):
            return False
        if side == "LONG":
            return sl > entry + 0.01
        return sl < entry - 0.01

    def _is_exchange_stop_acceptable_as_vps_floor(self, stop_px, entry=None, side=None):
        """盘口 STOP 贴近 TV 硬止损（或合法雷达）即可写回。"""
        stop_px = round(float(stop_px or 0), 2)
        if stop_px <= 0:
            return False
        tv = self._tv_hard_sl_target(entry, side)
        tol = max(float(SHIELD_STOP_TOLERANCE), stop_px * 0.002)
        if tv > 0 and abs(stop_px - tv) <= tol:
            return True
        return self._is_valid_radar_sl(stop_px, entry, side)

    def _sanitize_vps_hard_sl_ledger(self, source=""):
        """
        强制账本硬止损 = TV tv_sl（不得用 VPS% 覆盖）。
        若仅有 tv_sl_ref → 写入 tv_sl；两者皆无 → False（调用方告警）。
        """
        entry = float(self.watched_entry or 0)
        side = str(self.current_side or "").strip().upper()
        tv = self._tv_hard_sl_target(entry, side)
        if tv <= 0:
            logger.error(
                f"🚨 [{self.symbol}] 硬止损账本消毒失败：无 TV tv_sl | {source}"
            )
            return False
        cur = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        if abs(cur - tv) > SHIELD_STOP_TOLERANCE or cur <= 0:
            old = cur
            self.tv_sl = tv
            if float(getattr(self, "tv_sl_ref", 0) or 0) <= 0:
                self.tv_sl_ref = tv
            self._last_applied_exchange_sl = 0.0
            self._save_state()
            logger.info(
                f"🛡️ TV硬止损账本对齐 @{tv:.2f} "
                f"(原 {old or 0:.2f}) | {source or '消毒'}"
            )
        return True

    def _refresh_vps_hard_sl(self, entry=None, side=None, regime=None, atr=None,
                             tv_sl_ref=None, source=""):
        """
        硬止损刷新：严格写入 TV tv_sl 并作为盘口挂单价。
        禁止开仓价×档位% 的 VPS 宽止损覆盖。
        """
        entry = float(entry or self.watched_entry or self.tv_price or 0)
        side = (side or self.current_side or "").strip().upper()

        ref = 0.0
        if tv_sl_ref is not None:
            ref = round(self._safe_float(tv_sl_ref, 0), 2)
        if ref <= 0:
            ref = round(float(getattr(self, "tv_sl_ref", 0) or 0), 2)
        if ref <= 0:
            # 仅当无 ref 时才读 tv_sl（避免旧 VPS% 污染当「TV」）
            last = self.last_tv_signal if isinstance(self.last_tv_signal, dict) else {}
            for src in (
                last,
                last.get("payload") if isinstance(last.get("payload"), dict) else {},
                getattr(self, "_pending_open_defense_snap", None) or {},
            ):
                if not isinstance(src, dict):
                    continue
                cand = round(self._safe_float(src.get("tv_sl"), 0), 2)
                if cand > 0:
                    ref = cand
                    break
        if ref <= 0:
            ref = round(float(getattr(self, "tv_sl", 0) or 0), 2)

        if ref <= 0:
            logger.error(
                f"🚨 [{self.symbol}] TV硬止损缺失，无法刷新 | {source} "
                f"entry={entry} side={side}"
            )
            return False

        old = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        self.tv_sl_ref = ref
        self.tv_sl = ref
        if abs(ref - old) > SHIELD_STOP_TOLERANCE:
            self._last_applied_exchange_sl = 0.0
        self._save_state()
        logger.info(
            f"🛡️ TV硬止损 @{ref:.2f} | {side or '?'} entry={entry:.2f}"
            + (f" ({source})" if source else "")
            + (f" | 原 {old:.2f}" if old > 0 and abs(ref - old) > SHIELD_STOP_TOLERANCE else "")
        )
        return True

    def _apply_tv_sl_from_payload(self, payload, source=""):
        """TV tv_sl → 账本硬止损（严格）；开仓后由 sync 挂到交易所。"""
        tv_ref = payload.get("tv_sl")
        if tv_ref is None or tv_ref == "":
            ok = self._refresh_vps_hard_sl(source=source or "信号·无tv_sl字段")
            if not ok:
                dingtalk.report_system_alert(
                    f"TV硬止损缺失 [{self.symbol}]",
                    f"{source or '信号'} payload 无 tv_sl，无法挂硬止损",
                )
            return ok
        ref_px = round(self._safe_float(tv_ref, 0), 2)
        if ref_px <= 0:
            return False
        entry = float(self.tv_price or self.watched_entry or 0)
        side = str(payload.get("action") or payload.get("side") or self.current_side or "").upper()
        if side not in ("LONG", "SHORT"):
            side = self.current_side
        return self._refresh_vps_hard_sl(
            entry=entry, side=side,
            regime=self._resolve_hard_sl_regime(), atr=self.current_atr,
            tv_sl_ref=ref_px, source=source or "TV硬止损",
        )

    def _effective_exchange_stop(self, radar_sl=None):
        """
        合并止损：底线 = TV 硬止损；雷达已交棒且在浮盈侧时可替换为雷达保本。
        """
        floor = self._tv_hard_sl_target()
        if floor > 0:
            self.tv_sl = floor
        radar = None
        if radar_sl and float(radar_sl) > 0:
            cand = round(float(radar_sl), 2)
            if self._is_valid_radar_sl(cand):
                radar = cand
            else:
                logger.warning(
                    f"🛡️ [{self.symbol}] 拒绝非法雷达价 @{cand:.2f} "
                    f"→ 仅挂 TV硬止损@{floor or 0:.2f}"
                )
        if not floor and not radar:
            return None
        if not floor:
            return radar
        if not radar:
            return floor
        if self.current_side == "LONG":
            return max(radar, floor) if radar > floor else radar
        if self.current_side == "SHORT":
            return radar
        return floor

    def _clamp_radar_to_vps_floor(self, radar_sl):
        """雷达保本：非法 → 回退 TV 硬止损。"""
        if not radar_sl:
            return self._tv_hard_sl_target() or radar_sl
        if self._is_valid_radar_sl(radar_sl):
            return round(float(radar_sl), 2)
        return self._tv_hard_sl_target() or None

    def _clamp_radar_to_tv_floor(self, radar_sl):
        """兼容旧名 → TV 硬止损底线夹紧"""
        return self._clamp_radar_to_vps_floor(radar_sl)

    def _purge_all_close_position_stops(self):
        """撤净所有 closePosition 止损（TV硬止损与雷达共用单槽）"""
        cancelled = 0
        for o in binance_client.get_open_orders(self.symbol):
            order_type = str(o.get("type") or o.get("orderType") or "").upper()
            if order_type not in ("STOP", "STOP_MARKET"):
                continue
            if not binance_client._truthy_close_position(o.get("closePosition")):
                continue
            oid = o.get("orderId") or o.get("algoId")
            if oid:
                if o.get("algoId") is not None:
                    binance_client.cancel_algo_order(self.symbol, oid)
                else:
                    binance_client.cancel_order(self.symbol, oid)
                cancelled += 1
                time.sleep(0.12)
        return cancelled

    def _purge_all_protective_stops(self, keep_near=None, tolerance=None):
        """
        撤净全部保护性 STOP / STOP_MARKET（含 Stop-Limit reduceOnly + Algo closePosition）。
        keep_near: 若给出目标价，保留触发价贴近该价的单仓位；其余一律撤。
        """
        keep_near = float(keep_near or 0)
        tol = float(tolerance if tolerance is not None else SHIELD_STOP_TOLERANCE)
        cancelled = 0
        for o in binance_client.get_open_orders(self.symbol, include_algo=True):
            order_type = str(o.get("type") or o.get("orderType") or "").upper()
            if order_type not in ("STOP", "STOP_MARKET"):
                continue
            px = self._order_stop_price(o)
            if keep_near > 0 and px is not None and abs(px - keep_near) <= tol:
                continue
            oid = o.get("orderId") or o.get("algoId")
            if not oid:
                continue
            binance_client.cancel_order(self.symbol, order=o)
            cancelled += 1
            time.sleep(0.12)
        return cancelled

    def _count_protective_stops(self):
        return binance_client.find_protective_stop_prices(self.symbol)

    def _place_vps_hard_sl_order(self, live_qty, trigger_px, use_stop_limit=False):
        """
        TV 硬止损：Stop-Market closePosition（不占 reduceOnly 额度）。
        多空一律按 TV tv_sl 触发价挂单；禁止改回 VPS%。
        """
        live_qty = self._resolve_live_qty(live_qty)
        trigger_px = round(float(trigger_px or 0), 2)
        if live_qty <= 0 or trigger_px <= 0 or not self.current_side:
            return None
        curr_px = float(binance_client.get_current_price(self.symbol) or 0)
        if curr_px > 0:
            gap = max(2.5, curr_px * 0.0015)
            if self.current_side == "LONG" and trigger_px >= curr_px - gap:
                safe = round(curr_px - gap * 1.25, 2)
                logger.warning(
                    f"⚠️ [{self.symbol}] LONG TV硬止损 @{trigger_px:.2f} 贴/穿市 "
                    f"{curr_px:.2f} → 推低到安全 @{safe:.2f}（禁裸仓秒触）"
                )
                trigger_px = safe
            elif self.current_side == "SHORT" and trigger_px <= curr_px + gap:
                safe = round(curr_px + gap * 1.25, 2)
                logger.warning(
                    f"⚠️ [{self.symbol}] SHORT TV硬止损 @{trigger_px:.2f} 贴/穿市 "
                    f"{curr_px:.2f} → 推高到安全 @{safe:.2f}（禁裸仓秒触）"
                )
                trigger_px = safe
            if trigger_px <= 0:
                return None
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        if use_stop_limit:
            limit_px = compute_vps_hard_sl_limit_price(self.current_side, trigger_px)
            return binance_client.place_stop_limit_order(
                close_side, live_qty, trigger_px, symbol=self.symbol, limit_price=limit_px,
            )
        return binance_client.place_stop_market_order(
            close_side, trigger_px, symbol=self.symbol, quantity=None,
        )

    def _sync_exchange_stop(self, live_qty, radar_sl=None, reason="", force=False):
        """
        统一交易所保护止损为单槽：挂 TV 硬止损（或合法浮盈侧雷达）。
        禁止改回 VPS%；无 TV 价 → 告警且失败。
        """
        live_qty = self._resolve_live_qty(live_qty)
        if live_qty <= 0 or not self.current_side or not self.watched_entry:
            return {"ok": False, "skipped": True, "reason": "no_position"}

        self._lock_open_regime_from_sources(force=False)
        self._sanitize_vps_hard_sl_ledger(source=reason or "同步止损消毒")
        target = self._effective_exchange_stop(radar_sl)
        if not target or target <= 0:
            logger.error(
                f"🚨 [{self.symbol}] 同步硬止损失败：无 TV tv_sl | {reason}"
            )
            try:
                self._call_dingtalk(
                    dingtalk.report_system_alert,
                    title=f"TV硬止损缺失·无法挂单 [{self.symbol}]",
                    detail=(
                        f"{self.current_side} qty={live_qty} | {reason or '同步'} | "
                        f"请核对 TV payload tv_sl"
                    ),
                    level="紧急",
                    suggestion="等待带 tv_sl 的 TV 信号或人工挂止损",
                )
            except Exception:
                pass
            return {"ok": False, "skipped": True, "reason": "no_tv_sl"}
        target = round(float(target), 2)

        live_stops = self._count_protective_stops()
        near = [p for p in live_stops if abs(p - target) <= SHIELD_STOP_TOLERANCE]
        orphans = [p for p in live_stops if abs(p - target) > SHIELD_STOP_TOLERANCE]

        last = round(float(getattr(self, "_last_applied_exchange_sl", 0) or 0), 2)
        now = time.time()
        if not orphans and len(near) == 1:
            self._last_applied_exchange_sl = target
            self._last_hard_sl_sync_ts = now
            self.shield_active = True
            self.shield_sized_qty = live_qty
            self._tv_sl_missing_alerted = False
            if abs(last - target) > SHIELD_STOP_TOLERANCE:
                self._save_state()
            return {
                "ok": True, "skipped": True, "target": target,
                "reason": "idempotent_unified",
            }

        if (
            not force
            and last > 0
            and abs(last - target) <= SHIELD_STOP_TOLERANCE
            and (now - float(getattr(self, "_last_hard_sl_sync_ts", 0) or 0))
            < HARD_SL_SYNC_COOLDOWN_SEC
        ):
            if not orphans and (near or self._has_stop_sl_near(target, exclude_shield=False)):
                return {
                    "ok": True, "skipped": True, "target": target,
                    "reason": "cooldown_same_target",
                }

        purged = 0
        ok = False
        res = None
        had_old_stops = bool(live_stops)
        for attempt in range(3):
            if self._has_stop_sl_near(target, exclude_shield=False):
                ok = True
                break
            res = self._place_vps_hard_sl_order(
                live_qty, target, use_stop_limit=False,
            )
            time.sleep(0.45 if attempt == 0 else 0.7)
            ok = res is not None and self._has_stop_sl_near(
                target, exclude_shield=False,
            )
            if ok:
                break
            logger.warning(
                f"🛡️ [{self.symbol}] TV硬止损挂单未核实 @{target:.2f} "
                f"重试 {attempt + 1}/3"
            )

        if ok:
            purged = self._purge_all_protective_stops(keep_near=target)
            if purged or orphans:
                logger.warning(
                    f"🛡️ 统一TV硬止损：新挂已核实 @{target:.2f}，清孤儿 {purged} 笔 "
                    f"(原盘口{live_stops})"
                )
                time.sleep(0.35)
                if not self._has_stop_sl_near(target, exclude_shield=False):
                    res = self._place_vps_hard_sl_order(
                        live_qty, target, use_stop_limit=False,
                    )
                    time.sleep(0.45)
                    ok = res is not None and self._has_stop_sl_near(
                        target, exclude_shield=False,
                    )
        elif had_old_stops:
            logger.error(
                f"❌ [{self.symbol}] TV硬止损新挂失败 @{target:.2f}，"
                f"保留原盘口 STOP {live_stops}，禁止撤净裸仓 | {reason}"
            )
            self._record_shield_maintain(success=True)
            return {
                "ok": True, "skipped": False, "target": target, "purged": 0,
                "reason": "place_failed_keep_old",
            }
        else:
            logger.error(
                f"❌ [{self.symbol}] TV硬止损新挂失败且盘口无 STOP → 裸仓 | {reason}"
            )
            try:
                self._call_dingtalk(
                    dingtalk.report_system_alert,
                    title=f"裸仓告警·TV硬止损未挂上 [{self.symbol}]",
                    detail=(
                        f"{self.current_side} qty={live_qty} 目标TV_SL@{target:.2f} "
                        f"| {reason or '同步'} | 请人工挂 closePosition"
                    ),
                    level="紧急",
                    suggestion="币安 APP 按 TV tv_sl 手动挂止损；勿反复重启核武撤单",
                )
            except Exception:
                pass
            self._record_shield_maintain(success=False)
            return {"ok": False, "skipped": False, "target": target, "purged": 0}

        leftovers = [
            p for p in (self._count_protective_stops() or [])
            if abs(float(p) - target) > SHIELD_STOP_TOLERANCE
        ]
        if leftovers and ok:
            extra = self._purge_all_protective_stops(keep_near=target)
            purged += extra
            logger.warning(f"🛡️ 二次清孤儿 STOP{leftovers} 撤 {extra} 笔")
            time.sleep(0.3)
            if not self._has_stop_sl_near(target, exclude_shield=False):
                self._place_vps_hard_sl_order(live_qty, target, use_stop_limit=False)
                time.sleep(0.4)
                ok = self._has_stop_sl_near(target, exclude_shield=False)

        if ok:
            self._last_applied_exchange_sl = target
            self._last_hard_sl_sync_ts = time.time()
            self.shield_active = True
            self.shield_sized_qty = live_qty
            self._shield_fail_streak = 0
            self._tv_sl_missing_alerted = False
            self.current_sl = target
            self._save_state()
            self._record_shield_maintain(success=True)
            logger.info(
                f"✅ [{self.symbol}] TV硬止损已挂 @{target:.2f} | {reason} | "
                f"tv_sl={float(getattr(self, 'tv_sl', 0) or 0) or target:.2f} | "
                f"撤孤儿 {purged} 笔"
            )
        else:
            self._record_shield_maintain(success=False)
        return {"ok": ok, "skipped": False, "target": target, "purged": purged}

    def _handle_tv_sl_update(self, payload):
        """UPDATE_SL：按 TV 新硬止损改盘口（多空一致，严格挂单）。"""
        ref = round(self._safe_float(payload.get("tv_sl"), 0), 2)
        if ref <= 0:
            logger.error(f"UPDATE_SL 忽略：无有效 tv_sl | payload={payload}")
            dingtalk.report_system_alert(
                f"UPDATE_SL 无 tv_sl [{self.symbol}]",
                "TV UPDATE_SL 未带有效 tv_sl，盘口硬止损未改",
            )
            return
        self.tv_sl_ref = ref
        self.tv_sl = ref
        self._last_applied_exchange_sl = 0.0
        self._save_state()
        pos = self._get_active_position()
        live_qty = float((pos or {}).get("size") or self.watched_qty or 0)
        hung = []
        ok = False
        if live_qty > 0 and self.current_side:
            sync = self._sync_exchange_stop(
                live_qty, radar_sl=self._radar_sl_to_pass(),
                reason="UPDATE_SL·按TV硬止损重挂", force=True,
            )
            ok = bool(sync.get("ok"))
            hung = binance_client.find_protective_stop_prices(self.symbol)
        logger.info(
            f"UPDATE_SL 已按 TV 硬止损执行 | tv_sl={ref:.2f} | "
            f"盘口={hung} | ok={ok}"
        )
        try:
            self._call_dingtalk(
                dingtalk.report_tv_sl_updated,
                side=self.current_side or "",
                live_qty=live_qty,
                entry=float(self.watched_entry or 0),
                tv_sl=ref,
                exchange_stop=float(hung[0]) if hung else ref,
                radar_active=self._is_radar_active(),
                radar_sl=self._radar_sl_to_pass(),
                regime=self._resolve_hard_sl_regime(),
                verify_note=f"已按 TV tv_sl={ref:.2f} 同步盘口 | stop={hung}",
                verified=ok or bool(hung),
            )
        except Exception as e:
            logger.warning(f"UPDATE_SL 钉钉失败: {e}")

'''

MARKER_START = "    def _shield_stop_price(self, entry=None):"
MARKER_END = "    def _tp_is_marketable(self, side, tp_px, curr_px, buffer_pct=0.0002):"

def main():
    p = Path(__file__).with_name("position_supervisor_binance.py")
    text = p.read_text(encoding="utf-8")
    start = text.index(MARKER_START)
    end = text.index(MARKER_END)
    new_text = text[:start] + NEW + text[end:]
    p.write_text(new_text, encoding="utf-8")
    print(f"patched {end-start} -> {len(NEW)} chars")

if __name__ == "__main__":
    main()

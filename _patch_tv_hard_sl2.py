# -*- coding: utf-8 -*-
"""Second pass: rewrite remaining VPS-wide hang call sites to TV hard SL."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def patch_supervisor():
    p = ROOT / "position_supervisor_binance.py"
    t = p.read_text(encoding="utf-8")
    reps = [
        (
            'f"VPS宽硬止损@{float(self._vps_hard_sl_target(entry_px) or 0):.2f} "',
            'f"TV硬止损@{float(self._tv_hard_sl_target(entry_px) or 0):.2f} "',
        ),
        (
            """            # 重启叠单 → 强制统一为开仓档位 VPS 宽价（清掉 TV 紧价）
            live_stops = binance_client.find_protective_stop_prices(self.symbol)
            uniq = sorted({round(float(p), 2) for p in live_stops if float(p) > 0})
            target = round(float(self._vps_hard_sl_target(entry, side, hard_regime) or 0), 2)
            if target > 0 and (
                len(uniq) > 1
                or (uniq and all(abs(p - target) > SHIELD_STOP_TOLERANCE for p in uniq))
                or any(self._looks_like_tv_tight_stop(p, entry, side) for p in uniq)
            ):
                qty = float(pos.get("size") or pos.get("positionAmt") or self.watched_qty or 0)
                qty = abs(qty)
                if qty <= 0:
                    qty = float(self.watched_qty or 0)
                if qty > 0:
                    sync = self._sync_exchange_stop(
                        qty, radar_sl=None, reason="接管强制VPS宽硬止损", force=True,
                    )
                    if sync.get("ok"):
                        notes.append(
                            f"VPS宽硬止损@{sync.get('target'):.2f}"
                            f"(撤{sync.get('purged', 0)})"
                        )""",
            """            # 重启叠单/错价 → 强制统一为 TV 硬止损
            live_stops = binance_client.find_protective_stop_prices(self.symbol)
            uniq = sorted({round(float(p), 2) for p in live_stops if float(p) > 0})
            target = round(float(self._tv_hard_sl_target(entry, side) or 0), 2)
            if target > 0 and (
                len(uniq) > 1
                or (uniq and all(abs(p - target) > SHIELD_STOP_TOLERANCE for p in uniq))
                or not uniq
            ):
                qty = float(pos.get("size") or pos.get("positionAmt") or self.watched_qty or 0)
                qty = abs(qty)
                if qty <= 0:
                    qty = float(self.watched_qty or 0)
                if qty > 0:
                    sync = self._sync_exchange_stop(
                        qty, radar_sl=None, reason="接管强制TV硬止损", force=True,
                    )
                    if sync.get("ok"):
                        notes.append(
                            f"TV硬止损@{sync.get('target'):.2f}"
                            f"(撤{sync.get('purged', 0)})"
                        )""",
        ),
        (
            '                    else f"开仓强制挂防线#{r + 1}·VPS宽硬止损"',
            '                    else f"开仓强制挂防线#{r + 1}·TV硬止损"',
        ),
        (
            '开仓/接管铁律闭环：挂齐「应挂」剩余 TP + VPS 宽硬止损。',
            '开仓/接管铁律闭环：挂齐「应挂」剩余 TP + TV 硬止损。',
        ),
        (
            '"""兼容旧入口：一律走 VPS 宽硬止损同步，禁止按 TV 价/旧 Stop-Limit 挂单。"""',
            '"""兼容旧入口：一律走 TV 硬止损同步。"""',
        ),
        (
            'reason=reason or "VPS硬止损(旧盾入口)",',
            'reason=reason or "TV硬止损(旧盾入口)",',
        ),
        (
            '''    def _adopt_exchange_hard_sl(self, source=""):
        """
        实盘已有唯一 STOP 时写回账本；仅当该价贴近/宽于 VPS 计算价。
        TV 紧止损残留一律拒采纳，交统一同步清掉后挂 VPS 宽止损。
        """
        entry = float(self.watched_entry or 0)
        side = (self.current_side or "").upper()
        stops = binance_client.find_protective_stop_prices(self.symbol)
        if not stops:
            return 0.0
        uniq = sorted({round(float(p), 2) for p in stops if float(p) > 0})
        if len(uniq) > 1:
            logger.warning(
                f"🛡️ 盘口多笔硬止损 STOP{uniq} → 拒单笔采纳，强制统一"
                + (f" | {source}" if source else "")
            )
            return 0.0
        chosen = uniq[0]
        if side == "LONG" and entry > 0 and chosen >= entry - 0.01:
            return 0.0
        if side == "SHORT" and entry > 0 and chosen <= entry + 0.01:
            return 0.0
        if not self._is_exchange_stop_acceptable_as_vps_floor(chosen, entry, side):
            vps = self._vps_hard_sl_target(entry, side)
            logger.warning(
                f"🛡️ 拒采纳盘口紧止损 @{chosen:.2f}（疑似 TV）| "
                f"VPS宽止损应为 @{vps:.2f}"
                + (f" | {source}" if source else "")
            )
            return 0.0
        old = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        # 写回时仍归一到 VPS 计算价，避免把略宽盘口价当成永久底线漂移
        vps = self._vps_hard_sl_target(entry, side) or chosen
        self.tv_sl = vps
        if not self.current_sl or float(self.current_sl) <= 0:
            self.current_sl = vps
        self.shield_active = True
        self._tv_sl_missing_alerted = False
        self._last_applied_exchange_sl = chosen
        self._save_state()
        logger.info(
            f"🛡️ 盘口硬止损可接受 @{chosen:.2f} → 账本归一 VPS @{vps:.2f}"
            + (f" (原账本 {old:.2f})" if old and abs(old - vps) > 0.01 else "")
            + (f" | {source}" if source else "")
        )
        return vps''',
            '''    def _adopt_exchange_hard_sl(self, source=""):
        """
        实盘已有唯一 STOP 时写回账本；仅当贴近 TV 硬止损（或合法雷达）。
        禁止再用 VPS% 覆盖 TV 价。
        """
        entry = float(self.watched_entry or 0)
        side = (self.current_side or "").upper()
        stops = binance_client.find_protective_stop_prices(self.symbol)
        if not stops:
            return 0.0
        uniq = sorted({round(float(p), 2) for p in stops if float(p) > 0})
        if len(uniq) > 1:
            logger.warning(
                f"🛡️ 盘口多笔硬止损 STOP{uniq} → 拒单笔采纳，强制统一"
                + (f" | {source}" if source else "")
            )
            return 0.0
        chosen = uniq[0]
        if side == "LONG" and entry > 0 and chosen >= entry - 0.01:
            return 0.0
        if side == "SHORT" and entry > 0 and chosen <= entry + 0.01:
            return 0.0
        if not self._is_exchange_stop_acceptable_as_vps_floor(chosen, entry, side):
            tv = self._tv_hard_sl_target(entry, side)
            logger.warning(
                f"🛡️ 拒采纳盘口异价止损 @{chosen:.2f} | "
                f"TV硬止损应为 @{tv:.2f}"
                + (f" | {source}" if source else "")
            )
            return 0.0
        old = round(float(getattr(self, "tv_sl", 0) or 0), 2)
        tv = self._tv_hard_sl_target(entry, side) or chosen
        # 盘口已贴近 TV → 账本写 TV；若无 TV 则写盘口价
        self.tv_sl = tv
        if float(getattr(self, "tv_sl_ref", 0) or 0) <= 0:
            self.tv_sl_ref = tv
        if not self.current_sl or float(self.current_sl) <= 0:
            self.current_sl = tv
        self.shield_active = True
        self._tv_sl_missing_alerted = False
        self._last_applied_exchange_sl = chosen
        self._save_state()
        logger.info(
            f"🛡️ 盘口硬止损可接受 @{chosen:.2f} → 账本 TV @{tv:.2f}"
            + (f" (原账本 {old:.2f})" if old and abs(old - tv) > 0.01 else "")
            + (f" | {source}" if source else "")
        )
        return tv''',
        ),
        (
            '"""账本硬止损必须是 VPS 宽价；污染则重算，绝不保留 TV 紧止损。"""',
            '"""账本硬止损必须是 TV tv_sl；缺失则自愈/盘口采纳。"""',
        ),
        (
            '"""维护 VPS 宽硬止损 closePosition；雷达激活时合并为 max/min(雷达, VPS底)"""',
            '"""维护 TV 硬止损 closePosition；雷达激活时合并为雷达保本"""',
        ),
        (
            'defense_plan = "持有 TP123 + VPS宽硬止损"',
            'defense_plan = "持有 TP123 + TV硬止损"',
        ),
        (
            'else f"VPS宽硬止损 @ {stop_px:.2f}" if stop_px else "雷达区·待合并"',
            'else f"TV硬止损 @ {stop_px:.2f}" if stop_px else "雷达区·待合并"',
        ),
        (
            'shield_status = f"VPS宽硬止损已挂 @ {stop_px:.2f}" if stop_px else "已核实"',
            'shield_status = f"TV硬止损已挂 @ {stop_px:.2f}" if stop_px else "已核实"',
        ),
        (
            'f"VPS宽硬止损待补挂 @ {stop_px:.2f}" if stop_px',
            'f"TV硬止损待补挂 @ {stop_px:.2f}" if stop_px',
        ),
        (
            'vps_note = f"VPS宽硬止损(R{self._resolve_hard_sl_regime()})"',
            'vps_note = f"TV硬止损(tv_sl)"',
        ),
        (
            'f"🛡️ 重启：盘口 VPS宽硬止损已齐"',
            'f"🛡️ 重启：盘口 TV硬止损已齐"',
        ),
        (
            '"🛡️ 重启：VPS宽硬止损待补挂（宽限期后哨兵按冷却处理）"',
            '"🛡️ 重启：TV硬止损待补挂（宽限期后哨兵按冷却处理）"',
        ),
        (
            'reason="雷达守护·裸仓强制VPS硬止损", force=True,',
            'reason="雷达守护·裸仓强制TV硬止损", force=True,',
        ),
        (
            'f"保留 VPS宽硬止损(closePosition单槽)"',
            'f"保留 TV硬止损(closePosition单槽)"',
        ),
        (
            '"触碰硬止损平仓（VPS宽止损）"',
            '"触碰硬止损平仓（TV硬止损）"',
        ),
        (
            'f"VPS宽硬止损触发 @ {sl:.2f}（雷达未交棒）"',
            'f"TV硬止损触发 @ {sl:.2f}（雷达未交棒）"',
        ),
        (
            '''            # 开仓后硬闸：无论 TP 是否齐，强制 VPS 宽硬止损
            hung = binance_client.find_protective_stop_prices(self.symbol)
            vps_target = self._vps_hard_sl_target(verified["entry_price"])
            bad = [
                p for p in hung
                if self._looks_like_tv_tight_stop(p, verified["entry_price"])
                or (
                    vps_target > 0
                    and abs(float(p) - vps_target) > SHIELD_STOP_TOLERANCE
                    and not self._is_valid_radar_sl(p)
                )
            ]
            self._sync_exchange_stop(
                live_qty, radar_sl=None,
                reason=(
                    "开仓后强制VPS宽硬止损" if (bad or not hung)
                    else "开仓后确认VPS宽硬止损"
                ),
                force=True,
            )''',
            '''            # 开仓后硬闸：无论 TP 是否齐，强制 TV 硬止损
            hung = binance_client.find_protective_stop_prices(self.symbol)
            tv_target = self._tv_hard_sl_target(verified["entry_price"])
            bad = [
                p for p in hung
                if (
                    tv_target > 0
                    and abs(float(p) - tv_target) > SHIELD_STOP_TOLERANCE
                    and not self._is_valid_radar_sl(p)
                )
            ]
            self._sync_exchange_stop(
                live_qty, radar_sl=None,
                reason=(
                    "开仓后强制TV硬止损" if (bad or not hung or tv_target <= 0)
                    else "开仓后确认TV硬止损"
                ),
                force=True,
            )''',
        ),
        (
            'reason="开仓滞后核实·强制VPS硬止损", force=True,',
            'reason="开仓滞后核实·强制TV硬止损", force=True,',
        ),
        (
            'reason="重启强制VPS宽硬止损",',
            'reason="重启强制TV硬止损",',
        ),
        (
            'f"重启不自动平仓，改为挂齐 TP123 + VPS宽硬止损",',
            'f"重启不自动平仓，改为挂齐 TP123 + TV硬止损",',
        ),
        (
            'f"VPS宽硬止损@{vps_sl:.2f}"',
            'f"TV硬止损@{vps_sl:.2f}"',
        ),
        (
            "# 开仓后禁止雷达/近市保本：只允许 TP123 + VPS 宽硬止损",
            "# 开仓后禁止雷达/近市保本：只允许 TP123 + TV 硬止损",
        ),
        (
            "雷达仅档位激活线(R1=85%…R4=70%)或TP1真实成交后交棒；激活线前仅 VPS 宽硬止损。",
            "雷达仅档位激活线(R1=85%…R4=70%)或TP1真实成交后交棒；激活线前仅 TV 硬止损。",
        ),
        (
            "# 无论账本是否有价，一律消毒为 VPS 宽止损（防 TV 紧价污染横跳）",
            "# 无论账本是否有价，一律消毒对齐 TV tv_sl",
        ),
        (
            "重启一次性防线：只挂 VPS 宽硬止损（开仓档位）；雷达仅价触激活线交棒后合并。",
            "重启一次性防线：只挂 TV 硬止损；雷达仅价触激活线交棒后合并。",
        ),
        (
            "TP=reduceOnly；雷达/VPS宽硬止损=closePosition 单槽，互不抢份额。",
            "TP=reduceOnly；雷达/TV硬止损=closePosition 单槽，互不抢份额。",
        ),
        (
            "一律走 closePosition 单槽合并总线（雷达∪VPS宽底），不抢 TP reduceOnly。",
            "一律走 closePosition 单槽合并总线（雷达∪TV硬止损），不抢 TP reduceOnly。",
        ),
        (
            "# 开仓/TP1前：dynamic_sl 一律丢弃，只挂 VPS 宽硬止损",
            "# 开仓/TP1前：dynamic_sl 一律丢弃，只挂 TV 硬止损",
        ),
        (
            '"""仅交棒成功后才允许雷达价；否则 None → 只挂 VPS 宽止损"""',
            '"""仅交棒成功后才允许雷达价；否则 None → 只挂 TV 硬止损"""',
        ),
        (
            "③ VPS 宽硬止损 = 开仓×档位%，与雷达合并为同一 closePosition 单槽",
            "③ TV 硬止损 = tv_sl，与雷达合并为同一 closePosition 单槽",
        ),
        (
            "④ 贴 VPS 宽硬止损且未交棒 → vps_hard_sl",
            "④ 贴 TV 硬止损且未交棒 → vps_hard_sl(兼容名)/tv_hard_sl",
        ),
        (
            "将仅挂 VPS 宽硬止损，哨兵继续补 TP",
            "将仅挂 TV 硬止损，哨兵继续补 TP",
        ),
        (
            "# 开仓后 current_sl 必须是 VPS 宽硬止损，绝不能写成成本价（否则会被当成雷达）",
            "# 开仓后 current_sl 必须是 TV 硬止损，绝不能写成成本价（否则会被当成雷达）",
        ),
        (
            "# 持仓存在：先锁定开仓档位，再消毒/挂 VPS 宽止损（清掉 TV 紧价残留）",
            "# 持仓存在：先锁定开仓档位，再消毒/挂 TV 硬止损",
        ),
        (
            "TV 紧止损残留一律拒采纳，交统一同步清掉后挂 VPS 宽止损。",
            "异价 STOP 拒采纳，交统一同步清掉后挂 TV 硬止损。",
        ),
    ]
    n = 0
    for old, new in reps:
        if old not in t:
            print(f"MISS: {old[:80]!r}")
            continue
        t = t.replace(old, new, 1)
        n += 1
    p.write_text(t, encoding="utf-8")
    print(f"supervisor: {n}/{len(reps)} replacements")

def patch_dingtalk():
    p = ROOT / "dingtalk.py"
    t = p.read_text(encoding="utf-8")
    old = '''def report_tv_sl_updated(side, live_qty, entry, tv_sl, exchange_stop=None,
                         radar_active=False, radar_sl=None, regime=3,
                         verify_note="", verified=True):
    """UPDATE_SL：仅记录 TV 参考；盘口硬止损永远是 VPS 宽价，不挂 TV 紧价。"""
    tv_sl = float(tv_sl or 0)
    exchange_stop = float(exchange_stop or 0)
    action_txt = (
        f"TV UPDATE_SL → 仅记录参考 `{tv_sl:.2f}` · "
        f"盘口保持 VPS宽硬止损"
        + (f" @ `{exchange_stop:.2f}`" if exchange_stop > 0 else "")
        + "（永不挂 TV 紧止损）"
    )
    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "📦 保护头寸": _g(f"**{live_qty}** {_u()}", G_MAIN),
        "💰 开仓成本": _g(f"`{entry:.2f}` USDT", G_MUTED),
        "📊 开仓档位": get_regime_name(regime),
        "📡 TV参考 tv_sl": _g(f"**{tv_sl:.2f}**（仅日志）", G_MUTED),
        "🛡️ 盘口VPS硬止损": _g(
            f"**{exchange_stop:.2f}** USDT" if exchange_stop > 0 else "由军师按开仓档位维护",
            G_MAIN,
        ),
        "📡 雷达状态": _g(
            f"已激活 @ `{float(radar_sl):.2f}`" if radar_active and radar_sl
            else ("已激活" if radar_active else "待命监控中"),
            G_MAIN,
        ),
        "✅ 风控动作": _g(action_txt, G_ACCENT),
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | UPDATE_SL 仅更新参考，未改盘口硬止损",
            f"⏳ {VERIFY_DELAY_MARK}",
        ),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert("📡 TV参考止损 · 已记录（未改盘口）", data, G_TITLE)'''
    new = '''def report_tv_sl_updated(side, live_qty, entry, tv_sl, exchange_stop=None,
                         radar_active=False, radar_sl=None, regime=3,
                         verify_note="", verified=True):
    """UPDATE_SL：按 TV tv_sl 同步盘口硬止损（多空一致）。"""
    tv_sl = float(tv_sl or 0)
    exchange_stop = float(exchange_stop or 0)
    hung = exchange_stop if exchange_stop > 0 else tv_sl
    action_txt = (
        f"TV UPDATE_SL → 盘口硬止损改为 TV `{tv_sl:.2f}`"
        + (f" · 已核实 @{hung:.2f}" if hung > 0 else "")
    )
    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "📦 保护头寸": _g(f"**{live_qty}** {_u()}", G_MAIN),
        "💰 开仓成本": _g(f"`{entry:.2f}` USDT", G_MUTED),
        "📊 开仓档位": get_regime_name(regime),
        "🛡️ TV硬止损": _g(f"**{tv_sl:.2f}** USDT", G_MAIN),
        "📌 盘口STOP": _g(
            f"**{exchange_stop:.2f}** USDT" if exchange_stop > 0 else "同步中/待核实",
            G_MAIN,
        ),
        "📡 雷达状态": _g(
            f"已激活 @ `{float(radar_sl):.2f}`" if radar_active and radar_sl
            else ("已激活" if radar_active else "待命监控中"),
            G_MAIN,
        ),
        "✅ 风控动作": _g(action_txt, G_ACCENT),
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | UPDATE_SL 已按 TV tv_sl 改挂盘口硬止损",
            f"⏳ {VERIFY_DELAY_MARK}",
        ),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert("🛡️ TV硬止损 · UPDATE_SL 已同步盘口", data, G_TITLE)'''
    if old not in t:
        print("DINGTALK MISS report_tv_sl_updated")
    else:
        t = t.replace(old, new, 1)
        print("dingtalk: report_tv_sl_updated ok")
    # other string fixes
    for a, b in [
        ('（TV tv_sl 距离 **{stop_dist:.2f}**，**仅日志参考，永不挂盘**）',
         '（TV tv_sl 距离 **{stop_dist:.2f}**，**实盘按此挂硬止损**）'),
        ('"TP=reduceOnly | 雷达保本+VPS宽硬止损=closePosition单槽 · 互不抢份额"',
         '"TP=reduceOnly | 雷达保本+TV硬止损=closePosition单槽 · 互不抢份额"'),
        ('"由 VPS 宽硬止损触发（雷达尚未交棒）"',
         '"由 TV 硬止损触发（雷达尚未交棒）"'),
        ('"🛡️ VPS宽硬止损": _g(shield_status or "核查中", G_DEEP),',
         '"🛡️ TV硬止损": _g(shield_status or "核查中", G_DEEP),'),
        ('data["📡 TV参考tv_sl"] = _g(f"`{float(tv_sl):.2f}` (仅参考)", G_MUTED)',
         'data["🛡️ TV硬止损"] = _g(f"`{float(tv_sl):.2f}` (盘口挂单价)", G_MAIN)'),
        ('"收到 CLOSE_STOPLOSS → **立即市价全平**（优先于 VPS 宽止损挂单）"',
         '"收到 CLOSE_STOPLOSS → **立即市价全平**（优先于 TV 硬止损挂单）"'),
    ]:
        if a in t:
            t = t.replace(a, b)
            print(f"dingtalk fix: {b[:40]}")
        else:
            print(f"dingtalk miss: {a[:50]}")
    p.write_text(t, encoding="utf-8")

def patch_webhook_parser():
    p = ROOT / "webhook_parser.py"
    t = p.read_text(encoding="utf-8")
    reps = [
        ('EXIT_SOURCE_VPS_HARD_SL: "🛡️ VPS宽硬止损",',
         'EXIT_SOURCE_VPS_HARD_SL: "🛡️ TV硬止损",'),
        ('"""TV 紧止损 vs VPS 宽止损对比（实盘挂单价以 VPS 为准）"""',
         '"""TV 硬止损 vs 旧 VPS% 对照（实盘挂单价以 TV tv_sl 为准）"""'),
        (
            'f"VPS宽止损 `{vps_sl:.2f}` 距入场 {vps_dist:.2f}U(开仓×{pct}) · "',
            'f"旧VPS%对照 `{vps_sl:.2f}` 距入场 {vps_dist:.2f}U(开仓×{pct}) · 实盘挂TV "',
        ),
    ]
    for a, b in reps:
        if a in t:
            t = t.replace(a, b)
            print("webhook ok", b[:40])
        else:
            print("webhook miss", a[:50])
    p.write_text(t, encoding="utf-8")

if __name__ == "__main__":
    patch_supervisor()
    patch_dingtalk()
    patch_webhook_parser()

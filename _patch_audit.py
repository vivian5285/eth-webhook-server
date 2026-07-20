# -*- coding: utf-8 -*-
from pathlib import Path

ROOT = Path(__file__).resolve().parent
p = ROOT / "check_vps_logic.py"
text = p.read_text(encoding="utf-8")

start = text.index("def audit_module3_hard_sl(a: Audit):")
end = text.index("def audit_module4_radar(a: Audit):")

NEW = r'''def audit_module3_hard_sl(a: Audit):
    a.section("模块三 · TV 硬止损（实盘挂单）")
    from webhook_parser import VPS_HARD_SL_PCT, compute_vps_hard_sl

    # 旧 VPS% 表仍保留作 sizing/对照，但禁止作为实盘挂单价
    expected = {1: 0.0278, 2: 0.0389, 3: 0.0556, 4: 0.0833}
    for r, pct in expected.items():
        a.check(f"3.2 旧VPS%表 R{r}={pct*100:.2f}%(仅对照)", abs(VPS_HARD_SL_PCT[r] - pct) < 0.0001)

    eth_abs = {1: 50.0, 2: 70.0, 3: 100.0, 4: 150.0}
    for r, dist in eth_abs.items():
        sl = compute_vps_hard_sl("SHORT", 1800, regime=r)
        a.check(
            f"3.2b 旧对照 ETH@1800 R{r} ≈ +{dist:.0f}U",
            abs(sl - (1800 + dist)) < 0.2,
            f"sl={sl}",
        )

    xau_r3 = compute_vps_hard_sl("SHORT", 4003.94, regime=3)
    xau_r4 = compute_vps_hard_sl("SHORT", 4003.94, regime=4)
    a.check("3.2c 旧对照 XAU R3", abs(xau_r3 - 4226.56) < 0.05, f"sl={xau_r3}")
    a.check("3.2d 旧对照 XAU R4", abs(xau_r4 - 4337.47) < 0.05, f"sl={xau_r4}")
    a.check("3.2e ETH/XAU 共用同一 PCT 表", "ETH / XAU 同一套" in _read(os.path.join(ROOT, "webhook_parser.py")))

    sl_long = compute_vps_hard_sl("LONG", 1800, regime=3)
    a.check("3.3 旧对照做多 R3@1800", abs(sl_long - 1800 * (1 - 0.0556)) < 1, f"sl={sl_long}")

    sup = _read(os.path.join(ROOT, "position_supervisor_binance.py"))
    a.check(
        "3.7 实盘硬止损=TV tv_sl",
        "_tv_hard_sl_target" in sup
        and "禁止再用开仓价×档位%" in sup
        and "TV硬止损" in sup
        and "拒绝挂 TV 紧止损" not in sup,
    )
    a.check("3.5 STOP 挂单", "place_stop_market_order" in sup or "place_stop_limit" in sup)
    a.check(
        "3.8 硬止损 closePosition 不抢 TP 额度",
        "use_stop_limit=False" in sup and "不占 reduceOnly" in sup,
    )
    a.check(
        "3.9 全平归因 TV硬止损",
        "触碰硬止损平仓（TV硬止损）" in sup
        and "触碰硬止损平仓（VPS宽止损）" not in sup,
    )
    a.check(
        "3.10 开仓禁止 recover 核武连环撤",
        "开仓后防线对齐" in sup and "recover_mode=False" in sup,
    )
    a.check(
        "3.10b 防线 thrash 刹车",
        "NUCLEAR_REALIGN_MIN_INTERVAL_SEC" in sup
        and "_nuclear_backoff_remaining" in sup
        and "_defense_anomaly_is_severe" in sup
        and "idempotent_unified" in sup
        and "exclude_shield=False" in sup
        and "HARD_SL_SYNC_COOLDOWN_SEC" in sup,
    )
    a.check(
        "3.11 账本消毒对齐 TV",
        "_sanitize_vps_hard_sl_ledger" in sup
        and "_is_exchange_stop_acceptable_as_vps_floor" in sup
        and "不得用 VPS% 覆盖" in sup,
    )
    a.check(
        "3.12 重启禁止方向背离自动强平",
        "重启方向背离·保留持仓" in sup,
    )
    a.check(
        "3.13 雷达主判价触激活线",
        "_price_reached_radar_activation" in sup
        and "get_radar_activation_ratio" in _read(os.path.join(ROOT, "webhook_parser.py")),
    )
    a.check(
        "3.14 TV硬止损允许挂盘（废除紧价拒绝）",
        "_looks_like_tv_tight_stop" in sup
        and "恒返回 False" in sup
        and "拒绝挂 TV 紧止损" not in sup
        and "_is_valid_radar_sl" in sup,
    )
    a.check(
        "3.15 合并底线=TV硬止损",
        "仅挂 TV硬止损" in sup
        and "拒绝合并伪雷达/TV紧止损" not in sup,
    )
    a.check(
        "3.16 硬止损锁定 open_regime(雷达/TP用)",
        "_resolve_hard_sl_regime" in sup
        and "_lock_open_regime_from_sources" in sup
        and "_resolve_tv_open_regime_for_position" in sup
        and "以 TV 为准" in sup,
    )
    a.check(
        "3.17 开仓日志写 open_regime",
        '"open_regime": open_r' in sup or '"open_regime": open_r,' in sup
        or "open_regime\": open_r" in sup,
    )
    a.check(
        "3.18 重启先锁档再挂TV硬止损",
        "_lock_open_regime_from_sources" in sup
        and "重启强制TV硬止损" in sup
        and "重启强制VPS宽硬止损" not in sup,
    )
    a.check(
        "3.19 雷达激活线：ensure闸门",
        "_radar_placement_blocked" in sup
        and "POST_OPEN_RADAR_BLOCK_SEC" in sup
        and (
            "拒绝雷达挂单：未交棒/未达激活线" in sup
            or "_price_reached_radar_activation" in sup
        ),
    )
    a.check(
        "3.20 SHORT保本禁止抬过开仓价",
        "禁止抬到成本及以上" in sup
        and "_clamp_radar_sl_for_market" in sup,
    )
    a.check(
        "3.21 开仓日志按品种隔离",
        "_journal_path" in sup
        and "binance_{kind}_journal_" in sup
        and "_open_regime_sticky" in sup
        and "HARD_SL_SYNC_COOLDOWN_SEC" in sup,
    )
    a.check(
        "3.22 旧VPS%档位匹配已废弃",
        "_matches_any_vps_regime_stop" in sup
        and "旧 VPS% 档位匹配已废弃" in sup,
    )
    a.check(
        "3.23 重启锁按品种隔离",
        "recover_singleton_{self.symbol}" in sup
        or ".recover_singleton_" in sup
        and "_probe_position_for_recover" in sup
        and "AMBIGUOUS" in sup,
    )
    a.check(
        "3.24 hydrate 过滤 None 信源",
        "isinstance(s, dict)" in sup
        and "多品种启动恢复清单" in sup
        and "重启异常兜底" in sup,
    )
    a.check(
        "3.25 开仓裸仓闸 expected=0 不假齐",
        "开仓 TP123 补全失败" in sup
        and "开仓终检裸仓补挂" in sup
        and "不标 align_ok" in sup
        and "expected <= 0" in sup
        and "_ensure_tp123_prices_from_tv" in sup
        and "盘口无保护 STOP" in sup,
    )
    from webhook_parser import enrich_entry_tp_prices
    empty_tp = enrich_entry_tp_prices("LONG", 1800.0, 0, 1, {})
    a.check(
        "3.25b TV空ATR仍补全TP",
        float(empty_tp.get("tv_tp1") or 0) > 1800
        and float(empty_tp.get("tv_tp3") or 0) > float(empty_tp.get("tv_tp1") or 0),
        str(empty_tp),
    )
    a.check(
        "3.26 UPDATE_SL 必须同步盘口",
        "UPDATE_SL·按TV硬止损重挂" in sup
        and "UPDATE_SL 已按 TV 硬止损执行" in sup
        and "UPDATE_SL 已忽略盘口动作" not in sup,
    )
    a.check(
        "3.27 版本 TV硬止损-only",
        "v13.81.0-tv-hard-sl-only" in sup,
    )


'''

p.write_text(text[:start] + NEW + text[end:], encoding="utf-8")
print("check_vps_logic module3 patched")

# dingtalk asserts
text2 = p.read_text(encoding="utf-8")
old_dt = '''    a.check(
        "钉钉不宣称挂 TV硬止损",
        "send_alert(\\"🛡️ TV硬止损" not in dt
        and "TV硬止损 · UPDATE_SL" not in dt
        and "VPS宽硬止损" in dt,
    )
    a.check(
        "UPDATE_SL 仅记录参考",
        "永不挂 TV 紧止损" in dt or "未改盘口硬止损" in dt,
    )'''
new_dt = '''    a.check(
        "钉钉宣称挂 TV硬止损",
        "TV硬止损 · UPDATE_SL 已同步盘口" in dt
        and "VPS宽硬止损" not in dt,
    )
    a.check(
        "UPDATE_SL 同步盘口",
        "已按 TV tv_sl 改挂盘口硬止损" in dt
        and "永不挂 TV 紧止损" not in dt,
    )'''
if old_dt not in text2:
    # try without escape differences
    import re
    m = re.search(r'    a\.check\(\s*"钉钉不宣称挂 TV硬止损".*?a\.check\(\s*"UPDATE_SL 仅记录参考".*?\n    \)', text2, re.S)
    if m:
        text2 = text2[:m.start()] + new_dt + text2[m.end():]
        print("dingtalk asserts via regex")
    else:
        print("DINGTALK ASSERT MISS")
else:
    text2 = text2.replace(old_dt, new_dt)
    print("dingtalk asserts replaced")

old_rm = '''    if "exclusively 来自 TV `tv_sl`" in readme:
        a.check("README 硬止损描述", False, "仍写 tv_sl 为唯一来源，应改为 VPS 自主")
    else:
        a.check("README 硬止损描述", "VPS 自主" in readme or "开仓价百分比" in readme)'''
new_rm = '''    a.check(
        "README 硬止损描述",
        "TV 硬止损" in readme
        and "tv_sl" in readme
        and ("VPS 自主硬止损（开仓价" not in readme),
    )'''
if old_rm in text2:
    text2 = text2.replace(old_rm, new_rm)
    print("readme hard_sl assert ok")
else:
    print("README hard_sl assert miss")

text2 = text2.replace(
    '"v13.80.0-open-tv-defense-bind" in readme',
    '"v13.81.0-tv-hard-sl-only" in readme',
)
text2 = text2.replace(
    'a.check("README UPDATE_SL 仅参考", "仅更新" in readme and "tv_sl_ref" in readme)',
    'a.check("README UPDATE_SL 同步盘口", "UPDATE_SL" in readme and "按 TV" in readme)',
)
p.write_text(text2, encoding="utf-8")
print("check_vps_logic done")

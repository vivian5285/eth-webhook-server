# -*- coding: utf-8 -*-
"""One-shot: remove HARD_NOTIONAL_CAP checks/docs for v13.85."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --- check_vps_logic.py ---
p = ROOT / "check_vps_logic.py"
t = p.read_text(encoding="utf-8")
old = '''    # 硬上限绑定：极大 risk 时被 50000/price 卡住
    qty_cap, meta_cap = compute_tv_order_qty(
        1000, risk_pct=40.0, leverage=100, qty_ratio=1.0,
        price=2000, tv_sl=1990, regime=4,
    )
    a.check(
        "2.9 硬上限 50000/price",
        meta_cap.get("bind") == "hard_cap" and abs(qty_cap - 25.0) < 0.01,
        f"qty={qty_cap} bind={meta_cap.get('bind')}",
    )

    ok, cap_meta = check_total_notional_cap(1000, 6500, 6500, mult=13)
    a.check("双品种 13x 临界线", ok, f"total={cap_meta['total_notional']} cap={cap_meta['cap']}")
    ok2, _ = check_total_notional_cap(1000, 7000, 6500, mult=13)
    a.check("超标拒绝", not ok2)

    bc = _read(os.path.join(ROOT, "binance_client.py"))
    a.check("2.1 get_total_equity", "def get_total_equity" in bc)
    wp = _read(os.path.join(ROOT, "webhook_parser.py"))
    a.check("禁止旧保证金%表参与计算", "sizing_mode\": \"TV_RISK_FORMULA\"" in wp or "TV_RISK_FORMULA" in wp)
    a.check("旧 VPS_MARGIN 已清空", "VPS_MARGIN_PCT_BY_REGIME = {}" in wp)
'''
new = '''    # 无硬上限：极大 risk 不再被 50000/price 卡住，应绑理论或杠杆
    qty_big, meta_big = compute_tv_order_qty(
        1000, risk_pct=40.0, leverage=100, qty_ratio=1.0,
        price=2000, tv_sl=1990, regime=4,
    )
    a.check(
        "2.9 无硬上限·大风险走理论/杠杆",
        meta_big.get("bind") in ("theoretical", "leverage")
        and meta_big.get("bind") != "hard_cap"
        and float(meta_big.get("hard_cap_qty") or 0) == 0
        and qty_big > 25.0,
        f"qty={qty_big} bind={meta_big.get('bind')}",
    )
    a.check(
        "2.9b sizing_mode 无硬上限",
        "NO_HARD_CAP" in str(meta_big.get("sizing_mode") or ""),
    )

    ok, cap_meta = check_total_notional_cap(1000, 6500, 6500, mult=13)
    a.check("双品种 13x 临界线", ok, f"total={cap_meta['total_notional']} cap={cap_meta['cap']}")
    ok2, _ = check_total_notional_cap(1000, 7000, 6500, mult=13)
    a.check("超标拒绝", not ok2)

    bc = _read(os.path.join(ROOT, "binance_client.py"))
    a.check("2.1 get_total_equity", "def get_total_equity" in bc)
    wp = _read(os.path.join(ROOT, "webhook_parser.py"))
    a.check("禁止旧保证金%表参与计算", "TV_RISK_FORMULA" in wp)
    a.check("旧 VPS_MARGIN 已清空", "VPS_MARGIN_PCT_BY_REGIME = {}" in wp)
    a.check("HARD_NOTIONAL_CAP 恒0", "HARD_NOTIONAL_CAP = 0.0" in wp)
'''
if old not in t:
    raise SystemExit("check_vps_logic: old block not found")
t = t.replace(old, new)
t = t.replace("v13.84.0-tv-strategy-sync", "v13.85.0-no-hard-cap")
t = t.replace(
    'and "HARD_NOTIONAL_CAP" in _read(os.path.join(ROOT, "webhook_parser.py"))',
    'and "HARD_NOTIONAL_CAP = 0.0" in _read(os.path.join(ROOT, "webhook_parser.py"))',
)
p.write_text(t, encoding="utf-8")
print("check_vps_logic OK")

# --- README.md ---
r = ROOT / "README.md"
rt = r.read_text(encoding="utf-8")
rt = rt.replace("v13.84.0-tv-strategy-sync", "v13.85.0-no-hard-cap")
# careful: only replace remaining v13.84.0 that aren't already 13.85
rt = rt.replace("v13.84.0", "v13.85.0")
repls = [
    ("硬上限50000", "无单笔硬上限"),
    ("硬上限 50000", "无单笔硬上限"),
    ("min(理论,杠杆限,50000/价)×ratio", "min(理论,杠杆限)×ratio（无硬上限）"),
    ("min(理论, 杠杆限制, 硬上限) × qty_ratio", "min(理论, 杠杆限制) × qty_ratio（无硬上限）"),
    (
        "`min(风险金额/止损距离, 权益×leverage/价, 50000/价) × qty_ratio`",
        "`min(风险金额/止损距离, 权益×leverage/价) × qty_ratio`（无硬上限）",
    ),
    ("；硬上限 `HARD_NOTIONAL_CAP=50000`", "；已删除 `HARD_NOTIONAL_CAP`/`maxNotionalUSDT`"),
    (
        "TV 唯一公式开仓 sizing（risk_pct / |price-tv_sl| / leverage / 无单笔硬上限）",
        "TV 唯一公式开仓 sizing（risk_pct / |price-tv_sl| / leverage · **无硬上限**）",
    ),
    (
        "TV 唯一公式开仓 sizing（risk_pct / |price-tv_sl| / leverage / 硬上限50000）",
        "TV 唯一公式开仓 sizing（risk_pct / |price-tv_sl| / leverage · **无硬上限**）",
    ),
]
for a, b in repls:
    if a in rt:
        rt = rt.replace(a, b)
        print("readme:", a[:50])

marker = "#### v13.85.0 · `no-hard-cap`"
if marker not in rt:
    j = rt.find("#### v13.")
    if j < 0:
        j = rt.find("### 近期详细更新记录")
        j = rt.find("\n", j) + 1 if j >= 0 else -1
    if j >= 0:
        block = """#### v13.85.0 · `no-hard-cap`

**主题：删除 maxNotionalUSDT / HARD_NOTIONAL_CAP 单笔硬上限；仓位只受理论+杠杆约束**

- 唯一公式：`min(理论仓位, 杠杆限制) × qty_ratio`（无 50000U 硬上限）
- `HARD_NOTIONAL_CAP = 0.0`；禁止再参与 `min()`
- 保留双品种组合顶 13x（非单笔硬上限）
- 其余铁律不变：先平后开、TV硬止损、TP只挂一次、雷达候命、钉钉去重

"""
        rt = rt[:j] + block + rt[j:]
        print("readme changelog inserted")

# ensure version table has v13.85 row describing no hard cap
needle = "| **v13.85.0** |"
if needle in rt and "删除单笔硬上限" not in rt:
    # find first table row after needle and rewrite description if it's the iron-chain one from 84
    pass
if "| **v13.85.0** | **删除单笔硬上限" not in rt:
    # After bulk replace, 84 row became 85 with old 84 description — fix first occurrence
    import re
    rt2, n = re.subn(
        r"\| \*\*v13\.85\.0\*\* \| \*\*[^*]+\*\* \|",
        "| **v13.85.0** | **删除单笔硬上限 maxNotional/50000；公式=min(理论,杠杆)×ratio** |",
        rt,
        count=1,
    )
    if n:
        rt = rt2
        print("readme table row fixed", n)

r.write_text(rt, encoding="utf-8")
print("README OK")

# --- docs ---
for name in ("docs/VPS实盘检查清单.md",):
    dp = ROOT / name
    if not dp.exists():
        continue
    dt = dp.read_text(encoding="utf-8")
    dt = dt.replace("R1=85%…R4=70%", "R1=50%…R4=80%")
    dt = dt.replace(
        "最终量 = min(理论, 杠杆限制, 硬上限)×qty_ratio",
        "最终量 = min(理论, 杠杆限制)×qty_ratio（无硬上限）",
    )
    dt = dt.replace(
        "| 2.7 | 单笔硬上限 50000U / price | ✅ | `HARD_NOTIONAL_CAP` |",
        "| 2.7 | ~~单笔硬上限 50000U~~ **已删除** | ✅ | `HARD_NOTIONAL_CAP=0` |",
    )
    dt = dt.replace(
        "硬上限   = 50000 / price\n最终下单量 = min(理论, 杠杆限制, 硬上限) × qty_ratio",
        "最终下单量 = min(理论, 杠杆限制) × qty_ratio  # 无硬上限",
    )
    dt = dt.replace(
        "**禁止**旧「档位保证金% × 25x」路径",
        "**禁止**旧「档位保证金% × 25x」与「maxNotionalUSDT/50000硬上限」路径",
    )
    dp.write_text(dt, encoding="utf-8")
    print("docs OK")

print("done")

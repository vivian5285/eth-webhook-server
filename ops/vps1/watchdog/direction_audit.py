#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-30 新增：B/C/E组合引擎"开单方向一定对"的只读自动核查。

宝贝的问题："以后如何保证开单方向一定是对的？那就只有检查。"
每15分钟跑一次，逐账户、逐品种核对四件事，发现新问题才发钉钉：
  1. 净额一致：交易所真实净仓(方向+数量) == 引擎账本里全部虚拟子仓的代数和
     ——抓"开多没对冲完、剩了一截空"这类净额残留/方向反了的问题。
  2. 孤儿仓：交易所有仓但账本没有，或账本记着有仓但交易所已经是0。
  3. 止损方向：多单止损必须在现价下方、空单止损必须在现价上方，
     反了等于立刻触发。
  4. 擂台方向：每个子仓跟擂台同名策略在同品种的持仓方向比对，擂台持有
     相反方向 = 方向冲突(报警)；镜像闸门上线(09-30 12:00 bar)之后开的子仓
     擂台必须也有同向仓，否则报警。

只读：只调面板的 /api/all_positions(面板本身是只读子进程取数) +
读三个账户的state文件 + 读擂台只读接口，不下单、不改任何账户状态。
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, "/root/watchdog")
from dingtalk_notify import send_text  # noqa: E402

ACCOUNTS = {
    "B": "/home/binanceB/binance-engine/heikin_ashi_live_state.json",
    "C": "/home/binanceC/binance-engine/heikin_ashi_live_state.json",
    "E": "/home/binanceE/binance-engine/heikin_ashi_live_state.json",
}
ACCOUNT_LABEL = {"B": "B(妈妈)", "C": "C(宝贝)", "E": "E(MARIO)"}
DASHBOARD_URL = "http://127.0.0.1:8877/api/all_positions?force=1"
ARENA_URL = "http://187.53.133.188:8878/api/roster/compare/{strategy}/positions?status=open&limit=500"
ARENA_ALIAS = {"heikin_ashi_trend_ema7_25": "heikin_ashi_trend"}
MIRROR_START_BAR_MS = calendar.timegm((2026, 9, 30, 12, 0, 0)) * 1000
MIRROR_LAG_MS = 4 * 3600 * 1000
NET_REL_TOL = 0.05
SHADOW_URL = "http://187.53.133.188:8878/api/roster/shared-shadow"
LIVE_CODE_FILES = (
    "heikin_ashi_live.py", "asset_class_combo_strategy.py", "virtual_netting.py",
    "heikin_ashi_strategy.py", "binance_client.py", "strategy_engine/portfolio_guard.py",
    "strategy_engine/indicators.py",
)
# 影子文件名 -> 实盘源文件(影子钉的是"还原import后"的原始实盘哈希)
SHADOW_PIN_MAP = {
    "live_config.py": "asset_class_combo_strategy.py",
    "live_guard.py": "strategy_engine/portfolio_guard.py",
    "virtual_netting.py": "virtual_netting.py",
    "heikin_ashi_strategy.py": "heikin_ashi_strategy.py",
    "live_indicators.py": "strategy_engine/indicators.py",
}
STATE_FILE = "/root/watchdog/direction_audit_state.json"
REALERT_SEC = 6 * 3600


def _get(url: str, timeout: int = 30):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def _signed(side: str, qty: float) -> float:
    return abs(qty) if str(side).upper() == "LONG" else -abs(qty)


def _arena_index(strategies):
    index = {}
    for name in sorted(strategies):
        payload = _get(ARENA_URL.format(strategy=name), timeout=15)
        for row in payload.get("positions") or []:
            key = (name, str(row["symbol"]).upper())
            index.setdefault(key, []).append((str(row["side"]).upper(), int(row.get("entry_bar_time") or 0)))
    return index


def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def code_drift_checks(alerts: list, infos: list) -> None:
    """2026-09-30新增：(a) B/C/E三账户实盘代码必须逐字节一致；(b) 擂台
    /shadow/组合影子钉住的实盘配置哈希必须等于现行实盘文件——不一致说明
    实盘又调了配置，影子需要开新一期才继续有对照意义。"""
    engines = {a: os.path.dirname(p) for a, p in ACCOUNTS.items()}
    for rel in LIVE_CODE_FILES:
        hashes = {}
        for acct, root in engines.items():
            try:
                hashes[acct] = _sha256(os.path.join(root, rel))
            except OSError as e:
                hashes[acct] = f"unreadable:{e.__class__.__name__}"
        if len(set(hashes.values())) > 1:
            alerts.append((f"code_drift:{rel}:{'/'.join(sorted(set(v[:8] for v in hashes.values())))}",
                           f"B/C/E实盘代码不一致：{rel} " + " ".join(f"{a}={h[:8]}" for a, h in hashes.items())))
    try:
        pinned = (_get(SHADOW_URL, timeout=15).get("manifest") or {}).get("live_source_sha256") or {}
    except Exception as e:
        infos.append(f"组合影子接口读取失败，跳过配置对齐检查: {e}")
        return
    root = engines["B"]
    for shadow_name, live_rel in SHADOW_PIN_MAP.items():
        want = pinned.get(shadow_name)
        have = _sha256(os.path.join(root, live_rel))
        if want and want != have:
            alerts.append((f"shadow_stale:{shadow_name}:{have[:8]}",
                           f"组合影子(/shadow/)配置已落后实盘：{live_rel} 实盘={have[:8]} 影子钉住={want[:8]}，需开新一期"))


def audit():
    alerts, infos = [], []
    code_drift_checks(alerts, infos)
    positions = _get(DASHBOARD_URL, timeout=90)["accounts"]
    states = {}
    for acct, path in ACCOUNTS.items():
        with open(path, "r", encoding="utf-8") as fh:
            states[acct] = json.load(fh)
    strategies = {
        ARENA_ALIAS.get(n, n)
        for st in states.values() for rec in st.values()
        for n in (rec.get("sleeves") or {})
    }
    try:
        arena = _arena_index(strategies)
    except Exception as e:  # 擂台读不到只影响第4项，前三项照查
        arena = None
        alerts.append(("arena_unreachable", f"擂台持仓接口读取失败，第4项(擂台方向)本轮跳过: {e}"))

    for acct in ACCOUNTS:
        label = ACCOUNT_LABEL[acct]
        acct_pos = positions.get(acct) or {}
        if not acct_pos.get("ok"):
            alerts.append((f"{acct}:pos_unreadable", f"{label} 交易所持仓读取失败: {acct_pos.get('msg')}"))
            continue
        exch = {}
        for p in acct_pos.get("positions") or []:
            exch[p["symbol"]] = (_signed(p["side"], float(p["qty"])), float(p.get("mark_price") or 0))
        state = states[acct]
        for symbol in sorted(set(exch) | set(state)):
            rec = state.get(symbol) or {}
            sleeves = rec.get("sleeves") or {}
            live_amt, mark = exch.get(symbol, (0.0, 0.0))
            book = sum(_signed(s.get("side"), float(s.get("qty") or 0)) for s in sleeves.values())
            # 1+2 净额一致/孤儿仓
            if abs(live_amt) < 1e-12 and abs(book) < 1e-12:
                continue
            if abs(live_amt) < 1e-12 or abs(book) < 1e-12:
                alerts.append((f"{acct}:{symbol}:orphan",
                               f"{label} {symbol} 孤儿仓：交易所净仓={live_amt} 账本子仓合计={book}"))
            elif (live_amt > 0) != (book > 0):
                alerts.append((f"{acct}:{symbol}:side",
                               f"{label} {symbol} 方向反了：交易所净仓={live_amt} 账本子仓合计={book} 子仓={_fmt(sleeves)}"))
            elif abs(abs(live_amt) - abs(book)) > NET_REL_TOL * abs(live_amt):
                alerts.append((f"{acct}:{symbol}:net",
                               f"{label} {symbol} 净额残留：交易所净仓={live_amt} 账本子仓合计={book} 子仓={_fmt(sleeves)}"))
            if len({str(s.get('side')).upper() for s in sleeves.values()}) > 1:
                infos.append(f"{label} {symbol} 多空子仓并存(按设计净额)：{_fmt(sleeves)}")
            # 3 止损方向
            stop = float(rec.get("stop_loss") or 0)
            if stop and mark and abs(live_amt) > 0:
                if (live_amt > 0 and stop >= mark) or (live_amt < 0 and stop <= mark):
                    alerts.append((f"{acct}:{symbol}:stop",
                                   f"{label} {symbol} 止损在错误一侧：净仓={live_amt} 止损={stop} 现价={mark}"))
            # 4 擂台方向
            if arena is None:
                continue
            for name, s in sleeves.items():
                side = str(s.get("side")).upper()
                bar = int(s.get("entry_bar_time") or 0)
                rows = arena.get((ARENA_ALIAS.get(name, name), symbol), [])
                if any(r_side != side for r_side, _ in rows):
                    alerts.append((f"{acct}:{symbol}:{name}:arena_conflict",
                                   f"{label} {symbol} {name} 实盘{side}，擂台同策略持有{[r for r,_ in rows]} —— 方向冲突"))
                elif bar >= MIRROR_START_BAR_MS and not any(abs(t - bar) <= MIRROR_LAG_MS for _, t in rows):
                    alerts.append((f"{acct}:{symbol}:{name}:arena_missing",
                                   f"{label} {symbol} {name} {side} 是镜像闸门上线后开的，但擂台没有对应同向仓"))
                elif not rows:
                    infos.append(f"{label} {symbol} {name} {side} 闸门上线前的历史遗留子仓(擂台无对应仓)，按自身逻辑退出")
    return alerts, infos


def _fmt(sleeves):
    return [(n, s.get("side"), s.get("qty")) for n, s in sleeves.items()]


def main():
    alerts, infos = audit()
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[{ts}] direction_audit alerts={len(alerts)} infos={len(infos)}")
    for _, msg in alerts:
        print("  ALERT", msg)
    for msg in infos:
        print("  info ", msg)
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            sent = json.load(fh)
    except Exception:
        sent = {}
    now = time.time()
    fresh = [(k, m) for k, m in alerts if now - float(sent.get(k, 0)) > REALERT_SEC]
    if fresh and "--no-alert" not in sys.argv:
        text = "🧭 实盘开单方向核查发现问题\n" + "\n".join(f"• {m}" for _, m in fresh)
        if send_text(text):
            for k, _ in fresh:
                sent[k] = now
    sent = {k: v for k, v in sent.items() if now - float(v) < 3 * 86400}
    with open(STATE_FILE + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(sent, fh)
    os.replace(STATE_FILE + ".tmp", STATE_FILE)


if __name__ == "__main__":
    main()

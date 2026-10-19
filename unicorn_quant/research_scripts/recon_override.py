# -*- coding: utf-8 -*-
"""擂台 ↔ 回测 自动修复指令(2026-10-19 宝贝："不光提醒，要能自己修复")。

8890 的 engine/reconciler.py 每小时对账一次；某个(策略, 品种)擂台持仓和回测不一致、持续两次检查、
且判断是"安全可修"的类型时，写一条指令到 recon_override.json：
    {"策略名": {"BTCUSDT": {"want": "LONG"|"SHORT"|"FLAT", "stop": 89633.8, "reason": "...", "ts": 秒}}}
擂台策略在下一轮扫描时读到指令：
    want 和持仓同方向 → 继续拿(不让策略自己的逻辑平掉)
    want=FLAT 或反方向 → 平仓(反方向下一轮再按指令开)
    空仓且 want=LONG/SHORT → 按指令方向和止损价开仓(仓位照策略自己的算法)
指令超过 48 小时自动失效；对账一致后 reconciler 会删掉指令。只改纸面仓位，不改任何策略代码。
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

OVR_PATH = "/home/stratroster/unicorn_quant/optimal_data/recon_override.json"
_cache = {"mt": 0, "d": {}}


def get(strategy: str, symbol: str) -> Optional[dict]:
    try:
        mt = os.path.getmtime(OVR_PATH)
        if mt != _cache["mt"]:
            with open(OVR_PATH, "r", encoding="utf-8") as f:
                _cache.update(mt=mt, d=json.load(f) or {})
        o = (_cache["d"].get(strategy) or {}).get(symbol)
        if not o or time.time() - float(o.get("ts") or 0) > 48 * 3600:
            return None
        w = str(o.get("want") or "").upper()
        return {**o, "want": w} if w in ("LONG", "SHORT", "FLAT") else None
    except Exception:
        return None


def position_action(ov: Optional[dict], position: Optional[dict], price: float, bar_time: int) -> Optional[dict]:
    """持仓时按指令处理：返回 {"hold": True} / 平仓信号 / None(指令不涉及持仓，交给策略自己)"""
    if not ov or not position:
        return None
    side = str(position.get("side") or "").upper()
    if ov["want"] == side:
        return {"hold": True}
    return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time,
            "reason": f"自动对账修复：回测此时{'空仓' if ov['want'] == 'FLAT' else ('持多' if ov['want'] == 'LONG' else '持空')}，擂台跟随平仓；{ov.get('reason', '')}"}

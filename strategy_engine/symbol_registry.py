#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每个品种：用哪套策略 + 图表周期 + 策略参数 + 需要额外拉取的周期(mtf)。

━━━━━━━━━━━━━━━━ 2026-09-03 大重组：17 品种按宝贝的完整对照重新分组 ━━━━━━━━━━━
宝贝 2026-09-03 给了完整的「品种 → TV 真实 Pine 策略家族」对照，并逐字发来
5 份真实源码（存档在 strategy_engine/tv_pine_sources/，宝贝确认"这就是 TV
在用的策略"）。据此把注册表重建成 5 个策略家族：

  ① eth_pingkai_buhuchi        = 01版本"心跳版ETH·加仓最小改动"（宽TP，gate族）
                                  02版本"心跳版XAU加仓最小改动版"跟 01 参数一致，共用
     → ETHUSDT / XMRUSDT / BCHUSDT / XAUUSDT / XPDUSDT
  ② eth_pingkai_buhuchi_narrow = 03版本"心跳版ETH（平开不互斥版）"（窄TP，trend族）
     → METAUSDT / LITEUSDT / MUUSDT / GSUSDT / OPENAIUSDT / SKHYNIXUSDT / SNDKUSDT
  ③ eth_kdj_exempt_narrow      = 04版本"心跳版ETH（VPS适配·KDJ豁免温和版）"
                                  = 03版本 + 一个 KDJ 门槛豁免开关，其余逐字一致
     → TSLAUSDT / ANTHROPICUSDT / PAXGUSDT / ZECUSDT
  ④ bnb_heartbeat_real_reversal= BNB"心跳版本ETH（4H+日线·宽止盈等真反转版）"
                                  = 01版本简化版（去掉加仓/评分骤降/评分新鲜度），宽TP
     → BNBUSDT

这次重组相对旧版的变化：
  - SKHYNIXUSDT/PAXGUSDT/ZECUSDT：旧版挂在 zec_pingkai_buhuchi 下，现在
    SKHYNIX 归②、PAXG/ZEC 归③——不再是"ZEC版本"，zec_pingkai_buhuchi.py
    已无任何品种指向（保留文件作历史参考，见该文件 docstring）。
  - XMRUSDT/BCHUSDT/XPDUSDT：旧版是 _template 占位/根本没登记，现在归①。
  - METAUSDT/LITEUSDT/MUUSDT/GSUSDT/TSLAUSDT：旧版本文件里压根没登记
    （建表时还没上线），现在补上。
  - ASMLUSDT：宝贝说"已取消（胜率一直不高）"。保留条目但标记，不再建议
    擂台赛追踪（宝贝在 AskUserQuestion 里没最终拍板"删除 vs 暂停"，取
    可逆的一档：留着 + 注释，随时能改回或删掉）。

【2026-09-04追加】宝贝最终确认：ASMLUSDT + SKHYNIXUSDT 胜率都太低，"以后
不做他们了，删除"——四个真实账户(B/C/D/E)已核实空仓+零挂单，②家族从
7 个品种收窄到 6 个（META/LITE/MU/GS/OPENAI/SNDK）。跟实盘一致：
symbol_config.py::active_binance_symbols() 和各账户 .env 的
BINANCE_SYMBOLS 也同步去掉了这两个品种，往后 TV 就算误发这两个品种的信号
也会被 webhook 入口直接拒绝。

⚠️ timeframe 说明：已在表里的品种沿用原值；本次新登记的 5 个品种
（META/LITE/MU/GS/TSLA）的图表周期**没有拿到宝贝的控制面板截图**，按同
家族已知品种的常见周期先填占位（下面逐个标了 [占位]），宝贝核实后直接改
这一个字段即可，不用动任何逻辑。

⚠️ 评分形态待办（不在本次范围）：① 家族真实源码（01/02/BNB）用的是 gate
形态评分（见 tv_symbol_params.py），但 eth_pingkai_buhuchi.py 目前还是
2026-09-03 仓促按"只改宽 TP 系数"做的旧 trend 形态。BNB 模块这次按真实
gate 形态如实做了，eth_pingkai_buhuchi.py 的 gate 形态重建 = 单独一个待办。
引擎停用中，reference 参数只影响擂台赛回测精度，实盘雷达读 TV 真实 payload
TP，不受影响。

接入新品种/改参数的步骤（不变）：
  1. strategies/ 下新增模块，照抄真实策略逻辑
  2. strategies/__init__.py 的 STRATEGIES 里登记
  3. 这里把该品种的 strategy/timeframe/mtf/params 改成真实值
不需要改 live_runner.py / backtest_runner.py / shadow_log.py 任何一行。
"""
from __future__ import annotations

from typing import Dict

_MTF = ["4h", "1d"]  # 5 个家族统一：4H(裸K放量反转 + 大周期趋势打分) + 日线(大周期趋势打分)

# ── 家族① 心跳版ETH·加仓最小改动 / 心跳版XAU加仓最小改动版（宽TP）──────────
_ADD_ON = "eth_pingkai_buhuchi"
# ── 家族② 心跳版ETH（平开不互斥版）（窄TP）────────────────────────────────
_NARROW = "eth_pingkai_buhuchi_narrow"
# ── 家族③ 心跳版ETH（VPS适配·KDJ豁免温和版）（窄TP + KDJ豁免）─────────────
_KDJ_EXEMPT = "eth_kdj_exempt_narrow"
# ── 家族④ BNB 心跳版本ETH（4H+日线·宽止盈等真反转版）─────────────────────
_BNB = "bnb_heartbeat_real_reversal"

SYMBOLS: Dict[str, dict] = {
    # ── 家族① 加仓最小改动（宽TP）：ETH / XMR / BCH / XAU / XPD ─────────────
    "ETHUSDT":       {"strategy": _ADD_ON, "timeframe": "90m",  "mtf": _MTF, "params": {}},
    "XMRUSDT":       {"strategy": _ADD_ON, "timeframe": "6h",   "mtf": _MTF, "params": {}},
    "BCHUSDT":       {"strategy": _ADD_ON, "timeframe": "6h",   "mtf": _MTF, "params": {}},
    "XAUUSDT":       {"strategy": _ADD_ON, "timeframe": "50m",  "mtf": _MTF, "params": {}},
    "XPDUSDT":       {"strategy": _ADD_ON, "timeframe": "150m", "mtf": _MTF, "params": {}},

    # ── 家族② 平开不互斥版（窄TP）：META / LITE / MU / GS / OPENAI / SKHYNIX / SNDK
    "METAUSDT":      {"strategy": _NARROW, "timeframe": "150m", "mtf": _MTF, "params": {}},  # [占位] 待宝贝核实图表周期
    "LITEUSDT":      {"strategy": _NARROW, "timeframe": "150m", "mtf": _MTF, "params": {}},  # [占位] 待宝贝核实图表周期
    "MUUSDT":        {"strategy": _NARROW, "timeframe": "150m", "mtf": _MTF, "params": {}},  # [占位] 待宝贝核实图表周期
    "GSUSDT":        {"strategy": _NARROW, "timeframe": "150m", "mtf": _MTF, "params": {}},  # [占位] 待宝贝核实图表周期
    "OPENAIUSDT":    {"strategy": _NARROW, "timeframe": "150m", "mtf": _MTF, "params": {}},
    "SNDKUSDT":      {"strategy": _NARROW, "timeframe": "90m",  "mtf": _MTF, "params": {}},
    # SKHYNIXUSDT：2026-09-04 宝贝确认"胜率太低，以后不做他们了，删除"（跟
    # ASMLUSDT同一批决定）——四个账户(B/C/D/E)都已核实空仓+零挂单，注释掉
    # 而不物理删这一行，保留可逆性；如需恢复追踪，取消注释即可，原属家族②
    # （平开不互斥版）。
    # "SKHYNIXUSDT": {"strategy": _NARROW, "timeframe": "150m", "mtf": _MTF, "params": {}},

    # ── 家族③ KDJ豁免温和版（窄TP + KDJ豁免）：TSLA / ANTHROPIC / PAXG / ZEC ─
    "TSLAUSDT":      {"strategy": _KDJ_EXEMPT, "timeframe": "90m",  "mtf": _MTF, "params": {}},  # [占位] 待宝贝核实图表周期
    "ANTHROPICUSDT": {"strategy": _KDJ_EXEMPT, "timeframe": "90m",  "mtf": _MTF, "params": {}},
    "PAXGUSDT":      {"strategy": _KDJ_EXEMPT, "timeframe": "150m", "mtf": _MTF, "params": {}},
    "ZECUSDT":       {"strategy": _KDJ_EXEMPT, "timeframe": "150m", "mtf": _MTF, "params": {}},

    # ── 家族④ BNB 宽止盈等真反转版 ────────────────────────────────────────
    "BNBUSDT":       {"strategy": _BNB, "timeframe": "150m", "mtf": _MTF, "params": {}},

    # ── ASMLUSDT：2026-09-03 宝贝先说"已取消（胜率一直不高）"，2026-09-04
    #    确认最终决定"删除，以后不做他们了"（跟SKHYNIXUSDT同一批）。四个
    #    账户(B/C/D/E)已核实空仓+零挂单。仍然保留注释而不物理删这一行——
    #    其它文件无引用依赖，取消注释即可恢复追踪，原属家族②（平开不互斥
    #    版）。
    # "ASMLUSDT":    {"strategy": _NARROW, "timeframe": "90m", "mtf": _MTF, "params": {}},
}


def get_symbol_config(symbol: str) -> dict:
    cfg = SYMBOLS.get(str(symbol or "").upper())
    if not cfg:
        raise KeyError(f"未登记的品种: {symbol!r}")
    return cfg


def active_symbols():
    return list(SYMBOLS.keys())

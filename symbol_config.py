#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双品种（ETH / XAU）元数据与 TV ticker 归一化 — 币安 / 深币共用逻辑。"""
import os
import re

# 币安 USDT 永续
BINANCE_SYMBOL_META = {
    "ETHUSDT": {
        "symbol": "ETHUSDT",
        "unit": "ETH",
        "tag": "ETH",
        "qty_step": 0.001,
        "min_qty": 0.001,
        "dust_qty": 0.004,
        "price_precision": 2,
        "atr_fallback_symbol": "ETHUSDT",
        "breath": "ETH",
    },
    "XAUUSDT": {
        "symbol": "XAUUSDT",
        "unit": "XAU",
        "tag": "XAU",
        "qty_step": 0.001,
        "min_qty": 0.001,
        "dust_qty": 0.001,
        "price_precision": 2,
        "atr_fallback_symbol": "XAUUSDT",
        "breath": "XAU",
    },
    "BNBUSDT": {
        "symbol": "BNBUSDT",
        "unit": "BNB",
        "tag": "BNB",
        "qty_step": 0.01,
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,
        "atr_fallback_symbol": "BNBUSDT",
        "breath": "BNB",
    },
    "ZECUSDT": {
        "symbol": "ZECUSDT",
        "unit": "ZEC",
        "tag": "ZEC",
        "qty_step": 0.001,
        "min_qty": 0.001,
        "dust_qty": 0.005,
        "price_precision": 2,
        "atr_fallback_symbol": "ZECUSDT",
        "breath": "ZEC",
    },
    "BCHUSDT": {
        "symbol": "BCHUSDT",
        "unit": "BCH",
        "tag": "BCH",
        "qty_step": 0.01,
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,
        "atr_fallback_symbol": "BCHUSDT",
        "breath": "BCH",
    },
    "XMRUSDT": {
        "symbol": "XMRUSDT",
        "unit": "XMR",
        "tag": "XMR",
        "qty_step": 0.001,   # 2026-08-14：币安XMRUSDT.P LOT_SIZE stepSize实测值
        "min_qty": 0.001,
        "dust_qty": 0.005,
        "price_precision": 2,
        "atr_fallback_symbol": "XMRUSDT",
        "breath": "XMR",
    },
    "SNDKUSDT": {
        "symbol": "SNDKUSDT",
        "unit": "SNDK",
        "tag": "SNDK",
        "qty_step": 0.01,    # 2026-08-14：币安SNDKUSDT.P LOT_SIZE stepSize实测值
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "SNDKUSDT",
        "breath": "SNDK",
    },
    "PAXGUSDT": {
        "symbol": "PAXGUSDT",
        "unit": "PAXG",
        "tag": "PAXG",
        "qty_step": 0.001,   # 2026-08-14：币安PAXGUSDT.P LOT_SIZE stepSize实测值
        "min_qty": 0.001,
        "dust_qty": 0.001,   # 金价类高单价品种，参照XAU的dust惯例
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "PAXGUSDT",
        "breath": "PAXG",
    },
    "SKHYNIXUSDT": {
        "symbol": "SKHYNIXUSDT",
        "unit": "SKHYNIX",
        "tag": "SKHYNIX",
        # 2026-08-15：币安新品类TRADIFI_PERPETUAL(underlyingType=KR_EQUITY)，
        # 韩国SK海力士股票代币化永续。注意同名易混淆的SKHYUSDT(underlyingType
        # 只是通用EQUITY、baseAsset=SKHY非SKHYNIX、成交量约为一半)不是本品种，
        # 已用baseAsset/underlyingType/成交量三项核实排除。
        "qty_step": 0.01,    # 实测LOT_SIZE stepSize
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "SKHYNIXUSDT",
        "breath": "SKHYNIX",
    },
    "XPDUSDT": {
        "symbol": "XPDUSDT",
        "unit": "XPD",
        "tag": "XPD",
        # 2026-08-15：币安TRADIFI_PERPETUAL(underlyingType=COMMODITY)，钯金永续，
        # 跟XAU/PAXG同属贵金属类。
        "qty_step": 0.001,   # 实测LOT_SIZE stepSize
        "min_qty": 0.001,
        "dust_qty": 0.001,   # 贵金属类高单价品种，参照XAU/PAXG的dust惯例
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "XPDUSDT",
        "breath": "XPD",
    },
    "OPENAIUSDT": {
        "symbol": "OPENAIUSDT",
        "unit": "OPENAI",
        "tag": "OPENAI",
        # 2026-08-15：币安TRADIFI_PERPETUAL(underlyingType=PREMARKET)，OpenAI
        # 未上市股权盘前代币化永续。注意：这是"PREMARKET"品类，第一次遇到——
        # 24h成交量约449万U，比SKHYNIX(约18.8亿U)薄得多，流动性明显更差，
        # 实盘要留意滑点/挂单成交率。
        "qty_step": 0.01,    # 实测LOT_SIZE stepSize
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "OPENAIUSDT",
        "breath": "OPENAI",
    },
    "ANTHROPICUSDT": {
        "symbol": "ANTHROPICUSDT",
        "unit": "ANTHROPIC",
        "tag": "ANTHROPIC",
        # 2026-08-15：同OPENAI，币安TRADIFI_PERPETUAL(underlyingType=PREMARKET)，
        # Anthropic未上市股权盘前代币化永续。24h成交量约612万U，同样偏薄。
        "qty_step": 0.01,    # 实测LOT_SIZE stepSize
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "ANTHROPICUSDT",
        "breath": "ANTHROPIC",
    },
    "ASMLUSDT": {
        "symbol": "ASMLUSDT",
        "unit": "ASML",
        "tag": "ASML",
        # 2026-08-15：币安TRADIFI_PERPETUAL(underlyingType=EQUITY)，ASML(阿斯麦，
        # 光刻机设备)股票代币化永续，属于已上市正股类，跟SKHYNIX同类。
        "qty_step": 0.01,    # 实测LOT_SIZE stepSize
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "ASMLUSDT",
        "breath": "ASML",
    },
    "METAUSDT": {
        "symbol": "METAUSDT",
        "unit": "META",
        "tag": "META",
        # 2026-08-27：币安TRADIFI_PERPETUAL(underlyingType=EQUITY)，META
        # (脸书)股票代币化永续，跟GS/MU/LITE/TSLA同类。4小时周期。
        "qty_step": 0.01,    # 实测LOT_SIZE stepSize
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "METAUSDT",
        "breath": "META",
    },
    "TSLAUSDT": {
        "symbol": "TSLAUSDT",
        "unit": "TSLA",
        "tag": "TSLA",
        # 2026-08-27：币安TRADIFI_PERPETUAL(underlyingType=EQUITY)，TSLA
        # (特斯拉)股票代币化永续，跟GS/MU/LITE同类。6小时周期。
        "qty_step": 0.01,    # 实测LOT_SIZE stepSize
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "TSLAUSDT",
        "breath": "TSLA",
    },
    "GSUSDT": {
        "symbol": "GSUSDT",
        "unit": "GS",
        "tag": "GS",
        # 2026-08-25：币安TRADIFI_PERPETUAL(underlyingType=EQUITY)，GS(高盛)
        # 股票代币化永续，属于已上市正股类，跟ASML/SKHYNIX同类。90分钟周期。
        "qty_step": 0.01,    # 实测LOT_SIZE stepSize
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "GSUSDT",
        "breath": "GS",
    },
    "MUUSDT": {
        "symbol": "MUUSDT",
        "unit": "MU",
        "tag": "MU",
        # 2026-08-25：币安TRADIFI_PERPETUAL(underlyingType=EQUITY)，MU(美光科技)
        # 股票代币化永续，属于已上市正股类，跟ASML/GS同类。90分钟周期。
        "qty_step": 0.01,    # 实测LOT_SIZE stepSize
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "MUUSDT",
        "breath": "MU",
    },
    "LITEUSDT": {
        "symbol": "LITEUSDT",
        "unit": "LITE",
        "tag": "LITE",
        # 2026-08-25：币安TRADIFI_PERPETUAL(underlyingType=EQUITY)，LITE
        # 股票代币化永续，属于已上市正股类，跟ASML/GS/MU同类。90分钟周期。
        "qty_step": 0.01,    # 实测LOT_SIZE stepSize
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "LITEUSDT",
        "breath": "LITE",
    },
    "DELLUSDT": {
        "symbol": "DELLUSDT",
        "unit": "DELL",
        "tag": "DELL",
        # 2026-09-06：币安TRADIFI_PERPETUAL(underlyingType=EQUITY)，DELL
        # (戴尔科技)股票代币化永续，跟GS/MU/LITE/TSLA/META同类。3小时周期
        # (180分钟能被30整除，用30m合成，同BNB 150min手法)。
        "qty_step": 0.01,    # 实测LOT_SIZE stepSize
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "DELLUSDT",
        "breath": "DELL",
    },
    "GEVUSDT": {
        "symbol": "GEVUSDT",
        "unit": "GEV",
        "tag": "GEV",
        # 2026-09-06：币安TRADIFI_PERPETUAL(underlyingType=EQUITY)，GEV
        # (通用电气威能)股票代币化永续，跟GS/MU/LITE/TSLA/META同类。4小时
        # 周期，原生K线，同META周期一致。
        "qty_step": 0.01,    # 实测LOT_SIZE stepSize
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "GEVUSDT",
        "breath": "GEV",
    },
}

# 深币 SWAP
DEEPCOIN_SYMBOL_META = {
    "ETH-USDT-SWAP": {
        "symbol": "ETH-USDT-SWAP",
        "binance_mark": "ETHUSDT",
        "unit": "张",
        "tag": "ETH",
        "breath": "ETH",
        "face_value": 0.1,
        "qty_step": 1,
        "min_qty": 1,
        "dust_qty": 1,
        "price_precision": 2,
        "atr_fallback_symbol": "ETHUSDT",
    },
    "XAU-USDT-SWAP": {
        "symbol": "XAU-USDT-SWAP",
        "binance_mark": "XAUUSDT",
        "unit": "张",
        "tag": "XAU",
        "breath": "XAU",
        "face_value": 0.01,  # 启动后以 instruments 实盘覆盖
        "qty_step": 1,
        "min_qty": 1,
        "dust_qty": 1,
        "price_precision": 2,
        "atr_fallback_symbol": "XAUUSDT",
    },
}

_BINANCE_ALIASES = {
    "ETH": "ETHUSDT",
    "ETHUSDT": "ETHUSDT",
    "ETHUSD": "ETHUSDT",
    "ETHUSDT.P": "ETHUSDT",
    "BINANCE:ETHUSDT": "ETHUSDT",
    "BINANCE:ETHUSDT.P": "ETHUSDT",
    "XAU": "XAUUSDT",
    "XAUUSD": "XAUUSDT",
    "XAUUSDT": "XAUUSDT",
    "XAUUSDT.P": "XAUUSDT",
    "GOLD": "XAUUSDT",
    "BINANCE:XAUUSDT": "XAUUSDT",
    "BINANCE:XAUUSDT.P": "XAUUSDT",
    "BNB": "BNBUSDT",
    "BNBUSDT": "BNBUSDT",
    "BNBUSD": "BNBUSDT",
    "BNBUSDT.P": "BNBUSDT",
    "BINANCE:BNBUSDT": "BNBUSDT",
    "BINANCE:BNBUSDT.P": "BNBUSDT",
    "ZEC": "ZECUSDT",
    "ZECUSDT": "ZECUSDT",
    "ZECUSD": "ZECUSDT",
    "ZECUSDT.P": "ZECUSDT",
    "BINANCE:ZECUSDT": "ZECUSDT",
    "BINANCE:ZECUSDT.P": "ZECUSDT",
    "BCH": "BCHUSDT",
    "BCHUSDT": "BCHUSDT",
    "BCHUSD": "BCHUSDT",
    "BCHUSDT.P": "BCHUSDT",
    "BINANCE:BCHUSDT": "BCHUSDT",
    "BINANCE:BCHUSDT.P": "BCHUSDT",
    "XMR": "XMRUSDT",
    "XMRUSDT": "XMRUSDT",
    "XMRUSD": "XMRUSDT",
    "XMRUSDT.P": "XMRUSDT",
    "BINANCE:XMRUSDT": "XMRUSDT",
    "BINANCE:XMRUSDT.P": "XMRUSDT",
    "SNDK": "SNDKUSDT",
    "SNDKUSDT": "SNDKUSDT",
    "SNDKUSD": "SNDKUSDT",
    "SNDKUSDT.P": "SNDKUSDT",
    "BINANCE:SNDKUSDT": "SNDKUSDT",
    "BINANCE:SNDKUSDT.P": "SNDKUSDT",
    "PAXG": "PAXGUSDT",
    "PAXGUSDT": "PAXGUSDT",
    "PAXGUSD": "PAXGUSDT",
    "PAXGUSDT.P": "PAXGUSDT",
    "BINANCE:PAXGUSDT": "PAXGUSDT",
    "BINANCE:PAXGUSDT.P": "PAXGUSDT",
    "SKHYNIX": "SKHYNIXUSDT",
    "SKHYNIXUSDT": "SKHYNIXUSDT",
    "SKHYNIXUSD": "SKHYNIXUSDT",
    "SKHYNIXUSDT.P": "SKHYNIXUSDT",
    "BINANCE:SKHYNIXUSDT": "SKHYNIXUSDT",
    "BINANCE:SKHYNIXUSDT.P": "SKHYNIXUSDT",
    "XPD": "XPDUSDT",
    "XPDUSDT": "XPDUSDT",
    "XPDUSD": "XPDUSDT",
    "XPDUSDT.P": "XPDUSDT",
    "BINANCE:XPDUSDT": "XPDUSDT",
    "BINANCE:XPDUSDT.P": "XPDUSDT",
    "OPENAI": "OPENAIUSDT",
    "OPENAIUSDT": "OPENAIUSDT",
    "OPENAIUSD": "OPENAIUSDT",
    "OPENAIUSDT.P": "OPENAIUSDT",
    "BINANCE:OPENAIUSDT": "OPENAIUSDT",
    "BINANCE:OPENAIUSDT.P": "OPENAIUSDT",
    "ANTHROPIC": "ANTHROPICUSDT",
    "ANTHROPICUSDT": "ANTHROPICUSDT",
    "ANTHROPICUSD": "ANTHROPICUSDT",
    "ANTHROPICUSDT.P": "ANTHROPICUSDT",
    "BINANCE:ANTHROPICUSDT": "ANTHROPICUSDT",
    "BINANCE:ANTHROPICUSDT.P": "ANTHROPICUSDT",
    "ASML": "ASMLUSDT",
    "ASMLUSDT": "ASMLUSDT",
    "ASMLUSD": "ASMLUSDT",
    "ASMLUSDT.P": "ASMLUSDT",
    "BINANCE:ASMLUSDT": "ASMLUSDT",
    "BINANCE:ASMLUSDT.P": "ASMLUSDT",
    "GS": "GSUSDT",
    "META": "METAUSDT",
    "METAUSDT": "METAUSDT",
    "METAUSD": "METAUSDT",
    "METAUSDT.P": "METAUSDT",
    "BINANCE:METAUSDT": "METAUSDT",
    "BINANCE:METAUSDT.P": "METAUSDT",
    "TSLA": "TSLAUSDT",
    "TSLAUSDT": "TSLAUSDT",
    "TSLAUSD": "TSLAUSDT",
    "TSLAUSDT.P": "TSLAUSDT",
    "BINANCE:TSLAUSDT": "TSLAUSDT",
    "BINANCE:TSLAUSDT.P": "TSLAUSDT",
    "DELL": "DELLUSDT",
    "DELLUSDT": "DELLUSDT",
    "DELLUSD": "DELLUSDT",
    "DELLUSDT.P": "DELLUSDT",
    "BINANCE:DELLUSDT": "DELLUSDT",
    "BINANCE:DELLUSDT.P": "DELLUSDT",
    "GEV": "GEVUSDT",
    "GEVUSDT": "GEVUSDT",
    "GEVUSD": "GEVUSDT",
    "GEVUSDT.P": "GEVUSDT",
    "BINANCE:GEVUSDT": "GEVUSDT",
    "BINANCE:GEVUSDT.P": "GEVUSDT",
    "GSUSDT": "GSUSDT",
    "GSUSD": "GSUSDT",
    "GSUSDT.P": "GSUSDT",
    "BINANCE:GSUSDT": "GSUSDT",
    "BINANCE:GSUSDT.P": "GSUSDT",
    "MU": "MUUSDT",
    "MUUSDT": "MUUSDT",
    "MUUSD": "MUUSDT",
    "MUUSDT.P": "MUUSDT",
    "BINANCE:MUUSDT": "MUUSDT",
    "BINANCE:MUUSDT.P": "MUUSDT",
    "LITE": "LITEUSDT",
    "LITEUSDT": "LITEUSDT",
    "LITEUSD": "LITEUSDT",
    "LITEUSDT.P": "LITEUSDT",
    "BINANCE:LITEUSDT": "LITEUSDT",
    "BINANCE:LITEUSDT.P": "LITEUSDT",
}

_DEEPCOIN_ALIASES = {
    "ETH": "ETH-USDT-SWAP",
    "ETHUSDT": "ETH-USDT-SWAP",
    "ETHUSD": "ETH-USDT-SWAP",
    "ETH-USDT": "ETH-USDT-SWAP",
    "ETH-USDT-SWAP": "ETH-USDT-SWAP",
    "XAU": "XAU-USDT-SWAP",
    "XAUUSD": "XAU-USDT-SWAP",
    "XAUUSDT": "XAU-USDT-SWAP",
    "XAU-USDT": "XAU-USDT-SWAP",
    "XAU-USDT-SWAP": "XAU-USDT-SWAP",
    "GOLD": "XAU-USDT-SWAP",
}


def _clean_ticker(raw):
    s = str(raw or "").strip().upper()
    if not s:
        return ""
    s = s.replace(" ", "")
    # TradingView: BINANCE:ETHUSDT.P / EXCHANGE:SYMBOL
    if ":" in s:
        s = s.split(":")[-1]
    s = s.replace(".P", "")
    return s


def resolve_binance_symbol(raw, default="ETHUSDT"):
    """
    归一化 TV ticker → 币安合约。
    default=\"\" 时未识别返回 symbol=\"\"（禁止静默落到 ETH）。
    """
    key = _clean_ticker(raw)
    sym = _BINANCE_ALIASES.get(key) or _BINANCE_ALIASES.get(
        re.sub(r"[^A-Z0-9]", "", key), None
    )
    if not sym and key.endswith("USDT") and key in BINANCE_SYMBOL_META:
        sym = key
    if not sym:
        if default == "" or default is None:
            return {"symbol": "", "unit": "?", "qty_step": 0.001, "min_qty": 0.001}
        sym = default
    meta = dict(BINANCE_SYMBOL_META.get(sym, BINANCE_SYMBOL_META["ETHUSDT"]))
    try:
        from breath_profiles import get_breath_profile
        meta["breath_profile"] = get_breath_profile(meta.get("symbol") or sym, "binance")
    except Exception:
        meta["breath_profile"] = None
    return meta


def resolve_deepcoin_symbol(raw, default="ETH-USDT-SWAP"):
    key = _clean_ticker(raw)
    sym = _DEEPCOIN_ALIASES.get(key)
    if not sym and key.endswith("-USDT-SWAP") and key in DEEPCOIN_SYMBOL_META:
        sym = key
    if not sym:
        # map binance-style
        b = resolve_binance_symbol(key, default="")
        if b.get("symbol") == "ETHUSDT":
            sym = "ETH-USDT-SWAP"
        elif b.get("symbol") == "XAUUSDT":
            sym = "XAU-USDT-SWAP"
        else:
            sym = default
    meta = dict(DEEPCOIN_SYMBOL_META.get(sym, DEEPCOIN_SYMBOL_META["ETH-USDT-SWAP"]))
    try:
        from breath_profiles import get_breath_profile
        meta["breath_profile"] = get_breath_profile(meta.get("symbol") or sym, "deepcoin")
    except Exception:
        meta["breath_profile"] = None
    return meta


# 2026-09-04：宝贝确认ASMLUSDT/SKHYNIXUSDT胜率一直太低，"以后不做他们了，
# 删除"——两个账户确认过全部四个账户(B/C/D/E)都是空仓+零挂单，删除干净。
# 只从这里(活跃品种默认清单)和各账户.env的BINANCE_SYMBOLS里去掉，没有动
# BINANCE_SYMBOL_META/别名解析表——那些是静态参考数据，留着无害，以后万一
# 要恢复直接把这两个symbol重新加回下面两处清单即可，不用改别的代码。
# 2026-09-05：宝贝要求把SKHYNIXUSDT的TV重新接回实盘(ASMLUSDT不动，仍然
# 删除状态)——这里加回来，同步各账户.env的BINANCE_SYMBOLS。
# 2026-09-06：新增品种DELLUSDT(3小时周期，30m合成)、GEVUSDT(4小时周期，
# 原生K线)——币安TRADIFI_PERPETUAL(underlyingType=EQUITY)，跟GS/MU/LITE/
# TSLA/META同类，已核实stepSize/minQty/tickSize均为0.01，跟同族其它
# TradFi品种一致。
def active_binance_symbols():
    raw = os.getenv("BINANCE_SYMBOLS", "ETHUSDT,XAUUSDT,BNBUSDT,ZECUSDT,BCHUSDT,XMRUSDT,SNDKUSDT,PAXGUSDT,XPDUSDT,OPENAIUSDT,ANTHROPICUSDT,SKHYNIXUSDT,GSUSDT,MUUSDT,LITEUSDT,TSLAUSDT,METAUSDT,DELLUSDT,GEVUSDT")
    out = []
    for part in str(raw).split(","):
        meta = resolve_binance_symbol(part.strip(), default="")
        sym = meta.get("symbol")
        if sym and sym not in out and sym in BINANCE_SYMBOL_META:
            out.append(sym)
    return out or ["ETHUSDT", "XAUUSDT", "BNBUSDT", "ZECUSDT", "BCHUSDT", "XMRUSDT", "SNDKUSDT", "PAXGUSDT", "XPDUSDT", "OPENAIUSDT", "ANTHROPICUSDT", "SKHYNIXUSDT", "GSUSDT", "MUUSDT", "LITEUSDT", "TSLAUSDT", "METAUSDT", "DELLUSDT", "GEVUSDT"]


def active_deepcoin_symbols():
    raw = os.getenv("DEEPCOIN_SYMBOLS", "ETH-USDT-SWAP,XAU-USDT-SWAP")
    out = []
    for part in str(raw).split(","):
        meta = resolve_deepcoin_symbol(part.strip(), default="")
        sym = meta.get("symbol")
        if sym and sym not in out and sym in DEEPCOIN_SYMBOL_META:
            out.append(sym)
    return out or ["ETH-USDT-SWAP"]


def extract_symbol_from_payload(data):
    """从 TV / webhook 载荷提取 ticker（字段优先，全文扫描兜底）。"""
    if not isinstance(data, dict):
        return ""
    for key in (
        "symbol", "ticker", "Ticker", "sym", "pair", "market",
        "instrument", "instId", "inst_id",
    ):
        val = data.get(key)
        if val:
            return str(val).strip()
    # 兜底：扫描 JSON 文本中的已知合约（优先 XAU，避免误判 ETH）
    try:
        import json
        blob = json.dumps(data, ensure_ascii=False).upper()
    except Exception:
        blob = str(data).upper()
    for token in (
        "XAUUSDT.P", "BINANCE:XAUUSDT", "XAUUSDT", "XAU-USDT-SWAP", "XAUUSD",
        "ETHUSDT.P", "BINANCE:ETHUSDT", "ETHUSDT", "ETH-USDT-SWAP",
        "BNBUSDT.P", "BINANCE:BNBUSDT", "BNBUSDT",
        "ZECUSDT.P", "BINANCE:ZECUSDT", "ZECUSDT",
        "BCHUSDT.P", "BINANCE:BCHUSDT", "BCHUSDT",
        "XMRUSDT.P", "BINANCE:XMRUSDT", "XMRUSDT",
        "SNDKUSDT.P", "BINANCE:SNDKUSDT", "SNDKUSDT",
        "PAXGUSDT.P", "BINANCE:PAXGUSDT", "PAXGUSDT",
        "SKHYNIXUSDT.P", "BINANCE:SKHYNIXUSDT", "SKHYNIXUSDT",
        "XPDUSDT.P", "BINANCE:XPDUSDT", "XPDUSDT",
        "OPENAIUSDT.P", "BINANCE:OPENAIUSDT", "OPENAIUSDT",
        "ANTHROPICUSDT.P", "BINANCE:ANTHROPICUSDT", "ANTHROPICUSDT",
        "ASMLUSDT.P", "BINANCE:ASMLUSDT", "ASMLUSDT",
        "GSUSDT.P", "BINANCE:GSUSDT", "GSUSDT",
        "MUUSDT.P", "BINANCE:MUUSDT", "MUUSDT",
        "LITEUSDT.P", "BINANCE:LITEUSDT", "LITEUSDT",
        "TSLAUSDT.P", "BINANCE:TSLAUSDT", "TSLAUSDT",
        "METAUSDT.P", "BINANCE:METAUSDT", "METAUSDT",
        "DELLUSDT.P", "BINANCE:DELLUSDT", "DELLUSDT",
        "GEVUSDT.P", "BINANCE:GEVUSDT", "GEVUSDT",
    ):
        if token in blob:
            return token
    return ""

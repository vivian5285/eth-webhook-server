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
    "STXXUSDT": {
        "symbol": "STXXUSDT",
        "unit": "STXX",
        "tag": "STXX",
        # 2026-09-08：币安TRADIFI_PERPETUAL(underlyingType=EQUITY)，STXX
        # 股票代币化永续，跟GS/MU/LITE/TSLA/META/DELL/GEV同类。75分钟周期
        # (不是原生间隔，能被15整除，用15m合成，同ETH旧59min手法)。
        "qty_step": 0.01,    # 实测LOT_SIZE stepSize
        "min_qty": 0.01,
        "dust_qty": 0.05,
        "price_precision": 2,  # 实测PRICE_FILTER tickSize=0.01
        "atr_fallback_symbol": "STXXUSDT",
        "breath": "STXX",
    },
    "XPTUSDT": {
        "symbol": "XPTUSDT",
        "unit": "XPT",
        "tag": "XPT",
        # 2026-09-13：币安B系统新增品种，TRADIFI_PERPETUAL
        # (underlyingType=COMMODITY)，铂金永续，跟XAU/XPD同族贵金属。
        # 实测LOT_SIZE stepSize=0.001/minQty=0.001，PRICE_FILTER
        # tickSize=0.01，跟XAUUSDT filter形状完全一致。45分钟周期。
        "qty_step": 0.001,
        "min_qty": 0.001,
        "dust_qty": 0.001,   # 贵金属类高单价品种，参照XAU/XPD的dust惯例
        "price_precision": 2,
        "atr_fallback_symbol": "XPTUSDT",
        "breath": "XPT",
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
    "STXX": "STXXUSDT",
    "STXXUSDT": "STXXUSDT",
    "STXXUSD": "STXXUSDT",
    "STXXUSDT.P": "STXXUSDT",
    "BINANCE:STXXUSDT": "STXXUSDT",
    "BINANCE:STXXUSDT.P": "STXXUSDT",
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
    "XPT": "XPTUSDT",
    "XPTUSDT": "XPTUSDT",
    "XPTUSD": "XPTUSDT",
    "XPTUSDT.P": "XPTUSDT",
    "BINANCE:XPTUSDT": "XPTUSDT",
    "BINANCE:XPTUSDT.P": "XPTUSDT",
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
        # 2026-09-13：币安B系统("综合硬止损"体系)专属呼吸档——跟
        # position_supervisor_binance.py::_temp_hard_stop_from_tv同一个
        # 环境变量/同一套真假值解析，账户当前是A系统就完全不受影响
        # (system缺省"A"，行为跟改动前一致)。
        _smart_mode = str(os.getenv("SMART_HARD_STOP_ENABLED", "0")).strip().lower() in (
            "1", "true", "yes",
        )
        meta["breath_profile"] = get_breath_profile(
            meta.get("symbol") or sym, "binance", system=("B" if _smart_mode else "A"),
        )
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
# 2026-09-08：新增品种STXXUSDT(75分钟周期，15m合成)——同样是币安
# TRADIFI_PERPETUAL(underlyingType=EQUITY)，跟GS/MU/LITE/TSLA/META/DELL/
# GEV同类，已核实stepSize/minQty/tickSize均为0.01。
# 2026-09-12：宝贝拍板——实盘这么长时间下来，只留 OPENAIUSDT / XPDUSDT /
# SNDKUSDT 这三个品种继续吃 TV 信号，其余17个全部暂停（只停新开仓；已有
# 持仓沿用各自的永久硬止损/TP/雷达继续正常管理到平仓，不额外强平——本次
# 改动前核实过 B/C/D/E 四个账户，只有 B/E 两个账户的 BCHUSDT 还有仓，C/D
# 全空仓）。跟 2026-09-04 ASML/SKHYNIX 删除（commit e45383d）同一个机制：
# 只改这里(活跃品种默认清单)和各账户 .env 的 BINANCE_SYMBOLS，不动
# BINANCE_SYMBOL_META/别名解析表——那些是静态参考数据，留着无害，以后要
# 恢复直接把品种加回下面两处清单即可。跟 e45383d 一样的副作用：往后这17个
# 品种就算 TV 误发信号（包括 CLOSE）也会在 app.py/console_api.py 的
# webhook 入口被直接拒绝，已有仓位的平仓完全交给引擎自己的硬止损/雷达。
def active_binance_symbols():
    # 2026-09-12恢复BNBUSDT：17品种暂停(commit 0deff95)之后宝贝要求单独
    # 把BNB的TV网关接收+实盘开仓恢复回来，其余仍暂停(OPENAI/XPD/SNDK+BNB
    # 共4个)。跟ASML/SKHYNIX删除时同一套"注释不删除"的可逆写法。
    raw = os.getenv("BINANCE_SYMBOLS", "BNBUSDT,OPENAIUSDT,XPDUSDT,SNDKUSDT")
    out = []
    for part in str(raw).split(","):
        meta = resolve_binance_symbol(part.strip(), default="")
        sym = meta.get("symbol")
        if sym and sym not in out and sym in BINANCE_SYMBOL_META:
            out.append(sym)
    return out or ["BNBUSDT", "OPENAIUSDT", "XPDUSDT", "SNDKUSDT"]


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
        "STXXUSDT.P", "BINANCE:STXXUSDT", "STXXUSDT",
        "XPTUSDT.P", "BINANCE:XPTUSDT", "XPTUSDT",
    ):
        if token in blob:
            return token
    return ""

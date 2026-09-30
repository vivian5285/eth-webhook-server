#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代币动量门控（模型B）——2026-09-06新增。

对 token_feed 给出的每个趋势代币候选，套一组阈值 + 复用现有的
safety_filter（GoPlus 蜜罐/税/LP 检查），决定 BUY / WAIT / SKIP。
返回跟 signals/scorer.py 一样的 GateResult，让 main.py 后半段
(risk_gate -> buyer.buy -> 真相账本) 完全复用、一行不用改。

跟 signals/scorer.py（钱包跟单门控）的区别：那套的入场依据是"聪明钱
买了几次 + 熬过 N 个确认周期 + 有机增长"；这套没有钱包信号，纯看
代币本身的动量/热度/流动性/新鲜度 + 多源交叉验证 + 安全过滤。
"""
from __future__ import annotations

import logging

from models import GateResult
import signals.safety_filter as safety_filter

logger = logging.getLogger(__name__)


def _safety_summary(safety):
    if safety.passed:
        return f"ok(top10={safety.top10_holder_pct},tax={safety.buy_tax_pct}/{safety.sell_tax_pct},lp={safety.lp_locked})"
    return "fail:" + ",".join(safety.fail_reasons or [])


def _score(entry, n_sources):
    """0~1 综合分：多源命中 + 24h量 + 短周期动量还在向上 + 流动性。"""
    s = 0.0
    s += min(n_sources / 3.0, 1.0) * 0.30
    s += min(float(entry.get("vol_h24") or 0) / 2_000_000.0, 1.0) * 0.25
    s += (1.0 if float(entry.get("pc_h1") or 0) > 0 else 0.0) * 0.20
    s += min(float(entry.get("liq_usd") or 0) / 500_000.0, 1.0) * 0.15
    buys = int(entry.get("buys_m5") or 0)
    sells = int(entry.get("sells_m5") or 0)
    s += (1.0 if buys > sells else 0.0) * 0.10
    return round(s, 3)


def evaluate(entry, cfg):
    """entry: token_feed.get_trending() 的一个元素。cfg: config.Config。"""
    chain = str(cfg.token_mom_chains_list[0] if getattr(cfg, "token_mom_chains_list", None) else "bsc")
    token = entry["address"]
    ref_price = entry.get("price")
    n_src = len(entry.get("sources") or [])
    pc_h24 = float(entry.get("pc_h24") or 0.0)
    pc_h1 = float(entry.get("pc_h1") or 0.0)
    vol = float(entry.get("vol_h24") or 0.0)
    liq = float(entry.get("liq_usd") or 0.0)
    age_h = entry.get("pair_age_h")

    srcs = set(entry.get("sources") or [])

    # ---- 便宜检查（纯本地，先跑）----
    # ds_boost 是 DexScreener 的付费推广，谁都能花钱买——单靠它不算"趋势"，
    # 必须至少有一个 GeckoTerminal 的有机榜(trending / 成交额)命中，boost
    # 只当加分/佐证。
    if not (srcs & {"gt_trending", "gt_volume"}):
        return GateResult(action="WAIT", reason=f"no_organic_trending_source(only {sorted(srcs)})",
                          ref_price=ref_price)

    if n_src < int(cfg.token_mom_min_sources):
        return GateResult(action="WAIT", reason=f"sources={n_src}<min={cfg.token_mom_min_sources}",
                          ref_price=ref_price)

    if not (float(cfg.token_mom_min_gain_pct) <= pc_h24 <= float(cfg.token_mom_max_gain_pct)):
        return GateResult(action="SKIP",
                          reason=f"gain_h24={pc_h24:+.1f}%_out_of_[{cfg.token_mom_min_gain_pct},{cfg.token_mom_max_gain_pct}]",
                          ref_price=ref_price)

    if vol < float(cfg.token_mom_min_vol_usd):
        return GateResult(action="SKIP", reason=f"vol_h24=${vol:,.0f}<min=${cfg.token_mom_min_vol_usd:,.0f}",
                          ref_price=ref_price)

    if liq < float(cfg.token_mom_min_liq_usd):
        return GateResult(action="SKIP", reason=f"liq=${liq:,.0f}<min=${cfg.token_mom_min_liq_usd:,.0f}",
                          ref_price=ref_price)

    if age_h is not None and age_h > float(cfg.token_mom_max_pair_age_h):
        return GateResult(action="SKIP", reason=f"pair_age={age_h:.0f}h>max={cfg.token_mom_max_pair_age_h}h",
                          ref_price=ref_price)

    # 短周期动量：还在向上（1h 涨幅 > 阈值），不追已经在砸的
    if pc_h1 < float(cfg.token_mom_min_pc_h1):
        return GateResult(action="WAIT", reason=f"pc_h1={pc_h1:+.1f}%<min={cfg.token_mom_min_pc_h1}%_momentum_fading",
                          ref_price=ref_price)

    # ---- 贵检查（GoPlus 外部 API），只有过了上面所有门槛的才到这里 ----
    safety = safety_filter.check(token, cfg, chain)
    ss = _safety_summary(safety)
    if not safety.passed:
        return GateResult(action="SKIP", reason=f"safety_fail:{safety.fail_reasons}",
                          safety_summary=ss, ref_price=ref_price)

    score = _score(entry, n_src)
    logger.info("token-mom BUY gate passed token=%s(%s) chain=%s score=%s "
                "24h=%+.1f%% 1h=%+.1f%% vol=$%.0f liq=$%.0f src=%s",
                entry.get("symbol"), token, chain, score, pc_h24, pc_h1, vol, liq, entry.get("sources"))
    return GateResult(action="BUY", score=score, safety_summary=ss, ref_price=ref_price)

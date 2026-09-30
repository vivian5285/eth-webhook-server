#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复合门控。2026-09-04 重排：先跑**便宜**的检查（聪明钱买入次数=DB查询、
延迟确认=DB查询），过了再跑**贵**的外部 API 检查（GoPlus 安全、Helius 增长）。
原来 safety 在最前面，导致每个 event-window token 每 45s 都打一次 GoPlus，
免费层直接 429、全部 goplus_lookup_failed。现在只有"聪明钱买过 + 熬过 3 个
确认周期"的币才会触发 GoPlus/Helius 调用，量降一个数量级。

每次返回的 GateResult 都带"到目前为止的上下文快照"给真相账本 signal_events。
"""
import logging

import db
from models import GateResult

import signals.safety_filter as safety_filter
import signals.growth_filter as growth_filter
import signals.smart_money as smart_money

logger = logging.getLogger(__name__)


def _composite_score(safety, growth, sm_count):
    score = 0.0
    score += min(growth.unique_buyers / 20.0, 1.0) * 0.4
    score += min(sm_count / 3.0, 1.0) * 0.4
    score += (1.0 if safety.passed else 0.0) * 0.2
    return round(score, 3)


def _safety_summary(safety):
    if safety.passed:
        return f"ok(top10={safety.top10_holder_pct},tax={safety.buy_tax_pct}/{safety.sell_tax_pct},lp={safety.lp_locked})"
    return "fail:" + ",".join(safety.fail_reasons or [])


def evaluate(token_address, chain_adapter, cfg):
    chain = chain_adapter.chain_name

    # ---- 便宜检查（纯 DB）----
    sm_count = smart_money.count_recent_buys(token_address, chain, cfg.sm_window_sec)
    if sm_count < cfg.min_smart_wallet_buys:
        return GateResult(action="WAIT",
                          reason=f"smart_money_buys={sm_count}<min={cfg.min_smart_wallet_buys}",
                          sm_buys=sm_count)

    ref_price = chain_adapter.get_current_price(token_address)
    if ref_price is None:
        return GateResult(action="SKIP", reason="price_unavailable", sm_buys=sm_count)
    ref_price = float(ref_price)

    candidate = db.get_or_create_candidate(token_address, chain, ref_price)
    if candidate["confirmations"] < cfg.required_confirmations:
        db.bump_candidate_confirmation(token_address, chain)
        return GateResult(
            action="WAIT",
            reason=f"confirming {candidate['confirmations']+1}/{cfg.required_confirmations}",
            sm_buys=sm_count, ref_price=ref_price,
        )

    # ---- 贵检查（外部 API），只有熬过确认的币才到这里 ----
    safety = safety_filter.check(token_address, cfg, chain)
    ss = _safety_summary(safety)
    if not safety.passed:
        return GateResult(action="SKIP", reason=f"safety_fail:{safety.fail_reasons}",
                          sm_buys=sm_count, safety_summary=ss, ref_price=ref_price)

    growth = growth_filter.check(token_address, chain_adapter, cfg)
    if not growth.passed:
        return GateResult(action="SKIP", reason=f"insufficient_organic_growth:{growth.reason}",
                          sm_buys=sm_count, unique_buyers_5m=growth.unique_buyers,
                          safety_summary=ss, ref_price=ref_price)

    score = _composite_score(safety, growth, sm_count)
    logger.info("BUY gate passed token=%s chain=%s score=%s safety_ok growth=%s sm=%s",
                token_address, chain, score, growth.unique_buyers, sm_count)
    return GateResult(action="BUY", score=score, sm_buys=sm_count,
                      unique_buyers_5m=growth.unique_buyers, safety_summary=ss, ref_price=ref_price)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安全过滤——硬门槛。GoPlus Token Security API 封装。任何一项不达标直接拒绝。

两条链、两个端点、两套响应结构（GoPlus 对 Solana 和 EVM 的字段完全不同）：
  Solana: GET /api/v1/solana/token_security?contract_addresses=X
    - mintable.status=="1"    mint 权限还在
    - holders[].percent 前10  持仓集中度
    - transfer_fee.transfer_fee_rate  Token2022 转账税
    - non_transferable=="1"   最接近"蜜罐"
    - dex[].burn_percent      烧 LP 当锁定（Solana 没有独立锁仓合约概念）
  BSC (chain_id=56): GET /api/v1/token_security/56?contract_addresses=X（小写地址）
    - is_mintable=="1"        可增发
    - is_honeypot=="1"        蜜罐（GoPlus 真实模拟买卖判定）
    - buy_tax / sell_tax      买卖税（"0.1" = 10%）
    - holder_count, holders[].percent  持仓集中度（排除 LP/burn/交易所地址）
    - lp_holders[].is_locked / .percent  LP 锁定比例
    - cannot_sell_all / trading_cooldown / transfer_pausable / is_blacklisted
      / hidden_owner / can_take_back_ownership / selfdestruct  —— 各种后门
"""
import logging
import os
import time

import requests

from models import SafetyReport

logger = logging.getLogger(__name__)

_GOPLUS = {
    "solana": "https://api.gopluslabs.io/api/v1/solana/token_security",
    "bsc": "https://api.gopluslabs.io/api/v1/token_security/56",
}
_UA = {"User-Agent": "Mozilla/5.0 chain-sniper/1.0"}

# GoPlus 免费层 ~30 req/min；一个币的 mint 权限/LP 锁定/持仓集中度不会秒秒变，
# 加 TTL 缓存，每个币 10 分钟只真查一次；429/失败时短暂负缓存 60s 防雪崩。
_TTL = float(os.getenv("SAFETY_CACHE_TTL_SEC", "600"))
_NEG_TTL = float(os.getenv("SAFETY_NEG_CACHE_TTL_SEC", "60"))
_cache = {}  # (chain, token_lc) -> (ts, data_or_None)


def _fetch(token_address, chain):
    tok = token_address.lower() if chain == "bsc" else token_address
    key = (chain, tok)
    hit = _cache.get(key)
    if hit:
        ts, data = hit
        age = time.time() - ts
        if (data is not None and age < _TTL) or (data is None and age < _NEG_TTL):
            return data
    url = _GOPLUS.get(chain)
    if not url:
        return None
    try:
        r = requests.get(url, params={"contract_addresses": tok}, headers=_UA, timeout=10)
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 1:
            logger.warning("goplus non-ok [%s] %s: %s", chain, tok, body.get("message"))
            _cache[key] = (time.time(), None)
            return None
        result = body.get("result") or {}
        data = result.get(tok) or (next(iter(result.values())) if result else None)
        _cache[key] = (time.time(), data)
        return data
    except Exception as e:
        logger.warning("goplus request failed [%s] token=%s err=%s", chain, tok, e)
        _cache[key] = (time.time(), None)
        return None


def _check_solana(data, cfg):
    fail = []
    mint_active = (data.get("mintable", {}) or {}).get("status") == "1"
    if cfg.reject_if_mint_authority_active and mint_active:
        fail.append("mint_authority_active")

    holders = data.get("holders", []) or []
    top10 = sum(float(h.get("percent", 0) or 0) for h in holders[:10]) * 100
    if top10 > cfg.max_top10_holder_pct:
        fail.append(f"top10_holder_pct={top10:.1f}>max={cfg.max_top10_holder_pct}")

    tf = data.get("transfer_fee", {}) or {}
    tax = float(tf.get("transfer_fee_rate", 0) or 0) * 100 if tf else 0.0
    if tax > cfg.max_buy_tax_pct or tax > cfg.max_sell_tax_pct:
        fail.append(f"transfer_tax_pct={tax:.1f}")

    hp = data.get("non_transferable") == "1"
    if cfg.reject_if_honeypot and hp:
        fail.append("non_transferable(honeypot_proxy)")

    lp_locked = None
    if cfg.require_lp_locked:
        dex = data.get("dex", []) or []
        max_burn = max((float(d.get("burn_percent") or 0) for d in dex), default=0.0)
        lp_locked = max_burn >= 0.5
        if not lp_locked:
            fail.append(f"lp_not_locked(max_burn={max_burn:.2f})")

    return SafetyReport(passed=not fail, fail_reasons=fail, top10_holder_pct=top10,
                        buy_tax_pct=tax, sell_tax_pct=tax, lp_locked=lp_locked,
                        mint_authority_active=mint_active, is_honeypot=hp)


def _pct(v):
    try:
        return float(v) * 100.0
    except (TypeError, ValueError):
        return 0.0


def _check_bsc(data, cfg):
    fail = []
    mint_active = str(data.get("is_mintable")) == "1"
    if cfg.reject_if_mint_authority_active and mint_active:
        fail.append("is_mintable")

    hp = str(data.get("is_honeypot")) == "1" or str(data.get("cannot_sell_all")) == "1"
    if cfg.reject_if_honeypot and hp:
        fail.append("honeypot")

    buy_tax = _pct(data.get("buy_tax"))
    sell_tax = _pct(data.get("sell_tax"))
    if buy_tax > cfg.max_buy_tax_pct:
        fail.append(f"buy_tax={buy_tax:.1f}>max={cfg.max_buy_tax_pct}")
    if sell_tax > cfg.max_sell_tax_pct:
        fail.append(f"sell_tax={sell_tax:.1f}>max={cfg.max_sell_tax_pct}")

    holders = data.get("holders", []) or []
    top10 = sum(float(h.get("percent", 0) or 0) for h in holders[:10]) * 100
    if top10 > cfg.max_top10_holder_pct:
        fail.append(f"top10_holder_pct={top10:.1f}>max={cfg.max_top10_holder_pct}")

    lp_locked = None
    if cfg.require_lp_locked:
        lp_holders = data.get("lp_holders", []) or []
        locked_pct = sum(float(h.get("percent", 0) or 0)
                         for h in lp_holders if str(h.get("is_locked")) == "1") * 100
        # 烧毁地址(0x0/0xdead)也算锁定
        burned_pct = sum(float(h.get("percent", 0) or 0) for h in lp_holders
                         if (h.get("address") or "").lower() in (
                             "0x0000000000000000000000000000000000000000",
                             "0x000000000000000000000000000000000000dead")) * 100
        lp_locked = (locked_pct + burned_pct) >= 50.0
        if not lp_locked:
            fail.append(f"lp_not_locked(locked+burned={locked_pct+burned_pct:.1f}%)")

    # EVM 特有的后门开关
    for k, label in (("hidden_owner", "hidden_owner"),
                     ("can_take_back_ownership", "can_take_back_ownership"),
                     ("selfdestruct", "selfdestruct"),
                     ("transfer_pausable", "transfer_pausable"),
                     ("is_blacklisted", "is_blacklisted"),
                     ("trading_cooldown", "trading_cooldown")):
        if str(data.get(k)) == "1":
            fail.append(label)

    return SafetyReport(passed=not fail, fail_reasons=fail, top10_holder_pct=top10,
                        buy_tax_pct=buy_tax, sell_tax_pct=sell_tax, lp_locked=lp_locked,
                        mint_authority_active=mint_active, is_honeypot=hp)


def check(token_address, cfg, chain="solana"):
    data = _fetch(token_address, chain)
    if data is None:
        return SafetyReport(passed=False, fail_reasons=["goplus_lookup_failed"])
    if chain == "bsc":
        return _check_bsc(data, cfg)
    return _check_solana(data, cfg)


if __name__ == "__main__":
    import config
    logging.basicConfig(level=logging.INFO)
    cfg = config.load_config()
    print("SOL USDC:", check("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", cfg, "solana"))
    print("BSC CAKE:", check("0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82", cfg, "bsc"))

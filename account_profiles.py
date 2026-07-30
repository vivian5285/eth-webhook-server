#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
币安 API 档案 + 运行时仓位参数（Console 可热改）。

每套档案：名称、API Key/Secret、风险比例(risk_pct)、杠杆(leverage)。
切换生效档案会重绑 BinanceClient；改 risk/leverage 立即作用于下一笔开仓。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_PATH = os.path.join(BASE_DIR, "data", "account_profiles.json")
SYMBOL_SETTINGS_PATH = os.path.join(BASE_DIR, "data", "symbol_settings.json")
_LOCK = threading.RLock()

DEFAULT_RISK_PCT = 0.20
DEFAULT_LEVERAGE = 5.0


def _ensure_dir():
    os.makedirs(os.path.dirname(PROFILES_PATH), exist_ok=True)


def _mask_key(key: str) -> str:
    k = str(key or "")
    if len(k) <= 8:
        return "****" if k else ""
    return f"{k[:4]}…{k[-4:]}"


def _blank_store() -> Dict[str, Any]:
    return {
        "version": 1,
        "active_id": "",
        "webhook_secret": os.getenv("WEBHOOK_SECRET", "") or "",
        "profiles": [],
        "updated_at": 0.0,
    }


def _load_raw() -> Dict[str, Any]:
    _ensure_dir()
    if not os.path.isfile(PROFILES_PATH):
        store = _blank_store()
        # bootstrap from .env if present
        key = (os.getenv("BINANCE_API_KEY") or "").strip()
        secret = (os.getenv("BINANCE_API_SECRET") or "").strip()
        if key and secret:
            pid = uuid.uuid4().hex[:12]
            store["profiles"].append({
                "id": pid,
                "name": "默认账户(.env)",
                "api_key": key,
                "api_secret": secret,
                "risk_pct": DEFAULT_RISK_PCT,
                "leverage": DEFAULT_LEVERAGE,
                "created_at": time.time(),
                "updated_at": time.time(),
            })
            store["active_id"] = pid
        _save_raw(store)
        return store
    try:
        with open(PROFILES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _blank_store()
        data.setdefault("profiles", [])
        data.setdefault("active_id", "")
        data.setdefault("webhook_secret", os.getenv("WEBHOOK_SECRET", "") or "")
        return data
    except Exception as e:
        logger.error(f"[profiles] load failed: {e}")
        return _blank_store()


def _save_raw(store: Dict[str, Any]) -> None:
    _ensure_dir()
    store["updated_at"] = time.time()
    tmp = PROFILES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROFILES_PATH)


def list_profiles(include_secrets: bool = False) -> Dict[str, Any]:
    with _LOCK:
        store = _load_raw()
        out = []
        for p in store.get("profiles") or []:
            item = {
                "id": p.get("id"),
                "name": p.get("name") or "未命名",
                "api_key_masked": _mask_key(p.get("api_key") or ""),
                "has_secret": bool(str(p.get("api_secret") or "").strip()),
                "risk_pct": float(p.get("risk_pct") or DEFAULT_RISK_PCT),
                "leverage": float(p.get("leverage") or DEFAULT_LEVERAGE),
                "created_at": float(p.get("created_at") or 0),
                "updated_at": float(p.get("updated_at") or 0),
                "active": str(p.get("id")) == str(store.get("active_id") or ""),
            }
            if include_secrets:
                item["api_key"] = p.get("api_key") or ""
                item["api_secret"] = p.get("api_secret") or ""
            out.append(item)
        return {
            "active_id": store.get("active_id") or "",
            "webhook_secret_set": bool(str(store.get("webhook_secret") or "").strip()
                                       or os.getenv("WEBHOOK_SECRET")),
            "profiles": out,
            "updated_at": float(store.get("updated_at") or 0),
        }


def get_active_profile() -> Optional[Dict[str, Any]]:
    with _LOCK:
        store = _load_raw()
        aid = str(store.get("active_id") or "")
        for p in store.get("profiles") or []:
            if str(p.get("id")) == aid:
                return deepcopy(p)
        profiles = store.get("profiles") or []
        return deepcopy(profiles[0]) if profiles else None


def get_active_sizing(symbol: str = None) -> Tuple[float, float]:
    """
    返回 (risk_pct 0~1, leverage)。
    若 symbol 有独立设置则优先返回该币种的设置；
    否则返回当前生效档案的全局设置。
    """
    if symbol:
        per_sym = get_symbol_settings(symbol)
        if per_sym.get("enabled", False):
            return per_sym.get("risk_pct", DEFAULT_RISK_PCT), per_sym.get("leverage", DEFAULT_LEVERAGE)
    p = get_active_profile()
    if not p:
        return DEFAULT_RISK_PCT, DEFAULT_LEVERAGE
    try:
        risk = float(p.get("risk_pct") or DEFAULT_RISK_PCT)
    except (TypeError, ValueError):
        risk = DEFAULT_RISK_PCT
    try:
        lev = float(p.get("leverage") or DEFAULT_LEVERAGE)
    except (TypeError, ValueError):
        lev = DEFAULT_LEVERAGE
    risk = max(0.01, min(0.50, risk))
    lev = max(1.0, min(125.0, lev))
    return risk, lev


# ── per-symbol 独立仓位设置 ────────────────────────────────────────────────

def _load_symbol_settings_raw() -> Dict[str, Any]:
    _ensure_dir()
    if not os.path.isfile(SYMBOL_SETTINGS_PATH):
        return {}
    try:
        with open(SYMBOL_SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_symbol_settings_raw(data: Dict[str, Any]) -> None:
    _ensure_dir()
    tmp = SYMBOL_SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SYMBOL_SETTINGS_PATH)


def get_symbol_settings(symbol: str) -> Dict[str, Any]:
    """返回某币种的独立仓位设置。"""
    sym = str(symbol or "").upper()
    data = _load_symbol_settings_raw()
    entry = data.get(sym, {})
    return {
        "enabled": bool(entry.get("enabled", False)),
        "risk_pct": float(entry.get("risk_pct", DEFAULT_RISK_PCT)),
        "leverage": float(entry.get("leverage", DEFAULT_LEVERAGE)),
        "principal_override": float(entry.get("principal_override") or 0) or None,
        "mode": str(entry.get("mode", "risk")),  # "risk" | "fixed_amount"
        "fixed_amount": float(entry.get("fixed_amount", 0) or 0),
    }


def get_all_symbol_settings() -> Dict[str, Dict[str, Any]]:
    """返回所有已配置的币种设置。"""
    return dict(_load_symbol_settings_raw())


def set_symbol_settings(
    symbol: str,
    *,
    enabled: bool = None,
    risk_pct: float = None,
    leverage: float = None,
    principal_override: float = None,
    mode: str = None,
    fixed_amount: float = None,
) -> Dict[str, Any]:
    """更新某币种的独立仓位设置。"""
    sym = str(symbol or "").upper()
    if not sym:
        raise ValueError("symbol_required")
    with _LOCK:
        data = _load_symbol_settings_raw()
    entry = data.setdefault(sym, {
        "enabled": False,
        "risk_pct": DEFAULT_RISK_PCT,
        "leverage": DEFAULT_LEVERAGE,
        "mode": "risk",
    })
    if enabled is not None:
        entry["enabled"] = bool(enabled)
    if risk_pct is not None:
        entry["risk_pct"] = max(0.01, min(0.50, float(risk_pct)))
    if leverage is not None:
        entry["leverage"] = max(1.0, min(125.0, float(leverage)))
    if principal_override is not None:
        entry["principal_override"] = float(principal_override) if principal_override > 0 else 0
    if mode is not None:
        entry["mode"] = str(mode) if mode in ("risk", "fixed_amount") else "risk"
    if fixed_amount is not None:
        entry["fixed_amount"] = max(0.0, float(fixed_amount))
    with _LOCK:
        _save_symbol_settings_raw(data)
    return get_symbol_settings(sym)


def get_sizing_base_for_symbol(symbol: str) -> float:
    """
    返回某币种开仓用的本金基数。
    优先用 per-symbol principal_override，否则返回 None（走默认逻辑）。
    """
    if symbol:
        s = get_symbol_settings(symbol)
        po = s.get("principal_override")
        if po and po > 0:
            return po
    return 0.0


def get_webhook_secret() -> str:
    with _LOCK:
        store = _load_raw()
        s = str(store.get("webhook_secret") or "").strip()
        if s:
            return s
        return str(os.getenv("WEBHOOK_SECRET", "528586") or "").strip()


def set_webhook_secret(secret: str) -> None:
    with _LOCK:
        store = _load_raw()
        store["webhook_secret"] = str(secret or "").strip()
        _save_raw(store)


def upsert_profile(
    *,
    profile_id: str = "",
    name: str = "",
    api_key: str = "",
    api_secret: str = "",
    risk_pct: Optional[float] = None,
    leverage: Optional[float] = None,
) -> Dict[str, Any]:
    with _LOCK:
        store = _load_raw()
        profiles: List[Dict[str, Any]] = list(store.get("profiles") or [])
        now = time.time()
        if profile_id:
            target = None
            for p in profiles:
                if str(p.get("id")) == str(profile_id):
                    target = p
                    break
            if not target:
                raise ValueError("profile_not_found")
            if name:
                target["name"] = str(name).strip()[:64]
            if api_key:
                target["api_key"] = str(api_key).strip()
            if api_secret:
                target["api_secret"] = str(api_secret).strip()
            if risk_pct is not None:
                target["risk_pct"] = max(0.01, min(0.50, float(risk_pct)))
            if leverage is not None:
                target["leverage"] = max(1.0, min(125.0, float(leverage)))
            target["updated_at"] = now
            _save_raw(store)
            return deepcopy(target)

        if not name or not api_key or not api_secret:
            raise ValueError("name_api_key_secret_required")
        pid = uuid.uuid4().hex[:12]
        row = {
            "id": pid,
            "name": str(name).strip()[:64],
            "api_key": str(api_key).strip(),
            "api_secret": str(api_secret).strip(),
            "risk_pct": max(0.01, min(0.50, float(
                risk_pct if risk_pct is not None else DEFAULT_RISK_PCT
            ))),
            "leverage": max(1.0, min(125.0, float(
                leverage if leverage is not None else DEFAULT_LEVERAGE
            ))),
            "created_at": now,
            "updated_at": now,
        }
        profiles.append(row)
        store["profiles"] = profiles
        if not store.get("active_id"):
            store["active_id"] = pid
        _save_raw(store)
        return deepcopy(row)


def delete_profile(profile_id: str) -> None:
    with _LOCK:
        store = _load_raw()
        pid = str(profile_id or "")
        profiles = [p for p in (store.get("profiles") or []) if str(p.get("id")) != pid]
        if len(profiles) == len(store.get("profiles") or []):
            raise ValueError("profile_not_found")
        store["profiles"] = profiles
        if str(store.get("active_id")) == pid:
            store["active_id"] = str(profiles[0]["id"]) if profiles else ""
        _save_raw(store)


def apply_active_to_client(force: bool = False) -> Dict[str, Any]:
    """按当前生效档案重绑 Binance REST 客户端。"""
    from binance_client import binance_client

    p = get_active_profile()
    if not p:
        return {"ok": False, "reason": "no_profile"}
    key = str(p.get("api_key") or "").strip()
    secret = str(p.get("api_secret") or "").strip()
    if not key or not secret:
        return {"ok": False, "reason": "missing_credentials"}
    ok = binance_client.rebind_credentials(key, secret, force=force)
    risk, lev = get_active_sizing()
    return {
        "ok": bool(ok),
        "profile_id": p.get("id"),
        "name": p.get("name"),
        "risk_pct": risk,
        "leverage": lev,
        "api_key_masked": _mask_key(key),
    }


def activate_profile(profile_id: str, *, allow_with_position: bool = False) -> Dict[str, Any]:
    """切换生效档案。默认有仓时拒绝（防串账户）。"""
    from position_supervisor_binance import SUPERVISORS

    if not allow_with_position:
        for sym, sup in (SUPERVISORS or {}).items():
            qty = float(getattr(sup, "watched_qty", 0) or 0)
            if qty > 0 and bool(getattr(sup, "monitoring", False)):
                raise RuntimeError(
                    f"position_open:{sym}:{qty} — 平仓后再切换 API，或传 force=true"
                )

    with _LOCK:
        store = _load_raw()
        pid = str(profile_id or "")
        found = None
        for p in store.get("profiles") or []:
            if str(p.get("id")) == pid:
                found = p
                break
        if not found:
            raise ValueError("profile_not_found")
        store["active_id"] = pid
        _save_raw(store)

    applied = apply_active_to_client(force=True)
    logger.info(
        f"[profiles] 已切换生效档案 id={pid} name={found.get('name')} "
        f"risk={applied.get('risk_pct')} lev={applied.get('leverage')}"
    )
    return applied


def bootstrap_from_env() -> None:
    """启动时确保有档案并应用到 client。"""
    with _LOCK:
        _load_raw()
    try:
        apply_active_to_client(force=True)
    except Exception as e:
        logger.warning(f"[profiles] bootstrap apply skipped: {e}")

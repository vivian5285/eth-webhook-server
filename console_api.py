#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""币安单系统 Console API（口令会话 + 档案/日志/盈亏）。"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint,
    jsonify,
    redirect,
    request,
    send_from_directory,
    session,
)

logger = logging.getLogger(__name__)

console_bp = Blueprint("console", __name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static" / "console"
LOG_PATH = BASE_DIR / "logs" / "binance_brain.log"

CONSOLE_PASSWORD = (
    os.getenv("CONSOLE_PASSWORD")
    or os.getenv("ADMIN_PASSWORD")
    or "binance-console"
).strip()


def _password_ok(pw: str) -> bool:
    a = hashlib.sha256(str(pw or "").encode("utf-8")).digest()
    b = hashlib.sha256(CONSOLE_PASSWORD.encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("console_auth"):
            return jsonify({"status": "error", "message": "login_required"}), 401
        return fn(*args, **kwargs)
    return wrapper


@console_bp.route("/console")
@console_bp.route("/console/")
def console_index():
    return send_from_directory(STATIC_DIR, "index.html")


@console_bp.route("/console/assets/<path:name>")
def console_assets(name):
    return send_from_directory(STATIC_DIR, name)


@console_bp.route("/api/console/login", methods=["POST"])
def api_login():
    body = request.get_json(silent=True) or {}
    pw = str(body.get("password") or "")
    if not _password_ok(pw):
        time.sleep(0.4)
        return jsonify({"status": "error", "message": "bad_password"}), 403
    session["console_auth"] = True
    session["console_ts"] = time.time()
    session.permanent = True
    return jsonify({"status": "ok"})


@console_bp.route("/api/console/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"status": "ok"})


@console_bp.route("/api/console/me", methods=["GET"])
def api_me():
    return jsonify({
        "status": "ok",
        "authenticated": bool(session.get("console_auth")),
    })


@console_bp.route("/api/console/overview", methods=["GET"])
@require_login
def api_overview():
    from account_profiles import get_active_sizing, list_profiles
    from position_supervisor_binance import (
        SUPERVISORS,
        BINANCE_VPS_VERSION,
    )
    from binance_client import binance_client
    from webhook_parser import TV_STRATEGY_VERSION
    import risk_manager

    risk, lev = get_active_sizing()
    positions = []
    for sym, sup in (SUPERVISORS or {}).items():
        positions.append({
            "symbol": sym,
            "side": getattr(sup, "current_side", None),
            "qty": float(getattr(sup, "watched_qty", 0) or 0),
            "entry": float(getattr(sup, "watched_entry", 0) or 0),
            "monitoring": bool(getattr(sup, "monitoring", False)),
            "paused": bool(getattr(sup, "trading_paused", False)),
            "pause_reason": str(getattr(sup, "trading_pause_reason", "") or ""),
            "radar_activated": bool(getattr(sup, "radar_activated", False)),
            "tp_consumed": list(getattr(sup, "tp_levels_consumed", []) or []),
            "tps": list(getattr(sup, "tv_tps", []) or []),
            "hard_sl": float(getattr(sup, "frozen_hard_sl_px", 0) or 0),
            "current_sl": float(getattr(sup, "current_sl", 0) or 0),
        })
    equity = None
    try:
        equity = float(binance_client.get_total_equity() or 0)
    except Exception as e:
        logger.debug(f"equity: {e}")
    rm = risk_manager.risk_manager
    try:
        rm_status = rm.get_status() if hasattr(rm, "get_status") else {
            "daily_pnl": float(getattr(rm, "daily_pnl", 0) or 0),
            "today_trade_count": int(getattr(rm, "today_trade_count", 0) or 0),
            "consecutive_losses": int(getattr(rm, "consecutive_losses", 0) or 0),
        }
    except Exception:
        rm_status = {}
    host = request.host_url.rstrip("/")
    return jsonify({
        "status": "ok",
        "version": BINANCE_VPS_VERSION,
        "tv_strategy": TV_STRATEGY_VERSION,
        "risk_pct": risk,
        "leverage": lev,
        "equity": equity,
        "webhook_url": f"{host}/webhook",
        "profiles": list_profiles(),
        "positions": positions,
        "risk": rm_status,
        "stats": _pnl_stats(),
    })


def _pnl_stats():
    """从交易所 REALIZED_PNL 收入拉取近 30 天统计。"""
    from binance_client import binance_client
    wins = 0
    losses = 0
    total = 0.0
    rows = []
    try:
        end = int(time.time() * 1000)
        start = end - 30 * 24 * 3600 * 1000
        hist = binance_client.client.futures_income_history(
            incomeType="REALIZED_PNL",
            startTime=start,
            endTime=end,
            limit=1000,
        ) or []
        for h in hist:
            try:
                pnl = float(h.get("income") or 0)
            except (TypeError, ValueError):
                continue
            total += pnl
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
            rows.append({
                "time": int(h.get("time") or 0),
                "symbol": h.get("symbol") or "",
                "pnl": pnl,
                "tran_id": str(h.get("tranId") or ""),
            })
        rows.sort(key=lambda x: x["time"], reverse=True)
    except Exception as e:
        logger.warning(f"[console] pnl stats: {e}")
        return {
            "ok": False,
            "error": str(e),
            "total_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "trades": 0,
            "recent": [],
        }
    n = wins + losses
    return {
        "ok": True,
        "total_pnl": round(total, 4),
        "wins": wins,
        "losses": losses,
        "trades": n,
        "win_rate": round((wins / n * 100.0), 2) if n else 0.0,
        "recent": rows[:40],
    }


@console_bp.route("/api/console/logs", methods=["GET"])
@require_login
def api_logs():
    n = int(request.args.get("n") or 120)
    n = max(20, min(500, n))
    q = str(request.args.get("q") or "").strip().lower()
    lines = []
    try:
        if LOG_PATH.is_file():
            text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
            raw = text.splitlines()
            if q:
                raw = [ln for ln in raw if q in ln.lower()]
            lines = raw[-n:]
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "ok", "lines": lines, "path": str(LOG_PATH)})


@console_bp.route("/api/console/profiles", methods=["GET"])
@require_login
def api_profiles_list():
    from account_profiles import list_profiles
    return jsonify({"status": "ok", **list_profiles()})


@console_bp.route("/api/console/profiles", methods=["POST"])
@require_login
def api_profiles_create():
    from account_profiles import upsert_profile, list_profiles
    body = request.get_json(silent=True) or {}
    try:
        row = upsert_profile(
            name=str(body.get("name") or ""),
            api_key=str(body.get("api_key") or ""),
            api_secret=str(body.get("api_secret") or ""),
            risk_pct=body.get("risk_pct"),
            leverage=body.get("leverage"),
        )
        return jsonify({"status": "ok", "profile": {
            "id": row["id"], "name": row["name"],
            "risk_pct": row["risk_pct"], "leverage": row["leverage"],
        }, **list_profiles()})
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@console_bp.route("/api/console/profiles/<pid>", methods=["PATCH"])
@require_login
def api_profiles_patch(pid):
    from account_profiles import upsert_profile, list_profiles, get_active_profile, apply_active_to_client
    body = request.get_json(silent=True) or {}
    try:
        kw = {"profile_id": pid}
        if "name" in body and str(body.get("name") or "").strip():
            kw["name"] = str(body.get("name")).strip()
        if str(body.get("api_key") or "").strip():
            kw["api_key"] = str(body.get("api_key")).strip()
        if str(body.get("api_secret") or "").strip():
            kw["api_secret"] = str(body.get("api_secret")).strip()
        if body.get("risk_pct") is not None:
            kw["risk_pct"] = body.get("risk_pct")
        if body.get("leverage") is not None:
            kw["leverage"] = body.get("leverage")
        row = upsert_profile(**kw)
        # 若改的是当前生效档案，立即重绑（密钥变时）并生效 sizing
        active = get_active_profile() or {}
        if str(active.get("id")) == str(pid):
            if body.get("api_key") or body.get("api_secret"):
                apply_active_to_client(force=True)
        return jsonify({"status": "ok", "profile_id": row.get("id"), **list_profiles()})
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@console_bp.route("/api/console/profiles/<pid>/activate", methods=["POST"])
@require_login
def api_profiles_activate(pid):
    from account_profiles import activate_profile, list_profiles
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force"))
    try:
        applied = activate_profile(pid, allow_with_position=force)
        return jsonify({"status": "ok", "applied": applied, **list_profiles()})
    except RuntimeError as e:
        return jsonify({"status": "error", "message": str(e)}), 409
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@console_bp.route("/api/console/profiles/<pid>", methods=["DELETE"])
@require_login
def api_profiles_delete(pid):
    from account_profiles import delete_profile, list_profiles, apply_active_to_client
    try:
        delete_profile(pid)
        try:
            apply_active_to_client(force=True)
        except Exception:
            pass
        return jsonify({"status": "ok", **list_profiles()})
    except ValueError as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@console_bp.route("/api/console/webhook_secret", methods=["POST"])
@require_login
def api_webhook_secret():
    from account_profiles import set_webhook_secret, get_webhook_secret
    body = request.get_json(silent=True) or {}
    secret = str(body.get("secret") or "").strip()
    if len(secret) < 4:
        return jsonify({"status": "error", "message": "secret_too_short"}), 400
    set_webhook_secret(secret)
    return jsonify({
        "status": "ok",
        "webhook_secret_set": bool(get_webhook_secret()),
        "hint": "TradingView 警报里的 secret 字段请同步为新值",
    })


@console_bp.route("/api/console/resume/<path:symbol>", methods=["POST"])
@require_login
def api_resume(symbol):
    from position_supervisor_binance import get_supervisor
    from symbol_config import resolve_binance_symbol, active_binance_symbols
    meta = resolve_binance_symbol(symbol, default="")
    sym = meta.get("symbol") or ""
    if not sym or sym not in set(active_binance_symbols()):
        return jsonify({"status": "error", "message": "bad_symbol"}), 400
    sup = get_supervisor(sym)
    prev = str(getattr(sup, "trading_pause_reason", "") or "")
    sup.trading_paused = False
    sup.trading_pause_reason = ""
    try:
        sup._save_state()
    except Exception:
        pass
    return jsonify({"status": "ok", "symbol": sym, "previous_reason": prev})


def init_console(app):
    """注册蓝图与会话密钥。"""
    secret = (
        os.getenv("CONSOLE_SESSION_SECRET")
        or os.getenv("FLASK_SECRET_KEY")
        or hashlib.sha256(
            (CONSOLE_PASSWORD + "|binance-console").encode("utf-8")
        ).hexdigest()
    )
    app.secret_key = secret
    app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 12
    app.register_blueprint(console_bp)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(
        f"🖥️ Console 已挂载 /console  (默认口令环境变量 CONSOLE_PASSWORD)"
    )

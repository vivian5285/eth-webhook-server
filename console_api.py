#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""币安单系统 Console API（口令会话 + 档案/日志/盈亏）。"""
from __future__ import annotations

import hashlib
import hmac
import json
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
    from account_profiles import get_active_sizing, list_profiles, get_all_symbol_settings
    from position_supervisor_binance import (
        SUPERVISORS,
        BINANCE_VPS_VERSION,
    )
    from binance_client import binance_client
    from webhook_parser import TV_STRATEGY_VERSION
    from symbol_config import active_binance_symbols, BINANCE_SYMBOL_META
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
            "position_source": str(getattr(sup, "position_source", "TV") or "TV"),
            "grid_pending": bool(getattr(sup, "_grid_pending_order_id", None)),
            "grid_pending_side": getattr(sup, "_grid_pending_side", None),
            "grid_pending_deadline_ts": float(getattr(sup, "_grid_pending_deadline_ts", 0) or 0),
        })

    # per-symbol 仓位设置（随 overview 一起下发，前端直接用）
    all_sym_settings = get_all_symbol_settings()
    sym_settings = {}
    for sym in active_binance_symbols():
        meta = BINANCE_SYMBOL_META.get(sym, {})
        entry = all_sym_settings.get(sym, {})
        sym_settings[sym] = {
            "symbol": sym,
            "unit": meta.get("unit", sym),
            "tag": meta.get("tag", sym),
            "enabled": bool(entry.get("enabled", False)),
            "mode": str(entry.get("mode", "risk")),
            "risk_pct": float(entry.get("risk_pct", 0.20)),
            "leverage": float(entry.get("leverage", 5)),
            "principal_override": float(entry.get("principal_override") or 0) or None,
            "fixed_amount": float(entry.get("fixed_amount") or 0) or None,
            "trading_enabled": bool(entry.get("trading_enabled", True)),
        }

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
        "symbol_settings": sym_settings,
    })


def _pnl_stats():
    """从交易所 REALIZED_PNL 收入拉取近 30 天统计。
    修复（v16.9.2）：走 binance_client.fetch_income_history（含节流阀/冷却门禁），
    根治直接调 client.futures_income_history 绕过节流阀导致 -1003 的问题。
    """
    from binance_client import binance_client
    wins = 0
    losses = 0
    total = 0.0
    rows = []
    try:
        end = int(time.time() * 1000)
        start = end - 30 * 24 * 3600 * 1000
        hist = binance_client.fetch_income_history(
            start_time_ms=start,
            end_time_ms=end,
            limit=1000,
        )
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


# ── per-symbol 仓位设置 API ─────────────────────────────────────────────────

@console_bp.route("/api/console/symbol_settings", methods=["GET"])
@require_login
def api_symbol_settings_list():
    from account_profiles import get_all_symbol_settings
    from symbol_config import active_binance_symbols, BINANCE_SYMBOL_META
    all_settings = get_all_symbol_settings()
    active = active_binance_symbols()
    result = {}
    for sym in active:
        meta = BINANCE_SYMBOL_META.get(sym, {})
        entry = all_settings.get(sym, {})
        result[sym] = {
            "symbol": sym,
            "unit": meta.get("unit", sym),
            "tag": meta.get("tag", sym),
            "enabled": bool(entry.get("enabled", False)),
            "mode": str(entry.get("mode", "risk")),
            "risk_pct": float(entry.get("risk_pct", 0.20)),
            "leverage": float(entry.get("leverage", 5)),
            "principal_override": float(entry.get("principal_override") or 0) or None,
            "fixed_amount": float(entry.get("fixed_amount") or 0) or None,
            "trading_enabled": bool(entry.get("trading_enabled", True)),
        }
    return jsonify({"status": "ok", "symbols": result})


@console_bp.route("/api/console/symbol_settings/<symbol>", methods=["GET"])
@require_login
def api_symbol_settings_get(symbol):
    from account_profiles import get_symbol_settings
    sym = str(symbol or "").upper()
    return jsonify({"status": "ok", **get_symbol_settings(sym)})


@console_bp.route("/api/console/symbol_settings/<symbol>", methods=["PUT", "PATCH"])
@require_login
def api_symbol_settings_update(symbol):
    from account_profiles import set_symbol_settings, get_symbol_settings
    from symbol_config import active_binance_symbols
    sym = str(symbol or "").upper()
    if sym not in set(active_binance_symbols()):
        return jsonify({"status": "error", "message": "unknown_symbol"}), 400
    body = request.get_json(silent=True) or {}
    updated = set_symbol_settings(
        sym,
        enabled=body.get("enabled"),
        risk_pct=body.get("risk_pct"),
        leverage=body.get("leverage"),
        principal_override=body.get("principal_override"),
        mode=body.get("mode"),
        fixed_amount=body.get("fixed_amount"),
        trading_enabled=body.get("trading_enabled"),
    )
    return jsonify({"status": "ok", "symbol": sym, **updated})


# ── TV 信号日志 · 重放 · 手动发单 ──────────────────────────────────────────
# 直接同进程调用 app.py 的 process_webhook_payload，走和 TradingView 完全
# 相同的鉴权+解析+派发路径，不直接调用 supervisor 的任何方法。
#
# 2026-08-17 教训：这里原来是"回环 POST 自己的 127.0.0.1:$PORT/webhook"，
# 但 B/C/D 三账户 gunicorn 都是 `-w 1 --threads 1`（单进程单线程）——当前
# 这一个请求处理线程去等自己发给自己的 HTTP 请求的响应，而唯一能接这个
# 请求的又正好是它自己，直接死锁，连最简单的 PING 都会 10 秒超时。改成
# 同进程直接函数调用，没有网络往返，也就没有死锁的余地。

def _call_local_webhook(payload: dict, extra_headers: dict):
    import json as _json
    from app import process_webhook_payload
    from account_profiles import get_webhook_secret
    body = dict(payload)
    for k in ("secret", "token", "key"):
        body.pop(k, None)
    secret = str(get_webhook_secret() or "")
    if not secret:
        return {"status": "error", "message": "webhook_secret_not_configured"}, 500
    body["secret"] = secret
    headers = {"Content-Type": "application/json"}
    headers.update(extra_headers or {})
    try:
        raw_bytes = _json.dumps(body).encode("utf-8")
        resp, status = process_webhook_payload(
            raw_bytes, "application/json", headers, "127.0.0.1(console)",
        )
        return resp, status
    except Exception as e:
        return {"status": "error", "message": f"local_dispatch_failed: {e}"}, 502


@console_bp.route("/api/console/tv_signals", methods=["GET"])
@require_login
def api_tv_signals_list():
    import webhook_log
    rows = webhook_log.list_signals(
        limit=request.args.get("limit") or 50,
        offset=request.args.get("offset") or 0,
        symbol=request.args.get("symbol") or None,
        action=request.args.get("action") or None,
        source=request.args.get("source") or None,
    )
    return jsonify({"status": "ok", "signals": rows})


@console_bp.route("/api/console/tv_signals/meta", methods=["GET"])
@require_login
def api_tv_signals_meta():
    from symbol_config import active_binance_symbols, BINANCE_SYMBOL_META
    from webhook_parser import VALID_ACTIONS
    symbols = []
    for sym in active_binance_symbols():
        meta = BINANCE_SYMBOL_META.get(sym, {})
        symbols.append({
            "symbol": sym,
            "unit": meta.get("unit", sym),
            "tag": meta.get("tag", sym),
            "price_precision": meta.get("price_precision", 2),
            "qty_step": meta.get("qty_step"),
            "min_qty": meta.get("min_qty"),
        })
    return jsonify({
        "status": "ok",
        "symbols": symbols,
        "actions": sorted(VALID_ACTIONS),
        "tiers": [{"value": 0, "label": "弱"}, {"value": 1, "label": "中"}, {"value": 2, "label": "强"}],
    })


@console_bp.route("/api/console/tv_signals/<int:signal_id>", methods=["GET"])
@require_login
def api_tv_signal_detail(signal_id):
    import webhook_log
    row = webhook_log.get_signal(signal_id)
    if not row:
        return jsonify({"status": "error", "message": "not_found"}), 404
    return jsonify({"status": "ok", "signal": row})


@console_bp.route("/api/console/price/<symbol>", methods=["GET"])
@require_login
def api_console_price(symbol):
    """
    只读代查币安公开现价（手动发单/编辑重放面板"取现价"按钮用）。
    走公开 ticker 端点，不签名不需要 API Key，跟哪个账户无关。
    """
    import urllib.error
    import urllib.parse
    import urllib.request
    sym = str(symbol or "").strip().upper()
    if not sym:
        return jsonify({"status": "error", "message": "bad_symbol"}), 400
    url = "https://fapi.binance.com/fapi/v1/ticker/price?" + urllib.parse.urlencode({"symbol": sym})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "console-price-lookup"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        price = float(data.get("price") or 0)
        if price <= 0:
            return jsonify({"status": "error", "message": "empty_price"}), 502
        return jsonify({"status": "ok", "symbol": sym, "price": price})
    except urllib.error.HTTPError as e:
        return jsonify({"status": "error", "message": f"binance_http_{e.code}"}), 502
    except Exception as e:
        return jsonify({"status": "error", "message": f"lookup_failed: {e}"}), 502


def _inject_system_limit_order(payload: dict, body: dict) -> None:
    """
    Console 发出的 LONG/SHORT 默认按限价单执行（用户自己挑的时机/价格，不是
    追一根实时触发的K线，没必要吃市价滑点）；CLOSE_*/PING 不受影响，退出
    速度优先于省滑点。真实 TV webhook 走 /webhook 直连或网关，永远不会经过
    这个函数，market 路径不受任何影响。

    2026-08-17：面板加了"市价"选项——body.order_type 显式传 "MARKET" 时跳过
    限价注入，改走跟真实TV信号一样的市价路径（用户想现价追单/限价迟迟不成
    交时手动切市价的场景，接受滑点换即时成交）。不传该字段时默认仍是限价，
    不改变任何既有行为。
    """
    action = str(payload.get("action") or "").strip().upper()
    if action not in ("LONG", "SHORT"):
        return
    order_type = str(body.get("order_type") or "LIMIT").strip().upper()
    if order_type == "MARKET":
        payload.pop("order_type", None)
        payload.pop("limit_price", None)
        payload.pop("limit_timeout_sec", None)
        return
    try:
        limit_price = float(payload.get("price") or 0)
    except (TypeError, ValueError):
        limit_price = 0.0
    if limit_price <= 0:
        return
    payload["order_type"] = "LIMIT"
    payload["limit_price"] = limit_price
    try:
        timeout_min = float(body.get("limit_timeout_min") or 5)
    except (TypeError, ValueError):
        timeout_min = 5.0
    payload["limit_timeout_sec"] = min(max(timeout_min * 60.0, 30.0), 1800.0)


def _auto_fill_price(payload: dict) -> None:
    """
    控制面板专属兜底：手动发单漏填price（比如忘了点"取现价"）时，VPS自己
    查一次现价补上——LONG/SHORT在app.py里是硬性必填字段(缺了直接400拒收，
    HTTP层面因为_call_local_webhook包了一层，看着还是200，容易被误以为
    "发送成功但实际没生效"，2026-08-19实盘复现：用户连续5笔手动发单全部
    因为漏填price被静默拒收，只有点开返回JSON细看才能发现)。
    只在控制台手动发单入口调用；真实TV webhook必须自己带price，这里不
    影响那条主链路。
    """
    action = str(payload.get("action") or "").strip().upper()
    if action not in ("LONG", "SHORT"):
        return
    try:
        px_ok = float(payload.get("price") or 0) > 0
    except (TypeError, ValueError):
        px_ok = False
    if px_ok:
        return
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not symbol:
        return
    try:
        from binance_client import binance_client
        px = float(binance_client.get_current_price(symbol) or 0)
    except Exception as e:
        logger.warning(f"[控制台] {symbol} 漏填price且取现价失败: {e}")
        return
    if px <= 0:
        logger.warning(f"[控制台] {symbol} 漏填price且取现价为空，跳过自动补")
        return
    payload["price"] = px
    reason = str(payload.get("reason") or "").strip()
    payload["reason"] = (reason + " [VPS自动补现价]").strip()
    logger.info(f"[控制台] {symbol} 漏填price，自动补现价 price={px}")


def _auto_fill_atr(payload: dict) -> None:
    """
    控制面板专属兜底：LONG/SHORT 手动开仓漏填 atr 时，VPS 自己拉K线算一个。

    独立拆出来，不依赖 TP 是否缺失——之前这段逻辑长在 _auto_fill_tp_via_atr
    里面，只有 tp1/tp2/tp3 全部缺失时才会顺带算 ATR；如果用户自己填了TP
    （比如照着图上量出来的价位），只漏填了ATR，会被那个函数"TP已齐→直接
    return"的早退跳过，ATR永远补不上。position_supervisor_binance.py 那边
    "开仓 initial_atr 只认 TV webhook.atr>0"是铁律，ATR缺失会被下游直接
    拒开——2026-08-20实盘复现：手动发单ETHUSDT，TP1/2/3都填了但漏填ATR，
    日志停在"开仓qty核算"那一步不再往下走，没有任何报错，控制台这边因为
    price那次已经修过看着又像是"发出去了"，实际这笔根本没真正开仓。

    只在控制台手动发单/编辑重放这两个入口调用，理由跟price/TP那两个兜底
    一致：铁律防的是"VPS自己瞎猜TV该给什么"，这里是"用户自己手动开仓、
    自己没给，VPS给个合理起点"，不是同一件事，不影响真实TV webhook路径。
    """
    action = str(payload.get("action") or "").strip().upper()
    if action not in ("LONG", "SHORT"):
        return
    try:
        atr_ok = float(payload.get("atr") or 0) > 0
    except (TypeError, ValueError):
        atr_ok = False
    if atr_ok:
        return
    symbol = str(payload.get("symbol") or "").strip().upper()
    if not symbol:
        return
    try:
        from strategy_engine import klines as _sk_klines
        from strategy_engine import indicators as _sk_ind
        bars = _sk_klines.get_bars(symbol, "15m", limit=60)
        atr = _sk_ind.wilder_atr(bars, 14) if bars else 0.0
    except Exception as e:
        logger.warning(f"[控制台] {symbol} 漏填atr且自动拉取失败: {e}")
        return
    if atr <= 0:
        logger.warning(f"[控制台] {symbol} 漏填atr且取不到有效ATR，跳过自动补")
        return
    payload["atr"] = round(atr, 6)
    reason = str(payload.get("reason") or "").strip()
    payload["reason"] = (reason + " [VPS自动补ATR]").strip()
    logger.info(f"[控制台] {symbol} 漏填atr，自动补ATR atr={atr}")


def _auto_fill_tp_via_atr(payload: dict) -> None:
    """
    控制面板专属兜底：LONG/SHORT 手动开仓漏填 tp1/tp2/tp3 时，按中趋势档
    参数(1.35/2.5/3.6×ATR)补出完整 TP123，补完后行为跟真实 TV 信号开的仓
    完全一样——TP1/TP2 先锁 10%/20% 小部分利润，剩余 70% 交给现有的递进
    雷达动态追踪(即"跟踪止盈")。ATR 本身由 _auto_fill_atr 保证先行补齐，
    这里只管拿 atr 算 TP，不再重复拉K线。

    只在控制台手动发单/编辑重放这两个入口调用；真实 TV webhook 永远不
    经过这个函数，position_supervisor_binance.py 里"禁止 entry+ATR 本地
    重算覆盖 TV 价格"的铁律完全不受影响——那条铁律防的是"VPS 自己瞎猜
    TV 该给什么价"，这里是反过来："用户自己手动开仓、自己没给，VPS 给
    个跟系统里其它品种一致的合理起点"，场景不同。
    tp1/tp2/tp3 只要有一个缺失就当作"用户没打算自己填"，三个一起重算，
    避免用户手填一部分、VPS 补另一部分导致三档价位顺序不合理。
    """
    action = str(payload.get("action") or "").strip().upper()
    if action not in ("LONG", "SHORT"):
        return
    if all(float(payload.get(k) or 0) > 0 for k in ("tp1", "tp2", "tp3")):
        return
    try:
        entry = float(payload.get("price") or 0)
    except (TypeError, ValueError):
        entry = 0.0
    if entry <= 0:
        return
    try:
        atr = float(payload.get("atr") or 0)
    except (TypeError, ValueError):
        atr = 0.0
    symbol = str(payload.get("symbol") or "").strip().upper()
    if atr <= 0:
        logger.warning(f"[控制台] {symbol} 缺TP且无有效ATR，跳过自动补TP")
        return
    direction = 1.0 if action == "LONG" else -1.0
    payload["tp1"] = round(entry + direction * 1.35 * atr, 6)
    payload["tp2"] = round(entry + direction * 2.5 * atr, 6)
    payload["tp3"] = round(entry + direction * 3.6 * atr, 6)
    reason = str(payload.get("reason") or "").strip()
    payload["reason"] = (reason + " [VPS自动ATR算TP]").strip()
    logger.info(
        f"[控制台] {symbol} 漏填TP，自动补齐 atr={atr:.4f} "
        f"tp1={payload['tp1']} tp2={payload['tp2']} tp3={payload['tp3']}"
    )


@console_bp.route("/api/console/tv_signals/<int:signal_id>/replay", methods=["POST"])
@require_login
def api_tv_signal_replay(signal_id):
    import webhook_log
    row = webhook_log.get_signal(signal_id)
    if not row:
        return jsonify({"status": "error", "message": "not_found"}), 404
    try:
        payload = json.loads(row.get("raw_json") or "{}")
    except Exception:
        payload = {}
    body = request.get_json(silent=True) or {}
    overrides = body.get("overrides") or {}
    if not isinstance(overrides, dict):
        return jsonify({"status": "error", "message": "overrides_must_be_object"}), 400
    payload.update(overrides)
    for k in webhook_log.STRIP_ON_REPLAY_KEYS:
        payload.pop(k, None)
    if not payload.get("reason"):
        payload["reason"] = f"[控制台重放#{signal_id}]"
    _auto_fill_price(payload)
    _auto_fill_atr(payload)
    _auto_fill_tp_via_atr(payload)
    _inject_system_limit_order(payload, body)
    resp_body, status = _call_local_webhook(payload, {
        "X-TV-Source": "console_replay",
        "X-TV-Replay-Of": str(signal_id),
    })
    return jsonify({"status": "ok", "upstream_status": status, "upstream": resp_body}), 200


@console_bp.route("/api/console/tv_manual_send", methods=["POST"])
@require_login
def api_tv_manual_send():
    from symbol_config import active_binance_symbols
    from webhook_parser import VALID_ACTIONS
    body = request.get_json(silent=True) or {}
    symbol = str(body.get("symbol") or body.get("ticker") or "").strip().upper()
    action = str(body.get("action") or "").strip().upper()
    if action not in VALID_ACTIONS:
        return jsonify({"status": "error", "message": "bad_action", "allowed": sorted(VALID_ACTIONS)}), 400
    if action != "PING" and symbol not in set(active_binance_symbols()):
        return jsonify({"status": "error", "message": "bad_symbol", "allowed": active_binance_symbols()}), 400
    payload = {
        k: v for k, v in body.items()
        if k not in ("secret", "token", "key", "limit_timeout_min") and v not in (None, "")
    }
    payload["symbol"] = symbol
    payload["ticker"] = symbol
    payload["action"] = action
    if not payload.get("reason"):
        payload["reason"] = "[控制台手动发单]"
    _auto_fill_price(payload)
    _auto_fill_atr(payload)
    _auto_fill_tp_via_atr(payload)
    _inject_system_limit_order(payload, body)
    resp_body, status = _call_local_webhook(payload, {"X-TV-Source": "console_manual"})
    return jsonify({"status": "ok", "upstream_status": status, "upstream": resp_body}), 200


@console_bp.route("/api/console/grid_order", methods=["POST"])
@require_login
def api_grid_order():
    """
    网格套利（区间震荡）手动开仓——TV没信号覆盖的时间段，人工填限价开仓/
    精确止损/一次性止盈直接发单。刻意不经过 process_webhook_payload/
    webhook_parser/handle_signal（那条主链路强绑定TV六个action，止损会被
    自动加宽、止盈会被自动补成TP1/2/3三档，不适合网格"精确止损+一把平"的
    诉求），直接调用 supervisor.open_grid_position（见 position_supervisor_
    binance.py 顶部同名方法的详细注释）。
    """
    from position_supervisor_binance import get_supervisor
    from symbol_config import resolve_binance_symbol, active_binance_symbols
    import webhook_log

    body = request.get_json(silent=True) or {}
    meta = resolve_binance_symbol(str(body.get("symbol") or "").strip(), default="")
    sym = meta.get("symbol") or ""
    if not sym or sym not in set(active_binance_symbols()):
        return jsonify({"status": "error", "message": "bad_symbol", "allowed": active_binance_symbols()}), 400
    side = str(body.get("side") or "").strip().upper()
    if side not in ("LONG", "SHORT"):
        return jsonify({"status": "error", "message": "bad_side"}), 400
    try:
        entry_price = float(body.get("entry_price"))
        stop_price = float(body.get("stop_price"))
        tp_price = float(body.get("tp_price"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "bad_price"}), 400
    try:
        tier = int(body.get("tier", 1))
    except (TypeError, ValueError):
        tier = 1
    try:
        timeout_min = float(body.get("timeout_min", 30) or 30)
    except (TypeError, ValueError):
        timeout_min = 30.0

    sup = get_supervisor(sym)
    result = sup.open_grid_position(
        side, entry_price, stop_price, tp_price, tier=tier, timeout_min=timeout_min,
    )
    try:
        webhook_log.record_signal(
            {
                "action": f"GRID_{side}",
                "symbol": sym,
                "price": entry_price,
                "stop_loss": stop_price,
                "tp1": tp_price,
                "tier": tier,
                "reason": "[控制台网格套利]",
            },
            source="console_grid",
            http_status=200 if result.get("ok") else 400,
            http_message=str(result.get("message") or ""),
        )
    except Exception:
        pass
    status_code = 200 if result.get("ok") else 400
    return jsonify({"status": "ok" if result.get("ok") else "error", **result}), status_code


@console_bp.route("/api/console/grid_order/cancel", methods=["POST"])
@require_login
def api_grid_order_cancel():
    from position_supervisor_binance import get_supervisor
    from symbol_config import resolve_binance_symbol, active_binance_symbols

    body = request.get_json(silent=True) or {}
    meta = resolve_binance_symbol(str(body.get("symbol") or "").strip(), default="")
    sym = meta.get("symbol") or ""
    if not sym or sym not in set(active_binance_symbols()):
        return jsonify({"status": "error", "message": "bad_symbol"}), 400
    sup = get_supervisor(sym)
    result = sup.cancel_grid_pending_entry()
    status_code = 200 if result.get("ok") else 400
    return jsonify({"status": "ok" if result.get("ok") else "error", **result}), status_code


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

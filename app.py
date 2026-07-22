#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, threading, logging
from flask import Flask, request, jsonify
from position_supervisor_binance import (
    get_supervisor,
    get_supervisor_for_payload,
    SUPERVISORS,
    BINANCE_VPS_VERSION,
    bootstrap_supervisors,
)
from webhook_parser import (
    parse_webhook_request,
    normalize_tv_payload,
    format_webhook_log,
    TV_STRATEGY_VERSION,
)
from symbol_config import active_binance_symbols, resolve_binance_symbol

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] Flask-Binance: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.route('/webhook', methods=['POST'])
@app.route('/webhook/<path:ticker>', methods=['POST'])
def webhook(ticker=None):
    try:
        _, data = parse_webhook_request(
            request.get_data(),
            request.content_type or "",
            as_json=request.get_json(silent=True),
        )
        data = normalize_tv_payload(data)
    except ValueError as e:
        logger.warning(f"[Webhook] 解析失败: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

    if not data:
        return jsonify({"status": "error", "message": "Empty payload"}), 400
    # 鉴权：优先 secret（TV v6.5.6+）；兼容旧字段 token；值须 = WEBHOOK_SECRET
    auth = str(
        data.get("secret") or data.get("token") or ""
    ).strip()
    expected = str(os.getenv("WEBHOOK_SECRET", "528586")).strip()
    if auth != expected:
        return jsonify({"status": "error", "message": "Invalid secret"}), 403
    if not data.get("_parse_ok"):
        return jsonify({"status": "error", "message": "Missing or invalid action"}), 400

    # URL 路径品种优先（/webhook/XAUUSDT），否则读 payload ticker
    if ticker:
        data["ticker"] = ticker
        data["symbol"] = ticker

    raw_action = data.get("action", "UNKNOWN")
    if raw_action == "PING":
        return jsonify({
            "status": "success",
            "message": "pong",
            "action": "PING",
            "schema": TV_STRATEGY_VERSION,
            "symbols": active_binance_symbols(),
        }), 200

    supervisor, sym = get_supervisor_for_payload(data)
    if supervisor is None:
        logger.warning(f"[Webhook] 不支持的品种: {sym}")
        return jsonify({
            "status": "error",
            "message": f"Unsupported or missing symbol: {sym}",
            "hint": "TV JSON must include symbol/ticker e.g. ETHUSDT.P or XAUUSDT.P",
            "allowed": active_binance_symbols(),
        }), 400

    logger.info(f"[Webhook] [{sym}] {format_webhook_log(data)}")

    # 开仓必要字段：仅 price（ATR/ADX 由 VPS 行情引擎自算；webhook atr 仅调试比对）
    if raw_action in ("LONG", "SHORT"):
        px = data.get("price")
        try:
            px_ok = px is not None and float(px) > 0
        except (TypeError, ValueError):
            px_ok = False
        if not px_ok:
            return jsonify({
                "status": "error",
                "message": "LONG/SHORT require valid price (ATR/ADX computed on VPS)",
                "got": {"price": px},
            }), 400
        # stop_loss 仅作 sizing 调整系数 / 调试对比，不参与挂盘
        sl = data.get("stop_loss") or data.get("tv_sl")
        if sl is not None:
            data["_tv_sl_ref"] = sl

    try:
        threading.Thread(
            target=supervisor.handle_signal, args=(data,), daemon=True,
            name=f"tv-{sym}",
        ).start()
    except Exception as e:
        logger.error(f"启动线程失败 [{sym}]: {e}")
        return jsonify({
            "status": "error",
            "message": f"Failed to start processing: {e}",
            "symbol": sym,
        }), 500

    return jsonify({
        "status": "success",
        "message": "Signal received and processing started",
        "action": raw_action,
        "symbol": sym,
        "schema": TV_STRATEGY_VERSION,
    }), 200


@app.route('/admin/resume/<path:symbol>', methods=['POST'])
def admin_resume(symbol):
    """人工确认后解除交易暂停；同时清掉持仓期误累计的 ATR 降级污染。"""
    meta = resolve_binance_symbol(symbol, default="")
    sym = meta.get("symbol") or ""
    if not sym or sym not in set(active_binance_symbols()):
        return jsonify({
            "status": "error",
            "message": f"Unknown symbol: {symbol}",
            "allowed": active_binance_symbols(),
        }), 400
    sup = get_supervisor(sym)
    prev = str(getattr(sup, "trading_pause_reason", "") or "")
    was = bool(getattr(sup, "trading_paused", False))
    sup.trading_paused = False
    sup.trading_pause_reason = ""
    # 持仓期误跑开仓sizing留下的污染（假 ATR 降级）一并清掉
    try:
        sup._atr_div_streak = 0
        sup.atr_degraded = False
        if str(getattr(sup, "atr_source", "") or "").startswith("tv_implied"):
            sup.atr_source = "vps"
        sup._pending_atr_degrade = None
    except Exception as e:
        logger.warning(f"[admin/resume] ATR污染清理跳过: {e}")
    try:
        sup._save_state()
    except Exception as e:
        logger.warning(f"[admin/resume] 状态持久化跳过: {e}")
    logger.info(f"✅ [admin/resume] {sym} 解除暂停 | was={was} reason={prev or '—'}")
    return jsonify({
        "status": "success",
        "symbol": sym,
        "was_paused": was,
        "previous_reason": prev,
        "trading_paused": False,
        "atr_div_streak": 0,
        "atr_degraded": False,
    }), 200


@app.route('/health', methods=['GET'])
def health():
    from webhook_parser import SIZING_MODE
    return jsonify({
        "service": "binance_webhook",
        "status": "ok",
        "version": BINANCE_VPS_VERSION,
        "tv_strategy": TV_STRATEGY_VERSION,
        "sizing": SIZING_MODE,  # RISK20_NOTIONAL5
        "leverage": "fixed_5",
        "risk_pct": 0.20,
        "notional_mult": 5,
        "radar": "breath_stop_90m",
        "symbols": list(SUPERVISORS.keys()) or active_binance_symbols(),
        "monitoring": {
            s: bool(getattr(sup, "monitoring", False))
            for s, sup in SUPERVISORS.items()
        },
        "trading_paused": {
            s: bool(getattr(sup, "trading_paused", False))
            for s, sup in SUPERVISORS.items()
        },
        "trading_pause_reason": {
            s: str(getattr(sup, "trading_pause_reason", "") or "")
            for s, sup in SUPERVISORS.items()
        },
    }), 200


if __name__ == '__main__':
    bootstrap_supervisors()
    app.run(host='127.0.0.1', port=5003, debug=False, threaded=True)

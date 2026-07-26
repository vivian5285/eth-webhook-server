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
from console_api import init_console
from account_profiles import bootstrap_from_env, get_webhook_secret, get_active_sizing

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] Flask-Binance: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
init_console(app)
# gunicorn / systemd 入口也会加载本模块：启动时灌入档案并绑 API
try:
    bootstrap_from_env()
except Exception as _e:
    logger.warning(f"account_profiles bootstrap: {_e}")


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
    # 鉴权：优先 secret（TV v6.5.6+）；兼容旧字段 token；值来自 Console/档案或 .env
    auth = str(
        data.get("secret") or data.get("token") or ""
    ).strip()
    expected = str(get_webhook_secret() or os.getenv("WEBHOOK_SECRET", "528586")).strip()
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
        # stop_loss → 永久硬止损距离输入（与 atr 一并进入 v15.7.8+ 唯一公式）；亦可作 sizing 收紧 / 调试对比
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
    # resume 保持本地运维兼容（不强制 secret）；smoke_arm 才强制鉴权
    sup = get_supervisor(sym)
    prev = str(getattr(sup, "trading_pause_reason", "") or "")
    was = bool(getattr(sup, "trading_paused", False))
    was_mon = bool(getattr(sup, "api_monitor_only", False))
    sup.trading_paused = False
    sup.trading_pause_reason = ""
    try:
        if hasattr(sup, "_exit_api_monitor_only") and (
            was_mon or str(prev).startswith("api_monitor_only")
        ):
            # 人工 resume：清仅监控标志，但不强制再走 probe 对账路径的钉钉轰炸
            from binance_client import binance_client as _bc
            getattr(sup, "api_monitor_only", None)
            sup.api_monitor_only = False
            _bc.set_monitor_only(sym, False)
        elif was_mon:
            from binance_client import binance_client as _bc
            sup.api_monitor_only = False
            _bc.set_monitor_only(sym, False)
    except Exception as e:
        logger.warning(f"[admin/resume] 清仅监控跳过: {e}")
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


@app.route('/admin/smoke_arm_radar/<path:symbol>', methods=['POST'])
def admin_smoke_arm_radar(symbol):
    """
    烟测专用：在主进程军师上强制越过激活线挂雷达 STOP。
    仅当 monitoring 且有持仓、雷达仍休眠时生效；需 WEBHOOK_SECRET。
    """
    expected = str(os.getenv("WEBHOOK_SECRET", "528586")).strip()
    data = request.get_json(silent=True) or {}
    auth = str(
        data.get("secret")
        or request.form.get("secret")
        or request.args.get("secret")
        or request.headers.get("X-Webhook-Secret")
        or ""
    ).strip()
    if not expected or auth != expected:
        return jsonify({"status": "error", "message": "Invalid secret"}), 403
    meta = resolve_binance_symbol(symbol, default="")
    sym = meta.get("symbol") or ""
    if not sym or sym not in set(active_binance_symbols()):
        return jsonify({
            "status": "error",
            "message": f"Unknown symbol: {symbol}",
            "allowed": active_binance_symbols(),
        }), 400
    sup = get_supervisor(sym)
    if not bool(getattr(sup, "monitoring", False)):
        return jsonify({"status": "error", "message": "not monitoring"}), 400
    live = float(getattr(sup, "watched_qty", 0) or 0)
    if live <= 0:
        return jsonify({"status": "error", "message": "no live qty"}), 400
    if bool(getattr(sup, "radar_activated", False)):
        return jsonify({
            "status": "ok",
            "symbol": sym,
            "already_activated": True,
            "radar_activated": True,
        }), 200
    try:
        gate = float(sup._radar_activation_price() or 0)
    except Exception as e:
        return jsonify({"status": "error", "message": f"gate_err:{e}"}), 500
    side = str(getattr(sup, "current_side", "") or "").upper()
    if side == "SHORT":
        fake = gate * 0.998 if gate > 0 else 0.0
    else:
        fake = gate * 1.002 if gate > 0 else 0.0
    if fake <= 0:
        return jsonify({"status": "error", "message": "bad fake px"}), 500
    ok = bool(
        sup._maybe_arm_radar_on_activation(live, fake, source="admin_smoke_arm")
    )
    logger.info(
        f"[admin/smoke_arm_radar] {sym} ok={ok} gate={gate} fake={fake} "
        f"activated={getattr(sup, 'radar_activated', False)}"
    )
    return jsonify({
        "status": "success" if ok else "error",
        "symbol": sym,
        "armed": ok,
        "gate": gate,
        "fake_px": fake,
        "radar_activated": bool(getattr(sup, "radar_activated", False)),
        "radar_pending_arm": bool(getattr(sup, "radar_pending_arm", False)),
        "initial_stop": float(getattr(sup, "initial_stop", 0) or 0),
    }), 200 if ok else 500


@app.route('/admin/reload_notify', methods=['POST'])
def admin_reload_notify():
    """热加载 Telegram/钉钉环境变量，无需重启交易服务。"""
    try:
        import dingtalk
        status = dingtalk.reload_notify_config()
        return jsonify({"status": "ok", "notify": status}), 200
    except Exception as e:
        logger.error(f"[admin/reload_notify] {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/admin/notify_test', methods=['POST'])
def admin_notify_test():
    """
    验收双通道：
      {"level": 1} → 仅 TG
      {"level": 2} → TG + 钉钉
    """
    try:
        import dingtalk
        body = request.get_json(silent=True) or {}
        lvl = int(body.get("level") or 1)
        title = str(body.get("title") or f"双通道自检 L{lvl}")
        detail = str(body.get("detail") or "notify_test from /admin/notify_test")
        dingtalk.send_alert(
            title,
            {"📝 说明": detail, "🧪 level": str(lvl)},
            immediate=True,
            level=lvl,
        )
        return jsonify({
            "status": "ok",
            "level": lvl,
            "notify": dingtalk.notify_config_status(),
        }), 200
    except Exception as e:
        logger.error(f"[admin/notify_test] {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    from webhook_parser import SIZING_MODE
    from binance_client import binance_client as _bc
    try:
        import dingtalk
        notify = dingtalk.notify_config_status()
    except Exception:
        notify = {"telegram_configured": False, "dingtalk_configured": False}
    try:
        risk, lev = get_active_sizing()
    except Exception:
        risk, lev = 0.20, 5.0
    return jsonify({
        "service": "binance_webhook",
        "status": "ok",
        "version": BINANCE_VPS_VERSION,
        "tv_strategy": TV_STRATEGY_VERSION,
        "sizing": SIZING_MODE,  # RISK20_NOTIONAL5 公式骨架；数值来自生效档案
        "leverage": float(lev),
        "risk_pct": float(risk),
        "notional_mult": float(lev),
        "console": "/console",
        "radar": "breath_dual_eth_xau",
        "notify": notify,
        "symbols": list(SUPERVISORS.keys()) or active_binance_symbols(),
        "monitoring": {
            s: bool(getattr(sup, "monitoring", False))
            for s, sup in SUPERVISORS.items()
        },
        "trading_paused": {
            s: bool(getattr(sup, "trading_paused", False))
            for s, sup in SUPERVISORS.items()
        },
        "api_monitor_only": {
            s: bool(getattr(sup, "api_monitor_only", False))
            or bool(_bc.is_monitor_only(s))
            for s, sup in SUPERVISORS.items()
        },
        "trading_pause_reason": {
            s: str(getattr(sup, "trading_pause_reason", "") or "")
            for s, sup in SUPERVISORS.items()
        },
        "pipeline": {
            s: (
                (getattr(sup, "_pipeline_state_blob", lambda: {})() or {}).get("phase")
                if callable(getattr(sup, "_pipeline_state_blob", None))
                else None
            )
            for s, sup in SUPERVISORS.items()
        },
    }), 200


if __name__ == '__main__':
    bootstrap_from_env()
    bootstrap_supervisors()
    # 对外 IP:端口访问 Console（仍建议仅自己用；设 CONSOLE_PASSWORD）
    app.run(host='0.0.0.0', port=5003, debug=False, threaded=True)

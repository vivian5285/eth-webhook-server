#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, logging, re
from datetime import datetime
from flask import Flask, request, jsonify
from position_supervisor_binance import position_supervisor

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] Flask-Binance: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
WEBHOOK_RAW_JOURNAL = "logs/binance_webhook_raw.jsonl"


def _audit_raw_webhook(raw: str, ok: bool, err: str = "", action: str = ""):
    """每条入站 POST 先落盘，便于区分「TV 没发」vs「发了但解析失败」"""
    try:
        os.makedirs("logs", exist_ok=True)
        entry = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ok": ok,
            "action": action or None,
            "error": err or None,
            "raw_len": len(raw),
            "raw_preview": (raw[:500] if raw else ""),
            "remote": request.remote_addr,
        }
        with open(WEBHOOK_RAW_JOURNAL, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"原始 Webhook 审计写入失败: {e}")


def _parse_tv_payload(raw_text: str):
    """兼容 TradingView：text/plain / application/json / 外层 message 包裹"""
    raw = (raw_text or "").strip()
    if not raw:
        return None, "Empty payload"

    raw = raw.lstrip("\ufeff")

    candidates = [raw]
    try:
        outer = json.loads(raw)
        if isinstance(outer, dict):
            if "action" in outer:
                return outer, None
            for key in ("message", "msg", "text", "content", "alert"):
                inner = outer.get(key)
                if isinstance(inner, str) and inner.strip():
                    candidates.append(inner.strip())
                elif isinstance(inner, dict) and inner.get("action"):
                    return inner, None
    except json.JSONDecodeError:
        pass

    for text in candidates:
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data, None
        except json.JSONDecodeError:
            continue

    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)
    if cleaned != raw:
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data, None
        except json.JSONDecodeError:
            pass

    preview = raw[:240].replace("\n", "\\n")
    logger.error(f"[Webhook] JSON 解析失败 | 原文预览: {preview}")
    return None, "Invalid JSON"


@app.route('/webhook', methods=['POST'])
def webhook():
    # Flask request body 只能读一次，禁止在别处再次 get_data()
    raw_body = (request.get_data(as_text=True) or "")
    data, err = _parse_tv_payload(raw_body)
    if err:
        _audit_raw_webhook(raw_body, ok=False, err=err)
        logger.error(f"[Webhook] ❌ 解析失败: {err} | len={len(raw_body)} | preview={raw_body[:200]!r}")
        return jsonify({"status": "error", "message": err}), 400

    if str(data.get("secret", "")).strip() != os.getenv("WEBHOOK_SECRET", "528586"):
        _audit_raw_webhook(raw_body, ok=False, err="Invalid secret")
        return jsonify({"status": "error", "message": "Invalid secret"}), 403

    raw_action = str(data.get("action", "UNKNOWN")).strip().upper()
    if not raw_action or raw_action == "UNKNOWN":
        _audit_raw_webhook(raw_body, ok=False, err="Missing action")
        logger.error(f"[Webhook] ❌ JSON 无 action 字段 | preview={raw_body[:200]!r}")
        return jsonify({"status": "error", "message": "Missing action"}), 400

    data["action"] = raw_action
    reason = data.get("reason", "策略安全轮换")
    _audit_raw_webhook(raw_body, ok=True, action=raw_action)

    if "CLOSE_PROTECT" in raw_action:
        logger.info(
            f"[Webhook] 📥 收到信号 → 【保护性全平】 | 原因: {reason} | "
            f"Side: {data.get('side', 'N/A')} | PnL: {data.get('pnl_pct', 'N/A')}% | "
            f"Regime: {data.get('regime', 'N/A')}"
        )
    else:
        logger.info(f"[Webhook] 📥 收到信号 → 【{raw_action}】 | Regime: {data.get('regime', 'N/A')}")

    position_supervisor.enqueue_signal(data)

    return jsonify({
        "status": "success",
        "message": "Signal received and queued",
        "action": raw_action,
        "queue_depth": position_supervisor.signal_queue_depth(),
    }), 200


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"service": "binance_webhook", "status": "ok", "version": "v13.4.3-ws-radar"}), 200


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5003, debug=False, threaded=True)

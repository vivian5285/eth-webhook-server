#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""polymarket_quant 独立 Telegram 发送器。同 chain_sniper/notifier.py 的写法：
复用主程序 .env 里已在用的 TELEGRAM_* 变量，零 import 主程序模块，敏感值脱敏。
"""
import logging
import threading
import time

import requests

from config import load_config

logger = logging.getLogger(__name__)

_cfg = load_config()


def _redact(text):
    s = str(text or "")
    for secret in _cfg.secret_values():
        if secret and len(secret) >= 6:
            s = s.replace(secret, "***REDACTED***")
    return s


def send_text(message, parse_mode=None):
    token = _cfg.telegram_bot_token
    chat_id = _cfg.telegram_chat_id
    if not token or not chat_id:
        logger.warning("notify skip: telegram not configured")
        return False
    safe_message = _redact(message)
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    mode = parse_mode if parse_mode is not None else _cfg.telegram_parse_mode
    payload = {"chat_id": chat_id, "text": safe_message}
    if mode:
        payload["parse_mode"] = mode
        payload["disable_web_page_preview"] = True

    attempts = max(1, int(_cfg.telegram_retry_max or 3))
    delay = float(_cfg.telegram_retry_sec or 3)
    last_err = ""
    for i in range(attempts):
        try:
            r = requests.post(url, json=payload, timeout=8)
            body = {}
            try:
                body = r.json() if r.text else {}
            except Exception:
                body = {}
            if r.status_code == 200 and body.get("ok"):
                logger.info("notify ok attempt=%s/%s preview=%s", i + 1, attempts, safe_message[:72])
                return True
            last_err = f"HTTP {r.status_code} {str(body)[:160]}"
        except Exception as e:
            last_err = str(e)
        logger.error("notify fail attempt=%s/%s err=%s", i + 1, attempts, _redact(last_err))
        if i < attempts - 1:
            time.sleep(delay)
    return False


def _fire_async(text):
    def _run():
        try:
            send_text(text)
        except Exception as e:
            logger.error("notify async exception: %s", _redact(str(e)), exc_info=False)

    threading.Thread(target=_run, daemon=True, name="polyquant-notify").start()


def _tag(title):
    return f"【{_cfg.label}】{title}"


def send_buy(window_key, side, order_style, shares, price, order_id="", dry_run=False):
    prefix = "🧪[空跑]" if dry_run else "🟢"
    msg = (
        f"{_tag(prefix + ' 开仓')}\n"
        f"窗口: {window_key}\n方向: {side} ({order_style})\n份额: {shares}  价格: {price}\n"
        f"order: {order_id or '(dry-run，无真实订单)'}"
    )
    _fire_async(msg)


def send_exit(position, reason="", dry_run=False):
    prefix = "🧪[空跑]" if dry_run else "🔴"
    pnl = position.get("realized_pnl_usd")
    msg = (
        f"{_tag(prefix + ' 离场/结算')}\n"
        f"窗口: {position.get('window_key')}\n原因: {reason}\n"
        f"盈亏: {pnl if pnl is not None else '—'} USD"
    )
    _fire_async(msg)


def send_skip(reason, window_key=""):
    msg = f"{_tag('⏭️ 跳过')}\n窗口: {window_key}\n原因: {reason}"
    _fire_async(msg)


def send_error(context, error):
    msg = f"{_tag('⚠️ 异常')}\n场景: {context}\n错误: {_redact(error)}"
    _fire_async(msg)


def send_heartbeat(open_positions, daily_pnl, kill_switch_active_flag, trades_today):
    msg = (
        f"{_tag('💓 心跳')}\n"
        f"持仓: {open_positions}  今日已实现盈亏: {daily_pnl:+.2f} USD\n"
        f"熔断: {'是' if kill_switch_active_flag else '否'}  今日交易数: {trades_today}"
    )
    _fire_async(msg)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ok = send_text(f"{_tag('✅ 自检')}\npolymarket_quant notifier 自测消息，收到即说明链路正常。")
    print("send_text result:", ok)

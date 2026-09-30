#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chain_sniper 独立 Telegram 发送器。复用主程序 .env 里已在用的 TELEGRAM_* 变量
（VPS 上实际生效的通知渠道），但零 import 主程序模块——避免拖入 dingtalk.py 里
一整套合约持仓播报格式化逻辑。重试逻辑参考 dingtalk.py 的 send_telegram() 写法。

硬性安全规则：任何消息文本发送前都会过滤掉命中 config.secret_values() 的子串
（热钱包私钥/API key等），防止某个异常的 repr() 把作用域里的敏感值带出来。
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
    """发送 Telegram 消息，失败重试。绝不抛到调用方；返回 True/False。"""
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

    threading.Thread(target=_run, daemon=True, name="chainsniper-notify").start()


def _tag(title):
    return f"【{_cfg.label}】{title}"


def send_buy(chain, token_address, amount, price, tx_hash, dry_run=False):
    prefix = "🧪[空跑]" if dry_run else "🟢"
    msg = (
        f"{_tag(prefix + ' 买入')}\n"
        f"链: {chain}\n代币: {token_address}\n数量: {amount}\n价格: {price}\n"
        f"tx: {tx_hash or '(dry-run，无真实交易)'}"
    )
    _fire_async(msg)


def send_exit(position, result, reason="", dry_run=False):
    prefix = "🧪[空跑]" if dry_run else "🔴"
    pnl = position.get("realized_pnl_usd")
    msg = (
        f"{_tag(prefix + ' 离场')}\n"
        f"链: {position.get('chain')}\n代币: {position.get('token_address')}\n"
        f"原因: {reason}\n盈亏: {pnl if pnl is not None else '—'} USD\n"
        f"tx: {getattr(result, 'tx_hash', '') or '(dry-run)'}"
    )
    _fire_async(msg)


def send_skip(reason, token_address="", chain=""):
    msg = f"{_tag('⏭️ 跳过')}\n链: {chain}\n代币: {token_address}\n原因: {reason}"
    _fire_async(msg)


def send_error(context, error):
    msg = f"{_tag('⚠️ 异常')}\n场景: {context}\n错误: {_redact(error)}"
    _fire_async(msg)


def send_heartbeat(open_positions, daily_pnl, kill_switch_active, watchlist_size):
    msg = (
        f"{_tag('💓 心跳')}\n"
        f"持仓: {open_positions}  今日已实现盈亏: {daily_pnl:+.2f} USD\n"
        f"熔断: {'是' if kill_switch_active else '否'}  监控名单: {watchlist_size}"
    )
    _fire_async(msg)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ok = send_text(f"{_tag('✅ 自检')}\nchain_sniper notifier 自测消息，收到即说明链路正常。")
    print("send_text result:", ok)

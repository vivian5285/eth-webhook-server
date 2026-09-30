#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram 通知。没配 token/chat_id 就静默 no-op，只写日志。"""
import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def send(cfg, text: str):
    if not (cfg.telegram_bot_token and cfg.telegram_chat_id):
        logger.info("[notify:no-tg] %s", text.replace("\n", " | "))
        return
    try:
        data = urllib.parse.urlencode({
            "chat_id": cfg.telegram_chat_id, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": "true",
        }).encode()
        url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15) as r:
            j = json.loads(r.read().decode())
            if not j.get("ok"):
                logger.warning("telegram 返回非 ok: %s", j)
    except Exception as e:
        logger.warning("telegram 发送失败: %s", e)

#!/usr/bin/env python3
# dingtalk.py（支持加签 + 富文本版 - 2026-06-15）
import os
import time
import hmac
import hashlib
import base64
import urllib.parse
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK_URL", "")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")  # 加签密钥


def _generate_sign(secret: str) -> tuple:
    """生成加签 timestamp + sign"""
    timestamp = str(round(time.time() * 1000))
    secret_enc = secret.encode('utf-8')
    string_to_sign = f'{timestamp}\n{secret}'
    string_to_sign_enc = string_to_sign.encode('utf-8')
    hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def send_dingtalk_message(message: str, is_at_all: bool = False):
    """发送钉钉消息（支持加签）"""
    if not DINGTALK_WEBHOOK:
        logger.warning("[DingTalk] 未配置 DINGTALK_WEBHOOK_URL，跳过发送")
        return False

    try:
        url = DINGTALK_WEBHOOK
        if DINGTALK_SECRET:
            timestamp, sign = _generate_sign(DINGTALK_SECRET)
            url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"

        data = {
            "msgtype": "text",
            "text": {
                "content": f"🤖 [ETH量化系统]\n{message}"
            },
            "at": {
                "isAtAll": is_at_all
            }
        }

        resp = requests.post(url, json=data, timeout=8)
        if resp.status_code == 200 and resp.json().get("errcode") == 0:
            logger.info("[DingTalk] 消息发送成功")
            return True
        else:
            logger.error(f"[DingTalk] 发送失败: {resp.text}")
            return False

    except Exception as e:
        logger.error(f"[DingTalk] 发送异常: {e}", exc_info=True)
        return False

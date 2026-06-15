#!/usr/bin/env python3
# dingtalk.py（完整版 - 2026-06-15）
import os
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

# 从环境变量读取钉钉机器人 Webhook（推荐做法）
DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK_URL", "")

def send_dingtalk_message(message: str, is_at_all: bool = False):
    """
    发送钉钉消息
    """
    if not DINGTALK_WEBHOOK:
        logger.warning("[DingTalk] 未配置 DINGTALK_WEBHOOK_URL，跳过发送")
        return False

    try:
        data = {
            "msgtype": "text",
            "text": {
                "content": f"[ETH量化] {datetime.now().strftime('%H:%M:%S')}\n{message}"
            },
            "at": {
                "isAtAll": is_at_all
            }
        }

        resp = requests.post(DINGTALK_WEBHOOK, json=data, timeout=5)
        if resp.status_code == 200 and resp.json().get("errcode") == 0:
            logger.info("[DingTalk] 消息发送成功")
            return True
        else:
            logger.error(f"[DingTalk] 发送失败: {resp.text}")
            return False

    except Exception as e:
        logger.error(f"[DingTalk] 发送异常: {e}", exc_info=True)
        return False

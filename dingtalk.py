#!/usr/bin/env python3
# dingtalk.py（加强版 - 更好错误处理 + 详细日志）

import logging
import time
import hmac
import hashlib
import base64
import urllib.parse
import requests
from config import Config

logger = logging.getLogger(__name__)


def send_dingtalk_message(content: str, level: str = "INFO") -> bool:
    """
    发送钉钉消息（加强版）
    支持加签（secret）和普通 webhook
    """
    webhook_url = Config.DINGTALK_WEBHOOK
    secret = Config.DINGTALK_SECRET

    if not webhook_url:
        logger.warning("[DingTalk] 未配置 DINGTALK_WEBHOOK，跳过发送")
        return False

    try:
        # 如果配置了 secret，则进行加签
        if secret:
            timestamp = str(round(time.time() * 1000))
            string_to_sign = f"{timestamp}\n{secret}"
            hmac_code = hmac.new(
                secret.encode("utf-8"),
                string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256
            ).digest()
            sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
            url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"
        else:
            url = webhook_url

        headers = {"Content-Type": "application/json"}
        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"交易系统通知 - {level}",
                "text": content
            }
        }

        response = requests.post(url, headers=headers, json=data, timeout=10)

        if response.status_code == 200:
            result = response.json()
            if result.get("errcode") == 0:
                logger.info("[DingTalk] 消息发送成功")
                return True
            else:
                logger.error(f"[DingTalk] 发送失败: {result}")
                return False
        else:
            logger.error(f"[DingTalk] HTTP请求失败: {response.status_code} - {response.text}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"[DingTalk] 网络请求异常: {e}")
        return False
    except Exception as e:
        logger.error(f"[DingTalk] 发送异常: {e}")
        return False

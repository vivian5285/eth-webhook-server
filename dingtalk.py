#!/usr/bin/env python3
# dingtalk.py（修复版 - 签名逻辑优化）

import logging
import time
import hmac
import hashlib
import base64
import urllib.parse
import requests
from config import Config

logger = logging.getLogger(__name__)


def send_dingtalk_message(content: str, title: str = "交易系统通知") -> bool:
    """
    发送钉钉消息（修复版）
    支持加签和不加签两种模式
    """
    webhook_url = Config.DINGTALK_WEBHOOK
    secret = Config.DINGTALK_SECRET

    if not webhook_url:
        logger.warning("[DingTalk] 未配置 DINGTALK_WEBHOOK，跳过发送")
        return False

    try:
        # 构造请求 URL
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

        headers = {"Content-Type": "application/json; charset=utf-8"}
        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": content
            }
        }

        response = requests.post(url, headers=headers, json=data, timeout=8)

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

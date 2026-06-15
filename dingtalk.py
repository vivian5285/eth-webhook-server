#!/usr/bin/env python3
# dingtalk.py（最终兼容版）

import os
import time
import hmac
import hashlib
import base64
import requests
from urllib.parse import quote_plus

from config import Config


def send_dingtalk_message(content: str, title: str = "交易提醒"):
    """发送钉钉通知"""
    webhook = Config.DINGTALK_WEBHOOK
    secret = Config.DINGTALK_SECRET

    if not webhook:
        print(f"[DingTalk] 未配置 webhook，跳过通知: {content}")
        return

    try:
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f'{timestamp}\n{secret}'
        string_to_sign_enc = string_to_sign.encode('utf-8')
        secret_enc = secret.encode('utf-8')
        hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
        sign = quote_plus(base64.b64encode(hmac_code))

        url = f"{webhook}&timestamp={timestamp}&sign={sign}"

        data = {
            "msgtype": "text",
            "text": {
                "content": f"{title}\n{content}"
            }
        }

        resp = requests.post(url, json=data, timeout=5)
        if resp.status_code == 200:
            print("[DingTalk] 通知发送成功")
        else:
            print(f"[DingTalk] 发送失败: {resp.text}")
    except Exception as e:
        print(f"[DingTalk] 发送异常: {e}")

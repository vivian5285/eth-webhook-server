# dingtalk.py
import requests
import time
import hmac
import hashlib
import base64
import urllib.parse
from config import Config

def send_dingtalk(content: str, is_warning: bool = False):
    if not Config.DINGTALK_WEBHOOK:
        print("[DingTalk] 未配置 webhook，跳过发送")
        return

    webhook = Config.DINGTALK_WEBHOOK
    secret = Config.DINGTALK_SECRET

    # 加签处理
    if secret:
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f'{timestamp}\n{secret}'
        hmac_code = hmac.new(secret.encode(), string_to_sign.encode(), digestmod=hashlib.sha256).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        webhook = f"{webhook}&timestamp={timestamp}&sign={sign}"

    data = {
        "msgtype": "text",
        "text": {
            "content": content
        }
    }

    try:
        resp = requests.post(webhook, json=data, timeout=10)
        print(f"[DingTalk] 发送状态: {resp.status_code}")
    except Exception as e:
        print(f"[DingTalk] 发送失败: {e}")

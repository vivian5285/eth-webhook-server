# dingtalk.py（最终美化版）
import time
import hmac
import hashlib
import base64
import requests
import logging
from config import Config

def send_dingtalk(title: str, content: str, is_warning: bool = False):
    """
    发送美化版钉钉消息
    """
    webhook = Config.DINGTALK_WEBHOOK
    secret = Config.DINGTALK_SECRET

    if not webhook:
        logging.warning("[DingTalk] 未配置 webhook，跳过发送")
        return

    try:
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(
            secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256
        ).digest()
        sign = base64.b64encode(hmac_code).decode("utf-8")

        url = f"{webhook}&timestamp={timestamp}&sign={sign}"

        # 美化内容
        emoji = "🚨" if is_warning else "✅"
        markdown_text = f"### {emoji} {title}\n\n{content}"

        data = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": markdown_text
            }
        }

        resp = requests.post(url, json=data, timeout=10)
        if resp.status_code == 200 and resp.json().get("errcode") == 0:
            logging.info(f"[DingTalk] 发送成功: {title}")
        else:
            logging.error(f"[DingTalk] 发送失败: {resp.text}")

    except Exception as e:
        logging.error(f"[DingTalk] 发送异常: {e}")

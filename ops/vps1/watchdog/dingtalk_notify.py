#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
独立钉钉群机器人发送器——只给watchdog项目用，跟主程序dingtalk.py
（DINGTALK_DISABLE=True，已停用）完全无关，不共用任何配置。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import urllib.parse

import requests

WEBHOOK = os.getenv("WATCHDOG_DINGTALK_WEBHOOK", "")
SECRET = os.getenv("WATCHDOG_DINGTALK_SECRET", "")


def _signed_url() -> str:
    if not WEBHOOK:
        return ""
    if not SECRET:
        return WEBHOOK
    ts = str(round(time.time() * 1000))
    string_to_sign = f"{ts}\n{SECRET}"
    hmac_code = hmac.new(
        SECRET.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    sep = "&" if "?" in WEBHOOK else "?"
    return f"{WEBHOOK}{sep}timestamp={ts}&sign={sign}"


def send_text(text: str, at_all: bool = False) -> bool:
    """发送纯文本消息。返回是否成功（网络/配置问题都返回False，不抛异常，避免watchdog自己也崩）。"""
    url = _signed_url()
    if not url:
        print(f"[watchdog-dingtalk] 未配置webhook，跳过发送: {text[:80]}")
        return False
    payload = {
        "msgtype": "text",
        "text": {"content": text},
        "at": {"isAtAll": bool(at_all)},
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get("errcode") == 0:
            return True
        print(f"[watchdog-dingtalk] 发送失败: {data}")
        return False
    except Exception as e:
        print(f"[watchdog-dingtalk] 发送异常: {e}")
        return False


if __name__ == "__main__":
    ok = send_text("【watchdog】测试消息——独立监控项目钉钉通道已连通 ✅")
    print("发送结果:", ok)

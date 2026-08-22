#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
广播网关快速探活（独立于主监督狗的10分钟周期）。

背景：网关是唯一TV入口后，systemd 的 Restart=always 只能抓"进程崩溃"，
抓不住"进程还活着但卡死不响应"（比如线程死锁）；主监督狗check.py要等
10分钟一轮的周期才会查到网关健康，最坏情况下网关挂死10分钟都没人知道。
这个脚本由 gateway-heartbeat.timer 每60秒跑一次，独立、更快：
  1. 探活 /health，成功就安静退出，不产生日志噪音
  2. 失败 → 尝试 systemctl restart 自愈，2秒后复查
  3. 复查仍失败 → 钉钉紧急告警（网关没自愈，需要人工介入）
  4. 复查恢复了 → 也发一条钉钉（不是紧急级别，但让人知道发生过一次
     短暂失联+自动恢复，不能让问题悄悄发生又悄悄消失不留痕迹）

纯标准库实现，不依赖venv，跟gateway.py本身保持同一个零依赖原则。
"""
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HEALTH_URL = "http://127.0.0.1:5006/health"
SERVICE_NAME = "binance-gateway.service"
CHECK_TIMEOUT_SEC = 5
RECHECK_DELAY_SEC = 2

DINGTALK_WEBHOOK = os.getenv("WATCHDOG_DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("WATCHDOG_DINGTALK_SECRET", "")


def _dingtalk_signed_url() -> str:
    if not DINGTALK_WEBHOOK:
        return ""
    if not DINGTALK_SECRET:
        return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    string_to_sign = f"{ts}\n{DINGTALK_SECRET}"
    hmac_code = hmac.new(
        DINGTALK_SECRET.encode("utf-8"), string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    sep = "&" if "?" in DINGTALK_WEBHOOK else "?"
    return f"{DINGTALK_WEBHOOK}{sep}timestamp={ts}&sign={sign}"


def _dingtalk_send(text: str) -> None:
    url = _dingtalk_signed_url()
    if not url:
        print(f"[heartbeat] 未配置钉钉webhook，跳过发送: {text[:80]}")
        return
    payload = json.dumps({
        "msgtype": "text", "text": {"content": text}, "at": {"isAtAll": False},
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[heartbeat] 钉钉发送异常: {e}")


def _check_health() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=CHECK_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return resp.status == 200 and data.get("status") == "ok"
    except Exception:
        return False


def main() -> int:
    if _check_health():
        return 0  # 健康，安静退出，不留日志噪音

    print("[heartbeat] 网关探活失败，尝试 systemctl restart 自愈")
    try:
        subprocess.run(
            ["systemctl", "restart", SERVICE_NAME],
            capture_output=True, timeout=15,
        )
    except Exception as e:
        print(f"[heartbeat] 重启命令执行异常: {e}")

    time.sleep(RECHECK_DELAY_SEC)
    if _check_health():
        print("[heartbeat] 自愈成功")
        _dingtalk_send(
            "【广播网关】检测到短暂失联，已自动 restart 并恢复正常——"
            "不是紧急问题，但这段窗口内的TV信号可能漏了，建议核对一下"
        )
        return 0

    print("[heartbeat] 自愈失败，网关仍无响应")
    _dingtalk_send(
        f"🚨【广播网关】探活失败，已尝试 systemctl restart {SERVICE_NAME} 仍无响应！"
        f"所有走网关的TV信号现在都到不了账户，需要立即人工介入排查"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())

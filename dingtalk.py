#!/usr/bin/env python3
# dingtalk.py（加签 + 极致美观详细版 - 2026-06-15）
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
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")


def _generate_sign(secret: str) -> tuple:
    """生成加签 timestamp + sign"""
    timestamp = str(round(time.time() * 1000))
    secret_enc = secret.encode('utf-8')
    string_to_sign = f'{timestamp}\n{secret}'
    string_to_sign_enc = string_to_sign.encode('utf-8')
    hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def _get_signed_url() -> str:
    """返回带加签的完整 URL"""
    if not DINGTALK_WEBHOOK:
        return ""
    if DINGTALK_SECRET:
        timestamp, sign = _generate_sign(DINGTALK_SECRET)
        return f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"
    return DINGTALK_WEBHOOK


def send_dingtalk_message(message: str, is_at_all: bool = False):
    """通用发送函数（保留兼容）"""
    if not DINGTALK_WEBHOOK:
        logger.warning("[DingTalk] 未配置 DINGTALK_WEBHOOK_URL，跳过发送")
        return False

    try:
        url = _get_signed_url()
        if not url:
            logger.warning("[DingTalk] Webhook URL 无效")
            return False

        data = {
            "msgtype": "text",
            "text": {
                "content": f"🤖 [ETH量化系统] {datetime.now().strftime('%m-%d %H:%M:%S')}\n{message}"
            },
            "at": {"isAtAll": is_at_all}
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


# ==================== 专业报告函数 ====================

def report_open_position(side: str, price: float, qty: float, notional: float, order_id: str = ""):
    """开仓成功报告"""
    msg = (
        f"✅ 【开仓成功】{side}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📍 价格: {price}\n"
        f"📦 数量: {qty}\n"
        f"💰 名义金额: {notional:.2f} USDT\n"
        f"🆔 订单ID: {order_id}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )
    send_dingtalk_message(msg)


def report_close_position(side: str, reason: str, pnl: float = 0):
    """平仓报告"""
    emoji = "🔴" if side == "LONG" else "🟢"
    msg = (
        f"{emoji} 【平仓完成】{side}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"原因: {reason}\n"
        f"盈亏: {pnl:+.2f} USDT\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🕐 {datetime.now().strftime('%H:%M:%S')}"
    )
    send_dingtalk_message(msg)


def report_verification_success(expected: str, actual: str, qty: float):
    """实盘核实通过"""
    msg = (
        f"✅ 【实盘核实通过】\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"信号方向: {expected}\n"
        f"实盘方向: {actual}\n"
        f"持仓数量: {qty}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"系统动作已与TV信号一致"
    )
    send_dingtalk_message(msg)


def report_force_align(old_side: str, new_side: str):
    """强制对齐报告"""
    msg = (
        f"⚠️ 【强制对齐执行】\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"原持仓方向: {old_side}\n"
        f"已强制平掉并重开: {new_side}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"监督层已完成方向修正"
    )
    send_dingtalk_message(msg)


def report_anomaly(message: str):
    """异常提醒"""
    msg = (
        f"🚨 【系统异常提醒】\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{message}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"请立即检查实盘！"
    )
    send_dingtalk_message(msg, is_at_all=True)


def report_risk_trigger(message: str):
    """风控触发提醒"""
    msg = (
        f"🛑 【风控触发】\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{message}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"已拒绝新开仓信号"
    )
    send_dingtalk_message(msg, is_at_all=True)

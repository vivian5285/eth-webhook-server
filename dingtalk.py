#!/usr/bin/env python3
# dingtalk.py (V5.0 限价单刺客流·专属战报版)
import os
import json
import time
import hmac
import hashlib
import base64
import urllib.parse
import requests
import logging
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))
logger = logging.getLogger(__name__)

# 获取钉钉配置
DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")

def generate_sign(secret: str, timestamp: str) -> str:
    secret_enc = secret.encode('utf-8')
    string_to_sign = '{}\n{}'.format(timestamp, secret)
    string_to_sign_enc = string_to_sign.encode('utf-8')
    hmac_code = hmac.new(secret_enc, string_to_sign_enc, digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return sign

def send_markdown_message(title: str, text: str):
    """最底层的通用 Markdown 发送通道，供新版哨兵直接调用"""
    if not DINGTALK_WEBHOOK:
        logger.warning("未配置 DINGTALK_WEBHOOK，跳过发送钉钉消息。")
        return

    timestamp = str(round(time.time() * 1000))
    url = DINGTALK_WEBHOOK
    if DINGTALK_SECRET:
        sign = generate_sign(DINGTALK_SECRET, timestamp)
        url = f"{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}"

    headers = {'Content-Type': 'application/json'}
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": text
        }
    }
    
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=5)
        if response.status_code == 200:
            logger.info(f"[DingTalk] 战报发送成功: {title}")
        else:
            logger.error(f"[DingTalk] 战报发送失败: {response.text}")
    except Exception as e:
        logger.error(f"[DingTalk] 请求异常: {e}")

# ==================== 场景化战报模板 ====================

def report_supervisor_open(side: str, entry_price: float, qty: float, tp_dict: dict, account_info: dict):
    emoji = "🟩" if side == "LONG" else "🟥"
    side_str = "做多 (LONG)" if side == "LONG" else "做空 (SHORT)"
    
    balance = account_info.get("balance", 0.0)
    equity = account_info.get("equity", 0.0)
    
    text = f"""### 🚀 刺客流新开仓核实报告
> **实盘完全吃单，限价止盈网已撒下！**

📍 **实盘持仓核实 (单向一手)**
- **交易方向**: {emoji} {side_str}
- **实盘入场**: `{entry_price}` USDT
- **确认仓位**: `{qty}` ETH (严格防滑点模型)

🎯 **交易所限价单挂载完毕 (12/25/50)**
- **TP1 (40%)**: `{tp_dict.get('tp1')}`
- **TP2 (40%)**: `{tp_dict.get('tp2')}`
- **TP3 (20%)**: `{tp_dict.get('tp3')}`

📊 **当前账户风控状态**
- **可用保证金**: `{balance:.2f}` USDT
- **账户总权益**: `{equity:.2f}` USDT

*🤖 币安战神 V5.0 · 撮合引擎直连极限刺客*
"""
    send_markdown_message("新开仓实盘核实报告", text)

def report_supervisor_close(side: str, reason: str, real_pnl: float, account_info: dict):
    pnl_str = f"+{real_pnl:.2f} USDT" if real_pnl > 0 else f"{real_pnl:.2f} USDT"
    emoji = "💰" if real_pnl > 0 else "🩸"
    
    text = f"""### 🧹 阵地焦土清算报告
> **旧有挂单与仓位已被铁血抹除！**

- **清理动作**: {reason}
- **原持仓方向**: {side}
- **该笔实盘 PnL**: {emoji} **{pnl_str}**

*🤖 币安战神 V5.0 · 强制纯净模块*
"""
    send_markdown_message("阵地焦土清算", text)

def report_anomaly(reason: str):
    text = f"""### 🚨 异常或熔断拦截报告
- **拦截原因**: {reason}
- **系统状态**: 信号已被直接丢弃，不执行任何仓位动作。
"""
    send_markdown_message("风控拦截", text)

def report_force_align(real_side: str, expected_side: str):
    text = f"""### ⚠️ 强制对齐警报
- **侦测冲突**: 实盘方向为 `{real_side}`，但主逻辑要求 `{expected_side}`！
- **执行动作**: 正在启动紧急平仓，强制对齐 TV 信号防线！
"""
    send_markdown_message("强制对齐", text)

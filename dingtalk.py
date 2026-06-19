#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
币安 (Binance) 专属战报系统 V10.0
核心特性：ATR 自适应防线、保本移动状态机播报
"""
import os, time, hmac, hashlib, base64, urllib.parse, logging, requests
from datetime import datetime
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))
logger = logging.getLogger(__name__)

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")

def _get_signed_url():
    if not DINGTALK_SECRET: return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    hmac_code = hmac.new(DINGTALK_SECRET.encode('utf-8'), f'{ts}\n{DINGTALK_SECRET}'.encode('utf-8'), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return f"{DINGTALK_WEBHOOK}&timestamp={ts}&sign={sign}"

def send_alert(title, data_dict):
    text = "\n".join([f"- **{k}**: {v}" for k, v in data_dict.items()])
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": f"### {title}\n> **⏱ 战神核对**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n{text}\n\n---\n*🤖 Binance 万亿战神 V10.0 (机构级自适应版)*"
        }
    }
    try: requests.post(_get_signed_url(), json=payload, timeout=5)
    except Exception as e: logger.error(f"钉钉发送失败: {e}")

def report_supervisor_open(side, price, qty, tp_pxs, sl_px, atr):
    emoji = "🟩" if side == "LONG" else "🟥"
    send_alert("⚔️ 现价吃单完毕 (ATR动态防线)", {
        "防守方向": f"{emoji} {side}",
        "实盘均价": f"`{price:.2f}`",
        "吃单头寸": f"`{qty}` ETH",
        "真实波动(ATR)": f"`{atr:.2f}` 美金",
        "自适应止盈 (1.28/2.5/3.6X)": f"`{tp_pxs[0]}` | `{tp_pxs[1]}` | `{tp_pxs[2]}`",
        "初始止损 (0.92X)": f"`{sl_px:.2f}`"
    })

def report_intervention(qty, entry_px, new_tp, new_sl, action_msg):
    send_alert("⚠️ 雷达异动：触发自适应重装", {
        "触发原因": "止盈落袋，或遭遇人工干预 (加减仓)",
        "当前残余头寸": f"`{qty}` ETH",
        "更新后均价": f"`{entry_px:.2f}`",
        "止损状态更新": f"**{action_msg}**",
        "兜底限价止盈 (3.6X)": f"`{new_tp:.2f}`",
        "当前条件止损": f"`{new_sl:.2f}`"
    })

def report_force_align(real_side, expected_side):
    send_alert("🚨 严重违纪事件：触发铁血镇压", {
        "实盘方向": real_side,
        "TV应有方向": expected_side,
        "处理结果": "已强行平掉与策略相悖的持仓，坚决对齐大盘信号！"
    })

def report_supervisor_close(reason):
    send_alert("🧹 阵地彻底清算", {"触发机制": reason, "当前状态": "挂单全撤，仓位全平，资金回炉待命中"})

def report_system_alert(title, detail):
    send_alert(f"⚠️ 系统告警: {title}", {"详情": detail, "状态": "请管理员介入检查"})

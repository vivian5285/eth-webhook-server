#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, time, hmac, hashlib, base64, urllib.parse, logging, requests
from datetime import datetime
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))
logger = logging.getLogger(__name__)

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")

def _get_signed_url():
    if not DINGTALK_WEBHOOK: return ""
    if not DINGTALK_SECRET: return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    hmac_code = hmac.new(DINGTALK_SECRET.encode('utf-8'), f'{ts}\n{DINGTALK_SECRET}'.encode('utf-8'), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return f"{DINGTALK_WEBHOOK}&timestamp={ts}&sign={sign}"

def send_alert(title, data_dict):
    signed_url = _get_signed_url()
    if not signed_url: return
    
    text = "\n".join([f"* **{k}**: {v}" for k, v in data_dict.items()])
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": f"## {title}\n***\n> **⏱ 战神核对**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n{text}\n\n***\n*🤖 战神 V10.6 终极纯执行大脑*"
        }
    }
    try: 
        requests.post(signed_url, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"钉钉发送失败: {e}")

def report_deepcoin_open(side, price, qty, tp_pxs, sl_px, atr, old_qty=0):
    emoji = "🟩" if side == "LONG" else "🟥"
    clean_msg = "✅ 纯净新开 (旧仓已归零)" if old_qty == 0 else f"🚨 战阵反转 (强平旧仓 {old_qty} 张)"

    send_alert("⚔️ 深币现价吃单 (V10.6 完美透传)", {
        "防守方向": f"**{emoji} {side}**",
        "实盘均价": f"`{price:.2f}` USDT",
        "动态头寸": f"`{qty}` 张 (30/30/40 阶梯网格)",
        "状态反馈": f"**{clean_msg}**",
        "真实波动(ATR)": f"`{atr:.2f}`",
        "自适应止盈": f"`{tp_pxs[0]}` ｜ `{tp_pxs[1]}` ｜ `{tp_pxs[2]}`",
        "初始止损": f"**`{sl_px:.2f}`**"
    })

def report_supervisor_open(side, price, qty, tp_pxs, sl_px, atr):
    emoji = "🟩" if side == "LONG" else "🟥"
    send_alert("⚔️ 币安现价吃单 (V10.6 战略同步)", {
        "防守方向": f"**{emoji} {side}**",
        "实盘均价": f"`{price:.2f}` USDT",
        "动态头寸": f"`{qty}` ETH",
        "真实波动(ATR)": f"`{atr:.2f}`",
        "自适应止盈": f"`{tp_pxs[0]}` ｜ `{tp_pxs[1]}` ｜ `{tp_pxs[2]}`",
        "初始止损": f"**`{sl_px:.2f}`**"
    })

def report_intervention(qty, entry_px, new_tp, new_sl, action_msg):
    send_alert("⚠️ 雷达动态追踪防御", {
        "残余头寸": f"`{qty}`",
        "入场均价": f"`{entry_px:.2f}`",
        "雷达响应": f"**{action_msg}**",
        "最新安全止损": f"**`{new_sl:.2f}`**"
    })

def report_force_align(real_side, expected_side):
    send_alert("🚨 严重违纪：触发铁血对齐", {
        "实盘发现方向": f"`{real_side}`",
        "TV应有方向": f"`{expected_side}`",
        "处理结果": "**已执行物理级清仓，坚决对齐信号源！**"
    })

def report_deepcoin_clear(reason):
    send_alert("🧹 深币阵地彻底清盘", {
        "触发机制": f"*{reason}*",
        "当前状态": "**挂单全撤，仓位全平，资金回炉待命**"
    })

def report_supervisor_close(reason):
    send_alert("🧹 币安阵地彻底清盘", {
        "触发机制": f"*{reason}*",
        "当前状态": "**挂单全撤，仓位全平，资金回炉待命**"
    })

def report_system_alert(title, detail):
    send_alert(f"⚠️ 系统风险告警: {title}", {
        "核心详情": f"**{detail}**"
    })

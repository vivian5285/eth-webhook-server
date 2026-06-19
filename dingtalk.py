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
    if not DINGTALK_SECRET: return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    hmac_code = hmac.new(DINGTALK_SECRET.encode('utf-8'), f'{ts}\n{DINGTALK_SECRET}'.encode('utf-8'), hashlib.sha256).digest()
    return f"{DINGTALK_WEBHOOK}&timestamp={ts}&sign={urllib.parse.quote_plus(base64.b64encode(hmac_code))}"

def send_alert(title, data_dict):
    text = "\n".join([f"- **{k}**: {v}" for k, v in data_dict.items()])
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": f"### {title}\n> **⏱ 战神核对**：{datetime.now().strftime('%m-%d %H:%M:%S')}\n\n{text}\n\n---\n*🤖 Binance 万亿战神 V6.0 摄政王系统*"
        }
    }
    try: requests.post(_get_signed_url(), json=payload, timeout=5)
    except Exception as e: logger.error(f"钉钉发送失败: {e}")

def report_supervisor_open(side, price, qty, tp_pxs, sl_px):
    emoji = "🟩" if side == "LONG" else "🟥"
    send_alert("⚔️ 战神已吃单 (附带铁血止损)", {
        "防守方向": f"{emoji} {side}",
        "实盘均价": f"`{price:.2f}`",
        "标准头寸": f"`{qty}` ETH",
        "三阶止盈 (12/25/50)": f"`{tp_pxs[0]}` | `{tp_pxs[1]}` | `{tp_pxs[2]}`",
        "绝境止损 (20U限额)": f"`{sl_px:.2f}`"
    })

def report_intervention(qty, entry_px, tp_pxs, new_sl):
    send_alert("⚠️ 察觉仓位异动：哨兵自愈重新布防", {
        "触发原因": "检测到阶段止盈落袋，或人工干预加减仓",
        "残余头寸": f"`{qty}` ETH",
        "最新均价": f"`{entry_px:.2f}`",
        "动作": "已撤销旧单，重新铺设专属自愈防线",
        "新限价止损": f"`{new_sl:.2f}`"
    })

def report_force_align(real_side, expected_side):
    send_alert("🚨 严重违纪：强制对齐", {
        "实盘方向": real_side,
        "TV应有方向": expected_side,
        "惩罚措施": "已强行斩仓，并强制对齐大盘信号！"
    })

def report_supervisor_close(side, reason, pnl, account):
    send_alert("🧹 阵地彻底清盘", {"触发原因": reason, "当前状态": "挂单全撤，仓位全平，资金回炉待命"})

def report_anomaly(reason):
    send_alert("⛔ 风控拦截", {"原因": reason})

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, time, hmac, hashlib, base64, urllib.parse, logging, requests
from datetime import datetime
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))
logger = logging.getLogger(__name__)

WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
SECRET = os.getenv("DINGTALK_SECRET", "")

def _get_signed_url():
    if not SECRET: return WEBHOOK
    ts = str(round(time.time() * 1000))
    hmac_code = hmac.new(SECRET.encode('utf-8'), f'{ts}\n{SECRET}'.encode('utf-8'), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return f"{WEBHOOK}&timestamp={ts}&sign={sign}"

def send_alert(title, data_dict):
    text = "\n".join([f"- **{k}**: {v}" for k, v in data_dict.items()])
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": f"### {title}\n> **⏱ 战神核对**：{datetime.now().strftime('%m-%d %H:%M:%S')}\n\n{text}\n\n---\n*🤖 万亿战神 V9.0 (全域对齐版)*"
        }
    }
    try: requests.post(_get_signed_url(), json=payload, timeout=5)
    except Exception as e: logger.error(f"钉钉发送失败: {e}")

# ======= 深币 (Deepcoin) 专属汇报 =======
def report_deepcoin_open(side, price, qty, tp1, tp2, sl):
    emoji = "🟩" if side == "LONG" else "🟥"
    send_alert("⚔️ 深币现价吃单 (偶数复利)", {
        "防守方向": f"{emoji} {side}", "实盘均价": f"`{price:.2f}`", "吃单头寸": f"`{qty}` 张",
        "止盈网 (7 / 15)": f"`{tp1:.2f}` | `{tp2:.2f}`", "条件止损 (20价差)": f"`{sl:.2f}`"
    })

def report_deepcoin_clear(reason):
    send_alert("🧹 深币阵地清算", {"触发机制": reason, "当前状态": "挂单全撤，仓位全平"})

# ======= 币安 (Binance) 专属汇报 =======
def report_supervisor_open(side, price, qty, tp_pxs, sl_px):
    emoji = "🟩" if side == "LONG" else "🟥"
    send_alert("⚔️ 币安现价吃单 (精度护甲)", {
        "防守方向": f"{emoji} {side}", "实盘均价": f"`{price:.2f}`", "吃单头寸": f"`{qty}` ETH",
        "止盈网 (7/15/40)": f"`{tp_pxs[0]}` | `{tp_pxs[1]}` | `{tp_pxs[2]}`", "市价止损 (30价差)": f"`{sl_px:.2f}`"
    })

def report_supervisor_close(reason):
    send_alert("🧹 币安阵地清算", {"触发机制": reason, "当前状态": "挂单全撤，仓位全平"})

# ======= 全域通用汇报 =======
def report_intervention(qty, entry_px, new_tp, new_sl):
    send_alert("⚠️ 察觉雷达异动：触发防线自愈", {
        "触发原因": "检测到止盈落袋，或遭遇人工干预",
        "真实残余头寸": f"`{qty}`", "更新均价": f"`{entry_px:.2f}`",
        "兜底限价止盈": f"`{new_tp:.2f}`", "全新条件止损": f"`{new_sl:.2f}`"
    })

def report_force_align(real_side, expected_side):
    send_alert("🚨 严重违纪事件：触发铁血镇压", {
        "实盘方向": real_side, "TV应有方向": expected_side, "处理结果": "强行平掉违规持仓，强制对齐信号！"
    })

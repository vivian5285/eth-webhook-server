#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
币安 (Binance) 专属战报系统 V9.0
适配参数：7/15/40 止盈网，30美金价差市价止损
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
            "text": f"### {title}\n> **⏱ 战神核对**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n{text}\n\n---\n*🤖 Binance 万亿战神 V9.0 (全域自愈护甲版)*"
        }
    }
    try: requests.post(_get_signed_url(), json=payload, timeout=5)
    except Exception as e: logger.error(f"钉钉发送失败: {e}")

def report_supervisor_open(side, price, qty, tp_pxs, sl_px):
    emoji = "🟩" if side == "LONG" else "🟥"
    send_alert("⚔️ 现价吃单完毕 (币安专属防线)", {
        "防守方向": f"{emoji} {side}",
        "实盘均价": f"`{price:.2f}`",
        "吃单头寸": f"`{qty}` ETH",
        "三阶止盈网 (7/15/40)": f"`{tp_pxs[0]}`(30%) | `{tp_pxs[1]}`(30%) | `{tp_pxs[2]}`(40%)",
        "绝对防击穿止损 (30美金)": f"`{sl_px:.2f}`"
    })

def report_intervention(qty, entry_px, new_tp, new_sl):
    send_alert("⚠️ 察觉雷达异动：触发防线自愈", {
        "触发原因": "检测到某阶段止盈落袋，或遭遇人工加减仓干预",
        "当前真实残余头寸": f"`{qty}` ETH",
        "更新后底层均价": f"`{entry_px:.2f}`",
        "系统自愈动作": "已撤销错乱旧单，生成全新专属防线",
        "新统一限价止盈 (兜底40价差)": f"`{new_tp:.2f}`",
        "新绝对条件止损 (30价差)": f"`{new_sl:.2f}`"
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

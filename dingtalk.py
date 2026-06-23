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
    if not DINGTALK_WEBHOOK:
        return ""
    if not DINGTALK_SECRET:
        return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    hmac_code = hmac.new(DINGTALK_SECRET.encode('utf-8'), f'{ts}\n{DINGTALK_SECRET}'.encode('utf-8'), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return f"{DINGTALK_WEBHOOK}&timestamp={ts}&sign={sign}"

def send_alert(title, data_dict):
    signed_url = _get_signed_url()
    if not signed_url:
        return

    text = "\n".join([f"* **{k}**: {v}" for k, v in data_dict.items()])
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": f"## {title}\n***\n> **⏱ 战神核对**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n{text}\n\n***\n*🤖 战神 v6.9 最终自适应版*"
        }
    }
    try:
        requests.post(signed_url, json=payload, timeout=6)
    except Exception as e:
        logger.error(f"钉钉发送失败: {e}")

def get_regime_name(regime_code):
    if regime_code == 1: return "🧊 极弱震荡（保守防守）"
    if regime_code == 2: return "🚶 弱势波段（稳健为主）"
    if regime_code == 3: return "🏃 中势推升（均衡操作）"
    if regime_code == 4: return "🚀 强势单边（积极吃饱）"
    return "未知状态"

# ==================== 开仓报告 ====================
def report_supervisor_open(side, price, qty, tp_pxs, atr, regime=3):
    emoji = "🟩 多头" if side == "LONG" else "🟥 空头"
    tp_str = f"`{tp_pxs[0]:.2f}` → `{tp_pxs[1]:.2f}` → `{tp_pxs[2]:.2f}`"

    send_alert("⚔️ 币安现价开仓（最终自适应版）", {
        "防守方向": f"**{emoji}**",
        "市场强度": f"**{get_regime_name(regime)}**",
        "实盘均价": f"**`{price:.2f}`** USDT",
        "开仓数量": f"`{qty}` ETH",
        "止盈排队": tp_str,
        "ATR": f"`{atr:.2f}`",
        "策略逻辑": "四档位自适应 + 强势吃饱策略"
    })

# ==================== 动态保本 / 干预报告 ====================
def report_intervention(qty, entry_px, new_sl, action_msg):
    send_alert("🚀 雷达动态保本推移", {
        "残余头寸": f"`{qty}`",
        "入场均价": f"`{entry_px:.2f}`",
        "雷达动作": f"**{action_msg}**",
        "最新止损价": f"**`{new_sl:.2f}`**"
    })

# ==================== 强制对齐报告 ====================
def report_force_align(real_side, expected_side):
    send_alert("🚨 严重异常：强制物理对齐", {
        "实盘当前方向": f"`{real_side}`",
        "TV 期望方向": f"`{expected_side}`",
        "处理结果": "**已执行全平 + 撤单，强制对齐信号源**"
    })

# ==================== 清仓报告（增强区分） ====================
def report_supervisor_close(reason):
    if "TP3" in reason:
        title = "🎯 TP3 止盈全平"
        detail = f"**{reason}**（止盈目标达成）"
    elif "保护性全平" in reason or "反转保护" in reason or "RSI" in reason:
        title = "🛡️ 保护性全平"
        detail = f"**{reason}**（策略保护机制触发）"
    else:
        title = "🧹 仓位清盘"
        detail = f"**{reason}**"

    send_alert(title, {
        "触发原因": detail,
        "当前状态": "挂单已全部撤销，仓位已归零，资金回炉待命"
    })

# ==================== 系统告警 ====================
def report_system_alert(title, detail):
    send_alert(f"⚠️ 系统风险告警：{title}", {
        "核心详情": f"**{detail}**"
    })

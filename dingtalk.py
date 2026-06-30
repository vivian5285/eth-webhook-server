#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""币安专用钉钉 — 全金色主题，与深币紫金播报区分"""
import os
import time
import hmac
import hashlib
import base64
import urllib.parse
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))
logger = logging.getLogger(__name__)

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")

EXCHANGE_LABEL = "币安 Binance"
LEVERAGE_LABEL = "15x"
UNIT_LABEL = "ETH"

# 币安专属金色色板（与深币 #4B0082 紫金完全区分）
G_TITLE = "#F3BA2F"
G_MAIN = "#E8B923"
G_DEEP = "#B8860B"
G_LIGHT = "#FFE566"
G_ACCENT = "#F0B90B"
G_MUTED = "#C9A227"

FOOTER = "*🔶 Quant AI · 币安黄金趋势大波段引擎*"
VERIFY_TAG = "✅ 实盘核查通过"


def _g(text, color=G_MAIN):
    return f'<font color="{color}">{text}</font>'


def _get_signed_url():
    if not DINGTALK_WEBHOOK:
        return ""
    if not DINGTALK_SECRET:
        return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    hmac_code = hmac.new(
        DINGTALK_SECRET.encode('utf-8'),
        f'{ts}\n{DINGTALK_SECRET}'.encode('utf-8'),
        hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return f"{DINGTALK_WEBHOOK}&timestamp={ts}&sign={sign}"


def send_alert(title, data_dict, header_color=G_TITLE):
    signed_url = _get_signed_url()
    if not signed_url:
        return

    text_lines = [f"- **{k}** : {v}" for k, v in data_dict.items()]
    body_text = "\n".join(text_lines)
    now_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    markdown_text = f"""### <font color="{header_color}">{title}</font>
> **⏱ 军区时间**：`{now_time}`
> **📍 阵地标识**：[ {EXCHANGE_LABEL} · 主力进攻阵地 ]
> **🔶 主题色带**：`币安黄金`（与深币紫金播报区分）

---
{body_text}

---
{FOOTER}
"""
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": markdown_text}}
    try:
        requests.post(signed_url, json=payload, timeout=6)
    except Exception as e:
        logger.error(f"钉钉发送失败: {e}")


def get_regime_name(regime_code):
    names = {
        1: "🧊 [1档] 极弱震荡 (保守防守)",
        2: "🚶 [2档] 弱势波段 (稳健推升)",
        3: "🏃 [3档] 中势推升 (标准波段)",
        4: "🚀 [4档] 强势单边 (趋势吃满)",
    }
    shade = [G_MUTED, G_LIGHT, G_MAIN, G_ACCENT]
    idx = regime_code if 1 <= regime_code <= 4 else 0
    return _g(names.get(regime_code, "未知状态"), shade[idx - 1] if idx else G_MUTED)


def _format_tp_compare(tp_pxs, tv_tps=None):
    tp_str = ""
    for i, px in enumerate(tp_pxs):
        if px <= 0:
            continue
        prefix = "" if tp_str == "" else "\n\n  ➔ "
        line = f"{prefix}TP{i + 1} 物理挂单 `{px:.2f}`"
        if tv_tps and i < len(tv_tps) and tv_tps[i] > 0:
            diff = px - tv_tps[i]
            line += f" | TV理论 `{tv_tps[i]:.2f}` (偏差 {diff:+.2f})"
        tp_str += line
    return tp_str or "暂无有效 TP 价格"


def report_supervisor_open(side, entry_price, tv_price, qty, tp_pxs, atr, regime, tv_tps=None, verify_note=""):
    side_str = _g("🔶 开多 (LONG)", G_LIGHT) if side == "LONG" else _g("🟤 开空 (SHORT)", G_DEEP)
    slip_txt = (
        f"{(entry_price - tv_price if side == 'LONG' else tv_price - entry_price):+.2f} 刀"
        if tv_price > 0 else "未知"
    )

    data = {
        "🎛️ 趋势方向": side_str,
        "📊 市场强度": get_regime_name(regime),
        "💰 进场成本": _g(f"**{entry_price:.2f}** USDT (滑点: **{slip_txt}**)", G_MAIN),
        "📦 唯一头寸": _g(f"**{qty}** {UNIT_LABEL} ({EXCHANGE_LABEL} {LEVERAGE_LABEL} 稳健火力)", G_ACCENT),
        "🕸️ 止盈布防比对": _g(_format_tp_compare(tp_pxs, tv_tps), G_LIGHT),
        "📏 波动参考": _g(f"ATR = {atr:.4f}", G_MUTED),
        "📡 哨兵状态": _g(f"🟢 {VERIFY_TAG} | 限价 TP123 已挂，雷达待命", G_MAIN),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert("🔶 战神出击：币安大级别阵地建立", data)


def report_intervention(qty, entry_px, new_sl, action_msg, verify_note=""):
    data = {
        "🛡️ 战术动作": _g(action_msg, G_ACCENT),
        "📦 利润头寸": _g(f"`{qty}` {UNIT_LABEL}", G_MAIN),
        "💰 原始成本": _g(f"`{entry_px:.2f}` USDT", G_MUTED),
        "🔒 最新硬防线": _g(f"**{new_sl:.2f}** USDT (物理保本单已挂)", G_LIGHT),
        "📡 实盘核查": _g(f"{VERIFY_TAG} | 移动保本机制已触发", G_MAIN),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert("📈 捷报：追踪雷达锁死趋势利润", data, G_DEEP)


def report_manual_position_change(action_type, old_qty, new_qty, new_entry_price, verify_note=""):
    action_txt = _g("手动增仓", G_LIGHT) if "加仓" in action_type else _g("手动部分减仓", G_ACCENT)
    data = {
        "触发机制": _g("🛡️ 智慧大脑态势感知同步", G_MAIN),
        "实盘动作": action_txt,
        "数量变化": _g(f"`{old_qty}` ➔ `{new_qty}` {UNIT_LABEL}", G_ACCENT),
        "最新均价": _g(f"**{new_entry_price:.2f}** USDT", G_MAIN),
        "后续动作": _g(f"{VERIFY_TAG} | 已重挂最新比例限价 TP123", G_LIGHT),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert("🔄 币安阵地异动重置", data, G_ACCENT)


def report_force_align(real_side, expected_side, verify_note=""):
    data = {
        "🚨 异常状况": _g("**实盘方向与 TV 战略指令发生严重背离！**", G_DEEP),
        "🕵️ 现场方向": _g(real_side, G_ACCENT),
        "🧠 策略指令": _g(expected_side, G_LIGHT),
        "⚡ 仲裁结果": _g(f"{VERIFY_TAG} | 已核武全平，账本归零", G_MAIN),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert("🚨 严重警告：方向强行物理对齐", data, G_TITLE)


def report_supervisor_close(reason, verify_note=""):
    if "TP3" in reason or "止盈" in reason:
        title = "🏆 完美胜利：币安大趋势吃满收网"
        status = _g("三档网格已全部吃掉，暴利安全落袋。", G_LIGHT)
    elif "保护" in reason:
        title = "🛡️ 战术防守：保护平仓机制触发"
        status = _g("趋势警报解除，多空网格全撤，打扫战场空仓待命。", G_ACCENT)
    else:
        title = "🧹 先平后开 / 常规清场"
        status = _g("旧阵地已原子级爆破，账本归零等待新指令。", G_MUTED)

    data = {
        "📋 平仓原理解析": _g(f"**{reason}**", G_MAIN),
        "✅ 账本状态": status,
        "📡 实盘核查": _g(f"{VERIFY_TAG} | 盘口已无持仓", G_MAIN),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert(title, data)


def report_recover_takeover(side, qty, entry, tv_tps, regime, radar_active, sl_price,
                            verify_note="", tp_matched=0, tp_expected=0):
    radar_txt = (
        _g(f"已激活 (硬防线 `{sl_price:.2f}`)", G_LIGHT)
        if radar_active else _g("待命 (未达 TP1 激活阈值)", G_MUTED)
    )
    expected = tp_expected or sum(1 for t in tv_tps if t > 0)
    if expected > 0 and tp_matched >= expected:
        action_txt = f"{VERIFY_TAG} | 已撤旧单 → 补挂 TP123 → 恢复哨兵"
        action_color = G_MAIN
    elif tp_matched > 0:
        action_txt = f"⚠️ 部分成功 | 止盈 {tp_matched}/{expected} 档已挂 → 恢复哨兵"
        action_color = G_ACCENT
    elif expected > 0:
        action_txt = "❌ 止盈补挂失败 | 持仓已接管但限价 TP 未挂上，请人工核查"
        action_color = G_DEEP
    else:
        action_txt = f"{VERIFY_TAG} | 已撤旧单 → 恢复哨兵（无 TP 价格记录）"
        action_color = G_MAIN

    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "📦 核实头寸": _g(f"**{qty}** {UNIT_LABEL} @ `{entry:.2f}`", G_MAIN),
        "📊 恢复档位": get_regime_name(regime),
        "🕸️ TP123 布防": _g(_format_tp_compare(tv_tps, tv_tps), G_ACCENT),
        "📡 雷达状态": radar_txt,
        "✅ 接管动作": _g(action_txt, action_color),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert("🔄 币安 VPS 重启 · 闪电接管报告", data)


def report_system_alert(title, detail):
    send_alert(f"⚠️ 系统告警：{title}", {
        "⚠️ 告警级别": _g("最高级别 (CRITICAL)", G_DEEP),
        "📝 核心详情": _g(f"**{detail}**", G_ACCENT),
    }, G_TITLE)

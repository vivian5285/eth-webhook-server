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

def _green(text): return f'<font color="#00B050">{text}</font>'
def _red(text): return f'<font color="#FF3333">{text}</font>'
def _blue(text): return f'<font color="#0070C0">{text}</font>'
def _orange(text): return f'<font color="#F3BA2F">{text}</font>' 
def _gray(text): return f'<font color="#808080">{text}</font>'

def _get_signed_url():
    if not DINGTALK_WEBHOOK: return ""
    if not DINGTALK_SECRET: return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    hmac_code = hmac.new(DINGTALK_SECRET.encode('utf-8'), f'{ts}\n{DINGTALK_SECRET}'.encode('utf-8'), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return f"{DINGTALK_WEBHOOK}&timestamp={ts}&sign={sign}"

def send_alert(title, data_dict, header_color="#F3BA2F"):
    signed_url = _get_signed_url()
    if not signed_url: return

    text_lines = [f"- **{k}** : {v}" for k, v in data_dict.items()]
    body_text = "\n".join(text_lines)
    now_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    markdown_text = f"""### <font color="{header_color}">{title}</font>
> **⏱ 军区时间**：`{now_time}`
> **📍 阵地标识**：[ 币安 Binance · 主力进攻阵地 ]

---
{body_text}

---
*🔶 Quant AI 自动驾驶引擎*
"""
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": markdown_text}}
    try: requests.post(signed_url, json=payload, timeout=6)
    except Exception as e: logger.error(f"钉钉发送失败: {e}")

def get_regime_name(regime_code):
    if regime_code == 1: return _gray("🧊 [1档] 极弱震荡 (保守防守)")
    if regime_code == 2: return _blue("🚶 [2档] 弱势波段 (稳健推升)")
    if regime_code == 3: return _orange("🏃 [3档] 中势推升 (标准波段)")
    if regime_code == 4: return _green("🚀 [4档] 强势单边 (趋势吃满)")
    return "未知状态"

def report_supervisor_open(side, entry_price, tv_price, qty, tp_pxs, atr, regime, tv_tps=None):
    side_str = _green("🟩 开多 (LONG)") if side == "LONG" else _red("🟥 开空 (SHORT)")
    slip_txt = f"{(entry_price - tv_price if side == 'LONG' else tv_price - entry_price):+.2f} 刀" if tv_price > 0 else "未知"

    tp_str = ""
    for i in range(len(tp_pxs)):
        if tp_pxs[i] > 0:
            prefix = "" if tp_str == "" else "\n\n  ➔ "
            tv_val = f"(TV理论:`{tv_tps[i]:.2f}`)" if tv_tps and i < len(tv_tps) and tv_tps[i] > 0 else ""
            tp_str += f"{prefix}TP{i+1} 物理挂单 `{tp_pxs[i]:.2f}` {tv_val}"

    data = {
        "🎛️ 趋势方向": side_str,
        "📊 市场强度": get_regime_name(regime),
        "💰 进场成本": f"**{entry_price:.2f}** USDT (滑点: **{slip_txt}**)",
        "📦 唯一头寸": f"**{qty}** ETH (币安 20x 满血火力)",
        "🕸️ 止盈布防比对": _orange(tp_str),
        "📏 波动参考": _gray(f"ATR = {atr:.4f}"),
        "📡 哨兵状态": _blue("🟢 实盘核查：TP123限价网格已铺设，未设硬止损，雷达待命中！")
    }
    send_alert("🔶 战神出击：币安大级别阵地建立", data, header_color="#F3BA2F")

def report_intervention(qty, entry_px, new_sl, action_msg):
    send_alert("📈 捷报：追踪雷达锁死趋势利润", {
        "🛡️ 战术动作": _blue(action_msg),
        "📦 利润头寸": f"`{qty}` 张",
        "💰 原始成本": f"`{entry_px:.2f}` USDT",
        "🔒 最新硬防线": _green(f"**{new_sl:.2f}** USDT (物理保本单已挂)"),
        "📡 实盘核查": _blue("✅ 确认触发移动保本机制！实盘已挂载止损单锁定利润。")
    }, "#0070C0")

def report_manual_position_change(action_type, old_qty, new_qty, new_entry_price):
    action_color = _green("手动增仓") if "加仓" in action_type else _orange("手动部分减仓")
    send_alert("🔄 币安阵地异动重置", {
        "触发机制": _blue("🛡️ 智慧大脑态势感知同步"),
        "实盘动作": action_color,
        "数量变化": f"`{old_qty}` ➔ `{new_qty}`",
        "最新均价": f"**{new_entry_price:.2f}** USDT",
        "后续动作": "✅ 已无缝接管干预！强撤废旧单，重新铺设最新比例限价止盈！"
    }, "#FF9900")

def report_force_align(real_side, expected_side):
    send_alert("🚨 严重警告：方向强行物理对齐", {
        "🚨 异常状况": _red("**实盘方向与 TV 战略指令发生严重背离！**"),
        "🕵️ 现场方向": _red(real_side),
        "🧠 策略指令": _blue(expected_side),
        "⚡ 仲裁结果": _red("**已拒绝逆势持仓！核武全平完毕，强行保持干净状态。**")
    }, "#FF0000")

def report_supervisor_close(reason):
    if "TP3" in reason or "止盈" in reason: title, color, status = "🏆 完美胜利：大趋势吃满收网", "#00B050", "三档网格已全部吃掉，暴利安全落袋。"
    elif "保护" in reason: title, color, status = "🛡️ 战术防守：保护平仓机制触发", "#FF9900", "趋势警报解除，多空网格全撤，打扫战场空仓待命。"
    else: title, color, status = "🧹 先平后开 / 常规清场", "#808080", "旧阵地已原子级爆破，账本归零等待新指令。"

    send_alert(title, {"📋 平仓原理解析": f"**{reason}**", "✅ 账本状态": status}, color)

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
def _orange(text): return f'<font color="#FF9900">{text}</font>'
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
> **📍 策略节点**：[ 中海资本 · 币安万亿战神 20x 核心主阵地 ]

---
{body_text}

---
*🔶 Quant AI 趋势大波段·自动驾驶引擎*
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

# ==================== 开仓战报 (加入精准滑点计算) ====================
def report_supervisor_open(side, entry_price, tv_price, qty, tp_pxs, atr, regime, tv_tps=None):
    side_str = _green("🟩 现价做多 (LONG)") if side == "LONG" else _red("🟥 现价做空 (SHORT)")
    
    # 🎯 币安黄蓝版专属：滑点计算与网格交叉核对
    if tv_price > 0:
        slip = entry_price - tv_price if side == "LONG" else tv_price - entry_price
        slip_txt = f"{slip:+.2f} 刀"
    else:
        slip_txt = "未知 (TV未传价)"

    if tv_tps and len(tv_tps) == 3 and tv_tps[0] > 0:
        tp_str = f"TP1 `{tp_pxs[0]:.2f}` (TV:`{tv_tps[0]:.2f}`)\n\n" \
                 f"  ➔ TP2 `{tp_pxs[1]:.2f}` (TV:`{tv_tps[1]:.2f}`)\n\n" \
                 f"  ➔ TP3 `{tp_pxs[2]:.2f}` (TV:`{tv_tps[2]:.2f}`)"
    else:
        tp_str = f"TP1 `{tp_pxs[0]:.2f}` ➔ TP2 `{tp_pxs[1]:.2f}` ➔ TP3 `{tp_pxs[2]:.2f}`"

    data = {
        "🎛️ 趋势方向": side_str,
        "📊 市场强度": get_regime_name(regime),
        "💰 进场成本": f"**{entry_price:.2f}** USDT (滑点: **{slip_txt}**)",
        "📦 阵地头寸": f"**{qty}** ETH (20x 满血火力)",
        "🕸️ 止盈网格": _orange(tp_str),
        "📏 波动参考": _gray(f"ATR = {atr:.4f}"),
        "📡 哨兵状态": _blue("🟢 实盘核查：3挡实体限价单已挂载，初始硬止损隐身！")
    }
    send_alert("🔶 战神出击：币安趋势主阵地建立", data, header_color="#F3BA2F")

def report_intervention(qty, entry_px, new_sl, action_msg):
    data = {
        "🛡️ 战术动作": _blue(action_msg),
        "📦 利润头寸": f"`{qty}` ETH",
        "💰 原始成本": f"`{entry_px:.2f}` USDT",
        "🔒 最新止损": _green(f"**{new_sl:.2f}** USDT (已上移锁润)"),
        "📡 实盘核查": _blue("✅ 确认利润回吐雷达启动，物理保本单已推至盘口！")
    }
    send_alert("📈 捷报：追踪雷达锁死趋势利润", data, header_color="#0070C0")

def report_force_align(real_side, expected_side):
    send_alert("🚨 严重警告：方向强行物理对齐", {
        "🚨 异常状况": _red("**币安仓位与 TV 战略指令发生严重精神分裂！**"),
        "🕵️ 现场方向": _red(real_side),
        "🧠 策略指令": _blue(expected_side),
        "⚡ 仲裁结果": _red("**拒绝妥协！已完成物理级清仓，坚决对齐信号源！**")
    }, header_color="#FF0000")

def report_supervisor_close(reason):
    if "TP3" in reason:
        title = "🏆 完美胜利：币安大趋势吃满收网"
        header_color = "#00B050"
        color_reason = _green(f"**{reason}**")
        status = _green("TP3 终极网格全部吃掉，暴利已安全落袋。")
    elif "反转" in reason or "插针" in reason or "RSI" in reason or "保护" in reason:
        title = "🛡️ 战术防守：统一归一保护机制触发"
        header_color = "#FF9900"
        color_reason = _orange(f"**{reason}**")
        status = _gray("大级别防守警报到达，多空网格全撤，打扫战场待命。")
    elif "人工" in reason or "违规" in reason or "干预" in reason:
        title = "🛑 铁血截断：强制接管清盘"
        header_color = "#FF3333"
        color_reason = _red(f"**{reason}**")
        status = _red("系统已无情截断违规乱动，强制全平没收交易权限！")
    else:
        title = "🧹 换防清场：仓位已归零"
        header_color = "#808080"
        color_reason = _gray(f"**{reason}**")
        status = "交易所真实持仓已核实，账本完美归零。"

    send_alert(title, {"📋 触发归因": color_reason, "✅ 账本状态": status}, header_color=header_color)

def report_system_alert(title, detail):
    send_alert(f"⚠️ 系统熔断：{title}", {
        "⚠️ 告警级别": _red("最高级别 (CRITICAL)"),
        "📝 核心详情": _red(f"**{detail}**")
    }, header_color="#FF0000")

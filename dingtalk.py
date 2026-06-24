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

# ==================== Markdown 颜色渲染辅具 ====================
def _green(text): return f'<font color="#00B050">{text}</font>'
def _red(text): return f'<font color="#FF3333">{text}</font>'
def _blue(text): return f'<font color="#0070C0">{text}</font>'
def _orange(text): return f'<font color="#FF9900">{text}</font>'
def _gray(text): return f'<font color="#808080">{text}</font>'

def _get_signed_url():
    if not DINGTALK_WEBHOOK:
        return ""
    if not DINGTALK_SECRET:
        return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    hmac_code = hmac.new(DINGTALK_SECRET.encode('utf-8'), f'{ts}\n{DINGTALK_SECRET}'.encode('utf-8'), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return f"{DINGTALK_WEBHOOK}&timestamp={ts}&sign={sign}"

def send_alert(title, data_dict, header_color="#000000"):
    signed_url = _get_signed_url()
    if not signed_url:
        return

    # 构建高颜值 Markdown 文本
    text_lines = []
    for k, v in data_dict.items():
        text_lines.append(f"- **{k}** : {v}")
    
    body_text = "\n".join(text_lines)
    now_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    markdown_text = f"""### <font color="{header_color}">{title}</font>
> **⏱ 军区时间**：`{now_time}`
> **📍 策略节点**：[ 中海资本 · 万亿战神 v6.9.13 ]

---
{body_text}

---
*🤖 Quant AI 自动驾驶引擎持仓播报*
"""

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title, # 钉钉通知栏显示的标题
            "text": markdown_text
        }
    }
    try:
        requests.post(signed_url, json=payload, timeout=6)
    except Exception as e:
        logger.error(f"钉钉发送失败: {e}")

def get_regime_name(regime_code):
    if regime_code == 1: return _gray("🧊 [1档] 极弱震荡 (保守防守)")
    if regime_code == 2: return _blue("🚶 [2档] 弱势波段 (稳健为主)")
    if regime_code == 3: return _orange("🏃 [3档] 中势推升 (均衡操作)")
    if regime_code == 4: return _green("🚀 [4档] 强势单边 (积极吃饱)")
    return "未知状态"

# ==================== 开仓战报 (实盘 vs TV理论比对) ====================
def report_supervisor_open(side, price, qty, tp_pxs, atr, regime, tv_tps=None):
    side_str = _green("🟩 现价做多 (LONG)") if side == "LONG" else _red("🟥 现价做空 (SHORT)")
    
    # 🚀 展示实盘计算的 TP 与 TV理论传来的 TP 进行精准比对
    if tv_tps and len(tv_tps) == 3 and tv_tps[0] > 0:
        tp_str = f"TP1 `{tp_pxs[0]:.2f}` (TV:`{tv_tps[0]:.2f}`) ➔ TP2 `{tp_pxs[1]:.2f}` (TV:`{tv_tps[1]:.2f}`) ➔ TP3 `{tp_pxs[2]:.2f}` (TV:`{tv_tps[2]:.2f}`)"
    else:
        tp_str = f"TP1 `{tp_pxs[0]:.2f}` ➔ TP2 `{tp_pxs[1]:.2f}` ➔ TP3 `{tp_pxs[2]:.2f}`"

    data = {
        "🎛️ 交易方向": side_str,
        "📊 市场环境": get_regime_name(regime),
        "💰 进场均价": f"**{price:.2f}** USDT",
        "📦 部署数量": f"**{qty}** ETH",
        "🎯 实盘止盈阵列": _orange(tp_str),
        "📏 波动参考": _gray(f"ATR = {atr:.4f}")
    }
    send_alert("⚔️ 战神列阵：实盘建仓完毕", data, header_color="#000000")

# ==================== 动态保本 / 干预报告 ====================
def report_intervention(qty, entry_px, new_sl, action_msg):
    data = {
        "🛡️ 战术动作": _blue(action_msg),
        "📦 阵地头寸": f"`{qty}` ETH",
        "💰 入场成本": f"`{entry_px:.2f}` USDT",
        "🔒 最新防线": _green(f"**{new_sl:.2f}** USDT (已上移)")
    }
    send_alert("🚀 捷报：追踪止盈保本推移", data, header_color="#0070C0")

# ==================== 强制对齐报告 (极度危险警告) ====================
def report_force_align(real_side, expected_side):
    data = {
        "🚨 异常状况": _red("**实盘仓位与策略大脑发生严重精神分裂！**"),
        "🕵️ 实盘方向": _red(real_side),
        "🧠 策略指令": _blue(expected_side),
        "⚡ 仲裁结果": _red("**拒绝妥协！已执行物理斩仓，强行对齐信号源！**")
    }
    send_alert("🚨 严重警告：方向强行物理对齐", data, header_color="#FF0000")

# ==================== 清仓战报（智能语境分析） ====================
def report_supervisor_close(reason):
    if "TP3" in reason:
        title = "🏆 完美胜利：TP3 止盈收网"
        header_color = "#00B050"
        color_reason = _green(f"**{reason}**")
        status = _green("利润已全额落袋，资金回炉待命。")
    elif "保护" in reason or "反转" in reason or "RSI" in reason or "插针" in reason:
        title = "🛡️ 战术撤退：触发保护机制"
        header_color = "#FF9900"
        color_reason = _orange(f"**{reason}**")
        status = _gray("防守止盈/止损已触发，阵地已打扫干净。")
    elif "人工" in reason or "违规" in reason:
        title = "🛑 系统截断：拒绝人工干预"
        header_color = "#FF3333"
        color_reason = _red(f"**{reason}**")
        status = _red("系统已剥夺人工接管权限，执行强制物理清盘！")
    else:
        title = "🧹 阵地清场：仓位已归零"
        header_color = "#808080"
        color_reason = _gray(f"**{reason}**")
        status = "挂单已撤销，底层账本确认归零。"

    data = {
        "📋 触发归因": color_reason,
        "✅ 账本状态": status
    }
    send_alert(title, data, header_color=header_color)

# ==================== 系统底层风险告警 ====================
def report_system_alert(title, detail):
    data = {
        "⚠️ 告警级别": _red("最高级别 (CRITICAL)"),
        "📝 核心详情": _red(f"**{detail}**"),
        "🛠️ 建议动作": "请立即登录服务器或交易所APP复核状态！"
    }
    send_alert(f"⚠️ 系统熔断：{title}", data, header_color="#FF0000")

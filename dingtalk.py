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

def _purple(text): return f'<font color="#9B59B6">{text}</font>'
def _green(text): return f'<font color="#27AE60">{text}</font>'
def _red(text): return f'<font color="#E74C3C">{text}</font>'
def _blue(text): return f'<font color="#3498DB">{text}</font>'
def _orange(text): return f'<font color="#F39C12">{text}</font>' # 币安黄
def _gray(text): return f'<font color="#7F8C8D">{text}</font>'

def _get_signed_url():
    if not DINGTALK_WEBHOOK: return ""
    if not DINGTALK_SECRET: return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    hmac_code = hmac.new(DINGTALK_SECRET.encode('utf-8'), f'{ts}\n{DINGTALK_SECRET}'.encode('utf-8'), hashlib.sha256).digest()
    return f"{DINGTALK_WEBHOOK}&timestamp={ts}&sign={urllib.parse.quote_plus(base64.b64encode(hmac_code))}"

def send_alert(title, data_dict, header_color="#F39C12"):
    signed_url = _get_signed_url()
    if not signed_url: return
    body_text = "\n".join([f"- **{k}**: {v}" for k, v in data_dict.items()])
    # 专属币安大级别标签
    markdown_text = f"### <font color=\"{header_color}\">{title}</font>\n> **⏱ 军区时间**：`{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`  \n> **📍 阵地标识**：[ 币安 Binance · 150分钟四档位雷达版 V12.2 ]\n\n---\n{body_text}\n\n---\n*🖨️ Quant AI · 币安趋势波段收割机*"
    try: requests.post(signed_url, json={"msgtype": "markdown", "markdown": {"title": title, "text": markdown_text}}, timeout=6)
    except Exception as e: pass

def get_regime_name(regime_code):
    if regime_code == 1: return _gray("🧊 [1档] 极弱震荡")
    if regime_code == 2: return _blue("🚶 [2档] 弱势推升")
    if regime_code == 3: return _orange("🏃 [3档] 中势单边")
    if regime_code == 4: return _green("🚀 [4档] 强势主升")
    return "未知状态"

def report_binance_open(side, regime, atr, entry_price, tv_price, qty, tp_pxs, tv_tps):
    side_str = _green("🟩 开多 (LONG)") if side == "LONG" else _red("🟥 开空 (SHORT)")
    slip_txt = f"{(entry_price - tv_price if side == 'LONG' else tv_price - entry_price):+.2f} 刀" if tv_price > 0 else "未知"

    # 对比实盘挂单和TV理论值
    compare_str = ""
    for i in range(3):
        real_px = tp_pxs[i] if i < len(tp_pxs) else 0
        tv_px = tv_tps[i] if i < len(tv_tps) else 0
        prefix = "" if compare_str == "" else "\n  ➔ "
        compare_str += f"{prefix}TP{i+1}：实盘已挂 `{real_px:.2f}` (理论 `{tv_px:.2f}`)"

    send_alert("🔶 币安战神出击", {
        "🎛️ 持仓方向": side_str,
        "📊 市场强度": get_regime_name(regime),
        "💰 进场均价": f"**`{entry_price:.2f}`** USDT (滑点: **{slip_txt}**)",
        "📦 开仓数量": f"`{qty}` ETH（20x 唯一主仓位）",
        "📏 大级别ATR": _gray(f"{atr:.2f}"),
        "🕸️ 止盈布防比对": _orange(compare_str),
        "📡 系统动作": _blue("✅ 绝对净身入场！旧仓已洗清，限价止盈已挂网。")
    }, "#F39C12")

def report_radar_move(side, new_sl):
    send_alert("📈 币安雷达：锁润防线推升", {
        "方向": _green("多头") if side == "LONG" else _red("空头"),
        "最新止损单": _green(f"**{new_sl:.2f}** USDT"),
        "说明": _purple("趋势动能延续，硬止损已重新挂单至安全水位！")
    }, "#2980B9")

def report_manual_position_change(action_type, old_qty, new_qty, new_entry_price):
    send_alert("🔄 币安阵地异动重置", {
        "触发机制": _blue("🛡️ 态势感知引擎启动"),
        "系统判定": _orange(action_type),
        "仓位变化": f"`{old_qty}` -> `{new_qty}` ETH",
        "当前均价": f"**{new_entry_price:.2f}** USDT",
        "后续动作": "✅ 已接纳干预！撤销旧防线，基于最新仓位比例重新挂出 TP1/2/3 止盈单。"
    }, "#E67E22")

def report_force_align(real_side, expected_side):
    send_alert("🚨 方向异常强制核武对齐", {
        "实盘方向": f"`{real_side}`",
        "TV期望方向": f"`{expected_side}`",
        "处理结果": "**检测到极度背离，已动用市价对冲强平一切，恢复绝对净空！**"
    }, "#FF0000")

def report_binance_clear(reason):
    if "完结" in reason or "收网" in reason: title, color = "🏆 完美清场", "#27AE60"
    elif "保护" in reason: title, color = "🛡️ 战术撤退", "#F39C12"
    elif "强制" in reason or "先平" in reason: title, color = "🧹 策略核武全平", "#7F8C8D"
    else: title, color = "🛑 阵地重置", "#C0392B"

    # 重点展示解析后的平仓原因！
    send_alert(title, {
        "执行结果": "✅ 币安账户已撤销所有挂单，仓位绝对归零",
        "清场原理解析": f"**{reason}**",
        "系统状态": "静默待机，等待下一次 150 分钟级别确定性信号..."
    }, color)

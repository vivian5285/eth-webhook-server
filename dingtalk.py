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
LEVERAGE_LABEL = "20x"
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
VERIFY_DELAY_MARK = "REST 同步略延迟"


def _g(text, color=G_MAIN):
    return f'<font color="{color}">{text}</font>'


def _verify_line(verify_note, ok_message, delay_message=None, ok_color=G_MAIN, delay_color=G_ACCENT):
    """根据 verify_note 是否含 REST 延迟标记，切换核查文案"""
    if verify_note and VERIFY_DELAY_MARK in verify_note:
        msg = delay_message or f"⏳ 已提交，{VERIFY_DELAY_MARK} | 盘口稍后对齐"
        return _g(msg, delay_color)
    return _g(ok_message, ok_color)


def _classify_close(reason, verify_note="", swept_dust=False):
    """平仓/收网播报主题分类"""
    r = reason or ""
    note = verify_note or ""
    is_dust_ctx = swept_dust or "蚂蚁仓" in note or "蚂蚁仓" in r or "重启扫描" in r or "扫尾" in r

    if "TP3" in r or "完美胜利" in r or "止盈" in r or "重启对账" in note:
        return {
            "title": "🏆 完美胜利：币安大趋势吃满收网",
            "status": _g(
                "三档网格已全部吃掉，暴利安全落袋。"
                + ("（含蚂蚁仓扫尾）" if is_dust_ctx else "")
                + ("（重启对账补发）" if "重启对账" in note else ""),
                G_LIGHT,
            ),
            "header": G_TITLE,
        }
    if "保护" in r:
        return {
            "title": "🛡️ 战术防守：保护平仓机制触发",
            "status": _g("趋势警报解除，多空网格全撤，打扫战场空仓待命。", G_ACCENT),
            "header": G_ACCENT,
        }
    if is_dust_ctx:
        return {
            "title": "🐜 扫尾收网：币安蚂蚁仓/残量已清零",
            "status": _g("止盈残量或蚂蚁仓已 reduceOnly 扫平，账本复位待命。", G_LIGHT),
            "header": G_DEEP,
        }
    return {
        "title": "🧹 先平后开 / 常规清场",
        "status": _g("旧阵地已原子级爆破，账本归零等待新指令。", G_MUTED),
        "header": G_MUTED,
    }


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
> **⏰ 军区时间**：`{now_time}`
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


def _format_tp_audit(audit, tv_tps=None):
    """按档位展示：期望数量@TV价 vs 实盘状态"""
    if not audit or not audit.get("levels"):
        return _format_tp_compare(tv_tps or [], tv_tps)
    lines = []
    for lv in audit["levels"]:
        if lv.get("price", 0) <= 0:
            continue
        prefix = "" if not lines else "\n\n  ➔ "
        if lv.get("status") == "ok":
            lines.append(
                f"{prefix}TP{lv['level']} ✅ `{lv['actual_qty']}` ETH @ `{lv['price']:.2f}` "
                f"(比例期望 `{lv['qty']}`)"
            )
        else:
            lines.append(
                f"{prefix}TP{lv['level']} ❌ 期望 `{lv['qty']}` @ `{lv['price']:.2f}` "
                f"→ 状态 `{lv['status']}`"
                + (f" 实盘 `{lv.get('actual_qty', 0)}`" if lv.get("actual_qty") else "")
            )
    return "".join(lines) or "暂无有效 TP 审计"


def report_supervisor_open(side, entry_price, tv_price, qty, tp_pxs, atr, regime, tv_tps=None,
                           verify_note="", tp_audit=None, verified=True):
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
        "🕸️ 止盈布防比对": _g(
            _format_tp_audit(tp_audit, tv_tps) if tp_audit else _format_tp_compare(tp_pxs, tv_tps),
            G_LIGHT,
        ),
        "📏 波动参考": _g(f"ATR = {atr:.4f}", G_MUTED),
        "📡 哨兵状态": _verify_line(
            verify_note if not verified else "",
            f"🟢 {VERIFY_TAG} | 限价 TP123 已挂，雷达待命",
            "⏳ 开仓已提交，REST 同步略延迟 | 哨兵待确认",
        ),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert("🔶 战神出击：币安大级别阵地建立", data)


def report_intervention(qty, entry_px, new_sl, action_msg, verify_note="", verified=True):
    data = {
        "🛡️ 战术动作": _g(action_msg, G_ACCENT),
        "📦 利润头寸": _g(f"`{qty}` {UNIT_LABEL}", G_MAIN),
        "💰 原始成本": _g(f"`{entry_px:.2f}` USDT", G_MUTED),
        "🔒 最新硬防线": _g(f"**{new_sl:.2f}** USDT (物理保本单已挂)", G_LIGHT),
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | 移动保本机制已触发",
            "⏳ 止损已提交，REST 同步略延迟 | 移动保本机制已触发",
        ),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert("📈 捷报：追踪雷达锁死趋势利润", data, G_DEEP)


def report_tp_fill(tp_level, tp_price, filled_qty, remain_qty, entry_px, side, regime,
                   verify_note="", verified=True):
    data = {
        "🎯 成交档位": _g(f"**TP{tp_level}** @ **{tp_price:.2f}** USDT", G_LIGHT),
        "📦 本次止盈": _g(f"`{filled_qty}` {UNIT_LABEL}", G_ACCENT),
        "📊 剩余头寸": _g(f"`{remain_qty}` {UNIT_LABEL}", G_MAIN),
        "💰 持仓均价": _g(f"`{entry_px:.2f}` USDT", G_MUTED),
        "🧭 方向/档位": _g(f"{side} | Regime {regime}", G_MUTED),
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | TP{tp_level} 限价止盈已成交",
            "⏳ 止盈已成交，REST 同步略延迟 | 哨兵持续对齐",
        ),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert(f"🎯 捷报：币安 TP{tp_level} 止盈成交", data, G_DEEP)


def report_manual_position_change(action_type, old_qty, new_qty, new_entry_price,
                                  verify_note="", tp_audit=None, verified=True):
    action_txt = _g("手动增仓", G_LIGHT) if "加仓" in action_type else _g("手动部分减仓", G_ACCENT)
    data = {
        "触发机制": _g("🛡️ 智慧大脑态势感知同步", G_MAIN),
        "实盘动作": action_txt,
        "数量变化": _g(f"`{old_qty}` ➔ `{new_qty}` {UNIT_LABEL}", G_ACCENT),
        "最新均价": _g(f"**{new_entry_price:.2f}** USDT", G_MAIN),
        "后续动作": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | 已按最新仓位比例智能重挂 TP123",
            "⏳ 重挂已提交，REST 同步略延迟 | 哨兵持续对齐",
        ),
    }
    if tp_audit:
        data["🕸️ TP123 审计"] = _g(_format_tp_audit(tp_audit), G_ACCENT)
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert("🔄 币安阵地异动重置", data, G_ACCENT)


def report_force_align(real_side, expected_side, verify_note="", verified=True):
    data = {
        "🚨 异常状况": _g("**实盘方向与 TV 战略指令发生严重背离！**", G_DEEP),
        "🕵️ 现场方向": _g(real_side, G_ACCENT),
        "🧠 策略指令": _g(expected_side, G_LIGHT),
        "⚡ 仲裁结果": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | 已核武全平，账本归零",
            "⏳ 强平已提交，REST 同步略延迟 | 账本复位中",
        ),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert("🚨 严重警告：方向强行物理对齐", data, G_TITLE)


def report_supervisor_close(reason, verify_note="", verified=True, swept_dust=False):
    theme = _classify_close(reason, verify_note, swept_dust=swept_dust)
    ok_verify = f"{VERIFY_TAG} | 盘口已无持仓"
    delay_verify = "⏳ 扫尾/平仓已提交，REST 同步略延迟 | 盘口对齐中"
    if swept_dust or "蚂蚁仓" in (verify_note or ""):
        ok_verify = f"{VERIFY_TAG} | 蚂蚁仓已扫平，盘口已无持仓"
        delay_verify = "⏳ 蚂蚁仓扫尾已提交，REST 同步略延迟 | 盘口对齐中"

    data = {
        "📋 平仓原理解析": _g(f"**{reason}**", G_MAIN),
        "✅ 账本状态": theme["status"],
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            ok_verify,
            delay_verify,
        ),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert(theme["title"], data, theme["header"])


def report_recover_takeover(side, qty, entry, tv_tps, regime, radar_active, sl_price,
                            verify_note="", tp_matched=0, tp_expected=0, tp_audit=None,
                            last_tv_signal=None, radar_sl_ok=True):
    expected = tp_expected or sum(1 for t in tv_tps if t > 0)
    if expected > 0 and tp_matched >= expected:
        action_txt = f"{VERIFY_TAG} | 头寸+TV对账 → 比例 TP123 已对齐 → 恢复哨兵"
        action_color = G_MAIN
    elif tp_matched > 0:
        action_txt = f"⚠️ 部分对齐 | 止盈 {tp_matched}/{expected} 档 (价量审计未全过) → 恢复哨兵"
        action_color = G_ACCENT
    elif expected > 0:
        action_txt = "❌ 止盈补挂失败 | 持仓已接管但限价 TP 未对齐，请人工核查"
        action_color = G_DEEP
    else:
        action_txt = f"{VERIFY_TAG} | 已接管 → 恢复哨兵（无 TP 价格记录，请等 TV 信号）"
        action_color = G_MAIN

    if radar_active:
        sl_state = "止损已挂/已确认" if radar_sl_ok else "止损待哨兵补挂"
        radar_txt = _g(
            f"已激活 · 哨兵已点火 | 硬防线 `{sl_price:.2f}` | 轮询 2s | {sl_state}",
            G_LIGHT,
        )
        action_txt += " · 雷达哨兵已点火"
    else:
        radar_txt = _g("待命 (未达 TP1 激活阈值)", G_MUTED)

    tv_ref = ""
    if last_tv_signal:
        tv_ref = (
            f"{last_tv_signal.get('action', '?')} "
            f"R{last_tv_signal.get('regime', '?')} "
            f"@{last_tv_signal.get('ts', '')}"
        )

    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "📦 核实头寸": _g(f"**{qty}** {UNIT_LABEL} @ `{entry:.2f}`", G_MAIN),
        "📊 恢复档位": get_regime_name(regime),
        "📡 最新 TV 信号": _g(tv_ref or "无日志记录", G_MUTED),
        "🕸️ TP123 比例审计": _g(
            _format_tp_audit(tp_audit, tv_tps) if tp_audit else _format_tp_compare(tv_tps, tv_tps),
            G_ACCENT,
        ),
        "📡 雷达状态": radar_txt,
        "✅ 接管动作": _g(action_txt, action_color),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert("🔄 币安 VPS 重启 · 闪电接管报告", data)


def report_recover_standby(verify_note="", version=""):
    data = {
        "📡 实盘核查": _g(f"{VERIFY_TAG} | 盘口无持仓", G_MAIN),
        "✅ 系统状态": _g("空仓待命 · 挂单已清空 · 雷达/哨兵复位", G_LIGHT),
        "🔮 版本": _g(version or "binance_webhook", G_MUTED),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert("🔄 币安 VPS 重启 · 空仓待命", data, G_ACCENT)


def report_system_alert(title, detail):
    send_alert(f"⚠️ 系统告警：{title}", {
        "⚠️ 告警级别": _g("最高级别 (CRITICAL)", G_DEEP),
        "📝 核心详情": _g(f"**{detail}**", G_ACCENT),
    }, G_TITLE)

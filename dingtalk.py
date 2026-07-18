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
import contextvars
import queue
import threading
import requests
from datetime import datetime
from dotenv import load_dotenv
from webhook_parser import (
    format_tv_field_sources,
    classify_tv_close,
    close_type_display_label,
    format_vps_sizing_note,
    format_vps_hard_sl_note,
    format_tv_vps_sl_compare,
    format_tv_sizing_note,
    format_regime_tp_ratios_label,
    RADAR_STAGE_LABELS,
    get_radar_activation_ratio,
    VPS_RISK_PCT,
    VPS_REGIME_SCALE,
    VPS_MARGIN_LEVERAGE,
    EXCHANGE_LEVERAGE,
    normalize_entry_type,
    ENTRY_TYPE_OPEN,
    ENTRY_TYPE_PYRAMID,
    ENTRY_TYPE_PROFIT_ADD,
    CLOSE_TYPE_TP3,
    CLOSE_TYPE_PROTECT,
    CLOSE_TYPE_BREAKEVEN,
    CLOSE_TYPE_HARD_SL,
    CLOSE_TYPE_VPS_SHIELD,
    CLOSE_TYPE_GENERIC,
    EXIT_SOURCE_LABELS,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))
logger = logging.getLogger(__name__)

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")
WECHAT_WEBHOOK = os.getenv("WECHAT_WEBHOOK", "").strip()  # 钉钉失败备用（企业微信群机器人）
DINGTALK_BATCH_MAX = max(1, int(os.getenv("DINGTALK_BATCH_MAX", "8")))
DINGTALK_BATCH_FLUSH_SEC = float(os.getenv("DINGTALK_BATCH_FLUSH_SEC", "6"))
DINGTALK_BATCH_DISABLE = str(os.getenv("DINGTALK_BATCH_DISABLE", "")).strip().lower() in (
    "1", "true", "yes", "on",
)

EXCHANGE_LABEL = "币安 Binance"
LEVERAGE_LABEL = f"{int(EXCHANGE_LEVERAGE)}x"
DEFAULT_LEVERAGE = EXCHANGE_LEVERAGE
UNIT_LABEL = "ETH"  # 仅缺省；实盘必须传 unit_label / symbol

_ctx_unit = contextvars.ContextVar("dingtalk_unit", default=None)
_ctx_symbol = contextvars.ContextVar("dingtalk_symbol", default=None)


def _resolve_unit(unit_label=None, symbol=None):
    """按品种解析数量单位：XAUUSDT → XAU，ETHUSDT → ETH。禁止黄金单显示 ETH。"""
    if unit_label:
        u = str(unit_label).strip().upper()
        if u:
            return u
    sym = str(symbol or "").strip().upper().replace(".P", "")
    if ":" in sym:
        sym = sym.split(":")[-1]
    if "XAU" in sym or "GOLD" in sym:
        return "XAU"
    if "ETH" in sym:
        return "ETH"
    # 回退上下文（_call_dingtalk 注入）；禁止再递归读 context
    ctx_u = _ctx_unit.get()
    if ctx_u:
        return str(ctx_u).strip().upper()
    ctx_s = str(_ctx_symbol.get() or "").strip().upper().replace(".P", "")
    if ":" in ctx_s:
        ctx_s = ctx_s.split(":")[-1]
    if "XAU" in ctx_s or "GOLD" in ctx_s:
        return "XAU"
    if "ETH" in ctx_s:
        return "ETH"
    return UNIT_LABEL


def _u(unit_label=None, symbol=None):
    """当前播报单位（优先显式参数 → 上下文 → 缺省 ETH）"""
    return _resolve_unit(unit_label, symbol)


def bind_dingtalk_symbol(symbol=None, unit_label=None):
    """军师播报前绑定品种上下文，避免 TP/雷达文案误写 ETH。"""
    tokens = []
    if unit_label:
        tokens.append(_ctx_unit.set(str(unit_label).strip().upper()))
    if symbol:
        tokens.append(_ctx_symbol.set(str(symbol).strip().upper()))
    return tokens


def reset_dingtalk_symbol(tokens):
    for t in tokens or []:
        try:
            t.var.reset(t)
        except Exception:
            pass

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


def _classify_close(reason, verify_note="", swept_dust=False, close_type="", close_action="",
                    tv_reason=""):
    """平仓/收网播报主题 — 与 Pine v6.9.75 四标签对齐"""
    r = reason or ""
    note = verify_note or ""
    is_dust_ctx = swept_dust or "蚂蚁仓" in note or "蚂蚁仓" in r or "重启扫描" in r or "扫尾" in r
    ct = close_type or classify_tv_close(close_action, tv_reason or r)

    if ct == CLOSE_TYPE_TP3:
        return {
            "title": "🏆 TP3止盈 · 完美收网",
            "tag": _g("**TP3止盈**", G_LIGHT),
            "status": _g(
                "三档网格全部吃尽，暴利安全落袋。"
                + ("（含蚂蚁仓扫尾）" if is_dust_ctx else "")
                + ("（重启对账补发）" if "重启对账" in note else ""),
                G_LIGHT,
            ),
            "header": G_TITLE,
        }
    if ct == CLOSE_TYPE_PROTECT:
        return {
            "title": "🛡️ 风控拦截 · 保护性全平",
            "tag": _g("**风控拦截**", G_ACCENT),
            "status": _g("策略风控触发，多空网格全撤，空仓待命。", G_ACCENT),
            "header": G_ACCENT,
        }
    if ct == CLOSE_TYPE_BREAKEVEN:
        return {
            "title": "💚 防回吐保本 · 全平收网",
            "tag": _g("**防回吐保本**", G_LIGHT),
            "status": _g("追踪保本/微利护体触发，利润锁死离场。", G_MAIN),
            "header": G_LIGHT,
        }
    if ct in (CLOSE_TYPE_HARD_SL, CLOSE_TYPE_VPS_SHIELD):
        title = (
            "🛡️ VPS硬止损 · 全平"
            if ct == CLOSE_TYPE_VPS_SHIELD
            else "🛑 硬止损 · 全平离场"
        )
        tag_txt = "VPS硬止损" if ct == CLOSE_TYPE_VPS_SHIELD else "硬止损"
        return {
            "title": title,
            "tag": _g(f"**{tag_txt}**", G_DEEP),
            "status": _g("止损触发全平，多空网格全撤，账本复位待命。", G_DEEP),
            "header": G_DEEP,
        }
    if is_dust_ctx:
        return {
            "title": "🐜 扫尾收网：蚂蚁仓/残量已清零",
            "tag": _g("**扫尾收网**", G_MUTED),
            "status": _g("止盈残量或蚂蚁仓已 reduceOnly 扫平，账本复位待命。", G_LIGHT),
            "header": G_DEEP,
        }
    return {
        "title": "🧹 先平后开 / 常规清场",
        "tag": _g("**常规清场**", G_MUTED),
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


def _post_wechat_markdown(title, markdown_text):
    """企业微信群机器人备用渠道。"""
    if not WECHAT_WEBHOOK:
        return False
    # 企微 markdown 用 content；截断防超长
    content = f"**{title}**\n{markdown_text}"
    if len(content) > 3800:
        content = content[:3800] + "\n…(截断)"
    try:
        r = requests.post(
            WECHAT_WEBHOOK,
            json={"msgtype": "markdown", "markdown": {"content": content}},
            timeout=8,
        )
        ok = r.status_code == 200
        if not ok:
            logger.error(f"企业微信备用发送失败 HTTP {r.status_code}: {r.text[:200]}")
        return ok
    except Exception as e:
        logger.error(f"企业微信备用发送异常: {e}")
        return False


def _post_dingtalk_once(title, markdown_text):
    signed_url = _get_signed_url()
    if not signed_url:
        return False, "no_webhook"
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": markdown_text}}
    try:
        r = requests.post(signed_url, json=payload, timeout=6)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code} {r.text[:160]}"
        body = {}
        try:
            body = r.json() if r.text else {}
        except Exception:
            body = {}
        # 钉钉限流/错误：errcode != 0
        err = body.get("errcode", 0)
        if err not in (0, None):
            return False, f"errcode={err} {body.get('errmsg', '')}"
        return True, "ok"
    except Exception as e:
        return False, str(e)


def _post_with_retry(title, markdown_text, max_attempts=3):
    """指数退避 1s/2s/4s；三次失败后改用企业微信。"""
    delays = (1.0, 2.0, 4.0)
    last_err = ""
    for i in range(max_attempts):
        ok, info = _post_dingtalk_once(title, markdown_text)
        if ok:
            _batcher.mark_success()
            return True
        last_err = info
        logger.error(f"钉钉发送失败({i + 1}/{max_attempts}): {info}")
        if i < max_attempts - 1:
            time.sleep(delays[i])
    _batcher.mark_fail()
    if WECHAT_WEBHOOK:
        logger.warning(f"钉钉 {max_attempts} 次失败 → 企业微信备用 | 末次: {last_err}")
        if _post_wechat_markdown(title, markdown_text):
            return True
    return False


def _build_alert_markdown(title, data_dict, header_color=G_TITLE):
    text_lines = [f"- **{k}** : {v}" for k, v in (data_dict or {}).items()]
    body_text = "\n".join(text_lines)
    now_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    return f"""### <font color="{header_color}">{title}</font>
> **⏰ 军区时间**：`{now_time}`
> **📍 阵地标识**：[ {EXCHANGE_LABEL} · 主力进攻阵地 ]
> **🔶 主题色带**：`币安黄金`（与深币紫金播报区分）

---
{body_text}

---
{FOOTER}
"""


class _DingTalkBatcher:
    """攒批推送：默认 6s 或满 8 条合并一条 Markdown，规避 20条/分钟限流。"""

    def __init__(self):
        self._q = queue.Queue()
        self._lock = threading.Lock()
        self._started = False
        self.success_count = 0
        self.fail_count = 0

    def mark_success(self):
        with self._lock:
            self.success_count += 1

    def mark_fail(self):
        with self._lock:
            self.fail_count += 1

    def stats(self):
        with self._lock:
            return {
                "success": self.success_count,
                "fail": self.fail_count,
                "pending": self._q.qsize(),
            }

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
            threading.Thread(
                target=self._loop, daemon=True, name="dingtalk-batch"
            ).start()
            logger.info(
                f"📬 钉钉攒批已启动：flush={DINGTALK_BATCH_FLUSH_SEC}s "
                f"max={DINGTALK_BATCH_MAX} 备用企微={'是' if WECHAT_WEBHOOK else '否'}"
            )

    def enqueue(self, title, data_dict, header_color):
        self.start()
        self._q.put((str(title), dict(data_dict or {}), header_color, time.time()))

    def _loop(self):
        batch = []
        last_flush = time.time()
        while True:
            timeout = max(0.15, DINGTALK_BATCH_FLUSH_SEC - (time.time() - last_flush))
            try:
                item = self._q.get(timeout=timeout)
                batch.append(item)
            except queue.Empty:
                pass
            now = time.time()
            if batch and (
                len(batch) >= DINGTALK_BATCH_MAX
                or now - last_flush >= DINGTALK_BATCH_FLUSH_SEC
            ):
                try:
                    self._flush(batch)
                except Exception as e:
                    logger.error(f"钉钉攒批 flush 异常: {e}", exc_info=True)
                batch = []
                last_flush = time.time()

    def _flush(self, batch):
        if not batch:
            return
        if len(batch) == 1:
            title, data, color, _ = batch[0]
            md = _build_alert_markdown(title, data, color)
            _post_with_retry(title, md)
            return
        parts = []
        for title, data, color, ts in batch:
            tstr = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            body = "\n".join(f"- **{k}** : {v}" for k, v in (data or {}).items())
            parts.append(
                f'### <font color="{color}">{title}</font> `{tstr}`\n{body}'
            )
        now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        merged_title = f"📦 币安播报合并 ×{len(batch)}"
        markdown_text = f"""### <font color="{G_TITLE}">{merged_title}</font>
> **⏰ 军区时间**：`{now_time}`
> **📍 阵地标识**：[ {EXCHANGE_LABEL} · 攒批摘要 ]
> **📊 条数**：`{len(batch)}`（规避钉钉限流）

---
{chr(10).join(parts)}

---
{FOOTER}
"""
        _post_with_retry(merged_title, markdown_text)


_batcher = _DingTalkBatcher()


def dingtalk_batch_stats():
    return _batcher.stats()


def send_alert(title, data_dict, header_color=G_TITLE):
    """对外统一入口：默认攒批；DINGTALK_BATCH_DISABLE=1 则即时发送。"""
    if not DINGTALK_WEBHOOK and not WECHAT_WEBHOOK:
        return
    if DINGTALK_BATCH_DISABLE:
        md = _build_alert_markdown(title, data_dict, header_color)
        _post_with_retry(str(title), md)
        return
    _batcher.enqueue(title, data_dict, header_color)


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


def _format_tp_compare(tp_pxs, tv_tps=None, unit_label=None, symbol=None):
    unit = _resolve_unit(unit_label, symbol)
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
    return tp_str or f"暂无有效 TP 价格 ({unit})"


def _format_tp_audit(audit, tv_tps=None, unit_label=None, symbol=None):
    """按档位展示：期望数量@TV价 vs 实盘状态（单位随品种）"""
    unit = _resolve_unit(unit_label, symbol)
    if not audit or not audit.get("levels"):
        return _format_tp_compare(tv_tps or [], tv_tps, unit_label=unit, symbol=symbol)
    lines = []
    for lv in audit["levels"]:
        if lv.get("price", 0) <= 0:
            continue
        prefix = "" if not lines else "\n\n  ➔ "
        if lv.get("status") == "ok":
            lines.append(
                f"{prefix}TP{lv['level']} ✅ `{lv['actual_qty']}` {unit} @ `{lv['price']:.2f}` "
                f"(比例期望 `{lv['qty']}` {unit})"
            )
        else:
            lines.append(
                f"{prefix}TP{lv['level']} ❌ 期望 `{lv['qty']}` {unit} @ `{lv['price']:.2f}` "
                f"→ 状态 `{lv['status']}`"
                + (f" 实盘 `{lv.get('actual_qty', 0)}`" if lv.get("actual_qty") else "")
            )
    return "".join(lines) or f"暂无有效 TP 审计 ({unit})"


def _format_vps_sizing_basis(principal, meta=None, leverage=None):
    """VPS OPEN 仓位预算公式 — 管理员一眼看懂"""
    meta = meta or {}
    eff = float(meta.get("effective_risk_pct", VPS_RISK_PCT) or VPS_RISK_PCT)
    regime = int(meta.get("regime", 3) or 3)
    scale = float(meta.get("regime_scale", VPS_REGIME_SCALE.get(regime, 0.95)) or 0.95)
    margin = float(meta.get("margin", 0) or 0)
    order_amount = float(meta.get("order_amount", 0) or meta.get("position_value", 0) or 0)
    lev = int(round(float(
        leverage or meta.get("leverage") or EXCHANGE_LEVERAGE
    )))
    lines = [
        f"本金快照 **{float(principal):.2f}** USDT × **{VPS_RISK_PCT:.0f}%** "
        f"× **{VPS_MARGIN_LEVERAGE}** × R{regime}系数 **{scale:.2f}** "
        f"= 保证金 **{margin:.2f}** USDT" if margin > 0 else
        f"本金快照 **{float(principal):.2f}** USDT × **{VPS_RISK_PCT:.0f}%** "
        f"× **{VPS_MARGIN_LEVERAGE}** × R{regime}系数 **{scale:.2f}**",
    ]
    if order_amount > 0:
        lines.append(
            f"→ 保证金 **{margin:.2f}** × **{lev}x** 杠杆 = 头寸 **{order_amount:.2f}** USDT"
            if margin > 0 else
            f"→ 保证金 × **{lev}x** 杠杆 = 头寸 **{order_amount:.2f}** USDT"
        )
    stop_dist = float(meta.get("stop_dist", 0) or 0)
    if stop_dist > 0:
        lines.append(f"（TV tv_sl 距离 **{stop_dist:.2f}**，**仅日志参考，永不挂盘**）")
    return "\n".join(lines)


def _format_sizing_basis(principal, margin_pct, leverage, margin_usdt=None):
    """兼容旧调用 — 实为 VPS 有效风险%"""
    if margin_usdt is None:
        margin_usdt = float(principal or 0) * float(margin_pct or 0)
    return (
        f"本金快照 **{float(principal):.2f}** USDT × VPS有效风险 **{float(margin_pct or 0):.1%}** "
        f"× **{leverage}x** 杠杆 = **{margin_usdt:.2f}** USDT 下单额"
    )


def report_principal_snapshot(reason, principal, regime=None, margin_pct=None, target_qty=None,
                              leverage=None, verify_note="", vps_sizing_meta=None,
                              symbol=None, unit_label=None):
    """全平/开仓前本金快照 — 管理员可读"""
    lev = leverage or LEVERAGE_LABEL.replace("x", "")
    meta = vps_sizing_meta or {}
    unit = _resolve_unit(unit_label, symbol)
    data = {
        "📸 快照时机": _g(reason or "本金重置", G_MAIN),
        "💰 合约本金": _g(f"**{float(principal):.2f}** USDT（walletBalance，非可用保证金）", G_ACCENT),
        "📌 口径说明": _g(
            "VPS 自主风控：本金 × VPS_RISK_PCT% × REGIME_SCALE × GLOBAL_SCALE × 杠杆 ÷ |price-tv_sl|；"
            "完全忽略 TV risk_pct；禁止用 available / 剩余保证金",
            G_MUTED,
        ),
    }
    if regime and margin_pct is not None:
        data["🔢 TV 档位"] = get_regime_name(int(regime))
        if meta:
            data["📐 预算公式"] = _g(
                _format_vps_sizing_basis(principal, meta=meta, leverage=f"{lev}x"),
                G_LIGHT,
            )
        else:
            data["📐 预算公式"] = _g(
                _format_sizing_basis(principal, margin_pct, f"{lev}x"),
                G_LIGHT,
            )
    if target_qty is not None and float(target_qty) > 0:
        data["🎯 目标仓位"] = _g(f"**{target_qty}** {unit}", G_MAIN)
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert("📸 本金快照 · 档位预算基数已锁定", data, G_TITLE)


def report_supervisor_open(side, entry_price, tv_price, qty, tp_pxs, atr, regime, tv_tps=None,
                           verify_note="", tp_audit=None, verified=True,
                           principal_balance=None, margin_pct=None, margin_usdt=None, leverage=None,
                           tv_field_sources=None, vps_sizing_meta=None, symbol=None, unit_label=None,
                           hard_sl_px=None, radar_act_px=None, radar_act_ratio=None):
    side_str = _g("🔶 开多 (LONG)", G_LIGHT) if side == "LONG" else _g("🟤 开空 (SHORT)", G_DEEP)
    slip_txt = (
        f"{(entry_price - tv_price if side == 'LONG' else tv_price - entry_price):+.2f} 刀"
        if tv_price > 0 else "未知"
    )
    lev = leverage or DEFAULT_LEVERAGE
    unit = _resolve_unit(unit_label, symbol)
    sym = str(symbol or "").upper() or "ETHUSDT"
    act_ratio = float(
        radar_act_ratio if radar_act_ratio is not None else get_radar_activation_ratio(regime)
    )

    data = {
        "🎛️ 品种": _g(f"**{sym}**", G_ACCENT),
        "🎛️ 趋势方向": side_str,
        "📊 市场强度": get_regime_name(regime),
        "🕸️ TP123 比例": _g(
            f"开仓 R{regime} → **{format_regime_tp_ratios_label(regime)}%** (对齐 TV qty_percent)",
            G_LIGHT,
        ),
        "💰 进场成本": _g(f"**{entry_price:.2f}** USDT (滑点: **{slip_txt}**)", G_MAIN),
        "📦 开单头寸": _g(f"**{qty}** {unit} ({EXCHANGE_LABEL} {LEVERAGE_LABEL} 稳健火力)", G_ACCENT),
        "🕸️ 止盈布防比对": _g(
            _format_tp_audit(tp_audit, tv_tps, unit_label=unit, symbol=sym)
            if tp_audit else _format_tp_compare(tp_pxs, tv_tps, unit_label=unit, symbol=sym),
            G_LIGHT,
        ),
        "📏 波动参考": _g(f"ATR = {atr:.4f}", G_MUTED),
        "📡 TV字段": _g(format_tv_field_sources(tv_field_sources or {}), G_MUTED),
        "📡 哨兵状态": _verify_line(
            verify_note if not verified else "",
            f"🟢 {VERIFY_TAG} | TP123已挂(reduceOnly) · VPS硬止损已挂(closePosition) · "
            f"雷达待命(距TP1剩{(1 - act_ratio) * 100:.0f}%交棒·独立保本) · 三轨不抢份额",
            "⏳ 开仓已提交，REST 同步略延迟 | 哨兵待确认",
        ),
    }
    if hard_sl_px is not None and float(hard_sl_px or 0) > 0:
        data["🛡️ VPS硬止损"] = _g(
            f"**{float(hard_sl_px):.2f}** USDT (closePosition·与雷达单槽合并)",
            G_DEEP,
        )
    if radar_act_px is not None and float(radar_act_px or 0) > 0:
        data["📡 雷达激活线"] = _g(
            f"**{float(radar_act_px):.2f}** USDT "
            f"(距TP1剩{(1 - act_ratio) * 100:.0f}%·与TP123并行不冲突)",
            G_LIGHT,
        )
    data["🔍 头寸对账"] = _g(
        f"实盘 `{qty}` {unit} @ `{entry_price:.2f}` | "
        f"TP审计={'齐' if verified else '待补'} | "
        f"硬止损={'已挂' if hard_sl_px and float(hard_sl_px) > 0 else '待挂'} | "
        f"成交判据=价到+限价消失(微漂忽略)",
        G_MAIN if verified else G_ACCENT,
    )
    if principal_balance and margin_pct is not None:
        if vps_sizing_meta:
            data["📐 仓位预算"] = _g(
                _format_vps_sizing_basis(principal_balance, meta=vps_sizing_meta, leverage=lev),
                G_LIGHT,
            )
            data["📐 VPS参数"] = _g(format_vps_sizing_note(vps_sizing_meta, qty=qty), G_MUTED)
        else:
            data["📐 仓位预算"] = _g(
                _format_sizing_basis(principal_balance, margin_pct, lev, margin_usdt),
                G_LIGHT,
            )
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert(f"🔶 战神出击：币安 {sym} 阵地建立", data)


def report_intervention(qty, entry_px, new_sl, action_msg, verify_note="", verified=True,
                        symbol=None, unit_label=None):
    unit = _resolve_unit(unit_label, symbol)
    sym = str(symbol or _ctx_symbol.get() or "").upper() or "?"
    data = {
        "🎛️ 品种": _g(f"**{sym}**", G_ACCENT),
        "🛡️ 战术动作": _g(action_msg, G_ACCENT),
        "📦 利润头寸": _g(f"`{qty}` {unit}", G_MAIN),
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
    send_alert(f"📈 [{sym}] 捷报：追踪雷达锁死趋势利润", data, G_DEEP)


def report_tp_fill(tp_level, tp_price, filled_qty, remain_qty, entry_px, side, regime,
                   verify_note="", verified=True, symbol=None, unit_label=None):
    unit = _resolve_unit(unit_label, symbol)
    sym = str(symbol or _ctx_symbol.get() or "").upper() or "?"
    remain_levels = [lv for lv in (1, 2, 3) if lv > int(tp_level or 0)]
    wait_txt = (
        f"耐心等 TP{remain_levels} 限价成交（不再补挂已成交档）"
        if remain_levels else "TP123 已吃完 · 收网/扫尾"
    )
    data = {
        "🎛️ 品种": _g(f"**{sym}**", G_ACCENT),
        "🎯 成交档位": _g(f"**TP{tp_level}** @ **{tp_price:.2f}** USDT", G_LIGHT),
        "📦 本次止盈": _g(f"`{filled_qty}` {unit}", G_ACCENT),
        "📊 剩余头寸": _g(f"`{remain_qty}` {unit}", G_MAIN),
        "💰 持仓均价": _g(f"`{entry_px:.2f}` USDT", G_MUTED),
        "🧭 方向/档位": _g(f"{side} | TV {regime} 档", G_MUTED),
        "✅ 判定": _g("**价到 + 限价消失 = 成交**（非头寸微漂）", G_MAIN),
        "⏳ 后续": _g(wait_txt, G_LIGHT),
        "🛡️ 三轨": _g(
            "TP=reduceOnly | 雷达保本+VPS宽硬止损=closePosition单槽 · 互不抢份额",
            G_MUTED,
        ),
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | {sym} TP{tp_level} 限价止盈已成交",
            "⏳ 止盈已成交，REST 同步略延迟 | 哨兵持续对齐",
        ),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert(f"🎯 [{sym}] 捷报：TP{tp_level} 止盈成交", data, G_DEEP)


def report_manual_position_change(action_type, old_qty, new_qty, new_entry_price,
                                  verify_note="", tp_audit=None, verified=True):
    raw = str(action_type or "")
    if "加仓" in raw:
        action_txt = _g("手动增仓", G_LIGHT)
    elif "止盈" in raw or "对账" in raw:
        action_txt = _g("限价止盈对账", G_ACCENT)
    elif "减仓" in raw:
        action_txt = _g("仓位减仓（待匹配TP）", G_ACCENT)
    else:
        action_txt = _g(raw or "仓位变动", G_ACCENT)
    is_manual_open = "人工开仓" in raw
    if is_manual_open:
        action_txt = _g("人工首仓 · 系统接管", G_LIGHT)
    title = "🔄 币安阵地异动重置"
    if "止盈" in raw or "对账" in raw:
        title = "🎯 币安止盈对账同步"
    data = {
        "触发机制": _g("🛡️ 智慧大脑态势感知同步", G_MAIN),
        "实盘动作": action_txt,
        "数量变化": _g(f"`{old_qty}` ➔ `{new_qty}` {_u()}", G_ACCENT),
        "最新均价": _g(f"**{new_entry_price:.2f}** USDT", G_MAIN),
        "后续动作": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | 已按最新仓位比例智能重挂 TP123 | 硬止损/雷达 closePosition 单槽",
            "⏳ 重挂已提交，REST 同步略延迟 | 哨兵持续对齐",
        ),
    }
    if is_manual_open:
        data["📡 雷达/止损"] = _g(
            "阶段0仅 VPS硬止损 | 距TP1剩15%(路程85%)后雷达保本",
            G_MUTED,
        )
    if tp_audit:
        data["🕸️ TP123 审计"] = _g(_format_tp_audit(tp_audit), G_ACCENT)
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert(title, data, G_ACCENT)


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


def report_supervisor_close(reason, verify_note="", verified=True, swept_dust=False,
                            tv_pnl_pct=None, tv_side="", tv_price=None, close_action="",
                            tv_regime=None, tv_atr=None, tv_field_sources=None,
                            close_type="", tv_reason="", entry_px=None, closed_qty=None,
                            live_exit_px=None, exit_source="", exit_source_label=""):
    theme = _classify_close(
        reason, verify_note, swept_dust=swept_dust,
        close_type=close_type, close_action=close_action, tv_reason=tv_reason or reason,
    )
    ok_verify = f"{VERIFY_TAG} | 盘口已无持仓 | 挂单已清空"
    delay_verify = f"⏳ 全平已提交，{VERIFY_DELAY_MARK} | 盘口对齐中"
    if swept_dust or "蚂蚁仓" in (verify_note or ""):
        ok_verify = f"{VERIFY_TAG} | 蚂蚁仓已扫平，盘口已无持仓"
        delay_verify = f"⏳ 蚂蚁仓扫尾已提交，{VERIFY_DELAY_MARK} | 盘口对齐中"

    ct = close_type or classify_tv_close(close_action, tv_reason or reason, tv_pnl_pct)
    src_label = exit_source_label or EXIT_SOURCE_LABELS.get(
        exit_source, exit_source or ""
    )
    data = {
        "🏷️ 收网类型": theme.get("tag") or _g(close_type_display_label(ct, reason), G_MAIN),
        "📋 策略原由": _g(f"**{tv_reason or reason}**", G_MAIN),
        "✅ 账本状态": theme["status"],
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            ok_verify,
            delay_verify,
        ),
    }
    if src_label:
        data["🧭 平仓归因"] = _g(f"**{src_label}**", G_ACCENT)
        # 一眼区分：雷达保本 / TP3 / VPS硬止损
        if exit_source == "radar_be":
            data["📡 说明"] = _g(
                "由雷达移动保本 STOP 触发（非 TP 限价吃完）", G_LIGHT,
            )
        elif exit_source == "tp3":
            data["📡 说明"] = _g(
                "由 TP123 限价止盈吃完收网（非雷达保本）", G_LIGHT,
            )
        elif exit_source == "vps_hard_sl":
            data["📡 说明"] = _g(
                "由 VPS 宽硬止损触发（雷达尚未交棒）", G_DEEP,
            )
    if close_action:
        data["📡 TV动作"] = _g(close_action, G_MUTED)
    if tv_side:
        data["🎛️ 方向"] = _g(tv_side, G_LIGHT if tv_side == "LONG" else G_DEEP)
    if entry_px is not None and float(entry_px or 0) > 0:
        data["💰 开仓成本"] = _g(f"`{float(entry_px):.2f}` USDT", G_MUTED)
    if closed_qty is not None and float(closed_qty or 0) > 0:
        data["📦 平仓数量"] = _g(f"**{float(closed_qty):.3f}** {_u()}", G_MAIN)
    if live_exit_px is not None and float(live_exit_px or 0) > 0:
        data["💹 平仓价格"] = _g(f"`{float(live_exit_px):.2f}` USDT", G_ACCENT)
    elif tv_price is not None and float(tv_price or 0) > 0:
        data["💹 TV价格"] = _g(f"`{float(tv_price):.2f}` USDT", G_MUTED)
    if tv_pnl_pct is not None and tv_pnl_pct != "":
        pnl = float(tv_pnl_pct)
        data["📈 盈亏"] = _g(f"**{pnl:+.2f}%**", G_ACCENT if pnl >= 0 else G_DEEP)
    if tv_regime is not None:
        data["📊 TV档位"] = get_regime_name(int(tv_regime))
    if tv_atr is not None and float(tv_atr or 0) > 0:
        data["📏 TV ATR"] = _g(f"`{float(tv_atr):.4f}`", G_MUTED)
    if tv_field_sources:
        data["📡 TV字段"] = _g(format_tv_field_sources(tv_field_sources), G_MUTED)
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert(theme["title"], data, theme["header"])


def report_recover_tp_repair(side, initial_qty, live_qty, entry, consumed_levels,
                             tp_audit=None, verify_note="", verified=True):
    """重启：部分止盈后撤多余档 + 剩余 TP 重分"""
    consumed_txt = ", ".join(f"TP{lv}" for lv in (consumed_levels or [])) or "无"
    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "📦 开单头寸": _g(f"**{initial_qty}** {_u()} @ `{entry:.2f}`", G_MUTED),
        "📦 现仓剩余": _g(f"**{live_qty}** {_u()} (= TP2+TP3)", G_MAIN),
        "✂️ 已成交档": _g(consumed_txt, G_ACCENT),
        "🕸️ 剩余止盈审计": _g(
            _format_tp_audit(tp_audit, []) if tp_audit else "核查中",
            G_MAIN,
        ),
        "✅ 修复动作": _g(
            "撤多余已成交档 → 按现仓重分 TP2/TP3 → 雷达保本接力",
            G_MAIN,
        ),
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | 部分止盈修复完成",
            f"⏳ 修复已提交，{VERIFY_DELAY_MARK}",
        ),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert("🎯 重启 · 部分止盈修复", data, G_TITLE)


def report_recover_takeover(side, qty, entry, tv_tps, regime, radar_active, sl_price,
                            verify_note="", tp_matched=0, tp_expected=0, tp_audit=None,
                            last_tv_signal=None, radar_sl_ok=True,
                            pnl_label="", defense_plan="", shield_status="",
                            radar_progress=0.0, tv_aligned=True, qty_aligned=True,
                            initial_qty=0.0, tp_consumed_levels=None,
                            tv_regime=None, hard_sl_pct=None, radar_act_pct=None):
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
            f"已激活 · 进度 {radar_progress:.0%} | 保本 `{sl_price:.2f}` | "
            f"轮询 2s | {sl_state}",
            G_LIGHT,
        )
        action_txt += " · 雷达哨兵已点火"
    else:
        act = f"{float(radar_act_pct) * 100:.0f}%" if radar_act_pct else "85%(距TP1剩15%)"
        radar_txt = _g(
            f"待命 (价触激活线 {act} 后雷达5阶段保本)",
            G_MUTED,
        )

    tv_ref = ""
    tv_reg_from_sig = None
    if last_tv_signal:
        tv_ref = (
            f"{last_tv_signal.get('action', '?')} "
            f"R{last_tv_signal.get('regime', '?')} "
            f"@{last_tv_signal.get('ts', '')}"
        )
        try:
            tv_reg_from_sig = int(last_tv_signal.get("regime") or 0) or None
        except (TypeError, ValueError):
            tv_reg_from_sig = None
    tv_reg = int(tv_regime or tv_reg_from_sig or 0) or None
    open_reg = int(regime or 0) or None
    tv_align_txt = "一致" if tv_aligned else "⚠️ 与实盘方向有偏差(以实盘为准)"
    qty_align_txt = "一致" if qty_aligned else "⚠️ 账本数量有偏差(已同步实盘)"
    regime_mismatch = bool(tv_reg and open_reg and tv_reg != open_reg)

    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "📦 核实头寸": _g(f"**{qty}** {_u()} @ `{entry:.2f}`", G_MAIN),
        "📊 开仓档位(硬止损/TP)": get_regime_name(open_reg or 3),
    }
    if tv_reg:
        data["📡 TV信号档位"] = get_regime_name(tv_reg)
        if regime_mismatch:
            data["⚠️ 档位对账"] = _g(
                f"**不一致** TV=R{tv_reg} vs 锁档=R{open_reg} → 已以 TV 开仓档重算硬止损",
                G_DEEP,
            )
        else:
            data["✅ 档位对账"] = _g(f"TV R{tv_reg} = 开仓锁档 R{open_reg}", G_MAIN)
    if hard_sl_pct is not None and sl_price:
        data["🛡️ 硬止损比例"] = _g(
            f"R{open_reg or '?'} **{float(hard_sl_pct) * 100:.2f}%** → `{float(sl_price):.2f}`",
            G_ACCENT,
        )
    if initial_qty and float(initial_qty) > float(qty) + 0.001:
        consumed_txt = ", ".join(f"TP{lv}" for lv in (tp_consumed_levels or [])) or "推断中"
        data["📦 开单原始"] = _g(f"**{initial_qty}** {_u()}", G_MUTED)
        data["✂️ 已成交档"] = _g(consumed_txt, G_ACCENT)
    data.update({
        "📡 最新 TV 信号": _g(f"{tv_ref or '无日志记录'} ({tv_align_txt})", G_MUTED),
        "⚖️ 仓位核对": _g(qty_align_txt, G_MAIN if qty_aligned else G_ACCENT),
        "📈 盈亏态势": _g(pnl_label or "核查中", G_ACCENT if "浮亏" in (pnl_label or "") else G_MAIN),
        "🛡️ VPS宽硬止损": _g(shield_status or "核查中", G_MAIN),
        "🕸️ TP123 比例审计": _g(
            _format_tp_audit(tp_audit, tv_tps) if tp_audit else _format_tp_compare(tv_tps, tv_tps),
            G_ACCENT,
        ),
        "📡 雷达状态": radar_txt,
        "🧭 防线路由": _g(defense_plan or "哨兵接力维护", G_LIGHT),
        "✅ 接管动作": _g(action_txt, action_color),
    })
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert("🔄 币安 VPS 重启 · 闪电接管报告", data)


def report_recover_standby(verify_note="", version="", symbol=None):
    sym = str(symbol or _ctx_symbol.get() or "").upper() or "?"
    data = {
        "🎛️ 品种": _g(f"**{sym}**", G_ACCENT),
        "📡 实盘核查": _g(f"{VERIFY_TAG} | 盘口无持仓", G_MAIN),
        "✅ 系统状态": _g("空仓待命 · 挂单已清空 · 雷达/哨兵复位", G_LIGHT),
        "🔮 版本": _g(version or "binance_webhook", G_MUTED),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert(f"🔄 [{sym}] 币安 VPS 重启 · 空仓待命", data, G_ACCENT)


def report_smart_same_dir_decision(side, decision, live_entry, tv_price, diff_pct, threshold_pct,
                                   open_regime, tv_regime, open_atr, tv_atr, qty,
                                   tp_audit=None, verify_note=""):
    atr_txt = f"持仓 `{open_atr:.2f}` · TV `{tv_atr:.2f}`"
    atr_changed = abs(float(open_atr or 0) - float(tv_atr or 0)) > 0 and (
        max(abs(open_atr), abs(tv_atr), 1) == 0 or
        abs(float(open_atr) - float(tv_atr)) / max(abs(open_atr), abs(tv_atr), 1) > 0.03
    )

    if decision == "skip_duplicate_flat":
        title = "🧠 智能筛选：短时重复同向 · 已忽略"
        status = _g(
            f"**5 分钟内** ATR 未变 ({atr_txt})，价差 **{diff_pct:.3f}%** < **{threshold_pct}%**，"
            f"档位 **R{tv_regime}** → **未重复下单**。",
            G_ACCENT,
        )
    elif decision.startswith("reentry_"):
        reason_map = {
            "reentry_atr_changed": f"**① ATR 变化** ({atr_txt}) → **先平后开** 刷新仓位",
            "reentry_regime_changed": f"**② 档位** R{open_regime}→R{tv_regime} → **先平后开** 刷新仓位",
            "reentry_spread_ok": (
                f"**③ 理论价差** **{diff_pct:.3f}%** ≥ **{threshold_pct}%** "
                f"(ATR 未变 {atr_txt}) → **先平后开**"
            ),
        }
        title = "🧠 智能筛选：同向持仓 · 刷新仓位"
        status = _g(reason_map.get(decision, "同向刷新仓位 → **先平后开**"), G_TITLE)
    else:
        title = "🧠 智能筛选：同向持仓 · 仅刷新止盈"
        status = _g(
            f"**① ATR 未变** ({atr_txt}) + **③ 价差** **{diff_pct:.3f}%** < **{threshold_pct}%** "
            f"(档位 R{open_regime}) → **未再开仓**，已核实持仓并按新 TV 价刷新 TP123。",
            G_LIGHT,
        )
    data = {
        "📊 智能决策": status,
        "🎯 TV方向": _g(side, G_MAIN),
        "💰 实盘成本": _g(f"`{live_entry:.2f}` USDT" if live_entry > 0 else "空仓", G_MUTED),
        "📡 TV理论价": _g(f"`{tv_price:.2f}` USDT", G_MUTED),
        "🌊 ATR (优先)": _g(
            f"{atr_txt}" + (" ⚡已变化" if atr_changed and decision == "reentry_atr_changed" else " ✓未变"),
            G_ACCENT if atr_changed else G_MUTED,
        ),
        "📏 理论价差": _g(f"{diff_pct:.3f}% / 阈值 {threshold_pct}%", G_ACCENT),
        "🔢 档位": _g(f"开仓 R{open_regime} · TV R{tv_regime}", G_MUTED),
        "📦 持有": _g(f"**{qty}** {_u()}" if qty > 0 else "无持仓", G_ACCENT),
    }
    if tp_audit:
        data["🕸️ TP123 审计"] = _g(_format_tp_audit(tp_audit), G_ACCENT)
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    color = G_ACCENT if decision in ("skip_duplicate_flat",) else G_TITLE
    send_alert(title, data, color)


def report_system_alert(title, detail, level="紧急", suggestion=""):
    data = {
        "⚠️ 告警级别": _g(f"【{level}】需管理员关注", G_DEEP),
        "📝 发生了什么": _g(f"**{title}**", G_MAIN),
        "📋 详细说明": _g(detail, G_ACCENT),
    }
    if suggestion:
        data["💡 建议操作"] = _g(suggestion, G_LIGHT)
    send_alert(f"⚠️ 系统告警：{title}", data, G_TITLE)


def report_close_then_open_chain(phase="", side="", reason="", bar_index=None,
                                 chain_same_bar=False, verify_note="", ok=True):
    """
    TV 同K线先平后开链（CLOSE.seq < OPEN.seq）钉钉对账。
    永不出现「先开后平」同时发；VPS 按 seq 升序 + 无菌净场后再开。
    """
    phase = str(phase or "").strip() or "进度"
    side_u = str(side or "").strip().upper()
    title = f"📬 先平后开链 · {phase}"
    data = {
        "🧭 时序规则": _g(
            "同K线只可能：单独CLOSE 或 CLOSE(seq小)+OPEN(seq大)；按 (bar,seq) 升序",
            G_MUTED,
        ),
        "📋 阶段": _g(f"**{phase}**", G_MAIN if ok else G_DEEP),
        "📌 原因": _g(reason or "—", G_ACCENT),
    }
    if bar_index is not None:
        data["📊 bar_index"] = _g(str(int(bar_index)), G_MUTED)
    if chain_same_bar:
        data["🔗 同K链"] = _g("是 · 平干净再开（防残留限价成交）", G_LIGHT)
    if side_u:
        data["🎛️ 目标方向"] = _g(side_u, G_LIGHT if side_u == "LONG" else G_DEEP)
    data["✅ 结果"] = _g(
        "继续" if ok else "已中止·拒绝开仓",
        G_MAIN if ok else G_DEEP,
    )
    if verify_note:
        data["🔍 核实"] = _g(verify_note, G_MUTED)
    send_alert(title, data, G_MAIN if ok else G_TITLE)


def report_radar_guardian_realigned(side, qty, tp_audit=None, verify_note=""):
    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "📦 核实头寸": _g(f"**{qty}** {_u()}", G_MAIN),
        "🕸️ TP123 比例审计": _g(
            _format_tp_audit(tp_audit, None) if tp_audit else "已对齐",
            G_MAIN,
        ),
        "✅ 纠偏结果": _g("雷达守护已完成止盈对齐（重启接管竞态后补报）", G_MAIN),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert("📡 雷达守护 · 止盈已重新对齐", data, G_MAIN)


def report_radar_regime_cap_trim(side, old_qty, new_qty, target_qty, regime, margin_pct,
                                 tp_audit=None, verify_note="",
                                 principal_balance=None, margin_usdt=None, leverage=None,
                                 trim_qty=None):
    lev = leverage or DEFAULT_LEVERAGE
    excess = max(0.0, float(old_qty) - float(target_qty))
    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "📊 TV 档位上限": _g(
            f"**R{regime}** 档 · 保证金比例 **{margin_pct:.0%}** · 允许持仓 **{target_qty}** {_u()}",
            G_ACCENT,
        ),
        "📐 核算公式": _g(
            _format_sizing_basis(
                principal_balance or 0, margin_pct, lev, margin_usdt,
            ) if principal_balance else "本金快照 × 档位% × 杠杆（详见核实明细）",
            G_LIGHT,
        ),
        "⚖️ 超标情况": _g(
            f"实盘 **{old_qty}** {_u()} 超出目标 **{excess:.3f}** {_u()}"
            + (f" · 本次裁减 **{trim_qty}** {_u()}" if trim_qty else ""),
            G_ACCENT,
        ),
        "✂️ 裁减结果": _g(f"`{old_qty}` ➔ `{new_qty}` {_u()}", G_MAIN),
        "🕸️ TP123 重挂": _g(
            _format_tp_audit(tp_audit, None) if tp_audit else "已按新仓位重挂",
            G_MAIN,
        ),
        "✅ 纠偏结果": _g(
            "雷达最高权限：超标裁减至档位额度 → TP123 已对齐 · 移动止损逻辑不变",
            G_MAIN,
        ),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert("📡 雷达守护 · 档位限额强制对齐", data, G_TITLE)


def report_tv_signal_received(action, entry_type="", price=0, regime=3, atr=0,
                              tv_sl=0, risk_pct=0, leverage=None, qty_ratio=1.0,
                              reason="", vps_sizing_meta=None, vps_hard_sl_note="",
                              bar_index=None, seq=None):
    """TV Webhook 信号到达（接收确认，非成交核实）"""
    act = str(action or "").upper()
    et = normalize_entry_type(entry_type)
    type_map = {
        ENTRY_TYPE_OPEN: "首次开仓 OPEN",
        ENTRY_TYPE_PYRAMID: "金字塔加仓 PYRAMID",
        ENTRY_TYPE_PROFIT_ADD: "浮盈加仓 PROFIT_ADD",
    }
    type_txt = type_map.get(et, et or "—")
    close_actions = {
        "CLOSE_PROTECT": "保护性全平",
        "CLOSE_TP3": "TP3 收网",
        "CLOSE_STOPLOSS": "止损/保本平仓",
        "UPDATE_SL": "动态止损 UPDATE_SL",
        "UPDATE_TP": "动能止盈升级 UPDATE_TP",
        "CLOSE": "换防清场",
    }
    if act in close_actions:
        type_txt = close_actions[act]
    data = {
        "📡 信号类型": _g(f"**{act}** · {type_txt}", G_ACCENT),
        "💹 TV价格": _g(f"`{float(price or 0):.2f}` USDT", G_MUTED),
        "📊 档位": get_regime_name(regime),
        "📡 ATR": _g(f"`{float(atr or 0):.2f}`", G_MUTED),
    }
    if bar_index is not None or seq is not None:
        data["⏱️ 时序"] = _g(
            f"bar_index=`{bar_index}` · seq=`{seq}`",
            G_LIGHT,
        )
    if tv_sl and float(tv_sl) > 0:
        data["📡 TV参考tv_sl"] = _g(f"`{float(tv_sl):.2f}` (仅参考)", G_MUTED)
    if vps_hard_sl_note:
        data["🛡️ 止损分工"] = _g(vps_hard_sl_note, G_LIGHT)
    if act == "CLOSE_STOPLOSS":
        data["⚡ TV第一指令"] = _g(
            "收到 CLOSE_STOPLOSS → **立即市价全平**（优先于 VPS 宽止损挂单）",
            G_ACCENT,
        )
    if et == ENTRY_TYPE_OPEN and vps_sizing_meta:
        data["📐 VPS预算"] = _g(
            format_vps_sizing_note(vps_sizing_meta, entry_type=ENTRY_TYPE_OPEN),
            G_MUTED,
        )
    elif risk_pct and float(risk_pct) > 0:
        data["📐 比例参数"] = _g(
            format_tv_sizing_note(
                risk_pct, EXCHANGE_LEVERAGE, qty_ratio, regime=regime,
            ),
            G_MUTED,
        )
    data["⚙️ 实盘杠杆"] = _g(
        f"VPS **{EXCHANGE_LEVERAGE}x**（忽略 TV payload leverage 字段）",
        G_MUTED,
    )
    if reason:
        data["📝 原因"] = _g(str(reason)[:120], G_MUTED)
    data["✅ 状态"] = _g("信号已入队 · 等待实盘核实后二次播报", G_MAIN)
    send_alert(f"📡 TV信号接收 · {act}", data, G_MUTED)


def report_tv_sl_updated(side, live_qty, entry, tv_sl, exchange_stop=None,
                         radar_active=False, radar_sl=None, regime=3,
                         verify_note="", verified=True):
    """UPDATE_SL：仅记录 TV 参考；盘口硬止损永远是 VPS 宽价，不挂 TV 紧价。"""
    tv_sl = float(tv_sl or 0)
    exchange_stop = float(exchange_stop or 0)
    action_txt = (
        f"TV UPDATE_SL → 仅记录参考 `{tv_sl:.2f}` · "
        f"盘口保持 VPS宽硬止损"
        + (f" @ `{exchange_stop:.2f}`" if exchange_stop > 0 else "")
        + "（永不挂 TV 紧止损）"
    )
    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "📦 保护头寸": _g(f"**{live_qty}** {_u()}", G_MAIN),
        "💰 开仓成本": _g(f"`{entry:.2f}` USDT", G_MUTED),
        "📊 开仓档位": get_regime_name(regime),
        "📡 TV参考 tv_sl": _g(f"**{tv_sl:.2f}**（仅日志）", G_MUTED),
        "🛡️ 盘口VPS硬止损": _g(
            f"**{exchange_stop:.2f}** USDT" if exchange_stop > 0 else "由军师按开仓档位维护",
            G_MAIN,
        ),
        "📡 雷达状态": _g(
            f"已激活 @ `{float(radar_sl):.2f}`" if radar_active and radar_sl
            else ("已激活" if radar_active else "待命监控中"),
            G_MAIN,
        ),
        "✅ 风控动作": _g(action_txt, G_ACCENT),
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | UPDATE_SL 仅更新参考，未改盘口硬止损",
            f"⏳ {VERIFY_DELAY_MARK}",
        ),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert("📡 TV参考止损 · 已记录（未改盘口）", data, G_TITLE)


def report_tv_tp_updated(side, live_qty, entry, old_tps=None, new_tps=None,
                         placed=0, regime=3, verify_note="", verified=True, curr_px=0):
    """TV UPDATE_TP 动能止盈升级：只换限价 TP，不动硬止损/雷达"""
    old_tps = old_tps or []
    new_tps = new_tps or []

    def _fmt(tps):
        parts = []
        for i, t in enumerate(tps[:3]):
            try:
                v = float(t or 0)
            except (TypeError, ValueError):
                v = 0.0
            parts.append(f"TP{i + 1}=`{v:.2f}`" if v > 0 else f"TP{i + 1}=—")
        return " / ".join(parts) if parts else "—"

    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "📦 保护头寸": _g(f"**{live_qty}** {_u()}", G_MAIN),
        "💰 开仓成本": _g(f"`{float(entry or 0):.2f}` USDT", G_MUTED),
        "📊 档位": get_regime_name(regime),
        "📉 原 TP123": _g(_fmt(old_tps), G_MUTED),
        "🚀 新 TP123": _g(_fmt(new_tps), G_ACCENT),
        "📌 新挂档数": _g(f"**{int(placed or 0)}** 笔限价止盈", G_LIGHT),
        "💹 参考市价": _g(
            f"`{float(curr_px or 0):.2f}` USDT" if float(curr_px or 0) > 0 else "—",
            G_MUTED,
        ),
        "✅ 风控动作": _g(
            "动能 UPDATE_TP → 仅替换限价 TP123 · 硬止损与雷达 STOP 未触碰",
            G_MAIN,
        ),
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | UPDATE_TP 止盈已在盘口对齐",
            f"⏳ 止盈已提交，{VERIFY_DELAY_MARK} | 哨兵将继续核实",
        ),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert("🚀 动能止盈 · UPDATE_TP 已同步", data, G_TITLE)


def report_tv_position_add(side, entry_type, add_qty, old_qty, new_qty, old_entry, new_entry,
                           tv_sl=0, risk_pct=0, leverage=None, qty_ratio=1.0,
                           verify_note="", verified=True, base_qty=0, vps_sizing_meta=None,
                           add_count=0, max_add_times=2, regime=3, tp_audit="", radar_note="",
                           open_regime=None, tp_ratio_label=""):
    """PYRAMID / PROFIT_ADD 加仓核实 — 首仓×TV比例 + 新总头寸重挂 TP123/雷达"""
    type_label = {
        ENTRY_TYPE_PYRAMID: "金字塔加仓 PYRAMID",
        ENTRY_TYPE_PROFIT_ADD: "浮盈加仓 PROFIT_ADD",
    }.get(str(entry_type or "").upper(), str(entry_type or "ADD"))
    lev = leverage or DEFAULT_LEVERAGE
    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "📡 加仓类型": _g(type_label, G_ACCENT),
        "📊 档位": get_regime_name(regime),
        "🕸️ TP123 比例": _g(
            f"开仓 R{int(open_regime or regime)} → **{tp_ratio_label or format_regime_tp_ratios_label(open_regime or regime)}%**",
            G_LIGHT,
        ),
        "➕ 追加数量": _g(f"**+{add_qty}** {_u()}", G_MAIN),
        "📦 持仓变化": _g(
            f"`{old_qty}` → **`{new_qty}`** {_u()}",
            G_LIGHT,
        ),
        "💰 均价变化": _g(
            f"`{old_entry:.2f}` → **`{new_entry:.2f}`** USDT",
            G_MUTED,
        ),
        "📡 TV底线 tv_sl": _g(f"**{float(tv_sl or 0):.2f}** USDT", G_ACCENT),
        "📐 加仓公式": _g(
            format_vps_sizing_note(
                vps_sizing_meta or {
                    "base_qty": base_qty,
                    "qty_ratio": qty_ratio,
                    "regime": regime,
                    "max_add_times": max_add_times,
                    "sizing_mode": "VPS_ADD",
                },
                qty=add_qty,
                entry_type=entry_type,
            ),
            G_MUTED,
        ),
        "📡 TV加仓比例": _g(f"**{float(qty_ratio):.2f}** × 首仓", G_LIGHT),
        "🔢 加仓次数": _g(f"**{add_count}/{max_add_times}**", G_LIGHT),
        "🕸️ TP123 重挂": _g(tp_audit or "已按新总头寸重算", G_MAIN),
        "📡 雷达状态": _g(radar_note or "待命(TP1前)", G_LIGHT),
        "✅ 风控动作": _g(
            "加仓成交 → TV TP123 按新头寸重挂 + tv_sl/雷达同步",
            G_MAIN,
        ),
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | 加仓 + TP123 + 止损/雷达已同步",
            f"⏳ 加仓已提交，{VERIFY_DELAY_MARK} | 哨兵继续核实",
        ),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert(f"➕ TV加仓 · {type_label}", data, G_TITLE)


def report_adverse_shield_armed(side, entry, live_qty, adverse_pct, tier_prices, tier_pcts,
                                verify_note="", vps_hard_sl_note=""):
    stop_px = tier_prices[0] if tier_prices else entry
    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "💰 开仓成本": _g(f"`{entry:.2f}` USDT", G_MUTED),
        "📦 保护头寸": _g(f"**{live_qty}** {_u()} 全平", G_MAIN),
        "🛡️ VPS硬止损": _g(
            vps_hard_sl_note or f"`{stop_px:.2f}` USDT Stop-Limit",
            G_ACCENT,
        ),
        "✅ 风控动作": _g(
            "VPS按开仓价×档位%计算硬止损(Stop-Limit) · "
            "CLOSE_STOPLOSS=TV第一指令市价全平 · "
            "距TP1剩15%后雷达5阶段防回吐(全档统一85%)",
            G_MAIN,
        ),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert("🛡️ VPS硬止损 · 已武装", data, G_TITLE)


def report_shield_tier_fill(side, tier_pct, tier_price, filled_qty, remain_qty, entry_px,
                            remaining_tiers=None, verify_note=""):
    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "🛡️ 触发止损": _g(f"**-{tier_pct:.0%}** 硬止损 @ `{tier_price:.2f}` USDT", G_ACCENT),
        "✂️ 本次平仓": _g(f"`{filled_qty}` {_u()}", G_MAIN),
        "📊 剩余头寸": _g(f"`{remain_qty}` {_u()}", G_MAIN),
        "✅ 风控动作": _g("VPS硬止损成交 → TP123 已重算", G_MAIN),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert("🛡️ VPS硬止损 · 成交", data, G_TITLE)


def report_shield_disarmed(side, live_qty, entry, cancelled_count, reason="",
                           radar_progress=0.0, verify_note="", verified=True,
                           symbol=None, unit_label=None):
    unit = _resolve_unit(unit_label, symbol)
    sym = str(symbol or _ctx_symbol.get() or "").upper() or "?"
    data = {
        "🎛️ 品种": _g(f"**{sym}**", G_ACCENT),
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "💰 开仓成本": _g(f"`{entry:.2f}` USDT", G_MUTED),
        "📦 剩余头寸": _g(f"**{live_qty}** {unit}", G_MAIN),
        "📈 价格方向": _g("**价触激活线** → 交棒雷达保本", G_LIGHT),
        "🗑️ 撤销止损": _g(f"**{cancelled_count}** 笔 VPS硬止损", G_ACCENT),
        "📡 雷达状态": _g(
            "已激活移动保本" if radar_progress >= 0.2
            else f"进度 {radar_progress:.0%}，达激活线后挂雷达保本",
            G_MAIN,
        ),
        "✅ 风控动作": _g(
            reason or "价触激活线 → 雷达接管 · 成本±0.1%保本 · 硬止损退居次级",
            G_MAIN,
        ),
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | 硬止损已净，可挂雷达保本",
            f"⏳ 撤单已提交，{VERIFY_DELAY_MARK} | 哨兵继续清理",
        ),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert(f"🛡️ [{sym}] VPS硬止损 · 已撤销（转雷达）", data, G_TITLE)


def report_radar_activated(side, qty, entry, new_sl, radar_progress=1.0, regime=3,
                           shield_cleared=True, verify_note="", verified=True,
                           symbol=None, unit_label=None, trigger_gate="",
                           activation_price=None):
    unit = _resolve_unit(unit_label, symbol)
    sym = str(symbol or _ctx_symbol.get() or "").upper() or "?"
    gate = str(trigger_gate or "").strip() or (
        f"距TP1剩{(1 - get_radar_activation_ratio(regime)) * 100:.0f}%"
    )
    data = {
        "🎛️ 品种": _g(f"**{sym}**", G_ACCENT),
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "📦 利润头寸": _g(f"**{qty}** {unit} @ `{entry:.2f}`", G_MAIN),
        "📊 开仓档位": get_regime_name(regime),
        "🚪 启动闸门": _g(f"**{gate}**", G_ACCENT),
        "📡 雷达进度": _g(
            f"**{radar_progress:.0%}** "
            f"(5阶段·激活线{get_radar_activation_ratio(regime)*100:.0f}%)",
            G_ACCENT,
        ),
        "🗑️ 硬止损": _g("已撤销/合并" if shield_cleared else "清理中", G_MAIN),
        "🔒 保本止损": _g(f"**{new_sl:.2f}** USDT (成本±0.1%·距市价安全)", G_LIGHT),
        "✅ 风控动作": _g(
            f"{gate} → 安全交棒 · 与TP123并行(不撤剩余限价) · "
            f"closePosition单槽合并宽硬止损 · 止损只向有利方向",
            G_MAIN,
        ),
        "🛡️ 三轨": _g(
            "雷达保本独立锁利 | TP23 继续 reduceOnly | 互不抢份额",
            G_MUTED,
        ),
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | {sym} 雷达移动保本已启动",
            f"⏳ 止损已提交，{VERIFY_DELAY_MARK} | 雷达已启动",
        ),
    }
    if activation_price is not None and float(activation_price or 0) > 0:
        data["🎯 激活线价"] = _g(f"`{float(activation_price):.2f}` USDT", G_MUTED)
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert(f"📡 [{sym}] 雷达 · 移动保本已激活", data, G_DEEP)

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
    RADAR_ACTIVATE_TP1_FRAC,
    SIZING_MODE,
    normalize_entry_type,
    ENTRY_TYPE_OPEN,
    CLOSE_TYPE_TP3,
    CLOSE_TYPE_PROTECT,
    CLOSE_TYPE_QUICK,
    CLOSE_TYPE_RSI,
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
# 全交易所共用：同类标题短窗只发一条，避免对账/告警连环刷屏
DINGTALK_TITLE_DEDUP_SEC = float(os.getenv("DINGTALK_TITLE_DEDUP_SEC", "300"))
# 系统告警 / 异常减仓类更长去重
DINGTALK_ALERT_DEDUP_SEC = float(os.getenv("DINGTALK_ALERT_DEDUP_SEC", "600"))
_title_dedup_lock = threading.Lock()
_title_dedup_ts = {}  # key -> last_send_ts

EXCHANGE_LABEL = "币安 Binance"
LEVERAGE_LABEL = "TVx"  # 实盘杠杆以 TV 为准，禁止写死 25x
DEFAULT_LEVERAGE = 0
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
        # TP3 不挂限价：归入阶段二止损收网文案
        return {
            "title": "止损平仓（阶段二/趋势追踪）",
            "tag": _g("**止损平仓**", G_LIGHT),
            "status": _g("阶段二收网离场。" + ("（含扫尾）" if is_dust_ctx else ""), G_LIGHT),
            "header": G_TITLE,
        }
    if ct in (CLOSE_TYPE_PROTECT, CLOSE_TYPE_QUICK, CLOSE_TYPE_RSI):
        reason = (tv_reason or r or "反转保护").strip()
        # 防御：旧拼接残留「TV档位 R3」等不得进入标题
        import re
        reason = re.sub(r"\s*\|\s*TV档位\s*R\d+", "", reason)
        reason = re.sub(r"\bR[1-4]\b", "", reason)
        reason = re.sub(r"\s{2,}", " ", reason).strip(" |")
        return {
            "title": f"反转保护平仓：{reason[:80]}",
            "tag": _g("**反转保护**", G_ACCENT),
            "status": _g("市价全平 + 撤单 + 状态重置。", G_ACCENT),
            "header": G_ACCENT,
        }
    if ct == CLOSE_TYPE_BREAKEVEN:
        return {
            "title": "止损平仓（阶段二/趋势追踪）",
            "tag": _g("**止损平仓**", G_LIGHT),
            "status": _g("阶段二追踪止损触及，全平离场。", G_MAIN),
            "header": G_LIGHT,
        }
    if ct in (CLOSE_TYPE_HARD_SL, CLOSE_TYPE_VPS_SHIELD):
        return {
            "title": "止损平仓（阶段一）",
            "tag": _g("**止损平仓**", G_DEEP),
            "status": _g("价格触及呼吸止损，市价全平。", G_DEEP),
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
        # 同标题只保留最后一条，杜绝攒批里雷同告警连发
        collapsed = []
        seen = {}
        for title, data, color, ts in batch:
            key = str(title or "")[:96]
            if key in seen:
                collapsed[seen[key]] = (title, data, color, ts)
            else:
                seen[key] = len(collapsed)
                collapsed.append((title, data, color, ts))
        batch = collapsed
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


def _title_dedup_window(title):
    t = str(title or "")
    if "异常减仓" in t or "系统告警" in t:
        return float(DINGTALK_ALERT_DEDUP_SEC)
    return float(DINGTALK_TITLE_DEDUP_SEC)


def send_alert(title, data_dict, header_color=G_TITLE, immediate=False):
    """Unified DingTalk entry. immediate=True: send now (skip 6s batch)."""
    if not DINGTALK_WEBHOOK and not WECHAT_WEBHOOK:
        logger.warning(
            "DINGTALK_WEBHOOK empty -> skip alert: %s",
            str(title)[:72],
        )
        return
    sym = str(_ctx_symbol.get() or "").upper()
    raw_title = str(title or "")
    dedup_key = f"{sym}|{raw_title[:96]}"
    if not sym:
        for token in ("ETHUSDT", "XAUUSDT", "ETH", "XAU"):
            if token in raw_title.upper():
                dedup_key = f"{token}|{raw_title[:96]}"
                break
    now = time.time()
    window = 15.0 if immediate else _title_dedup_window(raw_title)
    with _title_dedup_lock:
        dead = [
            k for k, ts in _title_dedup_ts.items()
            if now - float(ts) > max(window, DINGTALK_TITLE_DEDUP_SEC) * 4
        ]
        for k in dead:
            _title_dedup_ts.pop(k, None)
        last = float(_title_dedup_ts.get(dedup_key) or 0)
        if last > 0 and now - last < window:
            logger.info(
                "DingTalk title dedup(%.0fs): %s",
                window,
                raw_title[:72],
            )
            return
        _title_dedup_ts[dedup_key] = now
    if immediate or DINGTALK_BATCH_DISABLE:
        md = _build_alert_markdown(title, data_dict, header_color)
        ok = _post_with_retry(str(title), md)
        if not ok:
            logger.error("DingTalk immediate send failed: %s", raw_title[:72])
        return
    _batcher.enqueue(title, data_dict, header_color)
def get_regime_name(regime_code):
    """
    旧「评分档位/中势推升」文案已废除。
    regime 内部编号可保留供逻辑使用，但用户可见文案一律只展示 RISK20 算仓模式，
    禁止再输出 R1/R2/R3/R4。
    """
    return _g("算仓=本金20%风险资金×5x杠杆（RISK20）", G_MUTED)


def _sizing_mode_label():
    return _g("本金20%风险资金 × 5x杠杆 · RISK20_NOTIONAL5", G_MAIN)


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
    """VPS 最终仓位：风险20%/VPS止损距 ∩ 名义×5 ∩ TV.qty×(TV距/VPS距)"""
    meta = meta or {}
    mode = str(meta.get("sizing_mode") or "MOM_EQUITY_1X")
    risk_capital = float(meta.get("risk_capital") or meta.get("margin") or 0)
    stop_dist = float(meta.get("vps_stop_dist") or meta.get("stop_dist") or 0)
    notional = float(meta.get("notional") or meta.get("order_amount") or 0)
    notional_cap = float(meta.get("notional_cap") or 0)
    tv_qty = meta.get("tv_qty")
    adj = float(meta.get("sl_adj") or 1.0)
    adj_tv = meta.get("adjusted_tv_qty")
    lines = [
        f"权益 **{float(principal):.2f}** U · sizing=`{mode}`",
        f"风险资金 **{risk_capital:.2f}** U（权益×20%）",
    ]
    if stop_dist > 0:
        lines.append(f"÷ VPS止损距 **{stop_dist:.2f}** → 风险仓上限")
    if notional_cap > 0:
        lines.append(f"名义上限 **{notional_cap:.2f}** U（权益×5）")
    if tv_qty is not None:
        lines.append(
            f"TV.qty **{float(tv_qty):.4f}** × sl_adj **{adj:.4f}** "
            f"→ 调整上限 **{float(adj_tv if adj_tv is not None else tv_qty):.4f}**"
        )
    if notional > 0:
        lines.append(
            f"→ 实际名义约 **{notional:.2f}** USDT"
            f"（生效约束=`{meta.get('binding', meta.get('bind', '?'))}`）"
        )
    return "\n".join(lines)

def _format_sizing_basis(principal, margin_pct, leverage, margin_usdt=None):
    """兼容旧调用 — 现按 TV risk_pct 展示"""
    if margin_usdt is None:
        margin_usdt = float(principal or 0) * float(margin_pct or 0)
    return (
        f"本金快照 **{float(principal):.2f}** USDT × TV risk **{float(margin_pct or 0):.2%}** "
        f"· lev **{leverage}** → 风险额 **{margin_usdt:.2f}** USDT"
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
            "唯一公式：风险金额/止损距离 → min(理论, 权益×TV_leverage/价)×qty_ratio（无硬上限）；"
            "直接用 TV risk_pct/qty_ratio/leverage，禁止旧保证金%/%硬上限逻辑",
            G_MUTED,
        ),
    }
    if regime and margin_pct is not None:
        data["📐 算仓模式"] = _sizing_mode_label()
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
    send_alert("📸 本金快照 · RISK20 预算基数已锁定", data, G_TITLE)


def report_supervisor_open(side, entry_price, tv_price, qty, tp_pxs, atr, regime, tv_tps=None,
                           verify_note="", tp_audit=None, verified=True,
                           principal_balance=None, margin_pct=None, margin_usdt=None, leverage=None,
                           tv_field_sources=None, vps_sizing_meta=None, symbol=None, unit_label=None,
                           hard_sl_px=None, radar_act_px=None, radar_act_ratio=None):
    """妈妈版开仓通知。"""
    unit = _resolve_unit(unit_label, symbol)
    sym = str(symbol or _ctx_symbol.get() or "").upper() or "?"
    direction = "LONG" if str(side).upper() == "LONG" else "SHORT"
    init_sl = float(hard_sl_px or 0)
    equity = float(principal_balance or (vps_sizing_meta or {}).get("principal") or 0)
    data = {
        "🎛️ 品种": _g(f"**{sym}**", G_ACCENT),
        "开仓": _g(f"**{direction}**", G_LIGHT if direction == "LONG" else G_DEEP),
        "价格": _g(f"**{float(entry_price):.2f}**", G_MAIN),
        "数量": _g(f"**{qty}** {unit}", G_ACCENT),
        "初始止损": _g(f"**{init_sl:.2f}**" if init_sl > 0 else "待挂", G_DEEP),
        "账户权益": _g(f"**{equity:.2f}** USDT" if equity > 0 else "—", G_MUTED),
        "TP": _g("TP1+TP2 已挂（余仓阶段二）", G_LIGHT),
        "呼吸止损": _g("阶段一 · 阶梯锁本", G_MAIN),
    }
    if verify_note:
        data["核实"] = _g(str(verify_note)[:200], G_MUTED)
    send_alert(f"📈 [{sym}] 开仓 {direction}", data, G_TITLE)



def report_intervention(qty, entry_px, new_sl, action_msg, verify_note="", verified=True,
                        symbol=None, unit_label=None, extreme=None, profit_pct=None):
    """止损移动：止损上移/下移至 {new_stop}，当前最高/最低价 {extreme}，浮盈 {profit}%。"""
    unit = _resolve_unit(unit_label, symbol)
    sym = str(symbol or _ctx_symbol.get() or "").upper() or "?"
    msg = str(action_msg or "")[:180]
    data = {
        "🎛️ 品种": _g(f"**{sym}**", G_ACCENT),
        "止损移动": _g(msg or f"止损移至 {float(new_sl):.2f}", G_ACCENT),
        "最新止损": _g(f"**{float(new_sl):.2f}**", G_LIGHT),
        "成本": _g(f"`{float(entry_px):.2f}`", G_MUTED),
        "头寸": _g(f"`{qty}` {unit}", G_MAIN),
    }
    if extreme is not None and float(extreme or 0) > 0:
        data["极值"] = _g(f"**{float(extreme):.2f}**", G_MAIN)
    if profit_pct is not None:
        data["浮盈"] = _g(f"**{float(profit_pct):+.2f}%**", G_LIGHT)
    if verify_note:
        data["核实"] = _g(str(verify_note)[:200], G_MUTED)
    send_alert(f"📈 [{sym}] 止损移动至 {float(new_sl):.2f}", data, G_DEEP)



def report_tp_fill(tp_level, tp_price, filled_qty, remain_qty, entry_px, side, regime,
                   verify_note="", verified=True, symbol=None, unit_label=None,
                   current_stop=None):
    """TP1/TP2 成交通知（不挂 TP3，故无 TP3 成交播报）。"""
    lv = int(tp_level or 0)
    if lv >= 3:
        return  # TP3 不挂限价，禁止旧文案
    unit = _resolve_unit(unit_label, symbol)
    sym = str(symbol or _ctx_symbol.get() or "").upper() or "?"
    remain_pct = {1: "70%", 2: "40%"}.get(lv, "—")
    stop = float(current_stop or 0)
    title = f"🎯 [{sym}] TP{lv} 止盈成交，剩余仓位 {remain_pct}"
    body = {
        "🎛️ 品种": _g(f"**{sym}**", G_ACCENT),
        f"TP{lv}": _g(f"**@{float(tp_price):.2f}**", G_LIGHT),
        "本次": _g(f"`{filled_qty}` {unit}", G_ACCENT),
        "剩余": _g(f"`{remain_qty}` {unit}（{remain_pct}）", G_MAIN),
        "当前止损": _g(f"**{stop:.2f}**" if stop > 0 else "—", G_DEEP),
    }
    if verify_note:
        body["核实"] = _g(str(verify_note)[:200], G_MUTED)
    send_alert(title, body, G_DEEP)



def report_manual_position_change(action_type, old_qty, new_qty, new_entry_price,
                                  verify_note="", tp_audit=None, verified=True):
    raw = str(action_type or "")
    if "加仓" in raw or "增仓" in raw:
        action_txt = _g("手动增仓", G_LIGHT)
    elif "止盈" in raw or "对账" in raw:
        action_txt = _g("限价止盈对账", G_ACCENT)
    elif "减仓" in raw:
        action_txt = _g("仓位减仓（待匹配TP）", G_ACCENT)
    else:
        action_txt = _g(raw or "仓位变动", G_ACCENT)
    is_manual_open = "人工开仓" in raw
    is_unverified = (
        "未登记" in raw
        or "来源待核实" in raw
        or "待核实" in raw
    )
    if is_unverified:
        action_txt = _g("未登记来源仓位 · 系统接管（来源待核实）", G_LIGHT)
    elif is_manual_open:
        # 兼容旧调用：不再对外宣称「确定人工开仓」
        action_txt = _g("未登记来源仓位 · 系统接管（来源待核实）", G_LIGHT)
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
            f"{VERIFY_TAG} | 已按最新仓位比例智能重挂 TP123 | 呼吸止损 closePosition 单槽",
            "⏳ 重挂已提交，REST 同步略延迟 | 哨兵持续对齐",
        ),
    }
    if is_unverified or is_manual_open:
        data["⚠️ 归因说明"] = _g(
            "未把该仓断言为「人工开仓」；可能是验证脚本/API直连/外部下单。"
            "未关联历史TV档位或旧tv_sl。",
            G_MUTED,
        )
        data["🫁 呼吸止损"] = _g(
            "开仓即挂 entry±1.5×ATR · 阶段一阶梯 · 浮盈≥3×ATR 切入ADX追踪",
            G_MUTED,
        )
        data["📐 算仓模式"] = _sizing_mode_label()
    if tp_audit:
        data["🕸️ TP123 审计"] = _g(_format_tp_audit(tp_audit), G_ACCENT)
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert(title, data, G_ACCENT)


def report_force_align(real_side, expected_side, verify_note="", verified=True):
    data = {
        "🚨 异常状况": _g("**实盘方向与最新 TV 不一致**", G_DEEP),
        "🕵️ 实盘方向": _g(real_side, G_ACCENT),
        "🧠 最新 TV": _g(expected_side, G_LIGHT),
        "⚡ 处置": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | 已市价全平 · 账本归零 · 等待下一笔 TV 开仓",
            "⏳ 强平已提交，REST 同步中 | 账本复位",
        ),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert(
        "🧭 TV 方向为准 · 已全平对齐",
        data,
        G_TITLE,
        immediate=True,
    )
def report_supervisor_close(reason, verify_note="", verified=True, swept_dust=False,
                            tv_pnl_pct=None, tv_side="", tv_price=None, close_action="",
                            tv_regime=None, tv_atr=None, tv_field_sources=None,
                            close_type="", tv_reason="", entry_px=None, closed_qty=None,
                            live_exit_px=None, exit_source="", exit_source_label="",
                            symbol=None, unit_label=None):
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
        # 一眼区分：呼吸止损 / TP3
        if exit_source == "radar_be":
            data["📡 说明"] = _g(
                "由呼吸止损 closePosition 触发（阶段一阶梯/阶段二ADX）", G_LIGHT,
            )
        elif exit_source == "tp3":
            data["📡 说明"] = _g(
                "由 TP123 限价止盈吃完收网（非呼吸止损）", G_LIGHT,
            )
        elif exit_source == "vps_hard_sl":
            data["📡 说明"] = _g(
                "由呼吸止损 closePosition 触发", G_DEEP,
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
        data["📐 算仓模式"] = _sizing_mode_label()
    if tv_atr is not None and float(tv_atr or 0) > 0:
        data["📏 ATR"] = _g(f"`{float(tv_atr):.4f}`", G_MUTED)
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


def report_tv_reconcile(symbol=None, action="", leg="", reason="", tv_qty=0,
                        tv_price=0, live_qty=0, unit_label=None):
    """v6.5.6 reconcile: no orders, notify only."""
    unit = _resolve_unit(unit_label, symbol)
    sym = str(symbol or "").upper() or "?"
    data = {
        "🎛️ 品种": _g(f"**{sym}**", G_ACCENT),
        "📋 对账动作": _g(f"**{action}** leg={leg or '-'}", G_MAIN),
        "📌 TV数量/价": _g(
            f"{float(tv_qty or 0)} {unit} @ {float(tv_price or 0):.2f}", G_LIGHT
        ),
        "📦 实盘仓位": _g(f"**{float(live_qty or 0):.4f}** {unit}", G_MAIN),
        "📝 reason": _g(str(reason or "—")[:120], G_MUTED),
        "✅ VPS动作": _g(
            "**状态同步+止损调整** · 不主动市价平仓",
            G_ACCENT,
        ),
    }
    send_alert(f"📋 [{sym}] TV对账 · {action}", data, G_TITLE)


def report_recover_takeover(side, qty, entry, tv_tps, regime, radar_active, sl_price,
                            verify_note="", tp_matched=0, tp_expected=0, tp_audit=None,
                            last_tv_signal=None, radar_sl_ok=True,
                            pnl_label="", defense_plan="", shield_status="",
                            radar_progress=0.0, tv_aligned=True, qty_aligned=True,
                            initial_qty=0.0, tp_consumed_levels=None,
                            tv_regime=None, hard_sl_pct=None, radar_act_pct=None,
                            symbol=None, unit_label=None):
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
        phase = "阶段二·ADX" if radar_progress >= 1.0 else "阶段一·阶梯"
        radar_txt = _g(
            f"呼吸止损已运行 · {phase} | 止损 `{sl_price:.2f}` | "
            f"轮询 0.5s | {sl_state}",
            G_LIGHT,
        )
        action_txt += " · 呼吸止损哨兵已点火"
    else:
        radar_txt = _g(
            "呼吸止损待对齐 (开仓即 entry±1.5×ATR · 哨兵补挂)",
            G_MUTED,
        )

    tv_ref = ""
    tv_reg_from_sig = None
    if last_tv_signal:
        tv_ref = (
            f"{last_tv_signal.get('action', '?')} "
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
        "📐 算仓模式": _sizing_mode_label(),
    }
    if hard_sl_pct is not None and sl_price:
        data["🫁 呼吸止损"] = _g(
            f"**{float(sl_price):.2f}** USDT (entry±1.5×ATR 起)",
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
        "🫁 呼吸止损": _g(shield_status or "核查中", G_MAIN),
        "🕸️ TP123 比例审计": _g(
            _format_tp_audit(tp_audit, tv_tps) if tp_audit else _format_tp_compare(tv_tps, tv_tps),
            G_ACCENT,
        ),
        "🫁 止损状态": radar_txt,
        "🧭 防线路由": _g(defense_plan or "哨兵接力维护", G_LIGHT),
        "✅ 接管动作": _g(action_txt, action_color),
    })
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert("🔄 重启恢复完成", data, immediate=True)


def report_recover_standby(verify_note="", version="", symbol=None):
    sym = str(symbol or _ctx_symbol.get() or "").upper() or "?"
    data = {
        "🎛️ 品种": _g(f"**{sym}**", G_ACCENT),
        "📡 实盘核查": _g(f"{VERIFY_TAG} | 盘口无持仓", G_MAIN),
        "✅ 系统状态": _g("空仓待命 · 挂单已清空 · 呼吸止损/哨兵复位", G_LIGHT),
        "🔮 版本": _g(version or "binance_webhook", G_MUTED),
    }
    if verify_note:
        data["🔍 核查明细"] = _g(verify_note, G_MUTED)
    send_alert(f"🔄 [{sym}] 币安 VPS · 空仓待命", data, G_ACCENT, immediate=True)


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
            f"**5 分钟内** ATR 未变 ({atr_txt})，价差 **{diff_pct:.3f}%** < **{threshold_pct}%** "
            f"→ **未重复下单**。",
            G_ACCENT,
        )
    elif decision.startswith("reentry_"):
        reason_map = {
            "reentry_atr_changed": f"**① ATR 变化** ({atr_txt}) → **先平后开** 刷新仓位",
            "reentry_regime_changed": "**② 内部参数变化** → **先平后开** 刷新仓位",
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
            f"→ **未再开仓**，已核实持仓并按新 TV 价刷新 TP。",
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
        "📐 算仓模式": _sizing_mode_label(),
        "📦 持有": _g(f"**{qty}** {_u()}" if qty > 0 else "无持仓", G_ACCENT),
    }
    if tp_audit:
        data["🕸️ TP123 审计"] = _g(_format_tp_audit(tp_audit), G_ACCENT)
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    color = G_ACCENT if decision in ("skip_duplicate_flat",) else G_TITLE
    send_alert(title, data, color)


def report_system_alert(title, detail, level="紧急", suggestion="", immediate=False,
                        symbol=None, unit_label=None):
    data = {
        "⚠️ 告警级别": _g(f"【{level}】需管理员关注", G_DEEP),
        "📝 发生了什么": _g(f"**{title}**", G_MAIN),
        "📋 详细说明": _g(detail, G_ACCENT),
    }
    if suggestion:
        data["💡 建议操作"] = _g(suggestion, G_LIGHT)
    send_alert(f"⚠️ 异常告警：{title}", data, G_TITLE, immediate=immediate)


def report_position_qty_reconcile(side="", baseline=0, live_qty=0, curr_px=0,
                                  note="", symbol=None, unit_label=None):
    """
    开仓后 / 微漂对账：只播报一次仓位核实（非异常告警）。
    由军师事件去重 + 标题去重双保险，禁止连环刷屏。
    """
    sym = str(symbol or _ctx_symbol.get() or "").upper() or "?"
    unit = _u(unit_label, symbol)
    side_u = str(side or "").strip().upper()
    b = float(baseline or 0)
    live = float(live_qty or 0)
    data = {
        "🎛️ 方向": _g(side_u or "—", G_LIGHT if side_u == "LONG" else G_DEEP),
        "📦 基线→实盘": _g(f"`{b:.4f}` → `{live:.4f}` {unit}", G_MAIN),
        "💹 现价": _g(f"`{float(curr_px or 0):.2f}`", G_MUTED),
        "📌 说明": _g(note or "仓位对账·已锚定实盘", G_ACCENT),
    }
    send_alert(f"📌 [{sym}] 仓位核实·已锚定实盘", data, G_ACCENT)


def report_close_then_open_chain(phase="", side="", reason="", bar_index=None,
                                 chain_same_bar=False, verify_note="", ok=True):
    """
    铁律钉钉：带开仓 → 先平后开；同秒开平同样先平后开；终态必须有仓。
    单独平仓不走本函数（走平仓清场钉钉）。
    """
    phase = str(phase or "").strip() or "进度"
    side_u = str(side or "").strip().upper()
    title = f"📬 先平后开 · {phase}"
    data = {
        "🧭 说明": _g(
            "先平后开：检测到已有持仓，已市价全平并撤单，准备执行新开仓",
            G_MUTED,
        ),
        "📋 阶段": _g(f"**{phase}**", G_MAIN if ok else G_DEEP),
        "📌 原因": _g(reason or "—", G_ACCENT),
    }
    if bar_index is not None:
        data["📊 bar_index"] = _g(str(int(bar_index)), G_MUTED)
    if chain_same_bar:
        data["🔗 同K链"] = _g("是 · 平干净再开（终态有仓）", G_LIGHT)
    if side_u:
        data["🎛️ 目标方向"] = _g(side_u, G_LIGHT if side_u == "LONG" else G_DEEP)
    data["✅ 结果"] = _g(
        "继续·终态开仓" if ok else "已中止·拒绝开仓",
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


def report_radar_regime_cap_trim(*args, **kwargs):
    """已废除 CAP_ALIGN：不再推送档位裁减钉钉。"""
    return


def report_hard_sl_fail_abort(side, qty, target_sl, attempts=3, reason="", detail=""):
    """HARD_SL_FAIL_ABORT：改单/挂止损失败重试耗尽，保持现状并告警。"""
    data = {
        "⚠️ 机制": _g("HARD_SL_FAIL_ABORT", G_ACCENT),
        "🎛️ 方向": _g(str(side or "—"), G_LIGHT),
        "📦 数量": _g(f"**{qty}** {_u()}", G_MAIN),
        "🛑 目标止损": _g(f"`{float(target_sl or 0):.2f}`", G_DEEP),
        "🔁 重试": _g(f"{int(attempts)} 次仍失败 → 保持当前止损不变", G_MUTED),
        "📌 场景": _g(reason or "呼吸止损改单/挂单", G_MUTED),
    }
    if detail:
        data["🔍 明细"] = _g(str(detail), G_MUTED)
    send_alert("🚨 止损执行失败 · HARD_SL_FAIL_ABORT", data, G_TITLE)


def report_close_then_open_fail_abort(symbol="", attempts=3, reason="", detail=""):
    """
    CLOSE_THEN_OPEN_FAIL_ABORT：先平后开净场失败重试耗尽。
    放弃本笔开仓 + 暂停该 symbol 自动开仓，需人工核对后 /admin/resume。
    """
    sym = str(symbol or _ctx_symbol.get() or "").upper() or "?"
    data = {
        "⚠️ 机制": _g("CLOSE_THEN_OPEN_FAIL_ABORT", G_DEEP),
        "🏷️ 品种": _g(sym, G_MAIN),
        "🔁 重试": _g(
            f"{int(attempts)} 次仍失败（间隔 1s/3s/6s）→ 本笔开仓已放弃",
            G_MUTED,
        ),
        "📌 原因": _g(reason or "先平后开净场失败", G_ACCENT),
        "🛑 状态": _g(
            "已暂停该品种自动开仓 · **需要人工介入**",
            G_DEEP,
        ),
        "💡 恢复": _g(
            f"核对币安持仓/挂单清净后：`POST /admin/resume/{sym}`",
            G_LIGHT,
        ),
    }
    if detail:
        data["📋 明细"] = _g(detail, G_MUTED)
    send_alert(
        f"🚨 清仓失败·需人工介入 [{sym}]",
        data,
        G_TITLE,
        immediate=True,
    )


def report_atr_degrade_abort(
    symbol="",
    reason="",
    vps_atr=0,
    tv_implied_atr=0,
    entry=0,
    qty=0,
    side="",
    detail="",
):
    """
    ATR 应急降级：本笔已用 TV 隐含 ATR 开仓；暂停后续自动开仓，需人工确认后 resume。
    禁止静默切换——必须高优告警。
    """
    sym = str(symbol or _ctx_symbol.get() or "").upper() or "?"
    data = {
        "⚠️ 机制": _g("ATR_DEGRADE_MANUAL_RESUME", G_DEEP),
        "🏷️ 品种": _g(sym, G_MAIN),
        "📌 触发原因": _g(reason or "—", G_ACCENT),
        "📉 VPS原始ATR": _g(f"`{float(vps_atr or 0):.4f}`", G_MUTED),
        "📐 TV隐含ATR": _g(
            f"`{float(tv_implied_atr or 0):.4f}` (=|price−sl|/1.0)",
            G_MAIN,
        ),
        "🚀 本笔执行": _g(
            f"{str(side or '').upper() or '—'} qty={qty} @ {float(entry or 0):.2f} · "
            f"**已用降级ATR** · 标签 `atr_source=tv_implied_degrade`",
            G_LIGHT,
        ),
        "🛑 状态": _g(
            "后续自动开仓已暂停 · **需要人工介入排查行情引擎**",
            G_DEEP,
        ),
        "💡 恢复": _g(
            f"复验 ATR/ADX 误差<5% 后：`POST /admin/resume/{sym}`",
            G_LIGHT,
        ),
    }
    if detail:
        data["📋 明细"] = _g(str(detail), G_MUTED)
    send_alert(
        f"🚨 ATR应急降级·需人工介入 [{sym}]",
        data,
        G_TITLE,
        immediate=True,
    )


def report_tv_signal_received(action, entry_type="", price=0, regime=3, atr=0,
                              tv_sl=0, risk_pct=0, leverage=None, qty_ratio=1.0,
                              reason="", vps_sizing_meta=None, vps_hard_sl_note="",
                              bar_index=None, seq=None):
    """TV Webhook 信号到达（接收确认，非成交核实）"""
    act = str(action or "").upper()
    et = normalize_entry_type(entry_type)
    type_map = {
        ENTRY_TYPE_OPEN: "首次开仓 OPEN",
    }
    type_txt = type_map.get(et, et or "—")
    close_actions = {
        "CLOSE_QUICK_EXIT": "反转保护",
        "CLOSE_RSI_EXIT": "反转保护(RSI)",
    }
    if act in close_actions:
        type_txt = close_actions[act]
    data = {
        "📡 信号类型": _g(f"**{act}** · {type_txt}", G_ACCENT),
        "💹 TV价格": _g(f"`{float(price or 0):.2f}` USDT", G_MUTED),
        "📐 算仓模式": _sizing_mode_label(),
        "📡 ATR": _g(f"`{float(atr or 0):.2f}`", G_MUTED),
    }
    if bar_index is not None or seq is not None:
        data["⏱️ 时序"] = _g(
            f"bar_index=`{bar_index}` · seq=`{seq}`",
            G_LIGHT,
        )
    if tv_sl and float(tv_sl) > 0:
        data["🛡️ TV硬止损"] = _g(f"`{float(tv_sl):.2f}` (盘口挂单价)", G_MAIN)
    if vps_hard_sl_note:
        data["🛡️ 止损分工"] = _g(vps_hard_sl_note, G_LIGHT)
    if act == "CLOSE_STOPLOSS":
        data["⚡ TV第一指令"] = _g(
            "收到 CLOSE_STOPLOSS → **立即市价全平**（优先于 TV 硬止损挂单）",
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
                risk_pct, leverage or 0, qty_ratio, regime=regime,
            ),
            G_MUTED,
        )
    lev_show = float(leverage or 0)
    data["⚙️ 仓位杠杆"] = _g(
        f"**{lev_show:.0f}x**（仓位公式 + set_leverage 同源，禁止固定 25x）",
        G_MUTED,
    )
    if reason:
        data["📝 原因"] = _g(str(reason)[:120], G_MUTED)
    data["✅ 状态"] = _g("信号已入队 · 等待实盘核实后二次播报", G_MAIN)
    send_alert(f"📡 TV信号接收 · {act}", data, G_MUTED)


def report_tv_sl_updated(side, live_qty, entry, tv_sl, exchange_stop=None,
                         radar_active=False, radar_sl=None, regime=3,
                         verify_note="", verified=True):
    """兼容旧入口：UPDATE_SL 不再改挂盘口，仅记录 TV 参考。"""
    tv_sl = float(tv_sl or 0)
    exchange_stop = float(exchange_stop or 0)
    hung = exchange_stop if exchange_stop > 0 else float(radar_sl or 0)
    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "📦 保护头寸": _g(f"**{live_qty}** {_u()}", G_MAIN),
        "💰 开仓成本": _g(f"`{entry:.2f}` USDT", G_MUTED),
        "📐 算仓模式": _sizing_mode_label(),
        "📡 TV参考止损": _g(f"**{tv_sl:.2f}** USDT (不挂盘)", G_MUTED),
        "🫁 呼吸止损": _g(
            f"**{hung:.2f}** USDT" if hung > 0 else "哨兵维护中",
            G_MAIN,
        ),
        "✅ 风控动作": _g(
            "UPDATE_SL 已忽略TV改挂 · 盘口维持呼吸止损单槽",
            G_ACCENT,
        ),
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | UPDATE_SL 仅记参考，呼吸止损未改用TV价",
            f"⏳ {VERIFY_DELAY_MARK}",
        ),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert("🫁 呼吸止损 · UPDATE_SL 已忽略TV改挂", data, G_TITLE)


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
        "📐 算仓模式": _sizing_mode_label(),
        "📉 原 TP123": _g(_fmt(old_tps), G_MUTED),
        "🚀 新 TP123": _g(_fmt(new_tps), G_ACCENT),
        "📌 新挂档数": _g(f"**{int(placed or 0)}** 笔限价止盈", G_LIGHT),
        "💹 参考市价": _g(
            f"`{float(curr_px or 0):.2f}` USDT" if float(curr_px or 0) > 0 else "—",
            G_MUTED,
        ),
        "✅ 风控动作": _g(
            "动能 UPDATE_TP → 仅替换限价 TP123 · 呼吸止损 STOP 未触碰",
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


def report_tv_position_add(*args, **kwargs):
    """妈妈版已废除追加仓位：不再推送旧文案。"""
    return



def report_adverse_shield_armed(side, entry, live_qty, adverse_pct, tier_prices, tier_pcts,
                                verify_note="", vps_hard_sl_note=""):
    """兼容旧入口 → 呼吸止损已武装。"""
    stop_px = tier_prices[0] if tier_prices else entry
    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "💰 开仓成本": _g(f"`{entry:.2f}` USDT", G_MUTED),
        "📦 保护头寸": _g(f"**{live_qty}** {_u()} 全平", G_MAIN),
        "🫁 呼吸止损": _g(
            vps_hard_sl_note or f"`{stop_px:.2f}` USDT closePosition · entry±1.5×ATR",
            G_ACCENT,
        ),
        "✅ 风控动作": _g(
            "呼吸止损已挂 closePosition · 开仓即追踪 · "
            "TP123=reduceOnly 互不抢份额",
            G_MAIN,
        ),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert("🫁 呼吸止损 · 已武装", data, G_TITLE)


def report_shield_tier_fill(side, tier_pct, tier_price, filled_qty, remain_qty, entry_px,
                            remaining_tiers=None, verify_note=""):
    data = {
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "🫁 触发止损": _g(f"**呼吸止损** @ `{tier_price:.2f}` USDT", G_ACCENT),
        "✂️ 本次平仓": _g(f"`{filled_qty}` {_u()}", G_MAIN),
        "📊 剩余头寸": _g(f"`{remain_qty}` {_u()}", G_MAIN),
        "✅ 风控动作": _g("呼吸止损成交 → TP123 已重算", G_MAIN),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert("🫁 呼吸止损 · 成交", data, G_TITLE)


def report_shield_disarmed(side, live_qty, entry, cancelled_count, reason="",
                           radar_progress=0.0, verify_note="", verified=True,
                           symbol=None, unit_label=None):
    """旧「撤硬止损转雷达」已废除；保留兼容入口但不暗示交棒。"""
    unit = _resolve_unit(unit_label, symbol)
    sym = str(symbol or _ctx_symbol.get() or "").upper() or "?"
    data = {
        "🎛️ 品种": _g(f"**{sym}**", G_ACCENT),
        "🎛️ 实盘方向": _g(side, G_LIGHT if side == "LONG" else G_DEEP),
        "💰 开仓成本": _g(f"`{entry:.2f}` USDT", G_MUTED),
        "📦 剩余头寸": _g(f"**{live_qty}** {unit}", G_MAIN),
        "🫁 呼吸止损": _g("单槽合并运行中（硬止损+雷达已合一）", G_LIGHT),
        "🗑️ 清理次数": _g(f"**{cancelled_count}** 笔旧 STOP", G_ACCENT),
        "✅ 风控动作": _g(
            reason or "呼吸止损单槽维护 · 止损只前进不回撤",
            G_MAIN,
        ),
        "📡 实盘核查": _verify_line(
            verify_note if not verified else "",
            f"{VERIFY_TAG} | 呼吸止损单槽已对齐",
            f"⏳ 撤单已提交，{VERIFY_DELAY_MARK} | 哨兵继续维护",
        ),
    }
    if verify_note:
        data["🔍 核实明细"] = _g(verify_note, G_MUTED)
    send_alert(f"🫁 [{sym}] 呼吸止损 · 单槽维护", data, G_TITLE)


# breath-stop phase2 (ADX trail) — replaces old ladder handoff notify
def report_radar_activated(side, qty, entry, new_sl, radar_progress=1.0, regime=3,
                           shield_cleared=True, verify_note="", verified=True,
                           symbol=None, unit_label=None, trigger_gate="",
                           activation_price=None, adx=None, trail_dist=None):
    """阶段切换：浮盈达 3.0×ATR，进入 ADX 连续追踪。"""
    unit = _resolve_unit(unit_label, symbol)
    sym = str(symbol or _ctx_symbol.get() or "").upper() or "?"
    adx_v = float(adx or 0)
    trail_v = float(trail_dist or 0)
    data = {
        "🎛️ 品种": _g(f"**{sym}**", G_ACCENT),
        "阶段切换": _g(
            f"止损已进入阶段二（趋势追踪），当前ADX={adx_v:.1f}，追踪距离={trail_v:.2f}×ATR"
            if adx_v > 0 or trail_v > 0
            else "止损已进入阶段二（趋势追踪）",
            G_ACCENT,
        ),
        "当前ADX": _g(f"**{adx_v:.1f}**" if adx_v > 0 else "—", G_MAIN),
        "追踪距离": _g(f"**{trail_v:.2f}×ATR**" if trail_v > 0 else "—", G_LIGHT),
        "止损": _g(f"**{float(new_sl):.2f}**", G_DEEP),
        "头寸": _g(f"**{qty}** {unit} @ `{float(entry):.2f}`", G_MUTED),
    }
    if verify_note:
        data["核实"] = _g(str(verify_note)[:200], G_MUTED)
    send_alert(f"📡 [{sym}] 阶段切换：止损已进入阶段二", data, G_DEEP)

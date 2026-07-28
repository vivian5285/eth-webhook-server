#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
深币专用通知 — TG全量 + 钉钉重要告警，全紫色主题
"""
import os
import re
import time
import hmac
import hashlib
import base64
import html
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
    ENTRY_TYPE_PYRAMID,
    ENTRY_TYPE_PROFIT_ADD,
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
_ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(_ENV_PATH)
logger = logging.getLogger(__name__)

# Level 1：TG 全量；Level 2：TG + 钉钉重要告警
NOTIFY_LEVEL_ALL = 1
NOTIFY_LEVEL_CRITICAL = 2

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")
WECHAT_WEBHOOK = os.getenv("WECHAT_WEBHOOK", "").strip()
# TG 配置
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_PARSE_MODE = os.getenv("TELEGRAM_PARSE_MODE", "HTML").strip() or "HTML"
TELEGRAM_RETRY_MAX = max(1, int(os.getenv("TELEGRAM_RETRY_MAX", "3")))
TELEGRAM_RETRY_SEC = float(os.getenv("TELEGRAM_RETRY_SEC", "3"))
DINGTALK_BATCH_MAX = max(1, int(os.getenv("DINGTALK_BATCH_MAX", "8")))
DINGTALK_BATCH_FLUSH_SEC = float(os.getenv("DINGTALK_BATCH_FLUSH_SEC", "6"))
DINGTALK_BATCH_DISABLE = str(os.getenv("DINGTALK_BATCH_DISABLE", "")).strip().lower() in ("1", "true", "yes", "on")
DINGTALK_TITLE_DEDUP_SEC = float(os.getenv("DINGTALK_TITLE_DEDUP_SEC", "300"))
DINGTALK_ALERT_DEDUP_SEC = float(os.getenv("DINGTALK_ALERT_DEDUP_SEC", "600"))
_title_dedup_lock = threading.Lock()
_title_dedup_ts = {}
_notify_cfg_lock = threading.Lock()

_SYSTEM_ALERT_L1_MARKERS = (
    "智能再入场限价已挂", "智能再入已成交", "再入放弃",
    "重入尝试", "重入成功", "重入放弃", "心跳",
)


def reload_notify_config():
    """热加载 .env 通知配置"""
    global DINGTALK_WEBHOOK, DINGTALK_SECRET, WECHAT_WEBHOOK
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_PARSE_MODE
    global TELEGRAM_RETRY_MAX, TELEGRAM_RETRY_SEC
    global DINGTALK_BATCH_MAX, DINGTALK_BATCH_FLUSH_SEC, DINGTALK_BATCH_DISABLE
    global DINGTALK_TITLE_DEDUP_SEC, DINGTALK_ALERT_DEDUP_SEC
    global SYSTEM_NAME, FOOTER
    with _notify_cfg_lock:
        load_dotenv(_ENV_PATH, override=True)
        DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK", "")
        DINGTALK_SECRET = os.getenv("DINGTALK_SECRET", "")
        WECHAT_WEBHOOK = os.getenv("WECHAT_WEBHOOK", "").strip()
        TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        TELEGRAM_PARSE_MODE = os.getenv("TELEGRAM_PARSE_MODE", "HTML").strip() or "HTML"
        TELEGRAM_RETRY_MAX = max(1, int(os.getenv("TELEGRAM_RETRY_MAX", "3")))
        TELEGRAM_RETRY_SEC = float(os.getenv("TELEGRAM_RETRY_SEC", "3"))
        DINGTALK_BATCH_MAX = max(1, int(os.getenv("DINGTALK_BATCH_MAX", "8")))
        DINGTALK_BATCH_FLUSH_SEC = float(os.getenv("DINGTALK_BATCH_FLUSH_SEC", "6"))
        DINGTALK_BATCH_DISABLE = str(os.getenv("DINGTALK_BATCH_DISABLE", "")).strip().lower() in ("1", "true", "yes", "on")
        DINGTALK_TITLE_DEDUP_SEC = float(os.getenv("DINGTALK_TITLE_DEDUP_SEC", "300"))
        DINGTALK_ALERT_DEDUP_SEC = float(os.getenv("DINGTALK_ALERT_DEDUP_SEC", "600"))
        SYSTEM_NAME = os.getenv("NOTIFY_SYSTEM_NAME", "深币军师").strip() or "深币军师"
        FOOTER = f"*🖥️ {SYSTEM_NAME} · 深币紫金趋势大波段引擎*"
    status = notify_config_status()
    logger.info("notify config reloaded | system=%s tg=%s ding=%s",
        status.get("system_name"), status.get("telegram_configured"), status.get("dingtalk_configured"))
    return status


def notify_config_status():
    tok = bool(TELEGRAM_BOT_TOKEN)
    chat = bool(TELEGRAM_CHAT_ID)
    return {
        "system_name": SYSTEM_NAME,
        "telegram_configured": bool(tok and chat),
        "telegram_chat_id": TELEGRAM_CHAT_ID if chat else "",
        "dingtalk_configured": bool(DINGTALK_WEBHOOK or WECHAT_WEBHOOK),
        "wechat_backup": bool(WECHAT_WEBHOOK),
        "tg_retry_max": TELEGRAM_RETRY_MAX,
        "tg_retry_sec": TELEGRAM_RETRY_SEC,
    }


SYSTEM_NAME = os.getenv("NOTIFY_SYSTEM_NAME", "深币军师").strip() or "深币军师"
EXCHANGE_LABEL = "深币 Deepcoin"
LEVERAGE_LABEL = f"{int(os.getenv('VPS_MARGIN_LEVERAGE', 25))}x"
DEFAULT_LEVERAGE = 25
UNIT_LABEL = "张"


def _brand_title(title):
    t = str(title or "").strip()
    tag = f"【{SYSTEM_NAME}】"
    if t.startswith(tag) or t.startswith(f"[{SYSTEM_NAME}]"):
        return t
    return f"{tag}{t}"


_ctx_unit = contextvars.ContextVar("dingtalk_unit", default=None)
_ctx_symbol = contextvars.ContextVar("dingtalk_symbol", default=None)


def _resolve_unit(unit_label=None, symbol=None):
    if unit_label:
        u = str(unit_label).strip().upper()
        if u:
            return u
    sym = str(symbol or "").strip().upper().replace(".P", "")
    if ":" in sym:
        sym = sym.split(":")[-1]
    if "ETH" in sym:
        return "ETH"
    ctx_u = _ctx_unit.get()
    if ctx_u:
        return str(ctx_u).strip().upper()
    return UNIT_LABEL


def _u(unit_label=None, symbol=None):
    return _resolve_unit(unit_label, symbol)


def bind_dingtalk_symbol(symbol=None, unit_label=None):
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


# 深币紫色色板
P_TITLE = "#4B0082"
P_MAIN = "#9B59B6"
P_DEEP = "#6C3483"
P_LIGHT = "#BB8FCE"
P_ACCENT = "#8E44AD"
P_MUTED = "#A569BD"

FOOTER = f"*🖥️ {SYSTEM_NAME} · 深币紫金趋势大波段引擎*"
VERIFY_TAG = "✅ 实盘核查通过"
VERIFY_DELAY_MARK = "REST 同步略延迟"


def _g(text, color=P_MAIN):
    return f'<font color="{color}">{text}</font>'


def _verify_line(verify_note, ok_message, delay_message=None, ok_color=P_MAIN, delay_color=P_ACCENT):
    if verify_note and VERIFY_DELAY_MARK in verify_note:
        msg = delay_message or f"⏳ 已提交，{VERIFY_DELAY_MARK}"
        return _g(msg, delay_color)
    return _g(ok_message, ok_color)


def _classify_close(reason, verify_note="", swept_dust=False, close_type="", close_action="", tv_reason=""):
    r = reason or ""
    note = verify_note or ""
    is_dust_ctx = swept_dust or "蚂蚁仓" in note or "蚂蚁仓" in r
    ct = close_type or classify_tv_close(close_action, tv_reason or r)

    if ct == CLOSE_TYPE_TP3:
        return {"title": "止盈平仓（雷达追踪收网）", "tag": _g("**雷达收网**", P_LIGHT),
            "status": _g("TP3余仓由雷达追踪止盈离场。（永不挂TP3限价单）", P_LIGHT), "header": P_TITLE}
    if ct in (CLOSE_TYPE_PROTECT, CLOSE_TYPE_QUICK, CLOSE_TYPE_RSI):
        return {"title": f"反转保护平仓：{reason[:80]}", "tag": _g("**反转保护**", P_ACCENT),
            "status": _g("市价全平 + 撤单 + 状态重置。", P_ACCENT), "header": P_ACCENT}
    if ct == CLOSE_TYPE_BREAKEVEN:
        return {"title": "止损平仓（阶段二/趋势追踪）", "tag": _g("**止损平仓**", P_LIGHT),
            "status": _g("阶段二追踪止损触及，全平离场。", P_MAIN), "header": P_LIGHT}
    if ct in (CLOSE_TYPE_HARD_SL, CLOSE_TYPE_VPS_SHIELD):
        return {"title": "止损平仓（阶段一）", "tag": _g("**止损平仓**", P_DEEP),
            "status": _g("价格触及呼吸止损，市价全平。", P_DEEP), "header": P_DEEP}
    if is_dust_ctx:
        return {"title": "蚂蚁仓扫尾收网", "tag": _g("**扫尾收网**", P_MUTED),
            "status": _g("蚂蚁仓已扫平，账本复位待命。", P_LIGHT), "header": P_DEEP}
    return {"title": "常规清场", "tag": _g("**常规清场**", P_MUTED),
        "status": _g("旧阵地已爆破，账本归零等待新指令。", P_MUTED), "header": P_MUTED}


def _get_signed_url():
    if not DINGTALK_WEBHOOK:
        return ""
    if not DINGTALK_SECRET:
        return DINGTALK_WEBHOOK
    ts = str(round(time.time() * 1000))
    hmac_code = hmac.new(DINGTALK_SECRET.encode('utf-8'), f'{ts}\n{DINGTALK_SECRET}'.encode('utf-8'), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return f"{DINGTALK_WEBHOOK}&timestamp={ts}&sign={sign}"


def _post_dingtalk_once(title, markdown_text):
    signed_url = _get_signed_url()
    if not signed_url:
        return False, "no_webhook"
    payload = {"msgtype": "markdown", "markdown": {"title": title, "text": markdown_text}}
    try:
        r = requests.post(signed_url, json=payload, timeout=6)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        body = r.json() if r.text else {}
        err = body.get("errcode", 0)
        if err not in (0, None):
            return False, f"errcode={err}"
        return True, "ok"
    except Exception as e:
        return False, str(e)


def _post_with_retry(title, markdown_text, max_attempts=3):
    delays = (1.0, 2.0, 4.0)
    last_err = ""
    for i in range(max_attempts):
        ok, info = _post_dingtalk_once(title, markdown_text)
        if ok:
            _batcher.mark_success()
            return True
        last_err = info
        if i < max_attempts - 1:
            time.sleep(delays[i])
    _batcher.mark_fail()
    return False


_FONT_TAG_RE = re.compile(r"</?font[^>]*>", re.I)


def _strip_rich(text):
    s = _FONT_TAG_RE.sub("", str(text or ""))
    s = s.replace("**", "")
    return s.strip()


def _build_tg_text(title, data_dict):
    """Telegram HTML 正文"""
    now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    branded = _brand_title(title)
    lines = [f"<b>{html.escape(_strip_rich(branded))}</b>",
             f"🖥️ {html.escape(SYSTEM_NAME)} · ⏰ {html.escape(now_time)} · {html.escape(EXCHANGE_LABEL)}", ""]
    for k, v in (data_dict or {}).items():
        lines.append(f"<b>{html.escape(_strip_rich(k))}</b>: {html.escape(_strip_rich(v))}")
    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3900] + "\n…(截断)"
    return text


def send_telegram(message, parse_mode=None):
    """发送 Telegram 消息"""
    token = (TELEGRAM_BOT_TOKEN or "").strip()
    chat_id = (TELEGRAM_CHAT_ID or "").strip()
    if not token or not chat_id:
        logger.warning("notify skip channel=telegram reason=not_configured")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    mode = parse_mode if parse_mode is not None else TELEGRAM_PARSE_MODE
    payload = {"chat_id": chat_id, "text": str(message or "")}
    if mode:
        payload["parse_mode"] = mode
        payload["disable_web_page_preview"] = True
    attempts = max(1, int(TELEGRAM_RETRY_MAX or 3))
    delay = float(TELEGRAM_RETRY_SEC or 3)
    for i in range(attempts):
        try:
            r = requests.post(url, json=payload, timeout=8)
            body = r.json() if r.text else {}
            if r.status_code == 200 and body.get("ok"):
                return True
        except Exception as e:
            last_err = str(e)
        if i < attempts - 1:
            time.sleep(delay)
    return False


def _fire_telegram_async(text):
    """后台线程发 TG"""
    def _run():
        try:
            send_telegram(text)
        except Exception as e:
            logger.error("notify tg async exception: %s", e)
    try:
        threading.Thread(target=_run, daemon=True, name="tg-notify").start()
    except Exception as e:
        logger.error("notify tg thread spawn failed: %s", e)


def _build_alert_markdown(title, data_dict, header_color=P_TITLE):
    text_lines = [f"- **{k}** : {v}" for k, v in (data_dict or {}).items()]
    body_text = "\n".join(text_lines)
    now_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    branded = _brand_title(title)
    return f"""### <font color="{header_color}">{branded}</font>
> **🏷️ 系统**：`{SYSTEM_NAME}（非币安）`
> **⏰ 时间**：`{now_time}`
> **📍 交易所**：[ {EXCHANGE_LABEL} ]
> **🔷 主题色带**：`深币紫金`

---
{body_text}

---
{FOOTER}
"""


class _DingTalkBatcher:
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

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
            threading.Thread(target=self._loop, daemon=True, name="dingtalk-batch").start()
            logger.info(f"📬 钉钉攒批已启动：flush={DINGTALK_BATCH_FLUSH_SEC}s")

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
            if batch and (len(batch) >= DINGTALK_BATCH_MAX or now - last_flush >= DINGTALK_BATCH_FLUSH_SEC):
                try:
                    self._flush(batch)
                except Exception as e:
                    logger.error(f"钉钉攒批 flush 异常: {e}")
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
            parts.append(f'### <font color="{color}">{title}</font> `{tstr}`\n{body}')
        now_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        merged_title = _brand_title(f"📦 播报合并 ×{len(batch)}")
        markdown_text = f"""### <font color="{P_TITLE}">{merged_title}</font>
> **🏷️ 系统**：`{SYSTEM_NAME}`
> **⏰ 时间**：`{now_time}`
> **📍 交易所**：[ {EXCHANGE_LABEL} ]
> **📊 条数**：`{len(batch)}`

---
{chr(10).join(parts)}

---
{FOOTER}
"""
        _post_with_retry(merged_title, markdown_text)


_batcher = _DingTalkBatcher()


def send_notification(message=None, level=NOTIFY_LEVEL_ALL, *, title="", data_dict=None, header_color=None, immediate=False):
    ttl = title or (str(message)[:48] if message else "通知")
    if message is not None and data_dict is None:
        send_alert(ttl, {"内容": str(message)}, header_color or P_TITLE, immediate=immediate, level=level, _tg_text=str(message))
        return
    send_alert(ttl, data_dict or {}, header_color or P_TITLE, immediate=immediate, level=level)


def send_alert(title, data_dict, header_color=P_TITLE, immediate=False, level=NOTIFY_LEVEL_ALL, _tg_text=None):
    """统一通知入口：level=1 仅TG，level=2 TG+钉钉"""
    try:
        lvl = int(level or NOTIFY_LEVEL_ALL)
    except (TypeError, ValueError):
        lvl = NOTIFY_LEVEL_ALL
    raw_title = str(title or "")
    tg_body = _tg_text if _tg_text is not None else _build_tg_text(title, data_dict)
    has_tg = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
    has_ding = bool(DINGTALK_WEBHOOK)
    if not has_tg and not has_ding:
        logger.warning("notify skip all channels: %s", raw_title[:72])
        return

    # TG：全量发送
    if has_tg:
        _fire_telegram_async(tg_body)
    else:
        logger.warning("notify tg not configured")

    # 钉钉：仅重要告警
    if lvl < NOTIFY_LEVEL_CRITICAL:
        return
    if not has_ding:
        return
    branded = _brand_title(raw_title)
    if immediate or DINGTALK_BATCH_DISABLE:
        md = _build_alert_markdown(title, data_dict, header_color)
        _post_with_retry(branded, md)
        return
    _batcher.enqueue(branded, data_dict, header_color)


def dingtalk_batch_stats():
    return _batcher.stats()

"""
监控面板 · VPS 本地版：直接读本机状态文件 + journalctl，不经 SSH。
不 import position_supervisor_binance，不改任何账户的运行参数/重启服务。

2026-08-12 新增：/api/close_position ——唯一的写操作，只做"市价全平+撤净残留挂单"
这一件事，通过 subprocess 跑进对应账户自己的 venv，只调用 binance_client 的
市价单/撤单方法（跟今天全天手动平仓时用的是同一套只读client-layer之上的
最小必要写操作），不触碰雷达/止损计算逻辑，不导入 position_supervisor。

2026-08-17 新增：TV 信号日志聚合 + 重放/编辑后重放 + 手动发单——三个账户各自
的 binance-engine 已经有一套完整的 Console API（/api/console/tv_signals 等，
登录会话保护，内部经过 webhook_parser 的完整校验+去重+风控），本面板不重新
实现任何交易逻辑，只是本机 HTTP 回环去"代按"那套已有、已验证的接口：读各账户
自己 .env 里的 CONSOLE_PASSWORD 登录换 session cookie，再转发一次调用。跟
close_position 一样只调用已有的最小必要接口，不导入 position_supervisor，
不绕过原账户自己的鉴权/校验/去重逻辑。

2026-08-17 新增：策略/回测面板——数据来自完全独立的 strategy_engine 服务
（部署在 /root/strategy-engine/，无API Key、不import任何账户代码、只读
公开K线自己算指标出信号）。本面板对它的数据只做只读查询（直接开
strategy_engine 自己维护的 sqlite 文件，跟读账户 state 文件是同一个"直接
读别的服务落盘产物"的模式，不需要额外起HTTP层）；"跑回测"这个唯一的
写操作，走跟 close_position 一样的 subprocess 调用模式——调 strategy_engine
自己venv里的python跑一次性脚本，dashboard进程本身不import它的任何代码。
"""
import http.cookiejar
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from flask import Flask, jsonify, Response, request

SYMBOLS = ["ETHUSDT", "XAUUSDT", "BNBUSDT", "ZECUSDT", "BCHUSDT", "XMRUSDT", "SNDKUSDT", "PAXGUSDT", "SKHYNIXUSDT", "XPDUSDT", "OPENAIUSDT", "ANTHROPICUSDT", "ASMLUSDT", "GSUSDT", "MUUSDT", "LITEUSDT"]
ACCOUNTS = [
    {"id": "B", "port": 5007, "label": "妈妈的币安账户", "user": "binanceB", "svc": "binanceB-engine"},
    {"id": "C", "port": 5008, "label": "我自己的币安账户", "user": "binanceC", "svc": "binanceC-engine"},
    {"id": "D", "port": 5009, "label": "我的币安子账户", "user": "binanceD", "svc": "binanceD-engine"},
    {"id": "E", "port": 5010, "label": "MARIO账户", "user": "binanceE", "svc": "binanceE-engine"},
]

STATE_MARK = "===STATE:{acct}:{sym}==="
LOG_MARK = "===LOGS:{acct}==="
SVC_MARK = "===SVC==="
REV_MARK = "===REV:{acct}==="
PRICE_MARK = "===PRICES==="

BINANCE_PRICE_URL = "https://fapi.binance.com/fapi/v1/ticker/price"

_cache_lock = threading.Lock()
_cache = {"ts": 0, "data": None, "error": None}
CACHE_TTL_SEC = 30  # 2026-08-12：从15s拉长到30s，降低对币安API的调用频率（跟watchdog同一次调整）

CLOSE_CODE_TEMPLATE = """
import json
from binance_client import binance_client
symbol = {symbol!r}
p = binance_client.get_position(symbol, prefer_ws=False, force_rest=True)
if not p or float(p.get("positionAmt", 0) or 0) == 0:
    print(json.dumps({{"ok": False, "msg": "仓位已空，无需平仓"}}))
else:
    amt = float(p["positionAmt"])
    side = "SELL" if amt > 0 else "BUY"
    qty = abs(amt)
    order = binance_client.place_market_order(side, qty, symbol=symbol, reduce_only=True)
    if order:
        binance_client.cancel_all_open_orders(symbol)
        print(json.dumps({{"ok": True, "msg": "平仓成功", "order_id": order.get("orderId"), "qty": qty, "side": side}}))
    else:
        print(json.dumps({{"ok": False, "msg": "市价平仓下单失败，请人工检查交易所"}}))
"""


def _run(cmd, timeout=15, cwd=None):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd)
        return p.stdout.decode("utf-8", errors="replace")
    except Exception as e:
        return f""


def fetch_live_prices():
    """公开行情接口，无需 API key，只读现价，不碰任何账户。"""
    try:
        req = urllib.request.Request(
            BINANCE_PRICE_URL, headers={"User-Agent": "dashboard-readonly"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw = resp.read().decode("utf-8")
        rows = json.loads(raw)
        wanted = set(SYMBOLS)
        return {
            r["symbol"]: float(r["price"])
            for r in rows if r.get("symbol") in wanted
        }
    except Exception:
        return {}


def build_local_raw():
    parts = []
    parts.append(PRICE_MARK)
    parts.append(json.dumps(fetch_live_prices()))
    for a in ACCOUNTS:
        for sym in SYMBOLS:
            parts.append(STATE_MARK.format(acct=a["id"], sym=sym))
            path = f'/home/{a["user"]}/binance-engine/binance_vps_state_{sym}.json'
            try:
                with open(path, encoding="utf-8") as f:
                    parts.append(f.read())
            except Exception:
                parts.append("{}")
    for a in ACCOUNTS:
        parts.append(REV_MARK.format(acct=a["id"]))
        rev = _run([
            "sudo", "-u", a["user"], "git", "-C",
            f'/home/{a["user"]}/binance-engine', "log", "--oneline", "-1",
        ])
        parts.append(rev.strip())
    for a in ACCOUNTS:
        parts.append(LOG_MARK.format(acct=a["id"]))
        parts.append(_run(["journalctl", "-u", a["svc"], "--no-pager", "-n", "400"], timeout=20))
    parts.append(SVC_MARK)
    svc_out = _run(["systemctl", "is-active"] + [a["svc"] for a in ACCOUNTS])
    parts.append(svc_out.strip())
    return "\n".join(parts)


LOG_LINE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[(\w+)\] Brain: (.*)"
)

EVENT_RULES = [
    ("tv_signal", re.compile(r"TV信号接收")),
    # 系统内部单（Console 手动发单/编辑重放，限价挂开仓单）必须排在通用
    # "open" 规则前面单独识别，否则会被"极速开仓"之类子串误吞（这两条
    # 文案刻意不重叠，但顺序仍然重要——EVENT_RULES 是"先命中先归类"）。
    ("system_order", re.compile(r"系统限价开仓")),
    ("open", re.compile(r"极速开仓|市价开仓成功|开仓共同第一步")),
    ("close", re.compile(r"平仓成功|全部平仓|止损触发平仓|止盈触发平仓")),
    ("tp_fill", re.compile(r"TP[123].*(成交|在盘口)")),
    ("radar", re.compile(r"雷达止损对齐|雷达已激活|雷达休眠至激活")),
    ("recover", re.compile(r"重启恢复完成|实盘阵地接管完毕|系统重启点火")),
    ("anomaly", re.compile(
        r"拒绝雷达止损：市价不安全|HARD_SL_FAIL_ABORT|裸仓|止损未确认|"
        r"挂单查询失败|止损缺失|暂停交易|trading_paused.*true"
    )),
]

NOISE_RE = re.compile(
    r"\[STATE\]|ADX档锁定|行情引擎|30s snapshot|对齐安静期跳过|重连等待|"
    r"WS 断开|WS 错误|Websocket connected|增订:|币安公开 WS 启动|币安私有 WS 启动|"
    r"notify ok|持久化订单ID|清理陈旧防御标签|敞口校验通过|仓位预算|开仓qty核算|"
    r"^🏷️|"
    r"TP已齐.*但止损未确认.*只补STOP|"
    # 2026-08-20：跟watchdog/check.py同步——"终检防线未齐"是重启终检瞬间的
    # 正常过渡状态，实测两次都是不到1秒内自己补挂修好，本面板有独立于
    # watchdog的这一份anomaly解析(读同一份journalctl)，之前只在watchdog
    # 那边过滤了，这里没同步，"异常N"角标照样会亮
    r"终检防线未齐|"
    # Telegram单次超时(还有重试机会)不算真失败，只有attempt=3/3才算真的
    # 通知不出去
    r"notify fail channel=telegram attempt=1/|"
    r"notify fail channel=telegram attempt=2/"
)

KNOWN_HARMLESS_ERROR_RE = re.compile(
    r"AttributeError: 'Client' object has no attribute 'session'|"
    r"NoneType. object has no attribute 'sock'"
)

# 2026-08-12：这类日志文案里已经明确写着"自己确认过、不用管"，之前被
# "挂单查询失败"这个anomaly规则的子串命中，跟真ERROR混在一起计数进顶部
# 红色"异常N"，容易让人误以为是新问题（实盘复现两次：IP限流下哨兵抢先
# 撤单，撤单本身成功，只是事后验证查询被限流，日志原话就是"已确认无
# 持仓→不暂停交易"）。单独分一类，进事件流但不进异常计数。
SELF_HEALED_RE = re.compile(
    r"已确认无持仓.*不暂停交易|平仓完成但.*查询失败.*不暂停交易"
)

# 2026-08-12：实盘复现两次（B账户XAU、D账户ETH+XAU）——行情插针直接
# 击穿刚激活的雷达止损，系统在"仓位已归零"确认落地前会有一串止损/TP
# 重挂失败的ERROR（挂单方向本身没错，只是仓位已经不在了，交易所正常
# 拒绝reduceOnly单）。这串chatter只有在同一时间窗口内能找到明确的
# "确认空仓"类日志时才降级，避免把真正的裸仓错误也一起放过。
CLOSING_CHATTER_RE = re.compile(
    r"止损单失败.*Order would immediately trigger|"
    r"TP后永久硬止损缺失且补挂失败|"
    r"限价单失败.*ReduceOnly Order is rejected|"
    r"❌ (挂|补挂|UPDATE_TP 挂) TP\d|"
    r"核武轮.*补挂=0|"
    r"止损 @[\d.]+ 已穿/贴市.*禁止推宽.*紧急平仓|"
    # 2026-08-24新增：跟watchdog同步——实盘复现(C账户PAXG)确认这条也是
    # 同一类"平仓过程中TP刷新撞上仓位已经归零"的收尾噪音，不是真裸仓。
    r"TV/账本/盘口均无有效 TP123"
)

FLAT_CONFIRM_RE = re.compile(
    r"确认空仓：WS\+REST均为0|"
    r"止损挂单未核实但复查仓位已归零|"
    r"仓位已由雷达/TP实际平仓，无需再挂止损|"
    r"确认平仓.*清除stale本地状态|"
    r"雷达/防线账本已清零"  # 2026-08-17：跟watchdog同步——这条才是平仓/账本
                            # 清零最常见的实际文案，原来四条经常对不上
)

FLAT_CONFIRM_WINDOW_SEC = 90

# 2026-08-17：跟watchdog/check.py同步加的第二条降噪证据——只靠"最终仓位
# 清零"太粗，裸奔窗口可能长达一两分钟才等到真正平仓，中间风险和"几秒内就
# 补上另一层防线"完全不是一回事。实测案例：B账户ETH止损补挂失败→4秒内
# 雷达止损就补上→117秒后才真正平仓，原90秒窗口没识别出来，其实裸奔窗口
# 只有4秒。新增"防线很快补上"这条独立证据，覆盖率更高也更贴近真实风险。
DEFENSE_RESTORED_RE = re.compile(
    r"place (HARD|RADAR) stop|"
    r"雷达止损已挂|"
    r"硬止损已挂"
)
DEFENSE_RESTORED_WINDOW_SEC = 30

ERROR_LINE_RE = re.compile(r"\[ERROR\]|Traceback|🚨")


def classify_line(ts, level, msg):
    if NOISE_RE.search(msg) or KNOWN_HARMLESS_ERROR_RE.search(msg):
        return None
    if SELF_HEALED_RE.search(msg):
        return "self_healed"
    for kind, rx in EVENT_RULES:
        if rx.search(msg):
            return kind
    if level == "ERROR" or "🚨" in msg:
        return "anomaly"
    return None


def _parse_ts(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def parse_logs_for_account(raw_block):
    events = []
    anomalies = []
    flat_confirm_ts = []
    defense_restored_ts = []
    parsed_lines = []

    for line in raw_block.splitlines():
        m = LOG_LINE_RE.search(line)
        if m:
            ts, level, msg = m.group(1), m.group(2), m.group(3)
            if FLAT_CONFIRM_RE.search(msg):
                dt = _parse_ts(ts)
                if dt:
                    flat_confirm_ts.append(dt)
            if DEFENSE_RESTORED_RE.search(msg):
                dt = _parse_ts(ts)
                if dt:
                    defense_restored_ts.append(dt)
            kind = classify_line(ts, level, msg)
            if kind:
                parsed_lines.append({"ts": ts, "level": level, "msg": msg.strip(), "kind": kind})
            continue
        if KNOWN_HARMLESS_ERROR_RE.search(line):
            continue
        stripped = line.strip()
        if "Traceback (most recent call last):" in stripped or "Exception ignored in:" in stripped:
            continue
        if SELF_HEALED_RE.search(stripped):
            continue
        if ERROR_LINE_RE.search(line):
            parsed_lines.append({"ts": "", "level": "ERROR", "msg": stripped[-200:], "kind": "anomaly"})

    for item in parsed_lines:
        kind = item.pop("kind")
        if kind == "anomaly" and CLOSING_CHATTER_RE.search(item["msg"]):
            dt = _parse_ts(item["ts"]) if item["ts"] else None
            if dt:
                restored = any(
                    0 <= (r - dt).total_seconds() <= DEFENSE_RESTORED_WINDOW_SEC
                    for r in defense_restored_ts
                )
                flattened = any(
                    abs((dt - c).total_seconds()) <= FLAT_CONFIRM_WINDOW_SEC
                    for c in flat_confirm_ts
                )
                if restored or flattened:
                    kind = "self_healed"
        if kind == "anomaly":
            anomalies.append(item)
        else:
            item["kind"] = kind
            events.append(item)

    events = events[-60:]
    anomalies = anomalies[-30:]
    events.reverse()
    anomalies.reverse()
    return events, anomalies


def parse_raw(raw):
    result = {a["id"]: {
        "id": a["id"], "port": a["port"], "label": a["label"],
        "positions": [], "events": [], "anomalies": [], "svc_active": None,
        "git_rev": "", "grid_pending": [], "catchup_status": [],
    } for a in ACCOUNTS}

    prices = {}
    price_m = re.search(r"===PRICES===\n(.*?)(?=\n===|\Z)", raw, re.S)
    if price_m:
        try:
            prices = json.loads(price_m.group(1).strip() or "{}")
        except Exception:
            prices = {}

    state_pattern = re.compile(r"===STATE:(\w):(\w+)===\n(.*?)(?=\n===|\Z)", re.S)
    for m in state_pattern.finditer(raw):
        acct, sym, blob = m.group(1), m.group(2), m.group(3).strip()
        if acct not in result:
            continue
        try:
            s = json.loads(blob) if blob else {}
        except Exception:
            s = {}
        qty = float(s.get("watched_qty", 0) or 0)
        if qty > 0 and s.get("current_side"):
            tps = list(s.get("tv_tps", []) or [])
            consumed = set(s.get("tp_levels_consumed", []) or [])
            side = s.get("current_side")
            entry = float(s.get("watched_entry", 0) or 0)
            mark = float(prices.get(sym, 0) or 0)
            pnl = None
            pnl_pct = None
            if mark > 0 and entry > 0:
                direction = 1 if side == "LONG" else -1
                pnl = round((mark - entry) * qty * direction, 2)
                pnl_pct = round((mark - entry) / entry * 100 * direction, 2)
            result[acct]["positions"].append({
                "symbol": sym,
                "side": side,
                "qty": qty,
                "entry": entry,
                "mark": mark,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "current_sl": float(s.get("current_sl", 0) or 0),
                "initial_stop": float(s.get("initial_stop", 0) or 0),
                "frozen_hard_sl": float(s.get("frozen_hard_sl_px", 0) or 0),
                "tv_tps": tps,
                "tp_consumed": sorted(consumed),
                "radar_activated": bool(s.get("radar_activated", False)),
                "best_price": float(s.get("best_price", 0) or 0),
                "trading_paused": bool(s.get("trading_paused", False)),
                "pause_reason": s.get("trading_pause_reason", "") or "",
                "position_source": s.get("position_source") or "TV",
            })
        elif s.get("grid_pending_order_id"):
            # 网格套利限价还没成交(watched_qty=0)，不算持仓卡片，单独一个
            # "挂单中"列表——跟持仓卡片走同一份state文件读取，不用额外请求。
            result[acct]["grid_pending"].append({
                "symbol": sym,
                "side": s.get("grid_pending_side") or "",
                "deadline_ts": float(s.get("grid_pending_deadline_ts", 0) or 0),
            })
        elif bool(s.get("catchup_active")):
            # 2026-08-21新增：TV心跳漏单追回已经武装(限价已挂/已升级市价)——
            # 跟grid_pending同一份state文件读取，不用额外请求/不导入
            # position_supervisor，纯只读展示。
            result[acct]["catchup_status"].append({
                "symbol": sym,
                "phase": str(s.get("catchup_phase") or "armed"),
                "side": s.get("catchup_side") or "",
                "tv_entry": float(s.get("catchup_tv_entry_frozen", 0) or 0),
                "limit_px": float(s.get("catchup_limit_px", 0) or 0),
                "deadline_ts": float(s.get("catchup_limit_deadline_ts", 0) or 0),
                "refreshes": int(s.get("catchup_unfilled_refreshes", 0) or 0),
            })
        elif str(s.get("tv_heartbeat_side") or "FLAT").upper() in ("LONG", "SHORT"):
            # 还没武装(可能还在180秒宽限期内，或者EMA多周期还没一起确认)——
            # 心跳显示TV仍持仓、本地却空仓，这个组合本身就值得让宝贝随时
            # 看到，不用等追回真正挂单才显示。
            result[acct]["catchup_status"].append({
                "symbol": sym,
                "phase": "watching",
                "side": str(s.get("tv_heartbeat_side") or ""),
                "tv_entry": float(s.get("tv_heartbeat_entry", 0) or 0),
                "limit_px": 0.0,
                "deadline_ts": 0.0,
                "refreshes": 0,
            })

    rev_pattern = re.compile(r"===REV:(\w)===\n(.*?)(?=\n===|\Z)", re.S)
    for m in rev_pattern.finditer(raw):
        acct, rev = m.group(1), m.group(2).strip().splitlines()
        if acct in result and rev:
            result[acct]["git_rev"] = rev[0][:80]

    log_pattern = re.compile(r"===LOGS:(\w)===\n(.*?)(?=\n===|\Z)", re.S)
    for m in log_pattern.finditer(raw):
        acct, blob = m.group(1), m.group(2)
        if acct not in result:
            continue
        events, anomalies = parse_logs_for_account(blob)
        result[acct]["events"] = events
        result[acct]["anomalies"] = anomalies

    svc_m = re.search(r"===SVC===\n(.*?)\Z", raw, re.S)
    if svc_m:
        lines = [l.strip() for l in svc_m.group(1).splitlines() if l.strip()]
        for a, st in zip(ACCOUNTS, lines):
            if a["id"] in result:
                result[a["id"]]["svc_active"] = (st == "active")

    return {"accounts": [result[a["id"]] for a in ACCOUNTS], "fetched_at": time.time()}


def refresh_cache_once():
    try:
        raw = build_local_raw()
        data = parse_raw(raw)
        err = None
    except Exception as e:
        data = _cache["data"]
        err = str(e)
    with _cache_lock:
        _cache["ts"] = time.time()
        _cache["data"] = data
        _cache["error"] = err


def background_refresher():
    while True:
        refresh_cache_once()
        time.sleep(CACHE_TTL_SEC)


app = Flask(__name__)


@app.route("/api/status")
def api_status():
    force = request.args.get("force") == "1"
    if force:
        refresh_cache_once()
    with _cache_lock:
        data, err = _cache["data"], _cache["error"]
    return jsonify({"ok": err is None, "error": err, "data": data})


@app.route("/api/close_position", methods=["POST"])
def api_close_position():
    """市价全平 + 撤净残留挂单。唯一的写操作，只调用 binance_client，不导入
    position_supervisor_binance，不改雷达/止损参数，不触碰其它任何品种/账户。"""
    body = request.get_json(force=True, silent=True) or {}
    acct_id = str(body.get("account") or "").strip()
    symbol = str(body.get("symbol") or "").strip()
    acct = next((a for a in ACCOUNTS if a["id"] == acct_id), None)
    if not acct or symbol not in SYMBOLS:
        return jsonify({"ok": False, "msg": "参数无效"}), 400

    acct_dir = f'/home/{acct["user"]}/binance-engine'
    code = CLOSE_CODE_TEMPLATE.format(symbol=symbol)
    result = {"ok": False, "msg": "未知错误"}
    try:
        p = subprocess.run(
            [f"{acct_dir}/venv/bin/python", "-c", code],
            capture_output=True, timeout=30, cwd=acct_dir,
        )
        out = p.stdout.decode("utf-8", errors="replace").strip()
        err = p.stderr.decode("utf-8", errors="replace").strip()
        if out:
            result = json.loads(out.splitlines()[-1])
        else:
            result = {"ok": False, "msg": f"平仓子进程无输出: {err[-200:]}"}
    except Exception as e:
        result = {"ok": False, "msg": f"平仓执行异常: {e}"}

    print(f"[CLOSE_POSITION] account={acct_id} symbol={symbol} result={result}", flush=True)
    refresh_cache_once()
    return jsonify(result)


# ── TV 信号日志聚合 / 重放 / 手动发单：本机回环"代按"各账户自己的 Console API ──

def _console_password(user):
    path = f"/home/{user}/binance-engine/.env"
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("CONSOLE_PASSWORD=") or line.startswith("ADMIN_PASSWORD="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return "binance-console"


def _console_call(acct, method, path, body=None, timeout=12):
    """登录该账户自己的 Console（换 session cookie）后转发一次 API 调用。
    返回 (http_status, json_or_text)。任何失败都不抛出，返回 (0, {...error...})。"""
    port = acct["port"]
    base = f"http://127.0.0.1:{port}"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login_body = json.dumps({"password": _console_password(acct["user"])}).encode("utf-8")
    try:
        opener.open(
            urllib.request.Request(
                base + "/api/console/login", data=login_body,
                headers={"Content-Type": "application/json"}, method="POST",
            ),
            timeout=timeout,
        )
    except Exception as e:
        return 0, {"status": "error", "message": f"console_login_failed: {e}"}

    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        code = e.code
    except Exception as e:
        return 0, {"status": "error", "message": str(e)}
    try:
        return code, json.loads(raw)
    except Exception:
        return code, {"status": "error", "message": "bad_upstream_response", "raw": raw[:300]}


def _find_account(acct_id):
    return next((a for a in ACCOUNTS if a["id"] == str(acct_id or "").strip().upper()), None)


@app.route("/api/tv_meta")
def api_tv_meta():
    acct = _find_account(request.args.get("account") or "B")
    if not acct:
        return jsonify({"status": "error", "message": "bad_account"}), 400
    code, data = _console_call(acct, "GET", "/api/console/tv_signals/meta")
    return jsonify(data), (code or 502)


@app.route("/api/tv_signals")
def api_tv_signals():
    acct_id = (request.args.get("account") or "").strip().upper()
    accts = [a for a in ACCOUNTS if a["id"] == acct_id] if acct_id and acct_id != "ALL" else ACCOUNTS
    fwd_params = {k: v for k, v in request.args.items() if k != "account"}
    qs = urllib.parse.urlencode(fwd_params)
    suffix = ("?" + qs) if qs else ""
    merged = []
    errors = {}
    for acct in accts:
        code, data = _console_call(acct, "GET", "/api/console/tv_signals" + suffix)
        if code == 200 and isinstance(data, dict):
            for row in (data.get("signals") or []):
                row = dict(row)
                row["_account"] = acct["id"]
                row["_account_label"] = acct["label"]
                merged.append(row)
        else:
            errors[acct["id"]] = data.get("message") if isinstance(data, dict) else str(data)
    merged.sort(key=lambda r: r.get("received_at", 0), reverse=True)
    return jsonify({"status": "ok", "signals": merged[:200], "errors": errors})


@app.route("/api/tv_replay", methods=["POST"])
def api_tv_replay():
    body = request.get_json(force=True, silent=True) or {}
    acct = _find_account(body.get("account"))
    sig_id = body.get("signal_id")
    if not acct or not sig_id:
        return jsonify({"status": "error", "message": "bad_params"}), 400
    try:
        sig_id = int(sig_id)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "bad_signal_id"}), 400
    overrides = body.get("overrides") or {}
    if not isinstance(overrides, dict):
        return jsonify({"status": "error", "message": "overrides_must_be_object"}), 400
    # limit_timeout_min 是顶层字段（不属于 overrides），账户自己的
    # console_api.py 的 _inject_system_limit_order 从请求体顶层读取，
    # 转发时必须一并带上，否则限价超时会静默退回默认 5 分钟。
    relay_body = {"overrides": overrides}
    if body.get("limit_timeout_min") is not None:
        relay_body["limit_timeout_min"] = body.get("limit_timeout_min")
    if body.get("order_type"):
        relay_body["order_type"] = body.get("order_type")
    code, data = _console_call(
        acct, "POST", f"/api/console/tv_signals/{sig_id}/replay", body=relay_body
    )
    print(f"[TV_REPLAY] account={acct['id']} signal_id={sig_id} overrides={overrides} -> {data}", flush=True)
    return jsonify(data), (code or 502)


@app.route("/api/price/<symbol>", methods=["GET"])
def api_price(symbol):
    """只读代查币安公开现价，跟哪个账户无关，不需要登录哪个账户。"""
    sym = str(symbol or "").strip().upper()
    # 2026-08-18修复：TV信号页面"编辑重放"里的品种直接来自TV原始payload
    # （比如"ANTHROPICUSDT.P"），取现价按钮把这个原样传给这个接口——币安
    # 公开行情端点不认TV那个".P"永续合约后缀，会400。手动发单面板走的是
    # mSymbol下拉菜单（值本来就是干净的symbol），没这个问题；这里统一做
    # 一次归一化，两边都覆盖到。
    if ":" in sym:
        sym = sym.rsplit(":", 1)[-1]
    if sym.endswith(".P"):
        sym = sym[:-2]
    if not sym:
        return jsonify({"status": "error", "message": "bad_symbol"}), 400
    url = "https://fapi.binance.com/fapi/v1/ticker/price?" + urllib.parse.urlencode({"symbol": sym})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "dashboard-price-lookup"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        price = float(data.get("price") or 0)
        if price <= 0:
            return jsonify({"status": "error", "message": "empty_price"}), 502
        return jsonify({"status": "ok", "symbol": sym, "price": price})
    except urllib.error.HTTPError as e:
        return jsonify({"status": "error", "message": f"binance_http_{e.code}"}), 502
    except Exception as e:
        return jsonify({"status": "error", "message": f"lookup_failed: {e}"}), 502


@app.route("/api/tv_manual_send", methods=["POST"])
def api_tv_manual_send():
    body = request.get_json(force=True, silent=True) or {}
    acct = _find_account(body.get("account"))
    if not acct:
        return jsonify({"status": "error", "message": "bad_account"}), 400
    payload = {k: v for k, v in body.items() if k != "account"}
    code, data = _console_call(acct, "POST", "/api/console/tv_manual_send", body=payload)
    print(f"[TV_MANUAL_SEND] account={acct['id']} payload={payload} -> {data}", flush=True)
    return jsonify(data), (code or 502)


# ── 策略/回测面板：直接读 strategy_engine 自己维护的 sqlite，"跑回测"走
# subprocess 调用它自己venv的python（跟 close_position 同一种模式）──────────

STRATEGY_ENGINE_DIR = "/root/strategy-engine"
STRATEGY_ENGINE_PY = f"{STRATEGY_ENGINE_DIR}/venv/bin/python"
SHADOW_DB_PATH = f"{STRATEGY_ENGINE_DIR}/strategy_engine/data/shadow.db"


def _shadow_query(sql, params=()):
    if not os.path.exists(SHADOW_DB_PATH):
        return []
    try:
        conn = sqlite3.connect(SHADOW_DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[strategy] shadow.db 查询失败: {e}", flush=True)
        return []


def _strategy_engine_call(code, timeout=20):
    """一次性子进程调 strategy_engine 自己venv的python，取stdout最后一行JSON。"""
    try:
        p = subprocess.run(
            [STRATEGY_ENGINE_PY, "-c", code],
            capture_output=True, timeout=timeout, cwd=STRATEGY_ENGINE_DIR,
        )
        out = p.stdout.decode("utf-8", errors="replace").strip()
        if out:
            return json.loads(out.splitlines()[-1])
        err = p.stderr.decode("utf-8", errors="replace")[-300:]
        return {"ok": False, "message": f"无输出: {err}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@app.route("/api/grid_order", methods=["POST"])
def api_grid_order():
    """网格套利手动开仓——跟tv_manual_send同一套"本机HTTP回环代按已有接口"
    模式，转发到对应账户自己的/api/console/grid_order，不重新实现任何
    交易逻辑。"""
    body = request.get_json(force=True, silent=True) or {}
    acct = _find_account(body.get("account"))
    if not acct:
        return jsonify({"status": "error", "message": "bad_account"}), 400
    payload = {k: v for k, v in body.items() if k != "account"}
    code, data = _console_call(acct, "POST", "/api/console/grid_order", body=payload)
    print(f"[GRID_ORDER] account={acct['id']} payload={payload} -> {data}", flush=True)
    return jsonify(data), (code or 502)


@app.route("/api/grid_order_cancel", methods=["POST"])
def api_grid_order_cancel():
    body = request.get_json(force=True, silent=True) or {}
    acct = _find_account(body.get("account"))
    if not acct:
        return jsonify({"status": "error", "message": "bad_account"}), 400
    payload = {"symbol": body.get("symbol")}
    code, data = _console_call(acct, "POST", "/api/console/grid_order/cancel", body=payload)
    print(f"[GRID_ORDER_CANCEL] account={acct['id']} payload={payload} -> {data}", flush=True)
    return jsonify(data), (code or 502)


@app.route("/api/strategy/registry")
def api_strategy_registry():
    code = (
        "from strategy_engine.symbol_registry import SYMBOLS\n"
        "import json\n"
        "print(json.dumps(SYMBOLS))"
    )
    result = _strategy_engine_call(code, timeout=10)
    if isinstance(result, dict) and result.get("ok") is False:
        return jsonify({"status": "error", "message": result.get("message")}), 502
    return jsonify({"status": "ok", "symbols": result})


@app.route("/api/strategy/summary")
def api_strategy_summary():
    rows = _shadow_query("""
        SELECT symbol,
               MAX(CASE WHEN run_type='live' THEN bar_time END) AS last_live_bar_time,
               COUNT(CASE WHEN run_type='live' THEN 1 END) AS live_signal_count
        FROM shadow_signals GROUP BY symbol
    """)
    return jsonify({"status": "ok", "summary": rows})


@app.route("/api/strategy/<symbol>/positions")
def api_strategy_positions(symbol):
    run_type = request.args.get("run_type", "live")
    run_id = request.args.get("run_id")
    if run_id:
        rows = _shadow_query(
            "SELECT * FROM shadow_positions WHERE symbol=? AND run_type=? AND run_id=? ORDER BY entry_bar_time ASC",
            (symbol, run_type, run_id),
        )
    else:
        rows = _shadow_query(
            "SELECT * FROM shadow_positions WHERE symbol=? AND run_type=? AND run_id IS NULL ORDER BY entry_bar_time ASC",
            (symbol, run_type),
        )
    return jsonify({"status": "ok", "positions": rows})


@app.route("/api/strategy/<symbol>/signals")
def api_strategy_signals(symbol):
    run_type = request.args.get("run_type", "live")
    run_id = request.args.get("run_id")
    limit = int(request.args.get("limit", 100))
    if run_id:
        rows = _shadow_query(
            "SELECT * FROM shadow_signals WHERE symbol=? AND run_type=? AND run_id=? ORDER BY id DESC LIMIT ?",
            (symbol, run_type, run_id, limit),
        )
    else:
        rows = _shadow_query(
            "SELECT * FROM shadow_signals WHERE symbol=? AND run_type=? AND run_id IS NULL ORDER BY id DESC LIMIT ?",
            (symbol, run_type, limit),
        )
    return jsonify({"status": "ok", "signals": rows})


@app.route("/api/strategy/<symbol>/backtest", methods=["POST"])
def api_strategy_backtest(symbol):
    sym = str(symbol or "").strip().upper()
    if sym not in SYMBOLS:
        return jsonify({"status": "error", "message": "bad_symbol"}), 400
    body = request.get_json(silent=True) or {}
    try:
        days = max(1, min(int(body.get("days") or 30), 180))
    except (TypeError, ValueError):
        days = 30
    code = (
        "from strategy_engine.backtest_runner import run_backtest\n"
        "import json\n"
        f"print(json.dumps(run_backtest({sym!r}, days={days})))"
    )
    result = _strategy_engine_call(code, timeout=60)
    return jsonify(result)


WATCHDOG_RUN_LINE_RE = re.compile(r"^(\S+) \S+ python\[\d+\]: (.*)$")


def _fetch_watchdog_runs(n_lines=1000, limit_runs=60):
    """解析 watchdog.service 的 journalctl 输出成"每轮检查"的结构化列表。
    2026-08-18：check.py 现在每轮都会把异常明细打成 [ANOMALY] key | text
    行（不受钉钉30分钟去重影响），这里按"遇到本轮无异常/本轮发现N条异常"
    这行收尾一轮，之前攒的 [ANOMALY] 行就是这一轮的明细。只读 journalctl，
    不碰 watchdog 自己的状态文件/进程。
    """
    raw = _run(["journalctl", "-u", "watchdog.service", "-n", str(n_lines), "-o", "short-iso", "--no-pager"], timeout=20)
    runs = []
    pending = []
    for line in raw.splitlines():
        m = WATCHDOG_RUN_LINE_RE.match(line)
        if not m:
            continue
        ts, body = m.group(1), m.group(2)
        if body.startswith("[ANOMALY] "):
            rest = body[len("[ANOMALY] "):]
            key, _, text = rest.partition(" | ")
            pending.append({"key": key, "text": text})
            continue
        if body == "本轮无异常":
            runs.append({"ts": ts, "ok": True, "anomaly_count": 0, "sent_count": 0, "anomalies": []})
            pending = []
            continue
        m2 = re.match(r"本轮发现 (\d+) 条异常，(\d+) 条新发送", body)
        if m2:
            runs.append({
                "ts": ts, "ok": False,
                "anomaly_count": int(m2.group(1)), "sent_count": int(m2.group(2)),
                "anomalies": pending,
            })
            pending = []
    runs.reverse()
    return runs[:limit_runs]


@app.route("/api/watchdog/logs")
def api_watchdog_logs():
    limit = min(int(request.args.get("limit", 60)), 200)
    runs = _fetch_watchdog_runs(n_lines=2000, limit_runs=limit)
    svc_state = _run(["systemctl", "is-active", "watchdog.timer"]).strip()
    return jsonify({"status": "ok", "runs": runs, "timer_active": svc_state == "active"})


@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


INDEX_HTML = open(__file__.replace("server.py", "index.html"), encoding="utf-8").read()

if __name__ == "__main__":
    t = threading.Thread(target=background_refresher, daemon=True)
    t.start()
    app.run(host="127.0.0.1", port=8877, debug=False, threaded=True)

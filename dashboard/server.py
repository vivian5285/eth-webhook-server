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
"""
import http.cookiejar
import json
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from flask import Flask, jsonify, Response, request

SYMBOLS = ["ETHUSDT", "XAUUSDT", "BNBUSDT", "ZECUSDT", "BCHUSDT", "XMRUSDT", "SNDKUSDT", "PAXGUSDT", "SKHYNIXUSDT", "XPDUSDT", "OPENAIUSDT", "ANTHROPICUSDT", "ASMLUSDT"]
ACCOUNTS = [
    {"id": "B", "port": 5007, "label": "妈妈的币安账户", "user": "binanceB", "svc": "binanceB-engine"},
    {"id": "C", "port": 5008, "label": "我自己的币安账户", "user": "binanceC", "svc": "binanceC-engine"},
    {"id": "D", "port": 5009, "label": "我的币安子账户", "user": "binanceD", "svc": "binanceD-engine"},
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
    r"TP已齐.*但止损未确认.*只补STOP"
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
    r"止损 @[\d.]+ 已穿/贴市.*禁止推宽.*紧急平仓"
)

FLAT_CONFIRM_RE = re.compile(
    r"确认空仓：WS\+REST均为0|"
    r"止损挂单未核实但复查仓位已归零|"
    r"仓位已由雷达/TP实际平仓，无需再挂止损|"
    r"确认平仓.*清除stale本地状态"
)

FLAT_CONFIRM_WINDOW_SEC = 90

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
    parsed_lines = []

    for line in raw_block.splitlines():
        m = LOG_LINE_RE.search(line)
        if m:
            ts, level, msg = m.group(1), m.group(2), m.group(3)
            if FLAT_CONFIRM_RE.search(msg):
                dt = _parse_ts(ts)
                if dt:
                    flat_confirm_ts.append(dt)
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
            if dt and any(abs((dt - c).total_seconds()) <= FLAT_CONFIRM_WINDOW_SEC for c in flat_confirm_ts):
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
        "git_rev": "",
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
    code, data = _console_call(
        acct, "POST", f"/api/console/tv_signals/{sig_id}/replay", body={"overrides": overrides}
    )
    print(f"[TV_REPLAY] account={acct['id']} signal_id={sig_id} overrides={overrides} -> {data}", flush=True)
    return jsonify(data), (code or 502)


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


@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


INDEX_HTML = open(__file__.replace("server.py", "index.html"), encoding="utf-8").read()

if __name__ == "__main__":
    t = threading.Thread(target=background_refresher, daemon=True)
    t.start()
    app.run(host="127.0.0.1", port=8877, debug=False, threaded=True)

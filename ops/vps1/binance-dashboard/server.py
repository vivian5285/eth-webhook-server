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

2026-08-29 新增：策略对比面板——数据来自同一个 strategy_engine 服务新增的
第二条独立线（multi_strategy_runner.py + strategy-compare.service）：把
Turtle海龟突破/跨品种动量/Connors RSI-2/Bollinger squeeze 这4套公开知名
战法跟原有的 tv_multiscore_v1(TV镜像)并排跑模拟仓，写进另一张表
shadow_positions_v2（跟老的 shadow.db/shadow_positions 完全不同的库文件，
按 strategy 字段分组，天然支持多策略共存）。读法跟上面的策略/回测面板
同一个模式——直接开 shadow_v2.db 只读查询，不额外起HTTP层；策略的人话
说明(STRATEGY_DESCRIPTIONS)走 subprocess 一次性调用 strategy_engine 自己
venv 的 python 取，缓存 5 分钟避免每次刷新面板都开一次子进程。
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

# 2026-09-04：宝贝确认ASMLUSDT/SKHYNIXUSDT胜率太低、已从symbol_config.py::
# active_binance_symbols()和各账户.env删除（commit e45383d）——这份dashboard
# 自己独立维护的SYMBOLS清单当时漏改了，导致这两个品种的旧状态文件(哪怕已经
# 空仓/不再更新)还在被面板轮询显示，SKHYNIX的"TV心跳追回"卡片一直卡在
# 陈旧状态出不去就是因为这个。这里同步删掉，不需要额外改动——旧的
# binance_vps_state_{ASMLUSDT,SKHYNIXUSDT}.json文件留着无害，本来就不会
# 再被更新，只是这个清单不再读它们。
# 2026-09-05：宝贝要求把SKHYNIXUSDT重新接回实盘，这里同步加回来。ASMLUSDT
# 不动，仍是删除状态。
SYMBOLS = ["ETHUSDT", "XAUUSDT", "BNBUSDT", "ZECUSDT", "BCHUSDT", "XMRUSDT", "SNDKUSDT", "PAXGUSDT", "XPDUSDT", "OPENAIUSDT", "ANTHROPICUSDT", "SKHYNIXUSDT", "GSUSDT", "MUUSDT", "LITEUSDT", "TSLAUSDT", "METAUSDT"]
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

# 2026-09-23新增：账户总览"全部持仓(所有币种)"——只读查询，跟CLOSE_CODE_
# TEMPLATE同一种subprocess client-layer模式，不import position_supervisor，
# 不限于SYMBOLS这份固定17品种白名单，交易所有什么仓位就如实显示什么。
ALL_POSITIONS_TEMPLATE = """
import json, os
from binance_client import binance_client
rows = binance_client.client.futures_position_information()
# 2026-09-30新增：宝贝要求持仓卡片上直接看到"为何开仓"——combo引擎自己
# 的本地账本(heikin_ashi_live_state.json)里每个品种记着哪几个sleeve
# 各自开了多少、什么权重(virtual netting的构成)，这是"为什么会有这笔
# 净仓"最直接的答案；完整的文字原因(比如"突破缠论中枢[...]")留在决策
# 日志feed里看，这里只做摘要，避免每次刷新都要解析一遍日志文本。
state = {}
try:
    # 这段代码是通过 python -c 执行的，没有 __file__；子进程的 cwd 已经
    # 由调用方设成账户自己的 binance-engine 目录，直接用相对路径即可。
    with open("heikin_ashi_live_state.json", encoding="utf-8") as f:
        state = json.load(f)
except Exception:
    state = {}
out = []
for p in rows:
    amt = float(p.get("positionAmt") or 0)
    if amt == 0:
        continue
    symbol = p.get("symbol")
    rec = state.get(symbol) if isinstance(state.get(symbol), dict) else {}
    sleeves = rec.get("sleeves") or {}
    sleeve_summary = [
        {"name": name, "weight": s.get("weight"), "side": s.get("side")}
        for name, s in sleeves.items()
    ] if isinstance(sleeves, dict) else []
    out.append({
        "symbol": symbol,
        "side": "LONG" if amt > 0 else "SHORT",
        "qty": abs(amt),
        "entry_price": float(p.get("entryPrice") or 0),
        "mark_price": float(p.get("markPrice") or 0),
        "unrealized_pnl": float(p.get("unRealizedProfit") or 0),
        "leverage": float(p.get("leverage") or 0),
        "sleeves": sleeve_summary,
        "combo_status": rec.get("status"),
    })
print(json.dumps({"ok": True, "positions": out}, default=str))
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
    r"notify fail channel=telegram attempt=2/|"
    # 2026-08-31：跟watchdog/check.py的NOISE_ERROR_PATTERNS同步——本面板
    # 独立解析同一份journalctl这件事本身就是08-20那条注释警告过的坑，这次
    # 复现了：watchdog那边08-29就加过这三条(用户反馈"老是说异常"追查出来
    # 的)，本面板一直没跟上，导致"异常N"角标常年虚高。实测这次核对：
    # 22条异常里有16条(73%)是这三类+下面的裸仓negation误判，真正的账户
    # 逻辑问题只有1条。"🧊 [IP限流] REST全局冷却"是纯提示(接下来60秒暂停
    # REST，不代表任何操作失败)；"preemptive_weight_limit"是只读查询被
    # 限流，代码自己退回用非空缓存；"ORDERS_QUERY_FAILED"这条消息原文自己
    # 就写着"禁止据此当空盘补挂/核武"，是代码自己声明的不可作为判据。
    r"🧊 \[IP限流\] REST 全局冷却至|"
    r"preemptive_weight_limit|"
    r"ORDERS_QUERY_FAILED|"
    # 2026-08-31第二批：同一次核对里又冒出来的两类——消息原文自己就写着
    # "仍在保护"/"禁止空仓清场"，是代码明确声明的安全分支，不是故障。
    # "硬止损仍在保护"：雷达止损按仓位变化收缩重挂失败，但永久硬止损没动，
    # 仓位没有失去保护，只是那一层"贴身跟随"没跟上。
    # "禁止空仓清场，交哨兵接力"：重启时IP限流查不到持仓，系统正确拒绝
    # 盲目当空仓处理(那样才是真正危险的误判)，把这个symbol交给后续哨兵
    # 巡检接力核实——本身就是"没有妄下结论"的保守分支。
    r"硬止损仍在保护|"
    r"禁止空仓清场，交哨兵接力|"
    r"保留账本/跳过空仓判定|"
    # 2026-09-01第三批：宝贝反馈"隔一会又几十条"追查出的两类——都是
    # IP限流期间各种具体操作(撤单/挂止损/挂限价/查挂单)自己主动放弃、
    # 明确声明"不确认就不敢动"的安全分支，不是真故障：
    # 1) "ip_rate_limited remaining=Ns"是IpRateLimitedError专用的固定
    #    文案(binance_client.py唯一出处)，只在代码自己主动拒绝REST时
    #    抛出，从不代表交易所真的报错——[撤单失败]/[止损单失败]/
    #    [限价单失败]/[Algo止损失败]等各种操作壳子外面套的都是同一个
    #    信号，用这条通用子串一次性覆盖，不用每种操作单独列一条。
    # 2) "_has_stop_sl_near...不可确认（禁止谎称已有）"：查不到挂单时
    #    宁可如实说"不确认"也不敢谎称"已经有止损了"，原文自己就是
    #    保守声明。
    r"ip_rate_limited remaining=|"
    r"不可确认（禁止谎称已有）|"
    r"接管上下文补全: 挂单查询失败"
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
    r"已确认无持仓.*不暂停交易|平仓完成但.*查询失败.*不暂停交易|"
    # 2026-08-31：跟上面NOISE_RE同一批发现的问题——"anomaly"规则里的
    # "裸仓"是纯子串匹配，"此前的失败判定是假阳性"/"非裸仓"这类原文
    # 已经明确写着"不是裸仓"的自愈消息，会被"裸仓"这个子串反向命中，
    # 变成"系统说没事却被当成异常"这种矛盾展示。这两条文案是代码自己
    # 声明的否定句，不是新的噪音类别猜测，直接按原文匹配。
    r"此前的失败判定是假阳性|非裸仓|"
    # 2026-09-01：宝贝反馈"异常动不动几十上百条，好吓人"追查出的两类——
    # 都是原文自己声明"没事/等下次"的自愈消息，进事件流但不算异常，跟
    # 上面几类同一个道理：
    # 1) 反转保护(RSI)平仓/撤单时撞上IP限流，文案自己写"等待下次机会"，
    #    是08-29上线的反转锁盈功能自己的IP限流重试分支，比前几轮降噪
    #    筛查晚，没跟上过滤名单。
    # 2) 哨兵自愈本身——_ensure_sentinel_running发现哨兵线程死了、自己
    #    重启，用logger.error+🚨记录，反而被当成红色异常；这是系统自己
    #    发现问题并修好，不是新问题。
    r"反转保护.*(等待下次机会)|"
    r"哨兵自愈：_sentinel_active=True但线程已死"
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
    r"雷达/防线账本已清零|"  # 2026-08-17：跟watchdog同步——这条才是平仓/账本
                            # 清零最常见的实际文案，原来四条经常对不上
    # 2026-09-01新增(C账户XPDUSDT实盘复现)：TP2挂单连续被ReduceOnly
    # Order rejected(CLOSING_CHATTER_RE已经认得这个文案)，但真正的
    # "雷达/防线账本已清零"确认要等mark极值兜底闩锁归因走完，实测隔了
    # 120秒，超过FLAT_CONFIRM_WINDOW_SEC(90秒)没能对上，异常没被降级。
    # 这条文案本身在TP拒绝后1-7秒内就会出现，且原文自己写着"大概率已
    # 被自己的止损打平，非挂单故障"——比等最终的账本清零快得多，加进来
    # 让CLOSING_CHATTER_RE的降级判定能更早对上。
    r"TP重试期间仓位已归零"
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
        elif (
            str(s.get("tv_heartbeat_side") or "FLAT").upper() in ("LONG", "SHORT")
            and not str(
                (s.get("last_tv_signal") or {}).get("action", "") or ""
            ).upper().startswith("CLOSE")
        ):
            # 还没武装(可能还在180秒宽限期内，或者EMA多周期还没一起确认)——
            # 心跳显示TV仍持仓、本地却空仓，这个组合本身就值得让宝贝随时
            # 看到，不用等追回真正挂单才显示。
            # 2026-09-04修复(宝贝发现XMR/META明明已经平仓——XMR宝贝手动
            # 平的、META是TV真实平仓的——面板却一直卡在"等待条件确认中"
            # 不消失)：心跳(HEARTBEAT，每根收盘K线才发一次，天然滞后)不等
            # 于TV最新意图，radar_reentry_mixin.py::_maybe_start_tv_
            # heartbeat_catchup早就有这个判断——最近一次真实TV webhook
            # 信号(last_tv_signal，跟HEARTBEAT是两条独立的流)如果已经是
            # CLOSE类，说明TV自己已经明确要求平仓，心跳还没来得及跟上，
            # 引擎自己内部已经判定"不追回"、只是没有暴露给面板看。这里
            # 面板复用同一份已经持久化的last_tv_signal字段做一样的判断，
            # 不新增任何状态、不碰引擎决策逻辑，纯只读展示层修复——判定为
            # "已知心跳滞后"的不再显示成"等待条件确认中"，等TV下一次心跳
            # 刷新成FLAT后，这条判断本身也会自然跟着消失。
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


# 2026-09-23新增：账户总览要看"所有账户的实时持仓、不限品种"（宝贝原话：
# 方便观看）——上面/api/status走的老路径只读本地状态文件、只覆盖SYMBOLS
# 这份固定17品种白名单，妈妈/MARIO账户切去跑擂台策略(dual_momentum，
# 交易ENA/ZEC/UNI/HYPE/SNDK/BCH/SOL/DOGE/PEPE等)之后，这些仓位在老路径
# 里完全看不到。这里单独开一条路径，直接问交易所"当前真实持仓"，不管
# 是哪个引擎(TV/dual_momentum/手动)开的仓、不管是不是在17品种白名单里，
# 一律如实显示——跟close_position一样走只读client-layer subprocess，
# 不import position_supervisor，不影响任何账户的运行参数。
_all_pos_cache = {"ts": 0.0, "data": None}
_all_pos_lock = threading.Lock()
ALL_POS_CACHE_TTL_SEC = 20.0


def _fetch_all_positions(force=False):
    now = time.time()
    with _all_pos_lock:
        cached = _all_pos_cache["data"]
        if not force and cached is not None and (now - _all_pos_cache["ts"]) < ALL_POS_CACHE_TTL_SEC:
            return cached
    out = {}
    for a in ACCOUNTS:
        r = _run_account_py(a, ALL_POSITIONS_TEMPLATE, timeout=15)
        if not isinstance(r, dict):
            r = {"ok": False, "msg": "bad_result", "positions": []}
        out[a["id"]] = r
    with _all_pos_lock:
        _all_pos_cache["ts"] = time.time()
        _all_pos_cache["data"] = out
    return out


@app.route("/api/all_positions")
def api_all_positions():
    force = request.args.get("force") == "1"
    return jsonify({"ok": True, "accounts": _fetch_all_positions(force=force)})


# 2026-09-23新增，2026-09-30改为指向当前真实在跑的引擎：原来这里指向
# dual_momentum/sndk_dual_ma这两个早就停跑或换掉的旧擂台实验引擎(见
# project_dual_momentum_live_20260922/project_vwap_live_20260912)，宝贝
# 现在实盘真正在跑的是asset_class_combo(heikin_ashi_live.py，5个crypto
# sleeve+3个stock sleeve netting到一个真实仓位)，服务名是binance{B,C,E}
# -heikin-ashi——旧配置完全没覆盖到这条真正在动真钱的pipeline，宝贝问
# "为何开仓和平仓原因细节，有无异常报错"时这个面板其实是空的/过时的。
# 改成指向新服务，关键词过滤表也按新引擎真实会打的日志原文重新配了一遍
# (旧的"开仓共同第一步"这类是老TV pipeline专属文案，新引擎完全不会打)。
ENGINE_LOG_SOURCES = {
    "B": [{"id": "combo", "label": "组合策略引擎(妈妈账户, stable_asset_combo_v3)", "svc": "binanceB-heikin-ashi"}],
    "C": [{"id": "combo", "label": "组合策略引擎(宝贝自己账户, stable_asset_combo_v3)", "svc": "binanceC-heikin-ashi"}],
    "E": [{"id": "combo", "label": "组合策略引擎(MARIO账户, stable_asset_combo_v3)", "svc": "binanceE-heikin-ashi"}],
}

# 开仓/平仓/风控决策相关的关键词——覆盖asset_class_combo_strategy.py各个
# sleeve(hma_trend/ttm_squeeze/keltner_channel/turtle_breakout/chanlun_
# pivot/heikin_ashi_trend/mtf_ema_macd_cci)真实会打的原因文案(突破/背驰/
# 结构失效/拐头/HMA/中枢等)，以及组合层面的净额执行、风控决策文案。
ENGINE_LOG_EVENT_RE = re.compile(
    r"🚀|✅|🛑|🛡️|📈|⚡|🚨|限价单成功|市价开仓成功|唯一保护止损已确认|"
    r"组合风控|仓位计算|合并信号|突破缠论中枢|突破Keltner通道|突破海龟|"
    r"背驰|结构失效|拐头|HMA\(|反手|补到交易所最小可下单量|"
    r"净额执行|恢复中断过渡|冻结该品种|唯一保护止损|保护止损|组合风险状态"
)

# 2026-09-30新增：宝贝要求"有无异常报错"要能一眼看到，不能只混在决策
# 日志里往下翻。用systemd自己的-p err优先级过滤(直接按ERROR/CRITICAL
# 日志级别取，不是猜关键词)，比关键词匹配更不会漏，跟决策日志走同一个
# service、同一次journalctl查询窗口，两条视图数据源一致。
def _fetch_engine_error_lines(svc, since="2 hours ago", max_lines=100):
    raw = _run(
        ["journalctl", "-u", svc, "--no-pager", "-p", "err",
         "--since", since, "-o", "short-iso"],
        timeout=20,
    )
    lines = [
        ln.strip() for ln in raw.splitlines()
        if ln.strip() and ln.strip() != "-- No entries --"
    ]
    return lines[-max_lines:]


def _fetch_engine_log_events(svc, lines=200):
    raw = _run(["journalctl", "-u", svc, "--no-pager", "-n", str(lines), "-o", "short-iso"])
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if line and ENGINE_LOG_EVENT_RE.search(line):
            out.append(line)
    return out


@app.route("/api/engine_logs/<acct_id>")
def api_engine_logs(acct_id):
    lines = min(int(request.args.get("lines", 200)), 1000)
    sources = ENGINE_LOG_SOURCES.get(acct_id.upper(), [])
    result = []
    for src in sources:
        events = _fetch_engine_log_events(src["svc"], lines=lines)
        errors = _fetch_engine_error_lines(src["svc"])
        result.append({
            "id": src["id"], "label": src["label"], "svc": src["svc"],
            "events": events[-60:], "errors": errors, "error_count": len(errors),
        })
    return jsonify({"ok": True, "account": acct_id.upper(), "sources": result})


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


# ═══════════════════════════════════════════════════════════════
# 控制面板：账户启停 + TV白名单品种开关
# 2026-09-12 宝贝要求：暂停/恢复某个账户、暂停/恢复某个品种接收TV+
# 实盘开仓这两件事，以前每次都要麻烦人工去VPS上敲systemctl/改.env，
# 现在开放成dashboard自己的写操作，跟close_position同一个安全边界
# (nginx basic auth保护/dashboard本身root权限/前端二次确认)。
#
# 账户启停：直接systemctl start/stop对应服务，dashboard本身以root
# 运行，不需要sudo。
#
# 品种白名单：四账户(B/C/D/E)的.env BINANCE_SYMBOLS本来就该保持一致
# (跟这次BNB恢复用的是同一份"注释不删除"可逆写法)，这里统一读/改
# 四份.env，改完只重启"当前真的在跑"的账户(用systemctl is-active现查，
# 不猜)——因为symbol_config.py::active_binance_symbols()只在
# bootstrap_supervisors()启动那一刻读一次，改.env本身不会让正在跑的
# 进程立刻生效，必须配一次重启；停着的账户(比如C在跑擂台策略/D空仓
# 待命)不动它，等下次它们自己被启动时自然读到新.env。
# ═══════════════════════════════════════════════════════════════

BINANCE_SYMBOLS_KEY = "BINANCE_SYMBOLS"
# 2026-09-13：币安B系统("综合硬止损"体系)独立的TV品种白名单，跟A系统的
# BINANCE_SYMBOLS彻底分开，账户当前是A/B哪个模式决定读哪一份(跟
# symbol_config.py::active_binance_symbols()同一套key，两边必须一致)。
BINANCE_SYMBOLS_B_KEY = "BINANCE_SYMBOLS_B"
_SYSTEM_TO_ENV_KEY = {"A": BINANCE_SYMBOLS_KEY, "B": BINANCE_SYMBOLS_B_KEY}


def _account_service_states():
    """四账户当前systemctl is-active状态，一次批量查询。"""
    out = _run(["systemctl", "is-active"] + [a["svc"] for a in ACCOUNTS])
    lines = out.strip().splitlines()
    while len(lines) < len(ACCOUNTS):
        lines.append("unknown")
    return {a["id"]: lines[i] for i, a in enumerate(ACCOUNTS)}


@app.route("/api/control/accounts")
def api_control_accounts():
    """四账户当前启停状态，供控制面板渲染。"""
    states = _account_service_states()
    return jsonify({
        "ok": True,
        "accounts": [
            {"id": a["id"], "label": a["label"], "svc": a["svc"], "status": states.get(a["id"], "unknown")}
            for a in ACCOUNTS
        ],
    })


# 2026-09-30新增：宝贝在控制面板截图里问"这个跟组合策略是不是两回事"——
# 是的，上面"账户运行哪个系统"(币安A/币安B/擂台C-VWAP)这一整套开关切的
# 是position_supervisor pipeline(binance{B,C,D,E}-engine)，跟宝贝现在
# 实盘真正在跑、动真钱的asset_class_combo组合策略(binance{B,C,E}-
# heikin-ashi)完全是两个不同的引擎/两份代码——控制面板一直没有任何地方
# 显示combo引擎的状态，宝贝盯着"全部停止"以为没在跑，其实combo引擎一直
# 是活的，只是这个面板看不见它。这里先只加只读状态展示(不加启停按钮)——
# 组合策略是3个账户共用的净额netting架构，跟A/B/VWAP那套"互斥切换、
# 自动先停旧系统"的模式完全不是一回事，直接塞进同一套切换逻辑风险很高，
# 需要宝贝先明确要不要在这个面板加启停控制，这里先解决"看不见"的问题。
COMBO_ACCOUNTS = [
    {"id": "B", "label": "妈妈的币安账户(B)", "svc": "binanceB-heikin-ashi"},
    {"id": "C", "label": "我自己的币安账户(C)", "svc": "binanceC-heikin-ashi"},
    {"id": "E", "label": "MARIO账户(E)", "svc": "binanceE-heikin-ashi"},
]


@app.route("/api/control/combo_status")
def api_control_combo_status():
    """组合策略引擎(asset_class_combo/heikin_ashi_live.py)三个账户的真实
    运行状态——只读展示，不提供启停(理由见上面注释)。"""
    svcs = [a["svc"] for a in COMBO_ACCOUNTS]
    out = _run(["systemctl", "is-active"] + svcs)
    lines = out.strip().splitlines()
    while len(lines) < len(COMBO_ACCOUNTS):
        lines.append("unknown")
    return jsonify({
        "ok": True,
        "accounts": [
            {"id": a["id"], "label": a["label"], "svc": a["svc"], "status": lines[i]}
            for i, a in enumerate(COMBO_ACCOUNTS)
        ],
    })


@app.route("/api/control/account/<acct_id>/start", methods=["POST"])
def api_control_account_start(acct_id):
    acct = _find_account(acct_id)
    if not acct:
        return jsonify({"ok": False, "msg": "未知账户"}), 400
    out = _run(["systemctl", "start", acct["svc"]], timeout=15)
    ok = not out.startswith("__ERR__")
    print(f"[ACCOUNT_START] {acct_id} ok={ok} out={out[:200]!r}", flush=True)
    refresh_cache_once()
    return jsonify({"ok": ok, "msg": "已启动" if ok else out})


@app.route("/api/control/account/<acct_id>/stop", methods=["POST"])
def api_control_account_stop(acct_id):
    acct = _find_account(acct_id)
    if not acct:
        return jsonify({"ok": False, "msg": "未知账户"}), 400
    out = _run(["systemctl", "stop", acct["svc"]], timeout=15)
    ok = not out.startswith("__ERR__")
    print(f"[ACCOUNT_STOP] {acct_id} ok={ok} out={out[:200]!r}", flush=True)
    refresh_cache_once()
    return jsonify({"ok": ok, "msg": "已停止" if ok else out})


# ═══════════════════════════════════════════════════════════════
# 账户"系统"切换 —— 币安A系统 / 币安B系统(综合硬止损) / 擂台C系统
# (VWAP均值回归，仅C账户)
# 2026-09-13新增(宝贝要求)：一开始只给C账户做了三选一，这次扩展到
# 妈妈的账户(B)和客户的账户(E/MARIO)——都要能随时在"币安A系统/币安B
# 系统"之间自由切换，每次切换都要走确认弹窗(前端二次确认，见index.html
# switchAccountSystem)。B/E账户没有VWAP这个选项(那是C账户专属的擂台
# 测试)，只有A/B两个系统可切。
#
# 币安A/B系统其实是*同一个*binance{X}-engine服务，只靠.env
# SMART_HARD_STOP_ENABLED这一个开关区分行为(见position_supervisor_
# binance.py的_temp_hard_stop_from_tv)——切A/B不需要换服务，只需要
# 改.env+重启同一个服务；C账户切到VWAP或从VWAP切出，才是"停一个服务、
# 起另一个服务"这种真正的服务级切换(binanceC-engine和vwap-live两个
# 完全独立的systemd服务不能同时跑，都会对同一个交易所账户下单/平仓，
# 抢同一份余额和仓位)。
#
# B/E账户是真实客户/家人在用的账户，正常情况下应该一直跑着币安A系统
# ——这里不做任何"默认帮你切换"的自作主张，只是把"切换能力"开放给
# 控制面板，切不切、什么时候切完全由宝贝自己在面板上点。
# ═══════════════════════════════════════════════════════════════

C_VWAP_SVC = "vwap-live"
SMART_HARD_STOP_KEY = "SMART_HARD_STOP_ENABLED"
# 每个账户支持切换到哪些系统——VWAP只有C账户有
ACCOUNT_SYSTEM_OPTIONS = {
    "B": ["A", "B"],
    "C": ["A", "B", "VWAP"],
    "E": ["A", "B"],
}


def _account_binance_env_path(acct_id):
    acct = _find_account(acct_id)
    return f'/home/{acct["user"]}/binance-engine/.env'


def _account_current_system(acct_id):
    """账户当前实际在跑哪个系统——用systemctl is-active现查(VWAP只对
    有这个选项的账户额外查一次)，不猜测/不用缓存，跟宝贝在面板上看到
    的必须完全一致。"""
    acct = _find_account(acct_id)
    if not acct:
        return "UNKNOWN"
    svc = acct["svc"]
    has_vwap = "VWAP" in ACCOUNT_SYSTEM_OPTIONS.get(acct_id, [])
    services = [svc] + ([C_VWAP_SVC] if has_vwap else [])
    out = _run(["systemctl", "is-active"] + services).strip().splitlines()
    while len(out) < len(services):
        out.append("unknown")
    binance_active = out[0] == "active"
    if has_vwap and out[1] == "active":
        return "VWAP"
    if binance_active:
        flag = ""
        try:
            with open(_account_binance_env_path(acct_id), encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith(f"{SMART_HARD_STOP_KEY}="):
                        flag = line.strip().split("=", 1)[1].strip()
                        break
        except Exception:
            pass
        return "B" if flag.lower() in ("1", "true", "yes") else "A"
    return "OFF"


def _set_account_binance_env_flag(acct_id, enabled: bool):
    """改该账户.env里的SMART_HARD_STOP_ENABLED这一行(没有就追加)。"""
    path = _account_binance_env_path(acct_id)
    line_new = f"{SMART_HARD_STOP_KEY}=" + ("1" if enabled else "0") + "\n"
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    found = False
    for i, l in enumerate(lines):
        if l.strip().startswith(f"{SMART_HARD_STOP_KEY}="):
            lines[i] = line_new
            found = True
            break
    if not found:
        lines.append(line_new)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


@app.route("/api/control/account_system")
def api_control_account_system_all():
    """B/C/E三个账户当前各自的系统状态+可选项，供控制面板一次性渲染
    三张卡片。"""
    out = {}
    for acct_id in ACCOUNT_SYSTEM_OPTIONS:
        out[acct_id] = {
            "system": _account_current_system(acct_id),
            "options": ACCOUNT_SYSTEM_OPTIONS[acct_id],
        }
    return jsonify({"ok": True, "accounts": out})


@app.route("/api/control/account_system/<acct_id>/<target>", methods=["POST"])
def api_control_account_system_switch(acct_id, target):
    """
    切换某账户运行的系统：A(币安A系统) / B(币安B系统·综合硬止损) /
    VWAP(擂台C系统均值回归，仅C账户) / OFF(全部停掉)。
    切换前先停掉这个账户当前可能在跑的所有系统服务(互斥保证)，再按
    目标启动对应服务；A/B之间只改.env标志位+重启同一个服务，不涉及
    服务切换。目标跟当前状态相同时直接跳过(不做无意义的重启)。
    """
    acct_id = str(acct_id or "").strip().upper()
    target = str(target or "").strip().upper()
    options = ACCOUNT_SYSTEM_OPTIONS.get(acct_id)
    if not options:
        return jsonify({"ok": False, "msg": "该账户不支持系统切换"}), 400
    if target not in options and target != "OFF":
        return jsonify({"ok": False, "msg": f"该账户只支持切换到: {options + ['OFF']}"}), 400

    acct = _find_account(acct_id)
    svc = acct["svc"]
    has_vwap = "VWAP" in options

    before = _account_current_system(acct_id)
    if before == target:
        return jsonify({"ok": True, "before": before, "target": target, "after": before,
                        "msg": "已经是目标系统，未做任何改动"})

    # 1) 先把这个账户所有互斥服务都停掉(幂等：已经停着的stop不会报错)
    if has_vwap:
        _run(["systemctl", "stop", C_VWAP_SVC], timeout=15)
    _run(["systemctl", "stop", svc], timeout=15)

    result = {"ok": True, "before": before, "target": target}
    try:
        if target == "OFF":
            pass  # 该停的都已经停了
        elif target == "VWAP":
            out = _run(["systemctl", "start", C_VWAP_SVC], timeout=15)
            if out.startswith("__ERR__"):
                result = {"ok": False, "msg": f"启动VWAP失败: {out}"}
        else:  # A or B
            _set_account_binance_env_flag(acct_id, enabled=(target == "B"))
            out = _run(["systemctl", "start", svc], timeout=15)
            if out.startswith("__ERR__"):
                result = {"ok": False, "msg": f"启动{acct_id}账户引擎失败: {out}"}
    except Exception as e:
        result = {"ok": False, "msg": f"切换异常: {e}"}

    after = _account_current_system(acct_id)
    result["after"] = after
    print(f"[ACCOUNT_SYSTEM_SWITCH] {acct_id}: {before} -> {target} (实际={after})", flush=True)
    refresh_cache_once()
    return jsonify(result)


def _full_symbol_catalog():
    """完整已知品种目录——直接问引擎自己的symbol_config.py，不用这份
    dashboard里容易跟引擎实际支持品种脱钩的SYMBOLS清单(ASML/SKHYNIX
    那次教训：watchdog/dashboard各自独立维护一份品种清单，引擎那边
    删/加品种时经常漏改，误报或选项缺失)。查询失败时退回本地SYMBOLS，
    至少还能用，不会让整个控制面板打不开。"""
    acct = ACCOUNTS[0]
    acct_dir = f'/home/{acct["user"]}/binance-engine'
    code = (
        "import json\n"
        "from symbol_config import BINANCE_SYMBOL_META\n"
        "print(json.dumps(sorted(BINANCE_SYMBOL_META.keys())))\n"
    )
    try:
        p = subprocess.run(
            [f"{acct_dir}/venv/bin/python", "-c", code],
            capture_output=True, timeout=10, cwd=acct_dir,
        )
        out = p.stdout.decode("utf-8", errors="replace").strip()
        return json.loads(out.splitlines()[-1]) if out else list(SYMBOLS)
    except Exception:
        return list(SYMBOLS)


def _normalize_system(system):
    s = str(system or "").strip().upper()
    return s if s in _SYSTEM_TO_ENV_KEY else None


def _read_symbol_whitelist(system="A"):
    """读B账户.env对应system的白名单key作为当前基准(四账户理应保持
    一致，不一致时以B为准，把其它三个拉齐)。2026-09-13：按system(A/B)
    分开读两份独立的key，互不影响。"""
    key = _SYSTEM_TO_ENV_KEY.get(_normalize_system(system) or "A", BINANCE_SYMBOLS_KEY)
    path = f'/home/{ACCOUNTS[0]["user"]}/binance-engine/.env'
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(f"{key}="):
                    raw = line.strip().split("=", 1)[1]
                    return [s.strip() for s in raw.split(",") if s.strip()]
    except Exception:
        pass
    return []


def _write_symbol_whitelist(symbols, system="A"):
    """把新白名单同步写进四账户各自的.env，逐个替换对应system那把key
    的那一行(没有就追加一行)。返回每个账户的写入结果，方便前端看出
    是不是哪个账户.env权限有问题写失败了。"""
    key = _SYSTEM_TO_ENV_KEY.get(_normalize_system(system) or "A", BINANCE_SYMBOLS_KEY)
    line_new = f"{key}=" + ",".join(symbols) + "\n"
    results = {}
    for a in ACCOUNTS:
        path = f'/home/{a["user"]}/binance-engine/.env'
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
            found = False
            for i, l in enumerate(lines):
                if l.strip().startswith(f"{key}="):
                    lines[i] = line_new
                    found = True
                    break
            if not found:
                lines.append(line_new)
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            results[a["id"]] = "ok"
        except Exception as e:
            results[a["id"]] = f"error: {e}"
    return results


def _restart_running_accounts(system=None):
    """白名单改动只有重启才会生效(active_binance_symbols()只在启动那
    一刻读一次)——只重启当前真的在跑的账户，停着的账户不碰，它们下次
    被启动时自然读到新.env。2026-09-13：传system时进一步只重启"当前
    真的在那个系统模式下跑"的账户——改A的白名单不该去重启正在跑B的
    账户(反之亦然)，那种重启对它没有任何效果、纯属多余。不传system
    保持旧行为(不管什么模式，只要在跑就重启)。"""
    states = _account_service_states()
    restarted = []
    for a in ACCOUNTS:
        if states.get(a["id"]) != "active":
            continue
        if system and _account_current_system(a["id"]) != system:
            continue
        _run(["systemctl", "restart", a["svc"]], timeout=20)
        restarted.append(a["id"])
    return restarted


@app.route("/api/control/symbols/<system>")
def api_control_symbols(system):
    """某个系统(A/B)当前的TV白名单 + 全部已知品种目录，供控制面板在
    对应系统的展示框里渲染开关列表。"""
    sys_norm = _normalize_system(system)
    if not sys_norm:
        return jsonify({"ok": False, "msg": "system必须是A或B"}), 400
    return jsonify({
        "ok": True,
        "system": sys_norm,
        "active": _read_symbol_whitelist(sys_norm),
        "catalog": _full_symbol_catalog(),
    })


@app.route("/api/control/symbols/<system>/<symbol>/enable", methods=["POST"])
def api_control_symbol_enable(system, symbol):
    sys_norm = _normalize_system(system)
    if not sys_norm:
        return jsonify({"ok": False, "msg": "system必须是A或B"}), 400
    symbol = symbol.strip().upper()
    catalog = _full_symbol_catalog()
    if symbol not in catalog:
        return jsonify({"ok": False, "msg": "未知品种"}), 400
    active = _read_symbol_whitelist(sys_norm)
    if symbol in active:
        return jsonify({"ok": True, "msg": "已经是启用状态", "active": active, "restarted": []})
    active = active + [symbol]
    write_result = _write_symbol_whitelist(active, sys_norm)
    restarted = _restart_running_accounts(sys_norm)
    print(f"[SYMBOL_ENABLE] system={sys_norm} {symbol} write={write_result} restarted={restarted}", flush=True)
    refresh_cache_once()
    return jsonify({"ok": True, "system": sys_norm, "active": active, "write": write_result, "restarted": restarted})


@app.route("/api/control/symbols/<system>/<symbol>/disable", methods=["POST"])
def api_control_symbol_disable(system, symbol):
    sys_norm = _normalize_system(system)
    if not sys_norm:
        return jsonify({"ok": False, "msg": "system必须是A或B"}), 400
    symbol = symbol.strip().upper()
    active = _read_symbol_whitelist(sys_norm)
    if symbol not in active:
        return jsonify({"ok": True, "msg": "已经是暂停状态", "active": active, "restarted": []})
    active = [s for s in active if s != symbol]
    write_result = _write_symbol_whitelist(active, sys_norm)
    restarted = _restart_running_accounts(sys_norm)
    print(f"[SYMBOL_DISABLE] system={sys_norm} {symbol} write={write_result} restarted={restarted}", flush=True)
    refresh_cache_once()
    return jsonify({"ok": True, "system": sys_norm, "active": active, "write": write_result, "restarted": restarted})


# ═══════════════════════════════════════════════════════════════
# CoinW币赢 · 品种白名单 (2026-09-21新增)
# 宝贝要求跟上面币安A/B系统同一套开关面板，方便他自己随时勾选，不用
# 再等改代码+redeploy。CoinW是独立仓库(coinw-hft-server)/独立VPS
# 目录(/home/coinw)，只有一个账户一份.env，比币安四账户那套简单——
# 改symbol_config.py::ACTIVE_SYMBOLS依赖的COINW_ACTIVE_SYMBOLS这一个
# key，coinw-engine.service在跑就重启它，不在跑就不碰(下次它自己启动
# 时自然读到新.env，跟币安那边"只重启当前真的在跑的账户"同一个道理)。
# ═══════════════════════════════════════════════════════════════

COINW_ENV_PATH = "/home/coinw/coinw-hft-server/.env"
COINW_ACTIVE_SYMBOLS_KEY = "COINW_ACTIVE_SYMBOLS"
COINW_SVC = "coinw-engine"


def _coinw_full_symbol_catalog():
    """CoinW已知品种目录(短代码，比如BNB/XAU，不带USDT后缀，跟symbol_
    config.py::ACTIVE_SYMBOLS用的记法一致)——直接问引擎自己的symbol_
    config.py::SymbolConfig.SYMBOL_MAP，不在dashboard这边另外维护一份
    容易跟引擎脱钩的清单(同_full_symbol_catalog()那次ASML/SKHYNIX教训)。
    查询失败时返回空列表，前端退回只显示当前已启用的品种，不会打不开。"""
    code = (
        "import json\n"
        "from symbol_config import SymbolConfig\n"
        "print(json.dumps(sorted(SymbolConfig.SYMBOL_MAP.keys())))\n"
    )
    try:
        p = subprocess.run(
            ["/home/coinw/venv/bin/python", "-c", code],
            capture_output=True, timeout=10, cwd="/home/coinw/coinw-hft-server",
        )
        out = p.stdout.decode("utf-8", errors="replace").strip()
        return json.loads(out.splitlines()[-1]) if out else []
    except Exception:
        return []


def _read_coinw_symbol_whitelist():
    try:
        with open(COINW_ENV_PATH, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(f"{COINW_ACTIVE_SYMBOLS_KEY}="):
                    raw = line.strip().split("=", 1)[1]
                    return [s.strip().upper() for s in raw.split(",") if s.strip()]
    except Exception:
        pass
    return []


def _write_coinw_symbol_whitelist(symbols):
    line_new = f"{COINW_ACTIVE_SYMBOLS_KEY}=" + ",".join(symbols) + "\n"
    try:
        with open(COINW_ENV_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        found = False
        for i, l in enumerate(lines):
            if l.strip().startswith(f"{COINW_ACTIVE_SYMBOLS_KEY}="):
                lines[i] = line_new
                found = True
                break
        if not found:
            lines.append(line_new)
        with open(COINW_ENV_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return "ok"
    except Exception as e:
        return f"error: {e}"


def _restart_coinw_if_running():
    """只在coinw-engine真的在跑时才重启，跟币安那边"只重启当前真的在跑
    的账户"同一个安全边界——服务本来就停着(比如人工维护中)不该被这次
    白名单改动意外拉起来。"""
    out = _run(["systemctl", "is-active", COINW_SVC]).strip()
    if out != "active":
        return False
    _run(["systemctl", "restart", COINW_SVC], timeout=20)
    return True


@app.route("/api/control/coinw_symbols")
def api_control_coinw_symbols():
    """CoinW当前的品种白名单 + 全部已知品种目录，供控制面板渲染开关列表。"""
    return jsonify({
        "ok": True,
        "active": _read_coinw_symbol_whitelist(),
        "catalog": _coinw_full_symbol_catalog(),
    })


@app.route("/api/control/coinw_symbols/<symbol>/enable", methods=["POST"])
def api_control_coinw_symbol_enable(symbol):
    symbol = symbol.strip().upper()
    catalog = _coinw_full_symbol_catalog()
    if catalog and symbol not in catalog:
        return jsonify({"ok": False, "msg": "未知品种"}), 400
    active = _read_coinw_symbol_whitelist()
    if symbol in active:
        return jsonify({"ok": True, "msg": "已经是启用状态", "active": active, "restarted": False})
    active = active + [symbol]
    write_result = _write_coinw_symbol_whitelist(active)
    restarted = _restart_coinw_if_running()
    print(f"[COINW_SYMBOL_ENABLE] {symbol} write={write_result} restarted={restarted}", flush=True)
    return jsonify({"ok": True, "active": active, "write": write_result, "restarted": restarted})


@app.route("/api/control/coinw_symbols/<symbol>/disable", methods=["POST"])
def api_control_coinw_symbol_disable(symbol):
    symbol = symbol.strip().upper()
    active = _read_coinw_symbol_whitelist()
    if symbol not in active:
        return jsonify({"ok": True, "msg": "已经是暂停状态", "active": active, "restarted": False})
    active = [s for s in active if s != symbol]
    write_result = _write_coinw_symbol_whitelist(active)
    restarted = _restart_coinw_if_running()
    print(f"[COINW_SYMBOL_DISABLE] {symbol} write={write_result} restarted={restarted}", flush=True)
    return jsonify({"ok": True, "active": active, "write": write_result, "restarted": restarted})


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


GATEWAY_WEBHOOK_URL = "http://127.0.0.1:5006/webhook"


@app.route("/api/tv_raw_send", methods=["POST"])
def api_tv_raw_send():
    """2026-09-01新增：宝贝要求——TV自己后台(或警报历史)复制出来的原始
    JSON文本，直接原样POST给目标(网关广播B/C/E，或指定单个账户自己的
    /webhook)，不经过tv_manual_send那套"表单字段重新拼payload"的改写，
    完全复刻TV真实发来的样子(包括payload自带的secret——账户/网关自己
    按各自既有逻辑校验，本接口不做任何额外解析/改写/鉴权，纯转发)。
    网关本身就是纯转发、无鉴权(binance-gateway/gateway.py顶部注释)，
    直发账户端口同样靠payload自带的secret，两条路径都不需要走console
    登录会话。"""
    body = request.get_json(force=True, silent=True) or {}
    target = str(body.get("target") or "").strip().upper()
    raw_text = body.get("raw")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return jsonify({"status": "error", "message": "empty_raw"}), 400
    try:
        raw_obj = json.loads(raw_text)
    except Exception as e:
        return jsonify({"status": "error", "message": f"invalid_json: {e}"}), 400
    if not isinstance(raw_obj, dict):
        return jsonify({"status": "error", "message": "json_must_be_object"}), 400

    if target == "GATEWAY":
        url = GATEWAY_WEBHOOK_URL
    else:
        acct = _find_account(target)
        if not acct:
            return jsonify({"status": "error", "message": "bad_target"}), 400
        url = f"http://127.0.0.1:{acct['port']}/webhook"

    data = json.dumps(raw_obj).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            code = resp.status
            resp_text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        code = e.code
        resp_text = e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return jsonify({"status": "error", "message": f"send_failed: {e}"}), 502
    try:
        upstream = json.loads(resp_text)
    except Exception:
        upstream = resp_text
    print(f"[TV_RAW_SEND] target={target} payload={raw_obj} -> {code} {upstream}", flush=True)
    return jsonify({"status": "ok", "target": target, "upstream_status": code, "upstream": upstream}), 200


# ═══════════════════════════════════════════════════════════════
# 手动发单（现货等值·单一止盈止损）—— 2026-09-21新增
# 宝贝要求：跟上面"手动发单（表单）"（等效于一笔真实TV警报，走
# webhook_parser→position_supervisor整套雷达/多档止盈系统）完全不同的
# 一条独立下单路径——因为那套会让这笔仓位被系统自动接管(雷达/呼吸/
# 多档TP)，而这里要的是"我自己手工控制的仓位，系统绝对不碰"(参见
# feedback_no_orphan_auto_adopt同一次谈话)。
#
# 实现上文彻底绕开position_supervisor：跟close_position(见上方
# CLOSE_CODE_TEMPLATE)同一种模式——subprocess调用目标账户自己venv的
# python，只import binance_client(纯交易所client层)直接下单/挂止损
# 止盈，不import/不实例化position_supervisor的任何类，这笔仓位挂完
# 之后不会进入任何后台管理循环。
#
# 下单量规则：可用余额(availableBalance) ÷ 价格，1倍现货等值，不放大
# 杠杆(不管账户在交易所设置的实际杠杆倍数是多少，这里都当1倍算)。
# 止损/止盈都是closePosition=true的条件单，不需要预先知道精确成交
# 数量——触发时平掉当时实际的全部仓位，天然满足"只有一个止损+一个
# 止盈全平，不分TP1/2/3"。
#
# 市价单：几乎立即成交，下单后短暂重试(最多5次×1秒)确认仓位已建立，
# 同一次HTTP请求里直接挂上止损/止盈。
# 限价单：下单立即返回，止损/止盈的挂单挪到后台线程里做——每5秒轮询
# 一次订单状态，成交了就挂止损止盈，到宝贝填的超时分钟数还没成交就
# 撤单，不阻塞这次HTTP请求(限价单可能要等最多30分钟)。
# ═══════════════════════════════════════════════════════════════

MANUAL_SPOT_ORDER_OPEN_TEMPLATE = """
import json
from binance_client import binance_client

symbol = {symbol!r}
side = {side!r}
order_type = {order_type!r}
limit_price = {limit_price!r}

acct_info = binance_client.client.futures_account()
avail = float(acct_info.get("availableBalance") or 0)
if avail <= 0:
    print(json.dumps({{"ok": False, "msg": f"可用余额不足或查询失败(avail={{avail}})"}}))
    raise SystemExit

if order_type == "LIMIT" and limit_price:
    ref_price = float(limit_price)
else:
    ticker = binance_client.client.futures_symbol_ticker(symbol=symbol)
    ref_price = float(ticker["price"])

qty = binance_client.format_quantity(avail / ref_price, symbol)
if qty <= 0:
    print(json.dumps({{"ok": False, "msg": f"算出下单量太小(avail={{avail}} price={{ref_price}})"}}))
    raise SystemExit

if order_type == "LIMIT" and limit_price:
    order = binance_client.place_limit_order(side, qty, float(limit_price), symbol=symbol, reduce_only=False)
else:
    order = binance_client.place_market_order(side, qty, symbol=symbol, reduce_only=False)

if not order:
    print(json.dumps({{"ok": False, "msg": "下单失败(详见该账户引擎日志)"}}))
    raise SystemExit

print(json.dumps({{"ok": True, "order": order, "qty": qty, "ref_price": ref_price, "avail_balance": avail}}, default=str))
"""

MANUAL_SPOT_ATTACH_SLTP_TEMPLATE = """
import json
import time as _time
from binance_client import binance_client

symbol = {symbol!r}
sl_price = {sl_price!r}
tp_price = {tp_price!r}

positions = binance_client.client.futures_position_information(symbol=symbol)
amt = 0.0
for p in positions:
    amt = float(p.get("positionAmt") or 0)
if amt == 0:
    print(json.dumps({{"ok": False, "msg": "当前查不到仓位，无法挂止损/止盈(可能还没成交或已平)"}}))
    raise SystemExit

live_side = "LONG" if amt > 0 else "SHORT"
close_side = "SELL" if live_side == "LONG" else "BUY"
result = {{"ok": True, "position_amt": amt, "live_side": live_side}}

if sl_price:
    sl_order = binance_client.place_stop_market_order(
        close_side, float(sl_price), symbol=symbol, quantity=None,
        client_order_id="MSpotSL" + str(int(_time.time()))[-8:],
    )
    result["sl_order"] = sl_order
    if not sl_order:
        result["ok"] = False
        result["msg"] = "止损挂单失败"

if tp_price:
    try:
        tp_order = binance_client.client.futures_create_order(
            symbol=symbol, side=close_side, type="TAKE_PROFIT_MARKET",
            stopPrice=binance_client.format_price(float(tp_price), symbol),
            closePosition="true", workingType="CONTRACT_PRICE",
        )
    except Exception as e:
        tp_order = {{"error": str(e)}}
        result["ok"] = False
        result["msg"] = (result.get("msg") or "") + f" 止盈挂单失败: {{e}}"
    result["tp_order"] = tp_order

print(json.dumps(result, default=str))
"""

MANUAL_CHECK_ORDER_STATUS_TEMPLATE = """
import json
from binance_client import binance_client
try:
    o = binance_client.client.futures_get_order(symbol={symbol!r}, orderId={order_id!r})
    print(json.dumps({{"ok": True, "status": o.get("status"), "executedQty": o.get("executedQty")}}))
except Exception as e:
    print(json.dumps({{"ok": False, "msg": str(e)}}))
"""

MANUAL_CANCEL_ORDER_TEMPLATE = """
import json
from binance_client import binance_client
try:
    r = binance_client.client.futures_cancel_order(symbol={symbol!r}, orderId={order_id!r})
    print(json.dumps({{"ok": True, "result": r}}, default=str))
except Exception as e:
    print(json.dumps({{"ok": False, "msg": str(e)}}))
"""


def _run_account_py(acct, code, timeout=20):
    """跟_full_symbol_catalog()/CLOSE_CODE_TEMPLATE同一种subprocess模式：
    在目标账户自己的repo目录下用它自己的venv python执行一段代码，只能
    import该账户自己能import到的模块(binance_client等纯client层)，不会
    误触发position_supervisor的任何常驻逻辑。"""
    acct_dir = f'/home/{acct["user"]}/binance-engine'
    try:
        p = subprocess.run(
            [f"{acct_dir}/venv/bin/python", "-c", code],
            capture_output=True, timeout=timeout, cwd=acct_dir,
        )
    except Exception as e:
        return {"ok": False, "msg": f"subprocess_failed: {e}"}
    out = p.stdout.decode("utf-8", errors="replace").strip()
    err = p.stderr.decode("utf-8", errors="replace").strip()
    if not out:
        return {"ok": False, "msg": f"no_output stderr={err[:500]}"}
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        return {"ok": False, "msg": f"bad_output: {out[:500]} stderr={err[:300]}"}


def _watch_manual_limit_fill(acct, symbol, side, order_id, sl_price, tp_price, timeout_min):
    """后台线程：每5秒查一次限价单状态，成交了就挂止损/止盈。
    2026-09-21改：超时未成交不再是单纯撤单了事——宝贝要求"设置超时就
    市价成交"，保证这笔手工单最终一定会有仓位，不会因为限价一直够不
    着而落空。改成：超时先撤掉那笔未成交的限价单(此时executedQty
    保证还是0，上面的成交分支已经拦掉了任何部分成交的情况)，再现场
    追一笔市价单(重新按那一刻的可用余额算1倍现货等值下单量，不是复用
    限价单当时算的旧数量)，成交后照常挂止损/止盈。整个过程只调用
    _run_account_py(纯client层脚本)，不碰position_supervisor。"""
    deadline = time.time() + float(timeout_min or 5) * 60
    while time.time() < deadline:
        time.sleep(5)
        st = _run_account_py(
            acct, MANUAL_CHECK_ORDER_STATUS_TEMPLATE.format(symbol=symbol, order_id=int(order_id)),
        )
        status = st.get("status")
        if status == "FILLED" or (st.get("executedQty") and float(st.get("executedQty") or 0) > 0):
            attach = _run_account_py(
                acct,
                MANUAL_SPOT_ATTACH_SLTP_TEMPLATE.format(
                    symbol=symbol,
                    sl_price=float(sl_price) if sl_price else None,
                    tp_price=float(tp_price) if tp_price else None,
                ),
            )
            print(f"[MANUAL_SPOT_LIMIT_FILLED] account={acct['id']} symbol={symbol} order_id={order_id} -> {attach}", flush=True)
            return
        if status in ("CANCELED", "EXPIRED", "REJECTED"):
            print(f"[MANUAL_SPOT_LIMIT_GONE] account={acct['id']} symbol={symbol} order_id={order_id} status={status}", flush=True)
            return

    cancel = _run_account_py(acct, MANUAL_CANCEL_ORDER_TEMPLATE.format(symbol=symbol, order_id=int(order_id)))
    market_result = _run_account_py(
        acct,
        MANUAL_SPOT_ORDER_OPEN_TEMPLATE.format(symbol=symbol, side=side, order_type="MARKET", limit_price=None),
    )
    print(
        f"[MANUAL_SPOT_LIMIT_TIMEOUT_MARKET_FALLBACK] account={acct['id']} symbol={symbol} "
        f"order_id={order_id} cancel={cancel} market={market_result}", flush=True,
    )
    if not market_result.get("ok"):
        return
    attach = None
    for _ in range(5):
        attach = _run_account_py(
            acct,
            MANUAL_SPOT_ATTACH_SLTP_TEMPLATE.format(
                symbol=symbol,
                sl_price=float(sl_price) if sl_price else None,
                tp_price=float(tp_price) if tp_price else None,
            ),
        )
        if attach.get("ok"):
            break
        time.sleep(1.0)
    print(f"[MANUAL_SPOT_LIMIT_TIMEOUT_MARKET_ATTACH] account={acct['id']} symbol={symbol} -> {attach}", flush=True)


@app.route("/api/manual_spot/order", methods=["POST"])
def api_manual_spot_order():
    body = request.get_json(force=True, silent=True) or {}
    acct = _find_account(body.get("account"))
    if not acct:
        return jsonify({"ok": False, "msg": "bad_account"}), 400
    symbol = str(body.get("symbol") or "").strip().upper()
    side = str(body.get("side") or "").strip().upper()
    order_type = str(body.get("order_type") or "MARKET").strip().upper()
    limit_price = body.get("limit_price")
    sl_price = body.get("sl_price")
    tp_price = body.get("tp_price")
    timeout_min = body.get("limit_timeout_min") or 5

    if not symbol or side not in ("LONG", "SHORT"):
        return jsonify({"ok": False, "msg": "品种/方向必填"}), 400
    if order_type not in ("MARKET", "LIMIT"):
        return jsonify({"ok": False, "msg": "订单类型必须是MARKET或LIMIT"}), 400
    if order_type == "LIMIT" and not limit_price:
        return jsonify({"ok": False, "msg": "限价单必须填限价"}), 400
    if not sl_price:
        return jsonify({"ok": False, "msg": "必须填止损价(这条路径没有雷达/系统止损兜底)"}), 400

    open_result = _run_account_py(
        acct,
        MANUAL_SPOT_ORDER_OPEN_TEMPLATE.format(
            symbol=symbol, side=side, order_type=order_type,
            limit_price=float(limit_price) if limit_price else None,
        ),
    )
    print(f"[MANUAL_SPOT_ORDER] account={acct['id']} symbol={symbol} side={side} type={order_type} -> {open_result}", flush=True)
    if not open_result.get("ok"):
        return jsonify(open_result), 200

    if order_type == "MARKET":
        attach = None
        for _ in range(5):
            attach = _run_account_py(
                acct,
                MANUAL_SPOT_ATTACH_SLTP_TEMPLATE.format(
                    symbol=symbol,
                    sl_price=float(sl_price) if sl_price else None,
                    tp_price=float(tp_price) if tp_price else None,
                ),
            )
            if attach.get("ok"):
                break
            time.sleep(1.0)
        open_result["attach"] = attach
        return jsonify(open_result), 200

    order_id = (open_result.get("order") or {}).get("orderId")
    if order_id:
        threading.Thread(
            target=_watch_manual_limit_fill,
            args=(acct, symbol, side, order_id, sl_price, tp_price, timeout_min),
            daemon=True,
        ).start()
        open_result["watching"] = True
    return jsonify(open_result), 200


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


# ── 策略对比面板：4套公开知名战法 vs tv_multiscore_v1，同一套"直接读sqlite
# +subprocess取一次性数据"模式，见上方2026-08-29注释 ─────────────────────

SHADOW_V2_DB_PATH = f"{STRATEGY_ENGINE_DIR}/strategy_engine/data/shadow_v2.db"

# 2026-09-12新增：VWAP实盘面板——vwap_mean_reversion真账户测试(vwap_live，
# 独立项目，本机 /root/vwap_live/，systemd 服务 vwap-live.service)。跟上面
# 策略对比面板同一个模式："直接读它自己落盘的 sqlite + journalctl，不
# import它的任何执行代码、不碰它的下单逻辑"——本面板对 vwap_live 完全
# 只读，没有任何写操作(跟已有的 close_position/grid_order 那两个唯一写
# 操作不是一回事，那两个走各账户自己 venv 的 subprocess 调用；这里连
# subprocess 调用 vwap_live 代码都没有，只读它的 db 文件 + 系统日志)。
VWAP_LIVE_DB_PATH = "/root/vwap_live/data/vwap_live.db"
VWAP_LIVE_SERVICE = "vwap-live.service"
VWAP_LIVE_ENV_PATH = "/root/vwap_live/.env"


def _vwap_live_armed_flags():
    """武装状态直接读 .env 的两道闸门，不从 decisions 表最后一条推断——
    还没触发过任何信号/风控拦截时 decisions 表可能是空的，那样推断会
    误报"未武装"。**只读取 LIVE_TRADING 这个布尔值 + 判断 KEY/SECRET
    是否非空**，never 把 key/secret 的值本身放进返回结果。"""
    live_trading = False
    key_present = False
    try:
        with open(VWAP_LIVE_ENV_PATH, "r", encoding="utf-8", errors="replace") as f:
            k = s = ""
            for line in f:
                line = line.strip()
                if line.startswith("LIVE_TRADING="):
                    live_trading = line.split("=", 1)[1].strip().lower() in ("1", "true", "yes", "on")
                elif line.startswith("BINANCE_API_KEY="):
                    k = line.split("=", 1)[1].strip()
                elif line.startswith("BINANCE_API_SECRET="):
                    s = line.split("=", 1)[1].strip()
            key_present = bool(k and s)
    except Exception as e:
        print(f"[vwap_live] .env 状态读取失败: {e}", flush=True)
    return {"live_trading": live_trading, "api_key_present": key_present, "armed": live_trading and key_present}


def _vwap_live_query(sql, params=()):
    if not os.path.exists(VWAP_LIVE_DB_PATH):
        return []
    try:
        conn = sqlite3.connect(f"file:{VWAP_LIVE_DB_PATH}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[vwap_live] db 查询失败: {e}", flush=True)
        return []

_descriptions_cache = {"ts": 0, "data": None}
_DESCRIPTIONS_CACHE_TTL_SEC = 300
_roster_cache = {"ts": 0, "data": None}
_ROSTER_CACHE_TTL_SEC = 300

# 2026-08-29修订：最初是把realized_pnl_atr_weighted按"每1×ATR=$100"的
# 粗略比例换算成金额，宝贝要求改成"每套策略从1000 USDT模拟净值起步，
# 按实盘同一套额度配比公式(风险20%×5倍杠杆×ADX档位系数，见strategy_
# engine/position_sizing.py)算真实开仓数量qty"——现在qty已经在开仓时
# 存进shadow_positions_v2.qty列，真实美元盈亏 = realized_pnl_atr_weighted
# ×atr0×qty，直接在SQL里算，不再是拍脑袋的固定比例。DEFAULT_STARTING_
# EQUITY跟strategy_engine.shadow_store.DEFAULT_STARTING_EQUITY保持一致
# (两边都是常量、不共享导入，改动时两处一起改)。
#
# 老数据缺口：这套qty机制上线之前已经平仓的几笔交易(qty列是NULL)，
# SUM(...×qty)里NULL会被自动跳过，不会拉低/污染新口径的总数，只是那
# 几笔不会计入美元合计——ATR倍数合计(total_pnl_atr)不受影响，仍然完整。
DEFAULT_STARTING_EQUITY = 1000.0


def _get_strategy_equities():
    """直接读shadow_v2.db的strategy_equity表(跟持仓表同一个sqlite文件，
    没有额外一层subprocess调用的必要)。返回{strategy: equity}，没有
    记录的策略视为还没结算过任何一笔，净值等于起始值。"""
    rows = _shadow_v2_query("SELECT strategy, equity FROM strategy_equity")
    return {r["strategy"]: float(r["equity"]) for r in rows}


def _get_comparison_strategy_names():
    """对比面板要展示的完整策略名单——不是shadow_v2.db里已经有记录的
    策略，是comparison_roster.py里配置在跑的全部策略(哪怕还一单都没
    触发过也要显示"0笔·等待中"，而不是从列表里消失，宝贝反馈过看不到
    还没触发的策略会以为它们没在跑)。tv_multiscore_v1不在这个roster里
    (它是shadow_engine.py自己独立的TV镜像循环)，单独补上。"""
    now = time.time()
    if _roster_cache["data"] is not None and now - _roster_cache["ts"] < _ROSTER_CACHE_TTL_SEC:
        return _roster_cache["data"]
    code = (
        "from strategy_engine.comparison_roster import SINGLE_SYMBOL_ROSTER, UNIVERSE_ROSTER, PAIRS_ROSTER\n"
        "import json\n"
        "names = sorted(set([e['strategy'] for e in SINGLE_SYMBOL_ROSTER] + [e['strategy'] for e in UNIVERSE_ROSTER] + [e['strategy'] for e in PAIRS_ROSTER]))\n"
        "print(json.dumps(names))"
    )
    result = _strategy_engine_call(code, timeout=10)
    names = result if isinstance(result, list) else []
    names = sorted(set(names) | {"tv_multiscore_v1"})
    _roster_cache["ts"] = now
    _roster_cache["data"] = names
    return names


def _shadow_v2_query(sql, params=()):
    if not os.path.exists(SHADOW_V2_DB_PATH):
        return []
    try:
        conn = sqlite3.connect(SHADOW_V2_DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[strategy_compare] shadow_v2.db 查询失败: {e}", flush=True)
        return []


def _get_strategy_descriptions():
    now = time.time()
    if _descriptions_cache["data"] is not None and now - _descriptions_cache["ts"] < _DESCRIPTIONS_CACHE_TTL_SEC:
        return _descriptions_cache["data"]
    code = (
        "from strategy_engine.strategies import STRATEGY_DESCRIPTIONS\n"
        "import json\n"
        "print(json.dumps(STRATEGY_DESCRIPTIONS))"
    )
    result = _strategy_engine_call(code, timeout=10)
    data = result if isinstance(result, dict) and not (result.get("ok") is False) else {}
    _descriptions_cache["ts"] = now
    _descriptions_cache["data"] = data
    return data


@app.route("/api/strategy/compare")
def api_strategy_compare():
    """跨策略顶层对比表：已平仓笔数/胜率/累计盈亏(ATR倍数+估算美元)/
    当前持仓数 + 每个策略的人话说明。2026-08-29修复：改成以
    comparison_roster.py里配置的完整策略名单为基准(不是"shadow_v2.db里
    已经有记录的策略")，还没触发过任何一单的策略也会显示"0笔·等待中"，
    不会从列表里消失。"""
    rows = _shadow_v2_query("""
        SELECT strategy,
               COUNT(*) AS trades,
               SUM(CASE WHEN realized_pnl_atr_weighted > 0 THEN 1 ELSE 0 END) AS wins,
               ROUND(SUM(realized_pnl_atr_weighted), 4) AS total_pnl_atr,
               ROUND(AVG(realized_pnl_atr_weighted), 4) AS avg_pnl_atr,
               ROUND(MIN(realized_pnl_atr_weighted), 4) AS worst_trade_atr,
               ROUND(SUM(realized_pnl_atr_weighted * atr0 * qty), 2) AS total_pnl_usd,
               ROUND(AVG(realized_pnl_atr_weighted * atr0 * qty), 2) AS avg_pnl_usd,
               ROUND(MIN(realized_pnl_atr_weighted * atr0 * qty), 2) AS worst_trade_usd
        FROM shadow_positions_v2 WHERE status='closed' GROUP BY strategy
    """)
    open_counts = _shadow_v2_query("""
        SELECT strategy, COUNT(*) AS open_count
        FROM shadow_positions_v2 WHERE status='open' GROUP BY strategy
    """)
    open_map = {r["strategy"]: r["open_count"] for r in open_counts}
    equities = _get_strategy_equities()
    by_strategy = {r["strategy"]: r for r in rows}
    for strat in _get_comparison_strategy_names():
        by_strategy.setdefault(strat, {
            "strategy": strat, "trades": 0, "wins": 0,
            "total_pnl_atr": 0.0, "avg_pnl_atr": None, "worst_trade_atr": None,
            "total_pnl_usd": 0.0, "avg_pnl_usd": None, "worst_trade_usd": None,
        })
    descriptions = _get_strategy_descriptions()
    out = []
    for strat, row in by_strategy.items():
        trades = int(row.get("trades") or 0)
        wins = int(row.get("wins") or 0)
        equity = equities.get(strat, DEFAULT_STARTING_EQUITY)
        out.append({
            **row,
            "open_count": int(open_map.get(strat, 0)),
            "win_rate": round(100.0 * wins / trades, 1) if trades > 0 else None,
            "description": descriptions.get(strat, ""),
            "equity": round(equity, 2),
            "equity_return_pct": round(100.0 * (equity - DEFAULT_STARTING_EQUITY) / DEFAULT_STARTING_EQUITY, 2),
        })
    out.sort(key=lambda r: r["strategy"])
    return jsonify({
        "status": "ok", "strategies": out,
        "starting_equity": DEFAULT_STARTING_EQUITY,
    })


@app.route("/api/strategy/compare/<strategy>/positions")
def api_strategy_compare_positions(strategy):
    status = request.args.get("status", "closed")
    limit = int(request.args.get("limit", 200))
    if status not in ("open", "closed"):
        status = "closed"
    order = "entry_bar_time DESC" if status == "closed" else "entry_bar_time ASC"
    rows = _shadow_v2_query(
        f"SELECT * FROM shadow_positions_v2 WHERE strategy=? AND status=? "
        f"ORDER BY {order} LIMIT ?",
        (strategy, status, limit),
    )
    if status == "open":
        # 持仓中的模拟仓没有realized_pnl(还没平仓)，宝贝要看的是"现在
        # 浮盈浮亏多少美元"——现价用跟账户总览同一个公开行情接口
        # (fetch_live_prices，无需API key)现算，美元金额用这笔仓位开仓
        # 时按实盘公式算好、存在qty列里的真实开仓数量(不是估算比例)。
        prices = fetch_live_prices()
        for r in rows:
            px = prices.get(r["symbol"])
            atr0 = float(r.get("atr0") or 0)
            entry = float(r.get("entry") or 0)
            qty = float(r.get("qty") or 0)
            if px is not None and atr0 > 0 and entry > 0:
                direction = 1.0 if r.get("side") == "LONG" else -1.0
                unrealized_atr = round(direction * (px - entry) / atr0, 4)
                r["current_price"] = px
                r["unrealized_pnl_atr"] = unrealized_atr
                r["unrealized_pnl_usd"] = round(unrealized_atr * atr0 * qty, 2) if qty > 0 else None
            else:
                r["current_price"] = None
                r["unrealized_pnl_atr"] = None
                r["unrealized_pnl_usd"] = None
    else:
        for r in rows:
            atr0 = float(r.get("atr0") or 0)
            qty = float(r.get("qty") or 0)
            pnl_atr = r.get("realized_pnl_atr_weighted")
            r["pnl_usd"] = round(float(pnl_atr or 0) * atr0 * qty, 2) if (qty > 0 and pnl_atr is not None) else None
    return jsonify({"status": "ok", "positions": rows})


@app.route("/api/strategy/compare/<strategy>/by_symbol")
def api_strategy_compare_by_symbol(strategy):
    """单个策略按品种拆开的胜率/盈亏——对比表点开某个策略后的下钻视图。"""
    rows = _shadow_v2_query("""
        SELECT symbol,
               COUNT(*) AS trades,
               SUM(CASE WHEN realized_pnl_atr_weighted > 0 THEN 1 ELSE 0 END) AS wins,
               ROUND(SUM(realized_pnl_atr_weighted), 4) AS total_pnl_atr,
               ROUND(AVG(realized_pnl_atr_weighted), 4) AS avg_pnl_atr,
               ROUND(SUM(realized_pnl_atr_weighted * atr0 * qty), 2) AS total_pnl_usd,
               ROUND(AVG(realized_pnl_atr_weighted * atr0 * qty), 2) AS avg_pnl_usd
        FROM shadow_positions_v2 WHERE status='closed' AND strategy=?
        GROUP BY symbol ORDER BY total_pnl_usd DESC
    """, (strategy,))
    for r in rows:
        trades = int(r.get("trades") or 0)
        wins = int(r.get("wins") or 0)
        r["win_rate"] = round(100.0 * wins / trades, 1) if trades > 0 else None
    return jsonify({"status": "ok", "by_symbol": rows})


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


# ── VWAP实盘面板：vwap_mean_reversion 真账户测试(独立项目 vwap_live)。
# 2026-09-12新增，完全只读——不 import vwap_live 的任何代码，不碰它的
# 下单逻辑，只读它自己的 sqlite 账本 + journalctl 系统日志 ──────────────

@app.route("/api/vwap_live/summary")
def api_vwap_live_summary():
    closed = _vwap_live_query("""
        SELECT COUNT(*) trades,
               SUM(CASE WHEN realized_pnl_usd > 0 THEN 1 ELSE 0 END) wins,
               ROUND(SUM(realized_pnl_usd), 2) total_pnl_usd
        FROM positions WHERE status='CLOSED'
    """)
    row = closed[0] if closed else {}
    trades = int(row.get("trades") or 0)
    wins = int(row.get("wins") or 0)
    open_rows = _vwap_live_query("SELECT COUNT(*) n FROM positions WHERE status='OPEN'")
    open_count = int(open_rows[0]["n"]) if open_rows else 0
    today = time.strftime("%Y-%m-%d", time.gmtime())
    daily = _vwap_live_query("SELECT realized_pnl_usd, halted FROM daily_pnl WHERE day_bucket=?", (today,))
    today_pnl = float(daily[0]["realized_pnl_usd"] or 0) if daily else 0.0
    halted = bool(daily[0]["halted"]) if daily else False
    armed = _vwap_live_armed_flags()["armed"]
    # 权益是交易所侧的实时数据，本面板刻意不为了显示它而额外发一次签名
    # 请求(每次刷新面板都打一次账户API不划算)，如实留空，前端显示"-"。
    equity = None
    svc_state = _run(["systemctl", "is-active", VWAP_LIVE_SERVICE]).strip()
    return jsonify({
        "status": "ok",
        "service_active": svc_state == "active",
        "summary": {
            "trades": trades, "wins": wins,
            "win_rate": round(100.0 * wins / trades, 1) if trades > 0 else None,
            "total_pnl_usd": float(row.get("total_pnl_usd") or 0),
            "today_pnl_usd": today_pnl,
            "open_count": open_count,
            "halted_today": halted,
            "armed": armed,
            "equity": equity,
        },
    })


@app.route("/api/vwap_live/positions")
def api_vwap_live_positions():
    status = request.args.get("status", "open").upper()
    limit = min(int(request.args.get("limit", 50)), 500)
    if status not in ("OPEN", "CLOSED"):
        status = "OPEN"
    rows = _vwap_live_query(
        "SELECT * FROM positions WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)
    )
    return jsonify({"status": "ok", "positions": rows})


@app.route("/api/vwap_live/decisions")
def api_vwap_live_decisions():
    limit = min(int(request.args.get("limit", 150)), 1000)
    rows = _vwap_live_query(
        "SELECT id, ts, symbol, action, side, price, reason, armed, order_result "
        "FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
    )
    return jsonify({"status": "ok", "decisions": rows})


@app.route("/api/vwap_live/logs")
def api_vwap_live_logs():
    """journalctl 原始日志——含真实报错/Traceback，出问题的时候这个比
    account/positions 那几张表更能看出"卡在哪一步"。只读 journalctl，
    不解析、不脱敏(vwap_live 自己的日志里本来就不打印 key/secret)。"""
    lines = min(int(request.args.get("lines", 200)), 1000)
    out = _run(["journalctl", "-u", VWAP_LIVE_SERVICE, "-n", str(lines), "--no-pager", "-o", "short-iso"])
    log_lines = [l for l in out.splitlines() if l.strip()]
    return jsonify({"status": "ok", "logs": log_lines})


@app.route("/")
def index():
    return Response(INDEX_HTML, mimetype="text/html")


INDEX_HTML = open(__file__.replace("server.py", "index.html"), encoding="utf-8").read()

if __name__ == "__main__":
    t = threading.Thread(target=background_refresher, daemon=True)
    t.start()
    app.run(host="127.0.0.1", port=8877, debug=False, threaded=True)

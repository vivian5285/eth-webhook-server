#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
独立只读监控：每轮检查三个币安账户的健康/持仓/TV信号一致性/幽灵单/真实ERROR，
异常发钉钉（30分钟内同一异常去重），每天08:00/20:00发心跳汇总。

只读原则：只调用 binance_client 的查询方法（跑在各账户自己的venv里，
复用它们各自的.env凭证），不导入 position_supervisor_*，不下单不撤单。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone

from dingtalk_notify import send_text

ACCOUNTS = [
    {"name": "B", "port": 5007, "dir": "/home/binanceB/binance-engine", "service": "binanceB-engine"},
    {"name": "C", "port": 5008, "dir": "/home/binanceC/binance-engine", "service": "binanceC-engine"},
    # 2026-08-20：D账户暂停监控——没放资金(权益0)也没接TV，任何健康/持仓/
    # 开仓检查对它来说本来就该是空的，反而容易制造假警报(比如今天验证网格
    # 套利闸门时D的"sizing拒绝：权益=0.0"就是预期内的正常拒绝，不是故障)。
    # monitor=False只是跳过检查，D账户本身/binance_vps_state文件都还在，
    # 以后放资金接TV了，把这行改回True (或直接删掉这个key) 即可恢复监控。
    {"name": "D", "port": 5009, "dir": "/home/binanceD/binance-engine", "service": "binanceD-engine", "monitor": False},
    {"name": "E", "port": 5010, "dir": "/home/binanceE/binance-engine", "service": "binanceE-engine"},
]
MONITORED_ACCOUNTS = [a for a in ACCOUNTS if a.get("monitor", True)]
SYMBOLS = ["ETHUSDT", "XAUUSDT", "BNBUSDT", "ZECUSDT", "BCHUSDT", "XMRUSDT", "SNDKUSDT", "PAXGUSDT", "SKHYNIXUSDT", "XPDUSDT", "OPENAIUSDT", "ANTHROPICUSDT", "ASMLUSDT"]

STATE_PATH = os.path.join(os.path.dirname(__file__), "watchdog_state.json")
ALERT_DEDUPE_SEC = 30 * 60
HEARTBEAT_HOURS = {8, 20}  # VPS本地时区(UTC)的整点小时
# 2026-08-20：TV心跳失联二级监控——只对"以前真的收到过心跳"的品种生效，
# 阈值故意给得很宽松(24小时)，够盖住所有品种(哪怕BCH/XMR这种6小时一根
# K线的)正常的心跳间隔，不会跟任何品种的正常节奏撞车误报，真出问题
# (比如某个品种的TV心跳代码被改坏/漏发)也不会拖太久才被发现。watchdog
# 刻意不import引擎自己的reentry_profiles.py(保持独立，引擎有bug也不
# 连累watchdog)，所以不按各品种精确TV周期算，直接用一个足够宽的固定值。
HEARTBEAT_SILENCE_SEC = 24 * 3600.0
NOISE_ERROR_PATTERNS = (
    "AttributeError: 'Client' object has no attribute 'session'",
    "NoneType' object has no attribute 'sock' - goodbye",
    "穿价 TP1 推离市价",
    "code=-4509",
    # 2026-08-20：今天连续部署了7轮(每轮D→B→C共21次重启)后发现watchdog噪音
    # 几乎全部集中在重启那几秒——"终检防线未齐"是重启后终检瞬间的正常过渡
    # 状态，实测两次(B账户OPENAI/C账户SKHYNIX)都是不到1秒内就被同一进程
    # 自己补挂修好("强制闭环"这四个字本身就是代码在原地自愈)，从没见过
    # 它自愈失败的情况，直接当噪音过滤，不用等证据。
    "终检防线未齐",
    # Telegram单次超时(还有重试机会)不算真失败，只有attempt=3/3(最后一次
    # 还失败)才算真的通知不出去，需要保留上报。
    "notify fail channel=telegram attempt=1/",
    "notify fail channel=telegram attempt=2/",
)


def _load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_alert": {}, "last_heartbeat_date_hour": ""}


def _save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _run(cmd: list, timeout: int = 20, cwd: str | None = None) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, cwd=cwd)
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", errors="replace")[-300:]
            return f"__ERR__rc={r.returncode} {err}"
        return r.stdout.decode("utf-8", errors="replace")
    except Exception as e:
        return f"__ERR__{e}"


# 2026-08-23：多品种账户(B/C/E各13个品种)重启后要逐品种核对TP/止损，实测
# 单线程gunicorn(-w1 --threads1)重启恢复期间/health经常5分钟以上才能腾出
# 手响应——check_health原有的"5次×3秒≈15秒"重试预算是很久以前只针对单一
# 品种校准的，早就跟不上现在的品种数量。同一天两轮部署重启都在这个窗口
# 里被watchdog误判成"健康异常"发钉钉，跟真故障混在一起容易让人脱敏。这
# 里不是无脑加长重试预算(那样真故障也要多等好几分钟才会报警)，而是让
# check_health能识别"是不是刚重启不久"，只有这种情况才放宽，真正长期
# 无响应还是照样第一时间报。
RESTART_GRACE_SEC = 480.0


def _service_uptime_sec(service: str):
    """服务自上次(re)start以来经过的秒数。用monotonic时钟(/proc/uptime +
    ActiveEnterTimestampMonotonic)算，不解析systemctl的wall-clock时间戳
    字符串——那个格式受locale/systemd版本影响，用strptime解析容易踩坑，
    纯数字的monotonic时钟没有这个问题。取不到(systemctl不可用/服务不
    存在)返回None，调用方必须按"不知道，保守当真异常处理"，不能因为
    拿不到时间戳就悄悄放过真正的故障。"""
    out = _run(["systemctl", "show", service, "--property=ActiveEnterTimestampMonotonic", "--value"])
    if out.startswith("__ERR__"):
        return None
    try:
        active_since_us = float(out.strip())
    except (TypeError, ValueError):
        return None
    if active_since_us <= 0:
        return None
    try:
        with open("/proc/uptime", "r") as f:
            uptime_now_sec = float(f.read().split()[0])
    except Exception:
        return None
    return uptime_now_sec - (active_since_us / 1_000_000.0)


def check_health(acct: dict) -> dict:
    """
    2026-08-12：部署重启(systemctl restart)几秒内端口会短暂无响应，
    单次curl失败就报警会跟运维自己的正常重启撞车产生假警报（当晚
    实盘复现两次，时间点跟deploy_safe_restart.sh的重启时刻精确重合）。
    2026-08-20：单轮部署实测(B/C各5个真实持仓品种)偶尔重启恢复比原来
    校准时(单一/少量品种)更久，9s窗口有几次跟watchdog的10分钟轮询撞上，
    再次假警报——重试从3次/3s(9s)放宽到5次/3s(15s)，正常重启仍能扛住，
    真的挂了(>15s持续无响应)才报警。
    2026-08-23：品种数量涨到13个之后，15s这个预算又不够了——单线程
    gunicorn重启后要逐品种核对TP/止损，实测繁忙账户经常5分钟以上都还
    在恢复中，两轮当天的部署重启都被当成"健康异常"发了钉钉，吓人还没用
    (真相是重启在正常进行，不是故障)。不再无脑加长这15s的重试预算(那样
    真故障也要多等好几分钟才会报警)，而是5次重试仍无响应时，额外查一下
    这个服务是不是最近才(re)start的(_service_uptime_sec)——是的话判定
    为"重启恢复中"，不算异常；查不到重启时间/重启已经超过宽限期还是没
    响应，才当真异常处理。
    """
    out = ""
    for attempt in range(5):
        out = _run(["curl", "-sf", "--max-time", "5", f"http://127.0.0.1:{acct['port']}/health"])
        if not out.startswith("__ERR__") and out.strip():
            break
        if attempt < 4:
            time.sleep(3)
    if out.startswith("__ERR__") or not out.strip():
        uptime = _service_uptime_sec(acct["service"])
        if uptime is not None and 0 <= uptime < RESTART_GRACE_SEC:
            return {
                "ok": False,
                "restarting": True,
                "detail": f"重启后{uptime:.0f}s仍无响应(判定为多品种核对中，非异常)",
                "open_in_progress": {},
            }
        return {"ok": False, "restarting": False, "detail": "无响应(重试5次仍失败)", "open_in_progress": {}}
    try:
        data = json.loads(out)
    except Exception:
        return {"ok": False, "detail": "响应无法解析", "open_in_progress": {}}
    paused = data.get("trading_paused") or {}
    paused_syms = [s for s, v in paused.items() if v]
    return {
        "ok": data.get("status") == "ok" and not paused_syms,
        "paused_syms": paused_syms,
        "open_in_progress": data.get("open_in_progress") or {},
    }


def check_dashboard() -> dict:
    """面板本身是否存活（跟三个交易账户是独立进程，各自可能单独挂掉）。"""
    out = _run(["curl", "-sf", "--max-time", "5", "http://127.0.0.1:8877/api/status"])
    if out.startswith("__ERR__") or not out.strip():
        return {"ok": False, "detail": "无响应"}
    try:
        data = json.loads(out)
    except Exception:
        return {"ok": False, "detail": "响应无法解析"}
    return {"ok": bool(data.get("ok", True)), "detail": ""}


def check_gateway() -> dict:
    """
    2026-08-15：新增。广播网关(binance-gateway.service, 127.0.0.1:5006)
    是部分品种TV警报唯一入口（TV订阅上限20条警报，这些品种改走网关
    一条警报覆盖B/C/D三账户）——网关若挂了，这些品种会静默漏单，
    比单独一个账户的/health异常更隐蔽（不会体现在ACCOUNTS的健康检查
    里），必须单独探活。
    """
    out = _run(["curl", "-sf", "--max-time", "5", "http://127.0.0.1:5006/health"])
    if out.startswith("__ERR__") or not out.strip():
        return {"ok": False, "detail": "无响应"}
    try:
        data = json.loads(out)
    except Exception:
        return {"ok": False, "detail": "响应无法解析"}
    return {"ok": data.get("status") == "ok", "detail": ""}


def check_nginx() -> dict:
    """
    2026-08-16：新增。此前只检查各账户/网关自己127.0.0.1直连的健康，
    全部绕过了nginx——nginx是所有TV流量(直连三账户路由+广播网关路由)
    共同的前门，真正对外(TV那边)看到的地址全部要经过它，它挂了TV
    连接直接被拒，比网关单独挂掉更致命，但之前完全没被独立探测过
    （三账户和网关的/health检查全部直连127.0.0.1，绕开了nginx，哪怕
    nginx已经挂了这些检查还会显示正常）。
    两层检查：① systemd进程本身在不在跑；② 真的从nginx监听的80端口
    走一次代理请求，确认反代链路本身没坏（不是进程活着但配置错了）。
    """
    svc = _run(["systemctl", "is-active", "nginx"])
    if svc.strip() != "active":
        return {"ok": False, "detail": f"nginx服务未运行(is-active={svc.strip()})"}
    # 走真实80端口代理路径核实，不直连127.0.0.1:5007，确保反代链路本身通畅。
    # 2026-08-23：这里本来只试一次、零重试——这条链路最终还是要打到B账户
    # 自己的/health，B重启后逐品种核对期间本来就可能几分钟没空响应(见
    # check_health同一天的修复注释)，单次curl零重试比check_health的"5次
    # 重试"还脆弱，B一重启这里几乎必现误报。同样用_service_uptime_sec
    # 判断B是不是刚重启不久，是的话不算异常。
    out = _run(["curl", "-sf", "--max-time", "5", "http://127.0.0.1/binance-b/health"])
    if out.startswith("__ERR__") or not out.strip():
        b_acct = next((a for a in ACCOUNTS if a["name"] == "B"), None)
        uptime = _service_uptime_sec(b_acct["service"]) if b_acct else None
        if uptime is not None and 0 <= uptime < RESTART_GRACE_SEC:
            return {
                "ok": True,
                "detail": f"反代目标B重启后{uptime:.0f}s仍无响应(判定为多品种核对中，非异常)",
            }
        return {"ok": False, "detail": "nginx进程在跑，但反代/binance-b/health无响应"}
    return {"ok": True, "detail": ""}


def check_disk_space(threshold_pct: int = 85) -> dict:
    """磁盘满了会静默拖垮日志/状态文件写入，属于容易被忽略的运维隐患。"""
    out = _run(["df", "--output=pcent", "/"], timeout=10)
    if out.startswith("__ERR__"):
        return {"ok": True, "pct": -1}  # 查询失败不报警，避免噪音
    try:
        pct_line = out.strip().splitlines()[-1].strip().rstrip("%")
        pct = int(pct_line)
    except Exception:
        return {"ok": True, "pct": -1}
    return {"ok": pct < threshold_pct, "pct": pct}


def fetch_positions_and_orders(acct: dict) -> dict:
    """
    subprocess跑进账户自己的venv，用它自己的.env凭证，纯只读查询。
    2026-08-15：批量重写——原逐品种循环（每品种最多2次REST：持仓+挂单）
    在10品种规模下实测耗时42.3s，超过subprocess 40s超时导致B/C/D三账户
    全部"持仓查询子进程失败"（XPD是第10个新增品种，压垮了这条链路，跟
    2026-08-14修的TV信号查询是同一类"逐品种循环REST不随品种数扩展"的
    问题）。改为3次账户级批量REST：futures_position_information(全量
    持仓)+futures_get_open_orders(全量普通挂单)+openAlgoOrders(全量条件
    单)，均不带symbol参数返回整个账户，本地按symbol分组；algo订单用
    orderType填充'type'字段，跟_normalize_algo_order的语义对齐，
    确保has_stop检测(扫type含'STOP')不因这次重写而漏判。实测<1s，
    且品种数再涨也不会再变慢。
    额外读取每个品种自己的本地状态文件（binance_vps_state_{SYM}.json，
    纯文件读取，不import position_supervisor_*），带出radar_activated/
    radar_activation_price/mark，供run_once()做雷达卡死检测（实盘复现
    过：XAU真实价格冲过激活线，账本radar_activated却一直是False，当时
    是靠人工翻K线才发现，现在watchdog独立核对一遍）。
    """
    code = (
        "import json\n"
        "from binance_client import binance_client\n"
        "all_pos = binance_client._refresh_all_positions(force=True) or {}\n"
        "all_orders = list(binance_client.client.futures_get_open_orders() or [])\n"
        "try:\n"
        "    algo_raw = binance_client.client._request_futures_api('get', 'openAlgoOrders', signed=True, data={}) or []\n"
        "except Exception:\n"
        "    algo_raw = []\n"
        "for a in algo_raw:\n"
        "    all_orders.append({'symbol': a.get('symbol'), 'type': a.get('orderType') or a.get('type') or '', 'orderId': a.get('algoId')})\n"
        "orders_by_sym = {}\n"
        "for o in all_orders:\n"
        "    s = str((o or {}).get('symbol') or '').upper()\n"
        "    if s:\n"
        "        orders_by_sym.setdefault(s, []).append(o)\n"
        "out = {}\n"
        f"for sym in {SYMBOLS!r}:\n"
        "    p = all_pos.get(sym)\n"
        "    amt = float(p.get('positionAmt', 0) or 0) if p else 0.0\n"
        "    orders = orders_by_sym.get(sym, [])\n"
        "    has_stop = False\n"
        "    for o in orders:\n"
        "        ot = str((o or {}).get('type', '') or '').upper()\n"
        "        if 'STOP' in ot:\n"
        "            has_stop = True\n"
        "            break\n"
        "    radar_activated = None\n"
        "    gate = 0.0\n"
        "    hb_side = None\n"
        "    hb_ts = 0.0\n"
        "    try:\n"
        "        with open(f'binance_vps_state_{sym}.json') as f:\n"
        "            st = json.load(f)\n"
        "        radar_activated = bool(st.get('radar_activated'))\n"
        "        gate = float(st.get('radar_activation_price') or 0)\n"
        "        hb_side = st.get('tv_heartbeat_side')\n"
        "        hb_ts = float(st.get('tv_heartbeat_ts') or 0)\n"
        "    except Exception:\n"
        "        pass\n"
        "    out[sym] = {\n"
        "        'side': ('LONG' if amt > 0 else 'SHORT') if amt != 0 else None,\n"
        "        'qty': abs(amt),\n"
        "        'entry': p.get('entryPrice') if p else None,\n"
        "        'mark': float(p.get('markPrice') or 0) if p else 0.0,\n"
        "        'orders': len(orders),\n"
        "        'has_stop': has_stop,\n"
        "        'radar_activated': radar_activated,\n"
        "        'radar_gate': gate,\n"
        "        'hb_side': hb_side,\n"
        "        'hb_ts': hb_ts,\n"
        "    }\n"
        "print(json.dumps(out))\n"
    )
    out = _run(
        [f"{acct['dir']}/venv/bin/python", "-c", code], timeout=40, cwd=acct["dir"],
    )
    if out.startswith("__ERR__"):
        print(f"[watchdog] {acct['name']} 持仓查询子进程失败: {out[:200]}")
        return {}
    try:
        return json.loads(out.strip().splitlines()[-1])
    except Exception as e:
        print(f"[watchdog] {acct['name']} 持仓查询输出解析失败: {e} | raw={out[:200]!r}")
        return {}


TV_SIGNAL_RE = re.compile(r"\[Webhook\] \[(\w+USDT)\] TV .*?【(LONG|SHORT)】")


def fetch_last_tv_signals_all(acct: dict, minutes: int = 15) -> dict:
    """
    2026-08-14：六品种升级——原来每个品种各自调一次journalctl（3账户×6品种=
    18次子进程），现在per-account只拉一次journalctl，一次性解析出全部品种
    最近的TV开仓信号，品种数再涨也只是多几行正则匹配，不会再多起journalctl
    子进程，跑一轮总耗时不随品种数线性增长。
    返回 {symbol: {"side":..., "line":...}}。
    """
    out = _run([
        "journalctl", "-u", acct["service"], "--no-pager", "-S", f"{minutes} min ago",
    ], timeout=20)
    last_by_sym: dict = {}
    for line in out.splitlines():
        if "[Webhook]" not in line:
            continue
        m = TV_SIGNAL_RE.search(line)
        if m:
            last_by_sym[m.group(1)] = {"side": m.group(2), "line": line}
    return last_by_sym


CLOSING_CHATTER_RE = re.compile(
    r"止损单失败.*Order would immediately trigger|"
    r"TP后永久硬止损缺失且补挂失败|"
    r"限价单失败.*ReduceOnly Order is rejected|"
    r"❌ (挂|补挂|UPDATE_TP 挂) TP\d|"
    r"核武轮.*补挂=0"
)
FLAT_CONFIRM_RE = re.compile(
    r"确认空仓：WS\+REST均为0|"
    r"止损挂单未核实但复查仓位已归零|"
    r"仓位已由雷达/TP实际平仓，无需再挂止损|"
    r"确认平仓.*清除stale本地状态|"
    r"雷达/防线账本已清零"  # 2026-08-17：实测这条才是平仓/账本清零最常见的实际文案，
                            # 原来四条都对不上导致这次117秒后才平仓的场景没被降级
)
# 2026-08-17：只靠"最终仓位清零"当证据太粗——中间那段止损缺失窗口如果长达
# 一两分钟，等到真正平仓才降级，会把"曾经短暂裸奔过但后来自己走了"和"根本
# 没裸奔、几秒内就补上另一层防线了"这两种情况混为一谈。这次实测（B账户ETH，
# 05:39:55止损补挂失败→05:39:59雷达止损4秒内就补上→05:41:53才真正平仓，
# 中间隔了117秒，超过90秒窗口，原逻辑没能识别）就是后一种——裸奔窗口其实
# 只有4秒，不该报警。新增一条"防线很快就补上了"的证据，独立于"最终平仓"
# 判断，覆盖率更高也更贴近真实风险（裸奔了多久，而不是仓位最终有没有平）。
DEFENSE_RESTORED_RE = re.compile(
    r"place (HARD|RADAR) stop|"
    r"雷达止损已挂|"
    r"硬止损已挂"
)
DEFENSE_RESTORED_WINDOW_SEC = 30
LOG_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+")
FLAT_CONFIRM_WINDOW_SEC = 90

# 2026-08-20：币安公开/私有WS偶尔断线是长连接的正常背景噪音（实测24小时内
# 每账户4-6次，跟当前有没有持仓无关），断线到自动重连一般1秒多就完事，
# 之前这类[ERROR]被原样当真异常报出去，面板"异常"角标一直亮着，D账户
# 空仓也照样报，容易让人误以为出了真问题。跟上面CLOSING_CHATTER_RE同一
# 思路：只有能在断线后短窗口内找到"Websocket connected"重连成功证据才
# 降级为噪音；真断了没重连回来（网络/VPS层面问题）依然照常上报，不会漏报。
WS_DISCONNECT_RE = re.compile(r"Connection to remote host was lost\. - goodbye")
WS_RECONNECTED_RE = re.compile(r"Websocket connected")
WS_SELFHEAL_WINDOW_SEC = 10  # 实测重连约1.3秒完成，留出余量


def fetch_real_errors(acct: dict, minutes: int = 12) -> list:
    """
    2026-08-13：跟面板同款去噪——"平仓过程中TP重挂失败/ReduceOnly被拒"这类
    chatter，只要90秒内能找到"确认空仓"类日志，就是仓位已经正常清空后的
    正常噪音（实盘复现：C账户BNB止损离场，5条这类chatter被当真ERROR连发
    5条钉钉），不是真异常，不该报警刷屏。跟丢弃逻辑一样，只在能找到
    "确认空仓"证据时才降级，找不到证据的ERROR照常上报，不会漏报真问题。
    """
    out = _run([
        "journalctl", "-u", acct["service"], "--no-pager", "-S", f"{minutes} min ago",
    ], timeout=20)
    lines = out.splitlines()

    flat_confirm_ts = []
    defense_restored_ts = []
    ws_reconnect_ts = []
    for line in lines:
        m = None
        if FLAT_CONFIRM_RE.search(line):
            m = LOG_TS_RE.search(line)
            if m:
                try:
                    flat_confirm_ts.append(
                        datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    )
                except Exception:
                    pass
        if DEFENSE_RESTORED_RE.search(line):
            m = LOG_TS_RE.search(line)
            if m:
                try:
                    defense_restored_ts.append(
                        datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    )
                except Exception:
                    pass
        if WS_RECONNECTED_RE.search(line):
            m = LOG_TS_RE.search(line)
            if m:
                try:
                    ws_reconnect_ts.append(
                        datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    )
                except Exception:
                    pass

    errs = []
    for line in lines:
        if "ERROR" not in line:
            continue
        if any(p in line for p in NOISE_ERROR_PATTERNS):
            continue
        if WS_DISCONNECT_RE.search(line):
            m = LOG_TS_RE.search(line)
            if m:
                try:
                    ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    healed = any(
                        0 <= (r - ts).total_seconds() <= WS_SELFHEAL_WINDOW_SEC
                        for r in ws_reconnect_ts
                    )
                    if healed:
                        continue
                except Exception:
                    pass
        if CLOSING_CHATTER_RE.search(line):
            m = LOG_TS_RE.search(line)
            if m:
                try:
                    ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    # 两条证据任一满足即降级为噪音：① 短时间内（30秒）另一层
                    # 防线已经补上，裸奔窗口很短；② 稍长时间内（90秒）仓位
                    # 本身已经被正常路径平掉，压根不需要再挂。只要都没证据，
                    # 照常上报，不会漏报真正长时间裸奔的情况。
                    restored = any(
                        0 <= (r - ts).total_seconds() <= DEFENSE_RESTORED_WINDOW_SEC
                        for r in defense_restored_ts
                    )
                    flattened = any(
                        abs((ts - c).total_seconds()) <= FLAT_CONFIRM_WINDOW_SEC
                        for c in flat_confirm_ts
                    )
                    if restored or flattened:
                        continue
                except Exception:
                    pass
        errs.append(line)
    return errs[-5:]


MIN_QTY_DUST = 0.001  # 低于这个数量不当真实仓位看，避免灰尘仓误报


def run_once() -> list:
    """跑一轮全部检查，返回异常列表（每条是个dict: key, text）。"""
    anomalies = []

    dash = check_dashboard()
    if not dash["ok"]:
        anomalies.append({
            "key": "dashboard:health",
            "text": f"🖥️ 监控面板(8877)异常: {dash.get('detail', '')}",
        })

    gw = check_gateway()
    if not gw["ok"]:
        anomalies.append({
            "key": "gateway:health",
            "text": f"📡 广播网关(5006)异常: {gw.get('detail', '')} —— 走网关的品种TV信号可能已漏单！",
        })

    ngx = check_nginx()
    if not ngx["ok"]:
        anomalies.append({
            "key": "nginx:health",
            "text": f"🚪 nginx异常: {ngx.get('detail', '')} —— 所有TV流量(直连+网关)可能全部失联！",
        })

    disk = check_disk_space()
    if not disk["ok"]:
        anomalies.append({
            "key": "vps:disk",
            "text": f"💽 VPS磁盘使用率 {disk['pct']}% 已过高，可能拖累日志/状态写入",
        })

    for acct in MONITORED_ACCOUNTS:
        name = acct["name"]
        h = check_health(acct)
        if not h["ok"]:
            if h.get("restarting"):
                # 判定为重启恢复窗口内，不算异常也不发钉钉，但这轮仍然
                # 没法拿到真实数据，照样跳过这个账户其它检查。
                print(f"[跳过·重启中] {name}:health | {h.get('detail')}")
                continue
            detail = h.get("detail") or f"trading_paused={h.get('paused_syms')}"
            anomalies.append({
                "key": f"{name}:health",
                "text": f"⚠️ {name}账户({acct['port']}) 健康异常: {detail}",
            })
            continue  # 健康都不行，跳过这个账户其它检查，避免连锁误报

        pos_data = fetch_positions_and_orders(acct)
        if not pos_data:
            anomalies.append({
                "key": f"{name}:query_failed",
                "text": f"⚠️ {name}账户 持仓查询失败(可能是venv/凭证问题，需要人工确认)",
            })
            continue

        open_in_progress = h.get("open_in_progress") or {}
        tv_signals = fetch_last_tv_signals_all(acct, minutes=15)

        for sym in SYMBOLS:
            info = pos_data.get(sym) or {}
            side = info.get("side")
            qty = float(info.get("qty") or 0)
            orders_n = info.get("orders", -1)
            has_stop = bool(info.get("has_stop"))

            # 幽灵单：仓位空但挂单在
            if side is None and orders_n and orders_n > 0:
                anomalies.append({
                    "key": f"{name}:{sym}:ghost_order",
                    "text": f"👻 {name}账户 {sym} 仓位已空但还有{orders_n}张挂单未清",
                })

            # 裸仓：有真实仓位但一张止损单都没有——最高优先级检查，
            # 跳过正在开仓执行中的品种(open_in_progress)，避免撞上
            # 开仓成交到止损挂出之间那几百毫秒的正常过渡窗口误报。
            if (
                side is not None
                and qty > MIN_QTY_DUST
                and not has_stop
                and not open_in_progress.get(sym, False)
            ):
                anomalies.append({
                    "key": f"{name}:{sym}:naked",
                    "text": (
                        f"🆘 {name}账户 {sym} 持仓{side} {qty} 但盘口没有任何止损单，"
                        f"疑似裸仓，请立即人工核查！"
                    ),
                })

            # 雷达卡死检测：仓位在、雷达未激活、但实时mark已经越过激活线——
            # 实盘复现过一次(XAU)：真实markPrice冲过了激活线，账本
            # best_price却卡住没跟上，radar_activated一直是False，
            # 位置本身还有硬止损兜底不算裸仓，但雷达失效意味着错过保本锁利。
            # 这里独立用REST查到的mark去核对，不依赖账本自己的best_price，
            # 跟VPS内部那道120秒强制核对互为备份，双保险。
            radar_activated = info.get("radar_activated")
            gate = float(info.get("radar_gate") or 0)
            mark = float(info.get("mark") or 0)
            if (
                side is not None
                and qty > MIN_QTY_DUST
                and radar_activated is False
                and gate > 0
                and mark > 0
                and not open_in_progress.get(sym, False)
            ):
                crossed = (
                    (side == "LONG" and mark >= gate)
                    or (side == "SHORT" and mark <= gate)
                )
                if crossed:
                    anomalies.append({
                        "key": f"{name}:{sym}:radar_stale",
                        "text": (
                            f"📡⚠️ {name}账户 {sym} 现价{mark}已越过激活线{gate}，"
                            f"但雷达仍未激活，疑似WS/账本卡死，错过保本锁利"
                        ),
                    })

            # TV信号 vs 实盘方向核对（只在有实盘仓位时比对，避免信号还没成交就误报）
            tv = tv_signals.get(sym)
            if tv and side and tv["side"] != side:
                anomalies.append({
                    "key": f"{name}:{sym}:side_mismatch",
                    "text": (
                        f"🔀 {name}账户 {sym} TV最近信号={tv['side']} 但实盘方向={side}，"
                        f"可能未同步或执行异常"
                    ),
                })

            # 2026-08-20新增：TV心跳失联检测——只对"以前收到过心跳"的品种
            # 才检查(hb_ts>0)，还没被加上心跳代码的品种(hb_ts恒为0)不算
            # 失联，不然13个品种里没加完的那些会天天报警。心跳一旦收到过
            # 却超过HEARTBEAT_SILENCE_SEC(固定24小时，足够盖住所有品种
            # 正常的TV周期，不怕误报)没再更新，说明TV那边的心跳代码可能
            # 被改坏/漏加了，没人会主动发现这种"安静失效"，单独探测一次。
            hb_ts = float(info.get("hb_ts") or 0)
            if hb_ts > 0:
                silent_sec = time.time() - hb_ts
                if silent_sec > HEARTBEAT_SILENCE_SEC:
                    anomalies.append({
                        "key": f"{name}:{sym}:heartbeat_silent",
                        "text": (
                            f"💔 {name}账户 {sym} TV心跳已连续{silent_sec / 3600:.1f}小时"
                            f"没更新，疑似该品种TV策略的心跳代码失效"
                        ),
                    })

        errs = fetch_real_errors(acct)
        for e in errs:
            # 用错误文本前60字符做key，避免同一类错误刷屏但不同参数被当成新异常
            snippet = e[-120:] if len(e) > 120 else e
            key = f"{name}:error:{hash(snippet[:60]) % 100000}"
            anomalies.append({"key": key, "text": f"🚨 {name}账户 真实ERROR: {snippet}"})

    return anomalies


def maybe_send_heartbeat(state: dict) -> None:
    now = datetime.now(timezone.utc)
    tag = f"{now.date()}:{now.hour}"
    if now.hour in HEARTBEAT_HOURS and state.get("last_heartbeat_date_hour") != tag:
        lines = [f"【watchdog心跳】{now.strftime('%Y-%m-%d %H:%M UTC')}"]
        for acct in MONITORED_ACCOUNTS:
            pos_data = fetch_positions_and_orders(acct)
            held = [s for s, i in (pos_data or {}).items() if i.get("side")]
            if held:
                lines.append(f"  {acct['name']}: 持仓 {', '.join(held)}")
            else:
                lines.append(f"  {acct['name']}: 空仓")
        lines.append("状态：监控正常运行中 ✅")
        send_text("\n".join(lines))
        state["last_heartbeat_date_hour"] = tag
        _save_state(state)


NAKED_DEDUPE_SEC = 5 * 60   # 裸仓级别最高优先，不能被30分钟去重窗口捂住
RADAR_STALE_DEDUPE_SEC = 10 * 60  # 雷达卡死次优先，比裸仓宽松但也不能等30分钟
HEARTBEAT_SILENT_DEDUPE_SEC = 6 * 3600  # 心跳失联不是分钟级紧急事件，6小时提醒一次够了


def _dedupe_window_for(key: str) -> int:
    if ":naked" in key:
        return NAKED_DEDUPE_SEC
    if ":radar_stale" in key:
        return RADAR_STALE_DEDUPE_SEC
    if ":heartbeat_silent" in key:
        return HEARTBEAT_SILENT_DEDUPE_SEC
    return ALERT_DEDUPE_SEC


def main():
    state = _load_state()
    anomalies = run_once()
    now_ts = time.time()
    last_alert = state.setdefault("last_alert", {})

    to_send = []
    for a in anomalies:
        last_ts = last_alert.get(a["key"], 0)
        if now_ts - last_ts >= _dedupe_window_for(a["key"]):
            to_send.append(a["text"])
            last_alert[a["key"]] = now_ts

    if to_send:
        header = f"【watchdog异常告警】{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        send_text(header + "\n".join(to_send))

    _save_state(state)
    maybe_send_heartbeat(state)

    if anomalies:
        # 2026-08-18：之前这里只打印一行计数，被去重吞掉的异常详情就彻底
        # 丢了（DingTalk 30分钟内同一异常不重发，但问题其实一直存在）。
        # dashboard 的"监督狗日志"面板要展示真实明细，所以这里把每条异常
        # 原文也打进 journalctl——每轮都打，不受钉钉去重影响，读日志的人
        # 能看到问题从第一次出现到消失的完整过程。
        for a in anomalies:
            print(f"[ANOMALY] {a['key']} | {a['text']}")
        print(f"本轮发现 {len(anomalies)} 条异常，{len(to_send)} 条新发送")
    else:
        print("本轮无异常")


if __name__ == "__main__":
    main()

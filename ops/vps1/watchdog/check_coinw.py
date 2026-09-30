#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
独立只读监控：CoinW账户健康/服务状态/心跳/真实ERROR检查，异常发钉钉
（30分钟内同一异常去重）；正常状态只记日志，不再定时推送。

2026-09-12新增，跟 /root/watchdog/check.py（币安三账户watchdog）同一套
方法论/复用同一个 dingtalk_notify.py 发送器（WATCHDOG_DINGTALK_WEBHOOK/
WATCHDOG_DINGTALK_SECRET，跟币安watchdog共用同一个钉钉机器人，告警落在
同一个群）——本脚本是V1，只做了币安watchdog里最核心的几类检查，币安
那份是过去一个月每次实盘踩坑后一条条加噪声过滤条件迭代出来的(892行，
30+条精确匹配的良性噪声模式)，CoinW这份不可能一次到位，后续跑出真实
误报会陆续加过滤规则，跟币安当年走的路一样。

只读原则：只调 /health 接口 + journalctl，不 import
position_supervisor_coinw.py（会触发真实bootstrap），不下单不撤单。

检查项（V1）：
  1. coinw-engine 服务是否 active
  2. /health 是否可达 + deploy_safe
  3. journalctl 近N分钟内的真实 ERROR/Traceback/CRITICAL（小范围噪声过滤，
     会随实盘跑出的真实误报逐步扩充，跟币安watchdog当年一样）
  4. [2026-09-20停用] 原本检查BNB/OPENAI/SNDK/XPD四个品种TV心跳是否连续
     24小时静默——宝贝确认TV那边已主动取消心跳播报（只有币安A系统的策略
     版本有心跳概念，当前没有账户在跑A系统实盘），这条检测永远只会误报，
     整体停用，见 check_tv_heartbeat_silence() 调用处的注释。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

from dingtalk_notify import send_text

SERVICE = "coinw-engine"
HEALTH_URL = "http://127.0.0.1:5002/health"
STATE_PATH = os.path.join(os.path.dirname(__file__), "watchdog_coinw_state.json")
ALERT_DEDUPE_SEC = 30 * 60
HEARTBEAT_HOURS = {0}  # 每天只记一次健康日志，默认不推送
JOURNAL_LOOKBACK_MIN = 12          # 跟timer的10分钟run间隔留一点重叠余量

# 只有这4个品种真的在接TV信号（BNB/OPENAI/SNDK/XPD），ETH/BTC/XAU没有
# TV警报指向CoinW——检查它们的心跳只会制造永远误报的噪声，故意跳过。
TV_ACTIVE_SYMBOLS = ["BNB", "OPENAI", "SNDK", "XPD"]
HEARTBEAT_SILENCE_SEC = 24 * 3600.0

# V1 噪声过滤——先只加"显然良性、会自愈"的几条，其余交给实盘跑出来后
# 再补（跟币安watchdog check.py的迭代方式一致，不假装能一次穷举）。
NOISE_ERROR_PATTERNS = (
    "穿价 TP1 推离市价",
    "code=-4509",
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


def _run(cmd: list, timeout: int = 20) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if r.returncode != 0:
            err = r.stderr.decode("utf-8", errors="replace")[-300:]
            return f"__ERR__rc={r.returncode} {err}"
        return r.stdout.decode("utf-8", errors="replace")
    except Exception as e:
        return f"__ERR__{e}"


def check_service() -> dict:
    out = _run(["systemctl", "is-active", SERVICE])
    active = out.strip() == "active"
    return {"active": active, "raw": out.strip()}


def check_health() -> dict:
    try:
        req = urllib.request.Request(HEALTH_URL, headers={"User-Agent": "coinw-watchdog/1"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.load(r)
        return {"ok": True, "deploy_safe": d.get("deploy_safe"), "trading_paused": d.get("trading_paused")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_journal_errors(minutes: int = JOURNAL_LOOKBACK_MIN) -> list:
    out = _run([
        "journalctl", "-u", SERVICE, "--since", f"{minutes} min ago", "--no-pager",
    ], timeout=25)
    if out.startswith("__ERR__"):
        return [f"journalctl读取失败: {out}"]
    findings = []
    for line in out.splitlines():
        if not re.search(r"\bERROR\b|\bCRITICAL\b|Traceback", line):
            continue
        if any(p in line for p in NOISE_ERROR_PATTERNS):
            continue
        findings.append(line.strip()[:300])
    return findings


def check_tv_heartbeat_silence(minutes: int = 60 * 26) -> list:
    """从journalctl里找每个活跃品种最近一条HEARTBEAT日志的时间，超过
    HEARTBEAT_SILENCE_SEC没更新就报——用journalctl本身的时间戳，不额外
    解析日志内容里的时间字段，跟币安watchdog的近似做法一致（够用、简单、
    不用额外读引擎自己的状态文件）。"""
    anomalies = []
    out = _run([
        "journalctl", "-u", SERVICE, "--since", f"{minutes} min ago",
        "--no-pager", "-o", "short-iso",
    ], timeout=25)
    if out.startswith("__ERR__"):
        return [f"journalctl读取失败(心跳检查): {out}"]
    last_seen = {}
    for line in out.splitlines():
        m = re.match(r"^(\S+)\s", line)
        if not m or "HEARTBEAT" not in line:
            continue
        ts_str = m.group(1)
        for sym in TV_ACTIVE_SYMBOLS:
            if sym in line:
                last_seen[sym] = ts_str
    now = time.time()
    for sym in TV_ACTIVE_SYMBOLS:
        ts_str = last_seen.get(sym)
        if not ts_str:
            anomalies.append(f"{sym}: 近{minutes//60}小时内journalctl里完全没找到HEARTBEAT记录")
            continue
        try:
            ts = datetime.fromisoformat(ts_str).timestamp()
        except Exception:
            continue
        silence = now - ts
        if silence > HEARTBEAT_SILENCE_SEC:
            anomalies.append(f"{sym}: TV心跳已连续{silence/3600:.1f}小时没更新")
    return anomalies


def _alert_key(text: str) -> str:
    import hashlib
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:12]


def _dedupe_and_send(state: dict, anomalies: list) -> int:
    now = time.time()
    sent = 0
    for a in anomalies:
        key = _alert_key(a)
        last = state["last_alert"].get(key, 0)
        if now - last < ALERT_DEDUPE_SEC:
            continue
        ok = send_text(f"[CoinW监督狗] {a}")
        if ok:
            state["last_alert"][key] = now
            sent += 1
    return sent


def main():
    state = _load_state()
    anomalies = []

    svc = check_service()
    if not svc["active"]:
        anomalies.append(f"coinw-engine 服务不是active(实际: {svc['raw']})")

    health = check_health()
    if not health.get("ok"):
        anomalies.append(f"/health 不可达: {health.get('error')}")
    elif health.get("deploy_safe") is False:
        anomalies.append("deploy_safe=False（可能有品种正在开仓处理中，若持续很久需要人工看一下）")

    anomalies.extend(f"[ERROR日志] {e}" for e in check_journal_errors())
    # 2026-09-20停用：宝贝确认TV那边已经主动取消了心跳播报——心跳播报只
    # 存在于币安A系统的策略版本里，CoinW/币安B系统用的策略版本从设计上
    # 就没有心跳这个概念，不是"心跳代码失效"。当前没有任何账户在跑A系统
    # 实盘，这条检测对CoinW永远只会误报，整体停用（跟check.py同一天同一
    # 根因同步处理）。
    # anomalies.extend(f"[心跳静默] {e}" for e in check_tv_heartbeat_silence())

    print(f"本轮发现 {len(anomalies)} 条异常")
    if os.getenv("WATCHDOG_DRY_RUN") == "1":
        for a in anomalies:
            print(f"[DRY_RUN][ANOMALY] {a}")
        return
    sent = _dedupe_and_send(state, anomalies)
    print(f"{sent} 条新发送（去重窗口{ALERT_DEDUPE_SEC}s）")
    for a in anomalies:
        print(f"[ANOMALY] {a}")

    now_dt = datetime.now(timezone.utc)
    date_hour = f"{now_dt.date()}-{now_dt.hour}"
    if now_dt.hour in HEARTBEAT_HOURS and state.get("last_heartbeat_date_hour") != date_hour:
        print(f"[HEALTH] CoinW {now_dt.strftime('%Y-%m-%d %H:%M UTC')} ")
        state["last_heartbeat_date_hour"] = date_hour

    _save_state(state)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""方案F 实盘执行器(2026-10-19)——以账户自己的系统用户运行(第一个账户：客户 E / binanceE)，只读 /var/lib/planf/targets.json。

默认【影子模式】：照常计算目标仓位、对比实际仓位、记录"本来要下的单"，但不下任何单。
只有同目录下存在 ARMED 文件时才真的下单(由宝贝自己创建)；存在 STOP 文件时无论如何都不下单。

规则(实盘执行规范，research_scripts/exec_study.py)：
  · 目标净仓位 = 目标比例 × 账户净值(totalMarginBalance)；净值 < 5000U 用不含 BTC 的目标(BTC 权重分给其余币)
  · 按交易所步长向零取整；低于最小名义金额就当 0
  · 需要调仓的情况：要平到 0、方向反转、从 0 开仓、或者偏离目标超过 25%(每天再平衡的容差，避免来回小单)
  · 下单：先挂只做 Maker 的限价单(GTX)在买一/卖一，价格变了 60 秒后撤单重挂，最多 15 分钟，剩下的用市价补；减仓单带 reduceOnly
  · 美股永续只在纽约常规时段(9:30-16:00，自动夏令时，跳过休市日)交易
护栏：目标文件过期(>10 分钟)或发布器报问题 → 冻结(不下单)；总名义 ≤ 2×净值、单品种 ≤ 0.6×净值；
      24 小时内净值从高点跌超 20% → 熔断：目标减半 24 小时；逐仓品种不交易；白名单外的持仓不碰只告警；
      所有自己的订单用 clientOrderId 前缀 pf_ 标记。告警走钉钉(账户 .env 里看门狗那组机器人)。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import sys
import time
import traceback
import urllib.parse
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo

import requests
from binance.client import Client

ACCT = os.environ.get("PLANF_ACCOUNT", "E")
HOME = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.environ.get("PLANF_ENV_FILE", "/home/binanceE/binance-engine/.env")
TARGETS = "/var/lib/planf/targets.json"
STATUS = f"/var/lib/planf/live_{ACCT}_status.json"
STATE = os.path.join(HOME, "state.json")
LOG = os.path.join(HOME, "actions.jsonl")
ARMED, STOP = os.path.join(HOME, "ARMED"), os.path.join(HOME, "STOP")
LOOP_SEC, STALE_SEC, REBAL_BAND, REPRICE_SEC, MAKER_MAX_SEC = 30, 600, 0.25, 60, 900
MAX_GROSS, MAX_SYM, CB_DROP, CB_HOURS = 2.0, 0.6, 0.20, 24
NYSE_HOLIDAYS = {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
                 "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
                 "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}
CRYPTO = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"}


def load_env(path):
    env = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env(ENV_FILE)
_last_alert = {}


def ding(text, key=None, every=1800):
    """钉钉(加签)。key 相同的告警 every 秒内只发一次。"""
    if key:
        if time.time() - _last_alert.get(key, 0) < every:
            return
        _last_alert[key] = time.time()
    url, sec = ENV.get("WATCHDOG_DINGTALK_WEBHOOK"), ENV.get("WATCHDOG_DINGTALK_SECRET")
    if not url:
        return
    try:
        ts = str(int(time.time() * 1000))
        if sec:
            sign = base64.b64encode(hmac.new(sec.encode(), f"{ts}\n{sec}".encode(), hashlib.sha256).digest())
            url = f"{url}&timestamp={ts}&sign={urllib.parse.quote_plus(sign)}"
        requests.post(url, json={"msgtype": "text", "text": {"content": f"【方案F实盘·{ACCT}账户】{text}"}}, timeout=10)
    except Exception:
        pass


def log(kind, **kw):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "t": time.strftime("%m-%d %H:%M:%S", time.gmtime()), "kind": kind, **kw}, ensure_ascii=False) + "\n")


def rd(p, default=None):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def wr(p, obj, mode=0o600):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, mode); os.replace(tmp, p)


def rth_now(now=None):
    ny = ZoneInfo("America/New_York"); d = datetime.fromtimestamp(now or time.time(), timezone.utc).astimezone(ny)
    if d.weekday() >= 5 or d.strftime("%Y-%m-%d") in NYSE_HOLIDAYS:
        return False
    return dtime(9, 30) <= d.time() <= dtime(16, 0)


def fl(x):
    return float(x) if x not in (None, "") else 0.0


class Exec:
    def __init__(self):
        self.c = Client(ENV["BINANCE_API_KEY"], ENV["BINANCE_API_SECRET"])
        self.filters = {}; self.fil_ts = 0
        self.st = rd(STATE, {}) or {}
        self.st.setdefault("working", {}); self.st.setdefault("eq_hist", []); self.st.setdefault("cb_until", 0)

    def load_filters(self):
        if time.time() - self.fil_ts < 6 * 3600 and self.filters:
            return
        info = self.c.futures_exchange_info(); out = {}
        for s in info["symbols"]:
            if s.get("status") != "TRADING":
                continue
            f = {x["filterType"]: x for x in s["filters"]}
            out[s["symbol"]] = {"step": fl(f["LOT_SIZE"]["stepSize"]), "minq": fl(f["LOT_SIZE"]["minQty"]),
                                "mstep": fl((f.get("MARKET_LOT_SIZE") or f["LOT_SIZE"])["stepSize"]),
                                "tick": fl(f["PRICE_FILTER"]["tickSize"]), "minn": fl((f.get("MIN_NOTIONAL") or {}).get("notional") or 5),
                                "type": s.get("contractType")}
        self.filters, self.fil_ts = out, time.time()

    @staticmethod
    def rdown(q, step):
        n = math.floor(abs(q) / step + 1e-9) * step
        return math.copysign(float(f"{n:.10f}"), q) if n else 0.0

    @staticmethod
    def fmt(x, step):
        dec = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
        return f"{abs(x):.{dec}f}"

    def loop_once(self):
        now = time.time(); self.load_filters()
        tg = rd(TARGETS, {}) or {}
        armed, stopped = os.path.exists(ARMED), os.path.exists(STOP)
        mode = "实盘" if armed and not stopped else ("停止(STOP)" if stopped else "影子(只算不下单)")
        acc = self.c.futures_account(); E = fl(acc.get("totalMarginBalance"))
        pr = self.c.futures_position_information()
        pos = {p["symbol"]: fl(p["positionAmt"]) for p in pr if fl(p["positionAmt"]) != 0}
        mark = {p["symbol"]: fl(p["markPrice"]) for p in pr}
        margin = {p["symbol"]: p.get("marginType") for p in pr}
        # 熔断
        self.st["eq_hist"] = [x for x in self.st["eq_hist"] if now - x[0] < CB_HOURS * 3600] + [[now, E]]
        peak = max(x[1] for x in self.st["eq_hist"])
        if E < peak * (1 - CB_DROP) and now > self.st["cb_until"]:
            self.st["cb_until"] = now + CB_HOURS * 3600
            log("熔断", equity=E, peak=peak); ding(f"⚠️ 熔断：24小时内净值从 {peak:.0f}U 跌到 {E:.0f}U(超过20%)，接下来24小时目标仓位减半", "cb", 3600)
        cb = now < self.st["cb_until"]
        frozen, why = False, []
        if not tg or now - fl(tg.get("ts")) > STALE_SEC:
            frozen = True; why.append("信号文件过期")
        if tg and not tg.get("ok", False):
            frozen = True; why += tg.get("problems") or ["发布器报问题"]
        use_btc = E >= fl(tg.get("btc_min_equity") or 5000)
        frac = (tg.get("targets") if use_btc else tg.get("targets_no_btc")) or {}
        white = set(tg.get("universe") or []) | {s for s in frac}          # 只管方案F自己的品种
        if not tg.get("universe"):
            frozen = True; why.append("信号文件缺少品种清单")
        foreign = [s for s in pos if s not in white]
        if foreign:
            ding(f"账户里有方案F之外的持仓：{','.join(foreign)}，执行器不会碰它们，请确认", "foreign", 6 * 3600)
        # 成交通知：持仓相比上一轮变了(实盘模式下)
        lastp = self.st.get("last_pos") or {}
        chg = [f"{s} {lastp.get(s, 0):g}→{pos.get(s, 0):g}" for s in sorted(set(lastp) | set(pos)) if s in white and abs(lastp.get(s, 0) - pos.get(s, 0)) > 1e-12]
        if chg and armed and not stopped and "last_pos" in self.st:
            ding("持仓变化(成交)：" + "；".join(chg[:12]))
            log("成交", changes=chg)
        self.st["last_pos"] = {s: v for s, v in pos.items()}
        my_orders = {}
        for o in self.c.futures_get_open_orders():
            if str(o.get("clientOrderId", "")).startswith("pf_"):
                my_orders.setdefault(o["symbol"], []).append(o)
        rows, plans = [], []
        gross_cap = MAX_GROSS * E
        tot = sum(abs(v) for v in frac.values()) * E * (0.5 if cb else 1)
        g_scale = min(1.0, gross_cap / tot) if tot > 0 else 1.0
        rth = rth_now(now)
        for sym in sorted(set(frac) | {s for s in pos if s in white}):
            f = self.filters.get(sym)
            if not f:
                continue
            px = mark.get(sym) or 0
            if px <= 0:
                px = fl(self.c.futures_mark_price(symbol=sym)["markPrice"])
            fr = frac.get(sym, 0.0) * (0.5 if cb else 1) * g_scale
            fr = max(-MAX_SYM, min(MAX_SYM, fr))
            tq = self.rdown(fr * E / px, f["step"])
            if abs(tq) * px < f["minn"] or abs(tq) < f["minq"]:
                tq = 0.0
            cur = pos.get(sym, 0.0); delta = tq - cur
            is_stock = f["type"] == "TRADIFI_PERPETUAL"
            need = False; reason = ""
            if abs(delta) >= f["step"] / 2:
                if tq == 0 and cur != 0:
                    need, reason = True, "平到0"
                elif cur != 0 and math.copysign(1, tq) != math.copysign(1, cur) and tq != 0:
                    need, reason = True, "反手"
                elif cur == 0 and tq != 0:
                    need, reason = True, "开仓"
                elif abs(delta) * px >= max(f["minn"], REBAL_BAND * abs(tq) * px):
                    need, reason = True, f"偏离{abs(delta) / max(abs(tq), 1e-12):.0%}再平衡"
            if margin.get(sym) and str(margin[sym]).lower() == "isolated":
                need, reason = False, "逐仓品种不交易"
                ding(f"{sym} 是逐仓模式，执行器不交易它，请改成全仓", f"iso_{sym}", 6 * 3600)
            if need and is_stock and not rth:
                need, reason = False, reason + "(等纽约常规时段)"
            if need and frozen:
                need, reason = False, reason + "(冻结：" + "；".join(why) + ")"
            rows.append({"symbol": sym, "target_frac": round(fr, 5), "target_qty": tq, "position": cur, "price": px,
                         "target_usdt": round(tq * px, 2), "position_usdt": round(cur * px, 2), "action": reason if need or reason else ""})
            if need:
                plans.append((sym, delta, cur, tq, px, f, reason))
        # 下单 / 影子记录
        acted = []
        for sym, delta, cur, tq, px, f, reason in plans:
            if abs(delta) * px > MAX_SYM * E * 1.05 + f["minn"]:
                ding(f"{sym} 单笔 {abs(delta)*px:.0f}U 超过单品种上限，跳过", f"cap_{sym}", 3600); continue
            if not (armed and not stopped):
                acted.append(f"{sym} {reason} {'买' if delta > 0 else '卖'} {self.fmt(delta, f['step'])}(≈{abs(delta)*px:.0f}U)")
                continue
            acted.append(self.execute(sym, delta, cur, tq, f, reason, my_orders.get(sym, [])))
        # 已经不需要的工作单撤掉
        for sym, olist in my_orders.items():
            if sym not in {p[0] for p in plans} and (armed and not stopped):
                for o in olist:
                    self.cancel(sym, o["clientOrderId"])
                self.st["working"].pop(sym, None)
        status = {"ts": now, "account": ACCT, "mode": mode, "equity": round(E, 2), "use_btc": use_btc, "frozen": frozen, "why": why,
                  "circuit_breaker": cb, "rth": rth, "gross_target_usdt": round(sum(abs(r["target_usdt"]) for r in rows), 2),
                  "gross_position_usdt": round(sum(abs(r["position_usdt"]) for r in rows), 2), "rows": rows,
                  "would_do" if not (armed and not stopped) else "did": acted, "signals_asof": tg.get("asof")}
        wr(STATUS, status, 0o644); wr(STATE, self.st)
        if acted:
            log("计划" if not (armed and not stopped) else "执行", items=acted, equity=E)
        return status

    def cancel(self, sym, cid):
        try:
            self.c.futures_cancel_order(symbol=sym, origClientOrderId=cid)
        except Exception as e:
            log("撤单失败", symbol=sym, cid=cid, err=str(e)[:200])

    def execute(self, sym, delta, cur, tq, f, reason, olist):
        now = time.time(); w = self.st["working"].get(sym)
        side = "BUY" if delta > 0 else "SELL"
        reduce = cur != 0 and abs(tq) < abs(cur) and math.copysign(1, tq or -cur) == math.copysign(1, cur) or (tq == 0)
        if not w:
            w = self.st["working"][sym] = {"start": now, "last": 0, "side": side}
        if w.get("side") != side:
            for o in olist:
                self.cancel(sym, o["clientOrderId"])
            w = self.st["working"][sym] = {"start": now, "last": 0, "side": side}
        if now - w["start"] > MAKER_MAX_SEC:            # 15 分钟还没成交完：市价补
            for o in olist:
                self.cancel(sym, o["clientOrderId"])
            q = self.rdown(abs(delta), f["mstep"])
            if q <= 0:
                return f"{sym} 剩余数量太小"
            try:
                r = self.c.futures_create_order(symbol=sym, side=side, type="MARKET", quantity=self.fmt(q, f["mstep"]),
                                                reduceOnly="true" if reduce else "false", newClientOrderId=f"pf_m_{int(now)}_{sym[:6]}")
                self.st["working"].pop(sym, None)
                log("市价成交", symbol=sym, side=side, qty=q, reason=reason, avg=r.get("avgPrice"), status=r.get("status"))
                ding(f"{sym} {reason}：限价15分钟未成交完，市价{'买入' if side == 'BUY' else '卖出'} {q}，均价 {r.get('avgPrice')}")
                return f"{sym} 市价 {side} {q}"
            except Exception as e:
                log("下单失败", symbol=sym, err=str(e)[:300]); ding(f"{sym} 市价单失败：{str(e)[:120]}", f"err_{sym}", 900)
                return f"{sym} 下单失败"
        if olist and now - w["last"] < REPRICE_SEC:
            return f"{sym} 限价单挂着"
        bt = self.c.futures_orderbook_ticker(symbol=sym)
        px = fl(bt["bidPrice"]) if side == "BUY" else fl(bt["askPrice"])
        if olist:
            if all(abs(fl(o["price"]) - px) < f["tick"] / 2 for o in olist):
                w["last"] = now; return f"{sym} 限价单在最优价"
            for o in olist:
                self.cancel(sym, o["clientOrderId"])
            return f"{sym} 撤单重挂"
        q = self.rdown(abs(delta), f["step"])
        if q <= 0:
            return f"{sym} 数量太小"
        dec = max(0, -int(math.floor(math.log10(f["tick"])))) if f["tick"] < 1 else 0
        try:
            self.c.futures_create_order(symbol=sym, side=side, type="LIMIT", timeInForce="GTX", quantity=self.fmt(q, f["step"]),
                                        price=f"{px:.{dec}f}", reduceOnly="true" if reduce else "false",
                                        newClientOrderId=f"pf_l_{int(now)}_{sym[:6]}")
            w["last"] = now
            log("挂限价", symbol=sym, side=side, qty=q, price=px, reason=reason)
            return f"{sym} 挂限价 {side} {q} @ {px}"
        except Exception as e:
            log("挂单失败", symbol=sym, err=str(e)[:300])
            return f"{sym} 挂单失败({str(e)[:60]})"


def main():
    ex = Exec(); last_mode = None; last_daily = ""
    ding(f"执行器启动。当前模式：{'实盘' if os.path.exists(ARMED) and not os.path.exists(STOP) else '影子(只算不下单)'}")
    while True:
        try:
            s = ex.loop_once()
            if s["mode"] != last_mode:
                if last_mode is not None:
                    ding(f"模式切换：{last_mode} → {s['mode']}")
                last_mode = s["mode"]
            if s["frozen"]:
                ding("冻结中(不下单)：" + "；".join(s["why"]), "frozen", 3600)
            day = time.strftime("%Y-%m-%d", time.gmtime())
            if time.gmtime().tm_hour == 0 and time.gmtime().tm_min >= 30 and last_daily != day:
                last_daily = day
                ding(f"日报：净值 {s['equity']}U，模式 {s['mode']}，目标总名义 {s['gross_target_usdt']}U，实际 {s['gross_position_usdt']}U，"
                     f"{'含' if s['use_btc'] else '不含'}BTC，{'熔断中' if s['circuit_breaker'] else '正常'}")
        except Exception as e:
            log("循环出错", err=f"{type(e).__name__}: {e}", tb=traceback.format_exc()[-600:])
            ding(f"执行器出错：{type(e).__name__}: {str(e)[:150]}", "loop_err", 1800)
            if "418" in str(e) or "429" in str(e):
                time.sleep(60)
        time.sleep(LOOP_SEC)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        print(json.dumps(Exec().loop_once(), ensure_ascii=False, indent=1))
    else:
        main()

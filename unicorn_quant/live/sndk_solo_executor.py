# -*- coding: utf-8 -*-
"""SNDK单票方案 v1(代号 sndk_solo，2026-10-31)——宝贝自己的 C 账户(binanceC)只做 SNDKUSDT 永续，和方案F/趋势A等以前的方案完全分开。

规则(research_scripts/sndk_tf_mtf.py、sndk_exit_study.py 回测选定)：
  · 1h K线收盘：EMA7 > EMA25 → 持多；EMA7 < EMA25 → 平多。只做多，不做空，不用雷达/保本/移动止损(回测都更差)。
  · 开仓时止损 = 开仓价 − 4×ATR(14，1h，Wilder)，挂交易所条件单(STOP_MARKET，按最新成交价触发，同回测按K线最低价判断)，之后不动。
  · 止损打掉后要等 EMA7 先跌回 EMA25 下方一次，才能再进(防止刚止损又追回去)。
  · 仓位 = 开仓时账户净值(totalMarginBalance) × 1 倍名义，持有期间数量不变(不随净值再平衡)，平仓后下次开仓按新净值算。

信号怎么算：每轮从 SNDKUSDT 上线(2026-04-07)起把全部已收盘的 1h K线按回测逻辑重放一遍，得到"回测现在该持多还是空仓、止损价多少"，
执行器把账户调成和回测一致——所以实盘就是回测本身，不会走样。

默认【影子模式】：照常重放、记录"本来要下的单"，不下单。同目录有 ARMED 文件才真下单(宝贝自己建)；有 STOP 文件一律不下单。
上线后不追半路行情：ARMED 之前回测就已经持有的那一段不跟，等 ARMED 之后出现的第一个新开仓信号(同目录放 JOIN 文件则立即跟上当前持仓)。
下单：先挂只做 Maker 的限价单(GTX，SNDK 挂单手续费 0)，价格变了 60 秒撤单重挂，最多 15 分钟，剩下的市价补。
账户是双向持仓模式就带 positionSide=LONG。自己的订单 clientOrderId 前缀 ss_；账户里别的持仓不碰，只告警。
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

import requests
from binance.client import Client

NAME = "SNDK单票方案 v1"
ACCT = os.environ.get("SNDK_SOLO_ACCOUNT", "C")
SYM = "SNDKUSDT"
HOME = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.environ.get("SNDK_SOLO_ENV_FILE", "/home/binanceC/binance-engine/.env")
STATE, LOG, STATUS = os.path.join(HOME, "state.json"), os.path.join(HOME, "actions.jsonl"), os.path.join(HOME, "status.json")
ARMED, STOP, JOIN = os.path.join(HOME, "ARMED"), os.path.join(HOME, "STOP"), os.path.join(HOME, "JOIN")
FAST, SLOW, ATR_N, STOP_ATR, SIZE = 7, 25, 14, 4.0, 1.0
H1 = 3600000
LIST_START = 1775520000000            # 2026-04-07 SNDKUSDT 上线
LOOP_SEC, REPRICE_SEC, MAKER_MAX_SEC = 30, 60, 900


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
    if os.environ.get("SNDK_SOLO_QUIET"):          # 测试时不发钉钉
        return
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
        requests.post(url, json={"msgtype": "text", "text": {"content": f"【{NAME}·{ACCT}账户】{text}"}}, timeout=10)
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


def fl(x):
    return float(x) if x not in (None, "") else 0.0


def ts(ms):
    return time.strftime("%m-%d %H:%M", time.gmtime(ms / 1000))


# ---------- 回测重放(和 research_scripts/sndk_exit_study.py run2(sk=4) 同一套逻辑) ----------
def ema(c, n):
    k = 2 / (n + 1); o = [c[0]]
    for x in c[1:]:
        o.append(o[-1] + k * (x - o[-1]))
    return o


def atr(b, n=ATR_N):
    A = [b[0]["h"] - b[0]["l"]]
    for i in range(1, len(b)):
        tr = max(b[i]["h"] - b[i]["l"], abs(b[i]["h"] - b[i - 1]["c"]), abs(b[i]["l"] - b[i - 1]["c"]))
        A.append(A[-1] + (tr - A[-1]) / n)
    return A


def replay(b):
    """返回回测在"最后一根已收盘K线"之后的状态：pos(1/0)、entry_t/entry/stop(持仓中)、pend(下一根开盘要做的：1 开多/0 平多/None)、
    lock(止损后等 EMA 转空)、最近几笔成交。"""
    c = [x["c"] for x in b]; ef, es = ema(c, FAST), ema(c, SLOW); A = atr(b)
    d = 0; pend = None; stop = None; lock = 0; ent = None; trades = []; eq = 1.0
    for i in range(1, len(b)):
        op, lo = b[i]["o"], b[i]["l"]
        if pend is not None:
            tgt = pend; pend = None
            if d and tgt != d:
                trades.append({"side": "LONG", "in_t": ent["t"], "in": ent["px"], "out_t": b[i]["t"], "out": op, "why": "EMA7下穿EMA25"})
                eq *= op / ent["px"]; d = 0
            if tgt and not d:
                d = 1; ent = {"t": b[i]["t"], "px": op}; stop = op - STOP_ATR * A[i - 1]
        if d and lo <= stop:
            px = min(op, stop)
            trades.append({"side": "LONG", "in_t": ent["t"], "in": ent["px"], "out_t": b[i]["t"], "out": px, "why": f"{STOP_ATR:g}×ATR止损"})
            eq *= px / ent["px"]; d = 0; lock = 1
        if i < SLOW:
            continue
        want = 1 if ef[i] > es[i] else 0
        if lock and want != lock:
            lock = 0
        if lock and want == lock:
            want = 0
        if want != d:
            pend = want
    last = len(b) - 1
    return {"pos": d, "pend": pend, "lock": lock, "stop": stop if d else None, "entry": ent if d else None,
            "ema_fast": ef[last], "ema_slow": es[last], "atr": A[last], "close": c[last], "bar_t": b[last]["t"],
            "next_stop": (None if pend != 1 else None), "trades": trades[-5:], "n_trades": len(trades), "bt_mult": eq}


class Exec:
    def __init__(self):
        self.c = Client(ENV["BINANCE_API_KEY"], ENV["BINANCE_API_SECRET"])
        self.st = rd(STATE, {}) or {}
        self.st.setdefault("working", None); self.st.pop("bars", None)
        self.f = None; self.b = []          # K线只放内存，不写进 state

    # ---- 行情 ----
    def bars(self):
        b = self.b; now = int(time.time() * 1000)
        s = b[-1]["t"] + 1 if b else LIST_START
        while True:
            r = self.c.futures_klines(symbol=SYM, interval="1h", startTime=s, limit=1500)
            if not r:
                break
            b += [{"t": int(x[0]), "o": fl(x[1]), "h": fl(x[2]), "l": fl(x[3]), "c": fl(x[4])} for x in r if int(x[0]) + H1 <= now]
            if len(r) < 1500:
                break
            s = int(r[-1][0]) + 1
        seen = set(); out = []
        for x in b:
            if x["t"] not in seen:
                seen.add(x["t"]); out.append(x)
        self.b = out
        return out

    def filters(self):
        if self.f:
            return self.f
        s = next(x for x in self.c.futures_exchange_info()["symbols"] if x["symbol"] == SYM)
        f = {x["filterType"]: x for x in s["filters"]}
        self.f = {"step": fl(f["LOT_SIZE"]["stepSize"]), "minq": fl(f["LOT_SIZE"]["minQty"]), "mstep": fl(f["MARKET_LOT_SIZE"]["stepSize"]),
                  "tick": fl(f["PRICE_FILTER"]["tickSize"]), "minn": fl(f["MIN_NOTIONAL"]["notional"])}
        return self.f

    @staticmethod
    def rdown(q, step):
        n = math.floor(abs(q) / step + 1e-9) * step
        return float(f"{n:.10f}")

    @staticmethod
    def fmt(x, step):
        dec = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
        return f"{abs(x):.{dec}f}"

    def pfmt(self, px):
        t = self.filters()["tick"]; dec = max(0, -int(math.floor(math.log10(t)))) if t < 1 else 0
        return f"{round(px / t) * t:.{dec}f}"

    # ---- 主循环 ----
    def loop_once(self):
        now = time.time(); f = self.filters()
        armed, stopped, join = os.path.exists(ARMED), os.path.exists(STOP), os.path.exists(JOIN)
        live = armed and not stopped
        mode = "实盘" if live else ("停止(STOP)" if stopped else "影子(只算不下单)")
        if live and not self.st.get("armed_at"):
            self.st["armed_at"] = int(now * 1000); ding("ARMED 已生效：从现在起出现的新开仓信号会真实下单")
        if not live:
            self.st["armed_at"] = None
        b = self.bars(); R = replay(b)
        target = R["pend"] if R["pend"] is not None else R["pos"]          # 回测"现在"该持有的状态(下一根开盘执行的也算上)
        # 回测这一段持仓是从什么时候开的(pend=1 就是现在这根开盘)
        seg_t = (R["entry"] or {}).get("t") if R["pos"] and R["pend"] is None else (b[-1]["t"] + H1 if R["pend"] == 1 else None)
        acc = self.c.futures_account(); E = fl(acc.get("totalMarginBalance")); avail = fl(acc.get("availableBalance"))
        hedge = bool(self.c.futures_get_position_mode().get("dualSidePosition"))
        pr = self.c.futures_position_information(symbol=SYM)
        qty = sum(fl(p["positionAmt"]) for p in pr if (p.get("positionSide") in ("LONG", "BOTH")))
        shorts = sum(fl(p["positionAmt"]) for p in pr if p.get("positionSide") == "SHORT" or (p.get("positionSide") == "BOTH" and fl(p["positionAmt"]) < 0))
        mark = fl(self.c.futures_mark_price(symbol=SYM)["markPrice"])
        others = [p["symbol"] for p in self.c.futures_position_information() if fl(p["positionAmt"]) != 0 and p["symbol"] != SYM]
        if others:
            ding(f"账户里有 SNDK 以外的持仓：{','.join(others)}，执行器不会碰，请确认", "foreign", 6 * 3600)
        if shorts < 0:
            ding(f"账户里有 SNDK 空单 {shorts:g}，本方案只做多，执行器不碰空单，请确认", "short", 6 * 3600)
        my = self.st.setdefault("my", {"qty": 0.0})                          # 执行器自己开的多单数量(非执行器的仓位不认)
        # 交易所止损触发 / 有人手动平了：账户多单比执行器记录的少 → 这段不再跟，等回测转空
        if live and my.get("virtual"):
            my.update(qty=0.0, virtual=False)                                 # 影子模式记的虚拟仓位，切实盘时清掉
        if live and my["qty"] > 0 and qty < my["qty"] - f["step"] / 2 and not self.st.get("working"):
            ding(f"SNDK 多单从 {my['qty']:g} 变成 {qty:g}(交易所止损单触发或手动平仓)，这一段不再买回，等 EMA7 跌回 EMA25 下方后重新开始")
            log("仓位减少", was=my["qty"], now=qty); self.st["live_lock"] = True; my["qty"] = max(qty, 0.0)
        if self.st.get("live_lock") and target == 0:
            self.st["live_lock"] = False
        # 能不能跟这一段：ARMED 之后才出现的新开仓，或者放了 JOIN
        arm_t = self.st.get("armed_at") or 0
        fresh = bool(seg_t) and (seg_t >= arm_t - H1 or join)
        want_long = target == 1 and not self.st.get("live_lock") and (fresh or my["qty"] > 0 or not live)
        plan = None
        if want_long and my["qty"] <= 0:
            q = self.rdown(SIZE * E / mark, f["step"]) if mark > 0 else 0.0
            lev = 2.0
            q = min(q, self.rdown(avail * lev * 0.95 / mark, f["step"])) if mark > 0 and live else q
            if q < f["minq"] or q * mark < f["minn"]:
                plan = ("skip", 0.0, f"净值 {E:.2f}U 不够最小一手({f['minq']:g}张/≥{f['minn']:g}U)")
                ding(plan[2], "toosmall", 6 * 3600)
            else:
                plan = ("open", q, f"回测开多(EMA7>EMA25)，按净值{E:.2f}U×1倍 ≈ {q * mark:.0f}U")
        elif not want_long and my["qty"] > 0:
            plan = ("close", my["qty"], "回测平多(EMA7<EMA25)" if target == 0 else "停止跟随")
        # 止损价：回测这段持仓的止损(回测还在"下一根开盘才开"的那一下，用 开盘≈现价 − 4×ATR)
        stop_px = R["stop"] if R["pos"] and R["pend"] is None else (mark - STOP_ATR * R["atr"] if R["pend"] == 1 else None)
        acted = []
        if plan and plan[0] != "skip":
            if live:
                acted.append(self.execute(plan, hedge, f, mark))
            else:                                                           # 影子：记一笔虚拟仓位，下一轮就不会重复"开多"
                acted.append(f"{'开多' if plan[0] == 'open' else '平多'} {plan[1]:g} 张 — {plan[2]}")
                my.update(qty=plan[1] if plan[0] == "open" else 0.0, virtual=True, px=mark)
        elif self.st.get("working") and live:
            self.cancel_all(); self.st["working"] = None
        stops = self.sync_stop(live, hedge, f, (qty if live else my["qty"]) if my["qty"] > 0 else 0.0, stop_px)
        # 成交后记账
        if live and self.st.get("last_qty") is not None and abs(qty - self.st["last_qty"]) > f["step"] / 2:
            ding(f"SNDK 持仓变化：{self.st['last_qty']:g} → {qty:g}(标记价 {mark:g})"); log("成交", was=self.st["last_qty"], now=qty, mark=mark)
        self.st["last_qty"] = qty
        status = {"ts": now, "name": NAME, "account": ACCT, "mode": mode, "equity": round(E, 2), "hedge_mode": hedge, "mark": mark,
                  "position_long": qty, "executor_qty": my["qty"], "live_lock": bool(self.st.get("live_lock")),
                  "signal": {"bar": ts(R["bar_t"]) + " UTC 收盘", "close": R["close"], "ema7": round(R["ema_fast"], 3), "ema25": round(R["ema_slow"], 3),
                             "atr": round(R["atr"], 3), "回测状态": "持多" if R["pos"] else "空仓", "下一根": {1: "开多", 0: "平多", None: "不动"}[R["pend"]],
                             "回测止损": R["stop"], "回测入场": R["entry"], "止损后等转空": bool(R["lock"])},
                  "target": "持多" if want_long else "空仓", "stop_px": stop_px, "stops": stops,
                  "bt_since_listing": f"{100 * (R['bt_mult'] - 1):+.1f}%({R['n_trades']}笔)", "recent_trades": R["trades"],
                  "would_do" if not live else "did": acted}
        wr(STATUS, status, 0o644); wr(STATE, self.st)
        if acted:
            log("计划" if not live else "执行", items=acted, equity=E)
            if not live:
                ding("影子模式·本来要做：" + "；".join(acted))
        return status

    # ---- 下单 ----
    def params(self, hedge, closing):
        if hedge:
            return {"positionSide": "LONG"}
        return {"reduceOnly": "true"} if closing else {}

    def my_orders(self):
        return [o for o in self.c.futures_get_open_orders(symbol=SYM) if str(o.get("clientOrderId", "")).startswith("ss_")]

    def cancel_all(self):
        for o in self.my_orders():
            try:
                self.c.futures_cancel_order(symbol=SYM, origClientOrderId=o["clientOrderId"])
            except Exception as e:
                log("撤单失败", err=str(e)[:200])

    def execute(self, plan, hedge, f, mark):
        kind, q_all, why = plan; now = time.time(); closing = kind == "close"
        side = "SELL" if closing else "BUY"
        w = self.st.get("working")
        if not w or w.get("kind") != kind:
            self.cancel_all()
            w = self.st["working"] = {"kind": kind, "start": now, "last": 0, "q": q_all, "pos0": self.st["my"]["qty"]}
            ding(f"{'开多' if not closing else '平多'} {q_all:g} 张：{why}")
        pr = self.c.futures_position_information(symbol=SYM)
        cur = sum(fl(p["positionAmt"]) for p in pr if p.get("positionSide") in ("LONG", "BOTH") and fl(p["positionAmt"]) > 0)
        done = (cur - w["pos0"]) if not closing else (w["pos0"] - cur)
        left = self.rdown(w["q"] - max(done, 0.0), f["step"])
        self.st["my"]["qty"] = cur if not closing else max(cur, 0.0)
        if left < f["minq"]:
            self.cancel_all(); self.st["working"] = None
            if closing:
                self.st["my"]["qty"] = 0.0
            log("完成", kind=kind, pos=cur); return f"{kind} 完成，多单 {cur:g}"
        olist = self.my_orders()
        if now - w["start"] > MAKER_MAX_SEC:
            self.cancel_all()
            q = self.rdown(left, f["mstep"])
            try:
                r = self.c.futures_create_order(symbol=SYM, side=side, type="MARKET", quantity=self.fmt(q, f["mstep"]),
                                                newClientOrderId=f"ss_m_{int(now)}", **self.params(hedge, closing))
                log("市价", kind=kind, qty=q, avg=r.get("avgPrice"))
                ding(f"限价15分钟没成交完，市价{'卖出' if closing else '买入'} {q:g}")
                return f"市价 {side} {q:g}"
            except Exception as e:
                log("下单失败", err=str(e)[:300]); ding(f"市价单失败：{str(e)[:150]}", "err_m", 900); return "市价失败"
        if olist and now - w["last"] < REPRICE_SEC:
            return "限价单挂着"
        bt = self.c.futures_orderbook_ticker(symbol=SYM)
        px = fl(bt["askPrice"]) if closing else fl(bt["bidPrice"])
        if olist:
            if all(abs(fl(o["price"]) - px) < f["tick"] / 2 and abs(fl(o["origQty"]) - fl(o["executedQty"]) - left) < f["step"] / 2 for o in olist):
                w["last"] = now; return "限价单在最优价"
            self.cancel_all(); return "撤单重挂"
        try:
            self.c.futures_create_order(symbol=SYM, side=side, type="LIMIT", timeInForce="GTX", quantity=self.fmt(left, f["step"]),
                                        price=self.pfmt(px), newClientOrderId=f"ss_l_{int(now)}", **self.params(hedge, closing))
            w["last"] = now; log("挂限价", kind=kind, qty=left, price=px)
            return f"挂限价 {side} {left:g} @ {px:g}"
        except Exception as e:
            log("挂单失败", err=str(e)[:300]); ding(f"限价单失败：{str(e)[:150]}", "err_l", 900); return "挂单失败"

    # ---- 交易所止损单 ----
    def algo(self, method, path, params):
        return self.c._request_futures_api(method, path, signed=True, data=params)

    def sync_stop(self, live, hedge, f, qty, stop_px):
        want = None
        if qty >= f["minq"] and stop_px:
            want = (float(self.pfmt(stop_px)), self.rdown(qty, f["step"]))
        if not live:
            return {"want": want}
        try:
            have = self.algo("get", "openAlgoOrders", {"symbol": SYM})
            have = [h for h in (have.get("orders", have) if isinstance(have, dict) else have or []) if str(h.get("clientAlgoId", "")).startswith("ss_")]
        except Exception as e:
            log("查条件单失败", err=str(e)[:200]); return {"want": want, "err": str(e)[:100]}
        keep = False
        for h in have:
            if want and not keep and abs(fl(h.get("triggerPrice")) - want[0]) < f["tick"] / 2 and abs(fl(h.get("quantity")) - want[1]) < f["step"] / 2:
                keep = True; continue
            try:
                self.algo("delete", "algoOrder", {"symbol": SYM, "algoId": int(h.get("algoId"))}); log("撤止损单", trigger=h.get("triggerPrice"))
            except Exception as e:
                log("撤止损单失败", err=str(e)[:200])
        if want and not keep:
            p = {"algoType": "CONDITIONAL", "symbol": SYM, "side": "SELL", "type": "STOP_MARKET", "triggerPrice": self.pfmt(want[0]),
                 "quantity": self.fmt(want[1], f["step"]), "workingType": "CONTRACT_PRICE",
                 "clientAlgoId": f"ss_s_{int(time.time() * 1000) % 10**10}"}
            p.update(self.params(hedge, True))
            try:
                self.algo("post", "algoOrder", p); log("挂止损单", trigger=want[0], qty=want[1])
                ding(f"挂交易所止损单：跌破 {want[0]:g} 卖出 {want[1]:g} 张")
            except Exception as e:
                log("挂止损单失败", err=str(e)[:300]); ding(f"⚠️ 止损单挂失败：{str(e)[:150]}", "stopfail", 900)
        return {"want": want, "kept": keep}


def main():
    ex = Exec(); last_mode = None; last_daily = ""
    ding(f"执行器启动。模式：{'实盘' if os.path.exists(ARMED) and not os.path.exists(STOP) else '影子(只算不下单)'}")
    while True:
        try:
            s = ex.loop_once()
            if s["mode"] != last_mode:
                if last_mode is not None:
                    ding(f"模式切换：{last_mode} → {s['mode']}")
                last_mode = s["mode"]
            day = time.strftime("%Y-%m-%d", time.gmtime())
            if time.gmtime().tm_hour == 0 and time.gmtime().tm_min >= 35 and last_daily != day:
                last_daily = day
                g = s["signal"]
                ding(f"日报：净值 {s['equity']}U，模式 {s['mode']}，SNDK 多单 {s['position_long']:g}，回测状态 {g['回测状态']}"
                     f"(EMA7 {g['ema7']} / EMA25 {g['ema25']})，止损 {s['stop_px']}，回测上线以来 {s['bt_since_listing']}")
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

# -*- coding: utf-8 -*-
"""SNDK单票方案 专属监督狗(常驻循环，每 60 秒；以 root 运行，因为要能重启执行器服务)。替代原来只报警的 sndk_solo_health.py。

原则：执行器活着时，监督狗只盯、不抢活(执行器每 30 秒自己会补止损、调仓)；只有执行器"该做没做"或者"已经挂了"，监督狗才出手，
而且出手的动作只限于"保护性"的：重启服务、补止损单、撤孤儿单、按回测规则平仓。监督狗**从不开仓**(开仓交给执行器)。

检查 → 自动处理：
  1. 执行器服务没在跑 / 状态 3 分钟没更新(卡死) → 重启服务(1 小时内最多 3 次，超过只报警，防止反复重启掩盖问题)
  2. 有 SNDK 多单但交易所止损单没覆盖全部数量：执行器活着时等它 2 轮(约 1 分钟)，还没补上 / 执行器挂了 → 监督狗自己按回测止损价补挂
  3. 有 SNDK 多单、最新价已经在止损价下方(止损单没触发/跳空) → 监督狗立即市价平(执行器正在平的话不重复下单)
  4. 有 SNDK 多单、回测已经是空仓、执行器又挂了超过 3 分钟 → 监督狗按回测规则市价平
  5. 没有持仓却还挂着 ss_ 止损单 / ss_ 限价单(执行器也没有进行中的单) → 撤掉(孤儿单，防 -4067 之类的连锁问题)
  6. 账户里出现方案以外的持仓/条件单 → 只报警不动(规则：外来仓位先确认，不自动处理)
  7. 执行器 10 分钟内报错 ≥5 次 / 净值一天内从高点跌超 15% / 操作日志超过 20MB → 报警 / 轮转日志
  8. 每天 UTC 00:40 发一条"监督狗日报"：检查了多少轮、自动修复了什么
监督狗自己每轮写 watchdog.json；执行器会反过来检查它(超过 10 分钟没更新就报警)，互相盯。"""
import json, os, subprocess, sys, time, traceback
HOME = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HOME)
import sndk_solo_executor as S

WD = os.path.join(HOME, "watchdog.json")
WDLOG = os.path.join(HOME, "watchdog_actions.jsonl")
SVC = "sndk-solo-C"
LOOP, STALE, DEAD, RESTART_MAX = 60, 180, 180, 3
_alert = {}


def ding(msg, key=None, every=3600):
    if key and time.time() - _alert.get(key, 0) < every:
        return
    if key:
        _alert[key] = time.time()
    S.ding("监督狗：" + msg)


def act(kind, **kw):
    with open(WDLOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%m-%d %H:%M:%S", time.gmtime()), "kind": kind, **kw}, ensure_ascii=False) + "\n")


class Dog:
    def __init__(self):
        self.ex = None; self.wd = S.rd(WD, {}) or {}
        self.wd.setdefault("restarts", []); self.wd.setdefault("fixes", []); self.wd.setdefault("checks", 0)
        self.miss = 0; self.peak = {"day": "", "eq": 0.0}; self.last_daily = ""

    def client(self):
        if self.ex is None:
            self.ex = S.Exec()
        return self.ex

    def fix(self, what):
        self.wd["fixes"].append([time.time(), what]); act("修复", what=what); ding("已自动处理：" + what)

    def svc_active(self):
        return subprocess.run(["systemctl", "is-active", "--quiet", SVC]).returncode == 0

    def restart(self, why):
        now = time.time(); self.wd["restarts"] = [t for t in self.wd["restarts"] if now - t < 3600]
        if len(self.wd["restarts"]) >= RESTART_MAX:
            ding(f"⚠️ {why}，1 小时内已重启 {RESTART_MAX} 次仍不正常，停止自动重启，请人工检查(journalctl -u {SVC})", "restart_max", 3600); return
        subprocess.run(["systemctl", "restart", SVC]); self.wd["restarts"].append(now); self.fix(f"{why} → 重启执行器服务")

    def once(self):
        now = time.time(); self.wd["checks"] += 1
        armed, stopped = os.path.exists(S.ARMED), os.path.exists(S.STOP)
        live = armed and not stopped
        st = S.rd(S.STATUS, {}) or {}; state = S.rd(S.STATE, {}) or {}
        age = now - float(st.get("ts") or 0); active = self.svc_active()
        exec_ok = active and age < 120
        # 1. 服务/卡死
        if not active:
            self.restart("执行器服务没在运行")
        elif age > STALE:
            self.restart(f"执行器状态 {age / 60:.0f} 分钟没更新(卡住)")
        ex = self.client(); c = ex.c; f = ex.filters()
        pos = c.futures_position_information()
        lng = sum(S.fl(p["positionAmt"]) for p in pos if p["symbol"] == S.SYM and S.fl(p["positionAmt"]) > 0)
        other = [f"{p['symbol']}{S.fl(p['positionAmt']):+g}" for p in pos if S.fl(p["positionAmt"]) != 0 and (p["symbol"] != S.SYM or S.fl(p["positionAmt"]) < 0)]
        algos = c._request_futures_api("get", "openAlgoOrders", signed=True, data={})
        algos = algos.get("orders", algos) if isinstance(algos, dict) else (algos or [])
        mine = [a for a in algos if a.get("symbol") == S.SYM and str(a.get("clientAlgoId", "")).startswith("ss_")]
        foreign_algo = sorted({a.get("symbol") for a in algos if not str(a.get("clientAlgoId", "")).startswith("ss_")})
        # 6. 外来仓位/条件单：只报警
        if other:
            ding(f"账户里有方案以外的持仓：{', '.join(other)}(监督狗不动，请确认)", "other", 6 * 3600)
        if foreign_algo:
            ding(f"账户里有不是执行器挂的条件单：{', '.join(foreign_algo)}(监督狗不动，请确认)", "falgo", 6 * 3600)
        last = S.fl(c.futures_symbol_ticker(symbol=S.SYM)["price"])
        stop_px = st.get("stop_px")
        working = state.get("working")
        hedge = bool(st.get("hedge_mode", True))
        if live and lng > 0:
            if not stop_px:                     # 状态里没有止损价(执行器挂了很久)：按回测重算
                R = S.replay(ex.bars()); stop_px = R["stop"] or (last - S.STOP_ATR * R["atr"])
            closing_now = working and working.get("kind") == "close" and now - working.get("start", 0) < S.MAKER_MAX_SEC + 120 and exec_ok
            # 3. 价格已在止损价下方
            if last <= stop_px and not closing_now:
                self.market_close(lng, hedge, f, f"最新价 {last:g} 已在止损价 {stop_px:g} 下方，止损单没生效")
                return self.save()
            # 4. 回测已空仓、执行器挂了
            if not exec_ok and age > DEAD:
                R = S.replay(ex.bars()); tgt = R["pend"] if R["pend"] is not None else R["pos"]
                if tgt == 0:
                    self.market_close(lng, hedge, f, "执行器挂了，回测已经是空仓(EMA7<EMA25 或已止损)，按回测规则平仓")
                    return self.save()
            # 2. 止损覆盖
            cover = sum(S.fl(a.get("quantity")) for a in mine)
            if cover + 1e-9 < lng:
                self.miss += 1
                if self.miss >= 2 or not exec_ok:
                    self.place_stop(lng - cover, stop_px, hedge, f, last)
                    self.miss = 0
            else:
                self.miss = 0
        elif live and lng <= 0:
            self.miss = 0
            # 5. 孤儿单
            if mine and not working:
                for a in mine:
                    try:
                        c._request_futures_api("delete", "algoOrder", signed=True, data={"symbol": S.SYM, "algoId": int(a["algoId"])})
                    except Exception as e:
                        act("撤孤儿条件单失败", err=str(e)[:150])
                self.fix(f"没有持仓却挂着 {len(mine)} 张执行器止损单 → 撤掉")
            olim = [o for o in c.futures_get_open_orders(symbol=S.SYM) if str(o.get("clientOrderId", "")).startswith("ss_")]
            if olim and not working:
                for o in olim:
                    try:
                        c.futures_cancel_order(symbol=S.SYM, origClientOrderId=o["clientOrderId"])
                    except Exception as e:
                        act("撤孤儿限价单失败", err=str(e)[:150])
                self.fix(f"执行器没有进行中的单却挂着 {len(olim)} 张限价单 → 撤掉")
        # 7. 报错频率 / 净值回撤 / 日志大小
        try:
            errs = 0; cutoff = now - 600
            with open(S.LOG, "rb") as fh:
                fh.seek(max(0, os.path.getsize(S.LOG) - 200000))
                for line in fh.read().decode("utf-8", "ignore").splitlines()[1:]:
                    try:
                        j = json.loads(line)
                        errs += j.get("ts", 0) > cutoff and j.get("kind") in ("循环出错", "下单失败", "挂单失败", "挂止损单失败")
                    except Exception:
                        pass
            if errs >= 5:
                ding(f"执行器 10 分钟内报错 {errs} 次，请看 actions.jsonl", "errs", 3600)
            if os.path.getsize(S.LOG) > 20 * 1024 * 1024:
                os.replace(S.LOG, S.LOG + time.strftime(".%Y%m%d")); act("轮转日志")
        except FileNotFoundError:
            pass
        eq = S.fl(st.get("equity")); day = time.strftime("%Y-%m-%d", time.gmtime())
        if self.peak["day"] != day:
            self.peak = {"day": day, "eq": eq}
        self.peak["eq"] = max(self.peak["eq"], eq)
        if eq and self.peak["eq"] and eq < self.peak["eq"] * 0.85:
            ding(f"⚠️ 净值今天从 {self.peak['eq']:.2f}U 跌到 {eq:.2f}U(超过 15%)", "dd", 6 * 3600)
        # 8. 日报
        g = time.gmtime()
        if g.tm_hour == 0 and g.tm_min >= 40 and self.last_daily != day:
            self.last_daily = day
            fx = [w for t, w in self.wd["fixes"] if now - t < 86400]
            S.ding(f"监督狗日报：过去 24 小时检查 {self.wd['checks']} 轮，自动处理 {len(fx)} 次" + ("：" + "；".join(fx[-5:]) if fx else "，一切正常")
                   + f"；执行器{'正常' if exec_ok else '异常'}，SNDK 多单 {lng:g} 张，止损单 {len(mine)} 张")
            self.wd["checks"] = 0
        return self.save()

    def save(self):
        self.wd["ts"] = time.time(); self.wd["fixes"] = [x for x in self.wd["fixes"] if time.time() - x[0] < 7 * 86400]
        S.wr(WD, self.wd, 0o644)

    def market_close(self, q, hedge, f, why):
        c = self.client().c; q = self.client().rdown(q, f["mstep"] or f["step"])
        p = {"positionSide": "LONG"} if hedge else {"reduceOnly": "true"}
        try:
            r = c.futures_create_order(symbol=S.SYM, side="SELL", type="MARKET", quantity=self.client().fmt(q, f["mstep"] or f["step"]),
                                       newClientOrderId=f"ss_wd_{int(time.time())}", **p)
            self.fix(f"{why} → 市价平多 {q:g} 张(均价 {r.get('avgPrice')})")
        except Exception as e:
            act("监督狗平仓失败", err=str(e)[:200]); ding(f"⚠️ {why}，监督狗市价平仓失败：{str(e)[:120]}，请立刻人工处理", "closefail", 600)

    def place_stop(self, q, stop_px, hedge, f, last):
        ex = self.client(); q = ex.rdown(q, f["step"])
        if q < f["minq"]:
            return
        if stop_px >= last:
            return self.market_close(q, hedge, f, f"补止损时发现最新价 {last:g} 已不高于止损价 {stop_px:g}")
        p = {"algoType": "CONDITIONAL", "symbol": S.SYM, "side": "SELL", "type": "STOP_MARKET", "triggerPrice": ex.pfmt(stop_px),
             "quantity": ex.fmt(q, f["step"]), "workingType": "CONTRACT_PRICE", "clientAlgoId": f"ss_s_wd{int(time.time() * 1000) % 10**9}"}
        p.update(ex.params(hedge, True))
        try:
            ex.algo("post", "algoOrder", p); self.fix(f"多单有 {q:g} 张没有交易所止损 → 补挂止损单 跌破 {float(ex.pfmt(stop_px)):g}")
        except Exception as e:
            act("监督狗补止损失败", err=str(e)[:200]); ding(f"⚠️ 补挂止损单失败：{str(e)[:120]}(价格跌破止损价时监督狗会直接市价平)", "stopfail", 900)


def main():
    dog = Dog(); ding("监督狗启动(每 60 秒检查：执行器存活、止损覆盖、止损兜底、孤儿单、外来仓位)")
    while True:
        try:
            dog.once()
        except Exception as e:
            act("监督狗出错", err=f"{type(e).__name__}: {e}", tb=traceback.format_exc()[-500:])
            ding(f"监督狗自身出错：{type(e).__name__}: {str(e)[:120]}", "self_err", 1800)
            dog.ex = None                       # 下一轮重建连接
            if "418" in str(e) or "429" in str(e):
                time.sleep(120)
        time.sleep(LOOP)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        d = Dog(); d.once(); print(json.dumps(S.rd(WD, {}), ensure_ascii=False)[:500])
    else:
        main()

# -*- coding: utf-8 -*-
"""SNDK单票方案 独立体检(systemd timer 每 10 分钟，User=binanceC，只读)。和执行器分开跑，执行器卡死/挂掉也能报警。
查：① 执行器服务在跑、status.json 5 分钟内更新过；② 有 SNDK 多单时交易所上有 ss_ 止损单且数量覆盖持仓；
③ 账户里没有 SNDK 以外的持仓/挂单(防旧程序又动这个账户)。有问题发钉钉(同一问题 1 小时内只发一次)。"""
import json, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SNDK_SOLO_QUIET", "")
import sndk_solo_executor as S

HOME = os.path.dirname(os.path.abspath(__file__))
SEEN = os.path.join(HOME, "health_seen.json")


def main():
    seen = S.rd(SEEN, {}) or {}
    probs = []
    if subprocess.run(["systemctl", "is-active", "--quiet", "sndk-solo-C"]).returncode != 0:
        probs.append(("svc", "执行器服务 sndk-solo-C 没在运行"))
    st = S.rd(S.STATUS, {}) or {}
    age = time.time() - float(st.get("ts") or 0)
    if age > 300:
        probs.append(("stale", f"执行器状态 {age / 60:.0f} 分钟没更新(可能卡住)"))
    try:
        c = S.Client(S.ENV["BINANCE_API_KEY"], S.ENV["BINANCE_API_SECRET"])
        pos = [p for p in c.futures_position_information() if S.fl(p["positionAmt"]) != 0]
        lng = sum(S.fl(p["positionAmt"]) for p in pos if p["symbol"] == S.SYM and S.fl(p["positionAmt"]) > 0)
        other = [f"{p['symbol']}{S.fl(p['positionAmt']):+g}" for p in pos if p["symbol"] != S.SYM or S.fl(p["positionAmt"]) < 0]
        if other:
            probs.append(("other", f"账户里有方案以外的持仓：{', '.join(other)}"))
        algos = c._request_futures_api("get", "openAlgoOrders", signed=True, data={})
        algos = algos.get("orders", algos) if isinstance(algos, dict) else (algos or [])
        mine = [a for a in algos if a.get("symbol") == S.SYM and str(a.get("clientAlgoId", "")).startswith("ss_")]
        foreign = [a.get("symbol") for a in algos if not str(a.get("clientAlgoId", "")).startswith("ss_")]
        if foreign:
            probs.append(("falgo", f"账户里有不是执行器挂的条件单：{', '.join(sorted(set(foreign)))}"))
        if lng > 0:
            cover = sum(S.fl(a.get("quantity")) for a in mine)
            if cover + 1e-9 < lng:
                probs.append(("nostop", f"⚠️ SNDK 多单 {lng:g} 张，交易所止损单只覆盖 {cover:g} 张"))
    except Exception as e:
        probs.append(("api", f"体检查询币安失败：{type(e).__name__}: {str(e)[:120]}"))
    now = time.time()
    for k, msg in probs:
        if now - seen.get(k, 0) > 3600:
            S.ding("体检告警：" + msg); seen[k] = now
    for k in list(seen):
        if k not in {p[0] for p in probs}:
            seen.pop(k)
    S.wr(SEEN, seen, 0o600)
    print(time.strftime("%m-%d %H:%M:%S"), "OK" if not probs else probs)


if __name__ == "__main__":
    main()

"""方向2(2026-11-01)：把第四批赢家的"大K确认"移植回方案F自己的两条8h腿(超级趋势、布林突破)。
新开仓/反手那根8h K线实体 ≥ k × 近20根平均实体才算数，否则维持原状态(平仓信号不受影响，照常离场)。
在 VPS 上跑(用 engine/optimal.py 原样回测，猴子补丁 _st_dirs/_bb_states)，输出写 /tmp/optF，不碰线上文件。"""
import json, os, sys, time
sys.path.insert(0, "/home/stratroster/unicorn_quant")
from engine import optimal as O

O.OUT = "/tmp/optF"; os.makedirs(O.OUT, exist_ok=True)
_st0, _bb0 = O._st_dirs, O._bb_states


def big_filter(orig, k, which):
    def f(b, *a, **kw):
        d = orig(b, *a, **kw)
        if k <= 0:
            return d
        body = [abs(x["c"] - x["o"]) for x in b]; out = [d[0]]; e = d[0]
        for i in range(1, len(b)):
            want = d[i]
            if want == e or want == 0:
                e = want
            else:
                avg = sum(body[max(0, i - 20):i]) / max(1, min(20, i))
                if body[i] >= k * avg and ((want > 0) == (b[i]["c"] > b[i]["o"])):
                    e = want
                elif e == -want:
                    e = 0                     # 反手信号但K线不够大：先平，不反手
            out.append(e)
        return out
    return f


def run(k, which):
    O._st_dirs = big_filter(_st0, k, which) if which in ("st", "both") else _st0
    O._bb_states = big_filter(_bb0, k, which) if which in ("bb", "both") else _bb0
    O.CURVES.clear() if hasattr(O, "CURVES") else None
    t0 = time.time(); r = O.build(); S = r["series"]
    def half(ser, a, z):
        v = [(t, x) for t, x in S[ser]["series"] if a <= t < z]
        if len(v) < 30: return None
        pk = dd = 0
        for _, x in v:
            pk = max(pk, x); dd = max(dd, 1 - x / pk)
        yrs = (v[-1][0] - v[0][0]) / (365.25 * O.DAY); cg = (v[-1][1] / v[0][1]) ** (1 / yrs) - 1
        return round(cg / dd, 2) if dd else None
    T22, T25 = 1640995200000, 1735689600000
    print(json.dumps({"k": k, "which": which, "F": {x: S["F"][x] for x in ("cagr", "max_dd", "ratio")},
                      "F_2017_21": half("F", 0, T22), "F_2022_24": half("F", T22, T25), "F_2025_": half("F", T25, 1e15),
                      "st8h": S["crypto_st8h"]["ratio"], "bb8h": S["crypto_bb8h"]["ratio"], "crypto": S["crypto"]["ratio"],
                      "sec": round(time.time() - t0)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    run(0, "none")
    for which in ("bb", "st", "both"):
        for k in (1.0, 1.5, 2.0):
            run(k, which)

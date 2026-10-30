"""下载 data.binance.vision 衍生品数据到 data/raw(主流5币)：
UM daily metrics(5分钟: 持仓量/大户多空比/全市场多空比/主动买卖量比) + monthly fundingRate + monthly premiumIndexKlines 1d(基差)。
合并成 data/deriv_{SYM}.pkl 日频表。"""
import os, io, zipfile, datetime as dt, urllib.request, concurrent.futures as cf
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); RAW = os.path.join(HERE, "data", "raw"); os.makedirs(RAW, exist_ok=True)
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
B = "https://data.binance.vision/data/futures/um"


def get(url, fn):
    p = os.path.join(RAW, fn)
    if os.path.exists(p): return p
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as r: data = r.read()
            open(p, "wb").write(data); return p
        except urllib.error.HTTPError as e:
            if e.code == 404: open(p, "wb").write(b""); return p
        except Exception: pass
    return None


def jobs():
    d0 = dt.date(2020, 9, 1); d1 = dt.date(2026, 9, 30)
    for s in SYMS:
        d = d0
        while d <= d1:
            yield f"{B}/daily/metrics/{s}/{s}-metrics-{d}.zip", f"m_{s}_{d}.zip"; d += dt.timedelta(days=1)
        for y in range(2020, 2027):
            for m in range(1, 13):
                ym = f"{y}-{m:02d}"
                if ym > "2026-09": break
                yield f"{B}/monthly/fundingRate/{s}/{s}-fundingRate-{ym}.zip", f"f_{s}_{ym}.zip"
                yield f"{B}/monthly/premiumIndexKlines/{s}/1d/{s}-1d-{ym}.zip", f"p_{s}_{ym}.zip"


def rd(p):
    if not p or os.path.getsize(p) == 0: return None
    z = zipfile.ZipFile(p); raw = z.read(z.namelist()[0]).decode()
    hdr = raw.split("\n", 1)[0]
    return pd.read_csv(io.StringIO(raw), header=0 if any(ch.isalpha() for ch in hdr) else None)


if __name__ == "__main__":
    J = list(jobs())
    with cf.ThreadPoolExecutor(16) as ex: list(ex.map(lambda a: get(*a), J))
    for s in SYMS:
        ms = [rd(os.path.join(RAW, fn)) for _, fn in J if fn.startswith(f"m_{s}_")]
        m = pd.concat([x for x in ms if x is not None]); m["create_time"] = pd.to_datetime(m.create_time)
        m = m.set_index("create_time").sort_index()
        num = ["sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
               "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]
        m = m[num].apply(pd.to_numeric, errors="coerce")
        # 日频：取当天最后一个5分钟快照(UTC 23:55)作该日收盘值；主动买卖比取当日均值
        day = m.resample("1D").last(); day["taker_mean"] = m.sum_taker_long_short_vol_ratio.resample("1D").mean()
        fs = [rd(os.path.join(RAW, fn)) for _, fn in J if fn.startswith(f"f_{s}_")]
        f = pd.concat([x for x in fs if x is not None]); f.columns = ["calc_time", "interval", "rate"][:len(f.columns)] if f.columns[0] != "calc_time" else f.columns
        f["t"] = pd.to_datetime(pd.to_numeric(f.calc_time), unit="ms"); fd = f.set_index("t")["last_funding_rate" if "last_funding_rate" in f else f.columns[-1]].astype(float).resample("1D").sum()
        ps = [rd(os.path.join(RAW, fn)) for _, fn in J if fn.startswith(f"p_{s}_")]
        p = pd.concat([x for x in ps if x is not None]); p = p.iloc[:, :5]; p.columns = ["t", "o", "h", "l", "c"]
        p = p[pd.to_numeric(p.t, errors="coerce").notna()]; p["t"] = pd.to_datetime(pd.to_numeric(p.t), unit="ms")
        pdly = p.set_index("t").c.astype(float).rename("premium")
        out = day.join(fd.rename("funding"), how="outer").join(pdly, how="outer")
        out.to_pickle(os.path.join(HERE, "data", f"deriv_{s}.pkl"))
        print(s, out.index.min(), out.index.max(), out.notna().sum().to_dict())

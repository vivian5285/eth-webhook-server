"""SEC EDGAR：美股腿股票池的历史财报发布日 = 8-K 且 items 含 2.02(Results of Operations)的提交日期。
外国发行人(TSM/ASML/ARM 等报 6-K)拿不到 → 记为缺失。存 data/earnings.json {ticker: [YYYY-MM-DD,...]}。
注意 8-K 提交日通常是发布当天(盘后发布则当天提交)，当作'财报日 E'，E+1 为首个完整反应日。"""
import os, json, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__)); UA = {"User-Agent": "unicorn-quant-research admin@example.com"}
import sys; sys.path.insert(0, os.path.join(HERE, "..", "..")); from engine.optimal import SEC_OF


def get(u):
    for k in range(5):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60))
        except Exception:
            if k == 4: raise
            time.sleep(3 * (k + 1))


cik = {v["ticker"]: v["cik_str"] for v in get("https://www.sec.gov/files/company_tickers.json").values()}
out = {}
for tk in SEC_OF:
    if tk not in cik: out[tk] = None; print(tk, "no cik"); continue
    j = get(f"https://data.sec.gov/submissions/CIK{cik[tk]:010d}.json"); dates = []
    blocks = [j["filings"]["recent"]] + [get("https://data.sec.gov/submissions/" + f["name"]) for f in j["filings"].get("files", [])]
    for b in blocks:
        for form, d, it in zip(b["form"], b["filingDate"], b["items"]):
            if form in ("8-K", "8-K/A") and "2.02" in (it or "") and d >= "2016-01-01": dates.append(d)
    out[tk] = sorted(set(dates)); print(tk, len(out[tk]), out[tk][-2:] if out[tk] else None, flush=True); time.sleep(0.15)
json.dump(out, open(os.path.join(HERE, "data", "earnings.json"), "w"))

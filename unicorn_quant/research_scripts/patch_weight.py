"""2026-10-18 擂台+独角兽搬到实盘VPS(187.77.130.144)：和实盘引擎共用同一个IP的币安限流额度(期货 2400 权重/分钟/IP)。
给擂台和独角兽的币安K线请求加"让路"：响应头 X-MBX-USED-WEIGHT-1M(整个IP本分钟已用权重)超过阈值就暂停到下一分钟，
保证实盘下单/查仓永远有额度。"""
import os

# ---- 擂台 strategy_engine/klines.py ----
p = "/home/stratroster/eth-webhook-server/strategy_engine/klines.py"
s = open(p, encoding="utf-8").read()
if "_observe_binance_ip_weight" not in s:
    helper = '''

# 2026-10-18：擂台搬到实盘VPS(187.77.130.144)，跟实盘引擎共用一个IP的币安限流(期货2400权重/分钟/IP)。
# X-MBX-USED-WEIGHT-1M 是整个IP本分钟已用的权重(含实盘)，超过阈值擂台就暂停到下一分钟，给实盘让路。
BINANCE_IP_WEIGHT_YIELD = int(os.getenv("ARENA_BINANCE_WEIGHT_YIELD", "900"))
_last_yield_log = 0.0


def _observe_binance_ip_weight(headers) -> None:
    global _soft_pause_until, _last_yield_log
    try:
        used = int(headers.get("X-MBX-USED-WEIGHT-1M") or headers.get("x-mbx-used-weight-1m") or -1)
    except (TypeError, ValueError):
        return
    if used < BINANCE_IP_WEIGHT_YIELD:
        return
    pause = 61.0 - (time.time() % 60.0)
    _soft_pause_until = max(_soft_pause_until, time.monotonic() + pause)
    if time.monotonic() - _last_yield_log > 60:
        _last_yield_log = time.monotonic()
        logger.warning("[klines] 本IP币安权重已用%s(阈值%s)，擂台暂停%.0fs给实盘让路", used, BINANCE_IP_WEIGHT_YIELD, pause)
'''
    anchor = "\ndef _fetch_klines_raw_binance("
    assert s.count(anchor) == 1
    s = s.replace(anchor, helper + "\n" + anchor, 1)
    i = s.index("def _fetch_klines_raw_binance(")
    j = s.index("with urllib.request.urlopen(req, timeout=timeout) as resp:", i)
    k = s.index("\n", j) + 1
    indent = " " * (len(s[s.rfind("\n", 0, j) + 1:j]) + 4)
    s = s[:k] + f"{indent}_observe_binance_ip_weight(resp.headers)\n" + s[k:]
    if "\nimport os" not in s and "import os\n" not in s:
        s = "import os\n" + s if not s.startswith("#") else s.replace("\nimport ", "\nimport os\nimport ", 1)
    s = s.replace("但擂台现在仍在独立 VPS(187.53.133.188)上，这个顾虑不存在。",
                  "2026-10-18 擂台搬回实盘VPS(187.77.130.144)，改为保留币安数据源 + 下面的IP权重让路。")
    open(p, "w", encoding="utf-8").write(s)

# ---- 独角兽 engine/data.py ----
p = "/home/stratroster/unicorn_quant/engine/data.py"
s = open(p, encoding="utf-8").read()
if "USED-WEIGHT" not in s:
    old = 'r = requests.get(FAPI + "/fapi/v1/klines", params=p, timeout=10)'
    assert s.count(old) == 1
    j = s.index(old); line_start = s.rfind("\n", 0, j) + 1; indent = s[line_start:j]
    add = (f"\n{indent}# 2026-10-18 搬到实盘VPS：本IP币安权重(含实盘)超过900就等到下一分钟，给实盘让路\n"
           f"{indent}if int(r.headers.get('X-MBX-USED-WEIGHT-1M') or 0) >= 900:\n"
           f"{indent}    time.sleep(61 - time.time() % 60)")
    s = s.replace(old, old + add, 1)
    open(p, "w", encoding="utf-8").write(s)
print("weight guard patched")

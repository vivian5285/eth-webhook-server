"""2026-10-19 复刻加固(擂台方案F四条腿，纸面)：
1 币圈信号/止损改用币安现货K线(与回测同源)；2 日线腿突破单改成"盘中碰到就触发"(按触价价成交)，同日止损后不再追；
3 灾难止损统一：日线趋势单/8h两腿 4×ATR → 10×ATR(回测不设止损，10倍只做宕机兜底)；4 美股腿只在纽约常规时段执行与判断3×ATR止损(自动夏令时)。"""
import shutil, time
R = "/home/stratroster/eth-webhook-server/strategy_engine"; ts = time.strftime("%Y%m%d_%H%M%S")
FILES = ["klines.py", "multi_strategy_runner.py", "comparison_roster.py", "strategies/daily_ema_trend_vt.py", "strategies/daily_ema_brk.py",
         "strategies/intraday_trend_cycle.py", "strategies/stock_sector_rotation.py"]
for f in FILES: shutil.copy(f"{R}/{f}", f"{R}/{f}.bak_replicate_{ts}")
def sub(path, old, new, n=1):
    s = open(f"{R}/{path}", encoding="utf-8").read(); assert s.count(old) == n, (path, old[:70], s.count(old))
    open(f"{R}/{path}", "w", encoding="utf-8").write(s.replace(old, new))
def app(path, txt): open(f"{R}/{path}", "a", encoding="utf-8").write(txt)
# ---------- 1. klines：现货K线 ----------
app("klines.py", '''

# ═══ 2026-10-19 方案F复刻加固：信号用币安现货K线(跟 8890 回测同一个数据源) ═══
# 实测(research_scripts/spot_perp_signal.py)：同样规则用永续K线算，2021起信号方向跟现货不一致——日线EMA 0.07%、8h布林 0.33%、
# 8h超级趋势 1.29%(永续插针让ATR通道提前/推迟翻转)。现货接口(api.binance.com)跟期货是两套独立的IP权重池，不挤实盘的期货额度。
_SPOT_URL = "https://api.binance.com/api/v3/klines"
_SPOT_MS = {"1m": 60000, "5m": 300000, "15m": 900000, "30m": 1800000, "1h": 3600000, "2h": 7200000, "4h": 14400000,
            "6h": 21600000, "8h": 28800000, "12h": 43200000, "1d": 86400000}
_spot_cache = {}


def get_spot_bars(symbol: str, interval: str, limit: int = 300) -> List[dict]:
    """币安现货已收盘K线(格式同 get_bars)。失败返回[]——调用方据此跳过本轮，不退回永续数据(宁可不动也不混数据源)。"""
    s = str(interval or "").lower(); iv = _SPOT_MS.get(s)
    if not iv:
        return get_bars(symbol, interval, limit)
    key = (str(symbol).upper(), s, int(limit)); now = time.time()
    hit = _spot_cache.get(key)
    if hit and now < hit[0]:
        return hit[1]
    rows, end, need = [], None, int(limit) + 1
    while need > 0:
        q = {"symbol": key[0], "interval": s, "limit": min(1000, need)}
        if end:
            q["endTime"] = end
        data = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(f"{_SPOT_URL}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "strategy-engine-readonly"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                if e.code in (418, 429):
                    logger.warning("[klines-spot] HTTP %s，本轮放弃", e.code); return []
                if e.code < 500:
                    logger.warning("[klines-spot] %s %s HTTP %s", key[0], s, e.code); return []
            except Exception as e:
                logger.warning("[klines-spot] %s %s 第%d次失败: %s", key[0], s, attempt + 1, e)
            time.sleep(0.6 * (attempt + 1))
        if not isinstance(data, list) or not data:
            if not rows:
                return []
            break
        rows = data + rows; need -= len(data); end = int(data[0][0]) - 1
        if len(data) < q["limit"]:
            break
        time.sleep(0.1)
    nowms = int(now * 1000)
    bars = [b for b in to_ohlcv_dicts(rows) if int(b["t"]) + iv <= nowms][-int(limit):]
    if bars:
        _spot_cache[key] = (now + 60, bars)
    return bars
''')
# ---------- runner：roster 条目 data_source="spot" 时，base/mtf K线(含擂台止损检查用的K线)都走现货 ----------
sub("multi_strategy_runner.py", 'def _cached_bars(cache: Dict[tuple, list], symbol: str, timeframe: str, limit: int) -> list:',
    'def _cached_bars(cache: Dict[tuple, list], symbol: str, timeframe: str, limit: int, source: Optional[str] = None) -> list:')
sub("multi_strategy_runner.py", '''    key = (symbol, timeframe, int(limit))
    if key not in cache:
        cache[key] = klines.get_bars(symbol, timeframe, limit=int(limit))''',
'''    key = (symbol, timeframe, int(limit)) if not source else (symbol, timeframe, int(limit), source)
    if key not in cache:
        # 2026-10-19 方案F复刻加固：roster 条目 data_source="spot" → 用现货K线(信号+止损检查都跟回测同源)
        cache[key] = klines.get_spot_bars(symbol, timeframe, int(limit)) if source == "spot" else klines.get_bars(symbol, timeframe, limit=int(limit))''')
sub("multi_strategy_runner.py", "    bars = _cached_bars(cache, symbol, timeframe, bars_limit)\n    if len(bars) < 30:\n        return\n",
    "    bars = _cached_bars(cache, symbol, timeframe, bars_limit, entry.get(\"data_source\"))\n    if len(bars) < 30:\n        return\n")
sub("multi_strategy_runner.py", "        bars_by_tf[str(tf)] = _cached_bars(cache, symbol, tf, bars_limit)",
    "        bars_by_tf[str(tf)] = _cached_bars(cache, symbol, tf, bars_limit, entry.get(\"data_source\"))")
s = open(f"{R}/multi_strategy_runner.py", encoding="utf-8").read()
if "Optional" not in s.split("def ")[0]:
    sub("multi_strategy_runner.py", "from typing import ", "from typing import Optional, ", 1)
# ---------- roster ----------
sub("comparison_roster.py", 'PLAN_B_8H_ROSTER = [{"symbol": s, "strategy": st, "timeframe": "8h", "params": {}}',
    'PLAN_B_8H_ROSTER = [{"symbol": s, "strategy": st, "timeframe": "8h", "params": {}, "data_source": "spot"}   # 2026-10-19 现货K线(同回测)')
sub("comparison_roster.py", 'BRK_ROSTER = [{"symbol": s, "strategy": "daily_ema_brk_cycle_main5", "timeframe": "1h", "params": {}}',
    'BRK_ROSTER = [{"symbol": s, "strategy": "daily_ema_brk_cycle_main5", "timeframe": "1h", "params": {}, "data_source": "spot"}   # 2026-10-19 现货K线(同回测)')
sub("comparison_roster.py", 'SECTOR_ROT_ROSTER = [{"symbol": s, "strategy": "stock_sector_rot6m5_long", "timeframe": "1d", "params": {}}',
    'SECTOR_ROT_ROSTER = [{"symbol": s, "strategy": "stock_sector_rot6m5_long", "timeframe": "1h", "params": {}}   # 2026-10-19 改1h：只在纽约常规时段执行(策略内判断)')
# ---------- BTC 方向(现货) ----------
app("strategies/daily_ema_trend_vt.py", '''

# 2026-10-19 方案F复刻加固：BTC 日线方向用现货K线(跟 8890 回测同源)。供 daily_ema_brk / intraday_trend_cycle 用；其它老策略仍用 _btc_state。
_BTC_SPOT_CACHE = {"ts": 0.0, "state": None}


def _btc_state_spot() -> Optional[int]:
    now = time.time()
    if now - _BTC_SPOT_CACHE["ts"] < 600:
        return _BTC_SPOT_CACHE["state"]
    from strategy_engine import klines
    state = None
    try:
        closes = [float(x["c"]) for x in (klines.get_spot_bars("BTCUSDT", "1d", 200) or [])]
        if len(closes) >= 40:
            k8, k30 = 2 / 9, 2 / 31; a = b = None
            for c in closes:
                a = c if a is None else a + k8 * (c - a); b = c if b is None else b + k30 * (c - b)
            state = 1 if a > b else -1
    except Exception:
        state = None
    if state is not None:
        _BTC_SPOT_CACHE.update(ts=now, state=state)
    return state
''')
# ---------- 日线腿 ----------
sub("strategies/daily_ema_brk.py", '''    return [x for x in (klines.get_bars(symbol, "1d", n) or []) if int(x["t"]) + DAY <= now]''',
    '''    return [x for x in (klines.get_spot_bars(symbol, "1d", n) or []) if int(x["t"]) + DAY <= now]   # 2026-10-19 现货(同回测)''')
sub("strategies/daily_ema_brk.py", '''        n = int(p["brk_n"])
        hi5 = max(x["h"] for x in d[-n:]); lo5 = min(x["l"] for x in d[-n:])
        if raw < 0 and price > hi5:''',
'''        n = int(p["brk_n"])
        hi5 = max(x["h"] for x in d[-n:]); lo5 = min(x["l"] for x in d[-n:])
        # 2026-10-19 复刻加固：回测是"当天盘中碰到前5日高/低就按触价价成交"，原来擂台要等1h收盘越过才开，冲上去又回落的会漏单。
        # 改成看今天(UTC)已收盘的1h K线有没有碰到；成交价=碰到那一根的 max(开盘, 触发价)；今天已经被止损过就不再追(回测一天只挂一次)。
        today0 = int(d[-1]["t"]) + DAY
        tb = [x for x in hb if int(x["t"]) >= today0]
        hit_hi = next((x for x in tb if float(x["h"]) >= hi5), None)
        hit_lo = next((x for x in tb if float(x["l"]) <= lo5), None)
        if tb and _recent_stopout(symbol, (time.time() * 1000 - today0) / 3600000 + 0.05):
            hit_hi = hit_lo = None
        if raw < 0 and hit_hi is not None:
            price = max(float(hit_hi["o"]), hi5)''')
sub("strategies/daily_ema_brk.py", '''        elif raw > 0 and price < lo5 and allowed("SHORT"):''',
    '''        elif raw > 0 and hit_lo is not None and allowed("SHORT"):
            price = min(float(hit_lo["o"]), lo5)''')
sub("strategies/daily_ema_brk.py", '''        if raw < 0 and hit_hi is not None:
            price = max(float(hit_hi["o"]), hi5)
            ok_dir = allowed("LONG")''', '''        if raw < 0 and hit_hi is not None:
            price = max(float(hit_hi["o"]), hi5)
            ok_dir = allowed("LONG")''')
s = open(f"{R}/strategies/daily_ema_brk.py", encoding="utf-8").read()
s = s.replace('''        elif raw > 0 and hit_lo is not None and allowed("SHORT"):
            price = min(float(hit_lo["o"]), lo5)
            want, kind, why = -1, "brk", f"盘中跌破前{n}日最低{lo5:.6g}(均线还没翻空)"''', '''        elif raw > 0 and hit_lo is not None and allowed("SHORT"):
            price = min(float(hit_lo["o"]), lo5)
            want, kind, why = -1, "brk", f"盘中跌破前{n}日最低{lo5:.6g}(均线还没翻空)"''')
open(f"{R}/strategies/daily_ema_brk.py", "w", encoding="utf-8").write(s)
sub("strategies/daily_ema_brk.py", '''        from strategy_engine.strategies.daily_ema_trend_vt import _btc_state
        if _btc_state() != want:''', '''        from strategy_engine.strategies.daily_ema_trend_vt import _btc_state_spot as _btc_state   # 2026-10-19 现货(同回测)
        if _btc_state() != want:''')
sub("strategies/daily_ema_brk.py", '''    k = 4.0 if kind == "trend" else 3.0''',
    '''    k = 10.0 if kind == "trend" else 3.0      # 2026-10-19 统一口径：回测趋势单不设止损，擂台/实盘只留10×ATR宕机兜底(原4×)；突破/底部反转单仍3×ATR(同回测)''')
# ---------- 8h 两腿 ----------
sub("strategies/intraday_trend_cycle.py", '''    "atr_len": 14, "atr_stop_mult": 4.0,''',
    '''    "atr_len": 14, "atr_stop_mult": 10.0,   # 2026-10-19 统一口径：回测8h腿不设止损(加3~6×ATR比值都变差，research_scripts/st8h_stop.py)，只留10×ATR宕机兜底(原4×)''')
sub("strategies/intraday_trend_cycle.py", "4×ATR 灾难止损。", "10×ATR 灾难止损(2026-10-19 由4×放宽，仅作宕机兜底)；K线用现货(同回测)。")
sub("strategies/intraday_trend_cycle.py", '''        from strategy_engine.strategies.daily_ema_trend_vt import _btc_state
        if _btc_state() != d:''', '''        from strategy_engine.strategies.daily_ema_trend_vt import _btc_state_spot as _btc_state   # 2026-10-19 现货(同回测)
        if _btc_state() != d:''')
# ---------- 美股腿：纽约常规时段 ----------
sub("strategies/stock_sector_rotation.py", '''def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    perp = bars_by_tf.get("base") or []''', '''# 2026-10-19 复刻加固：回测是"正股收盘出信号→下一个交易日开盘成交"、3×ATR止损只看正股常规时段的价格。
# 擂台原来挂在永续日线(UTC 0点)上执行，止损也被24小时的永续插针触发。现在：roster 改成每小时跑，只在纽约常规时段(9:30-16:00，
# 自动处理夏令时、跳过休市日)才开平仓；3×ATR 止损由这里按常规时段内的价格判断；擂台引擎里的止损放到10×ATR，只作宕机兜底。
_NYSE_HOLIDAYS = {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
                  "2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18",
                  "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24"}


def _rth_window(now_s: float):
    """返回今天纽约常规时段的(开盘, 收盘) UTC 秒；不是交易日返回 None"""
    from datetime import datetime, time as dtime, timezone
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    d = datetime.fromtimestamp(now_s, timezone.utc).astimezone(ny)
    if d.weekday() >= 5 or d.strftime("%Y-%m-%d") in _NYSE_HOLIDAYS:
        return None
    o = datetime.combine(d.date(), dtime(9, 30), ny).timestamp(); c = datetime.combine(d.date(), dtime(16, 0), ny).timestamp()
    return o, c


def generate_signal(bars_by_tf: Dict[str, List[dict]], params: Optional[dict] = None, position: Optional[dict] = None) -> Optional[dict]:
    import time as _tm
    _w = _rth_window(_tm.time())
    if not _w or not (_w[0] <= _tm.time() <= _w[1]):
        return None                                        # 非纽约常规时段：不开不平(擂台引擎的10×ATR兜底止损照常)
    perp = bars_by_tf.get("base") or []''')
sub("strategies/stock_sector_rotation.py", '''        # 2026-10-18 对齐回测：本轮均线多头里回测已经跌破过3×ATR止损(按规则锁定)，擂台还拿着就平掉''',
'''        # 2026-10-19 常规时段内的 3×ATR 止损(回测口径：开仓价 - 3×开仓时ATR)；只看今天开盘以后已收盘的1h K线
        try:
            _stp3 = float(position.get("entry") or position.get("entry_price")) - 3.0 * float(position.get("atr0") or 0)
        except (TypeError, ValueError):
            _stp3 = 0.0
        if _stp3 > 0 and position.get("atr0"):
            _lows = [float(x["l"]) for x in perp if int(x["t"]) / 1000 >= _w[0] - 1800 and int(x["t"]) / 1000 + 3600 <= _w[1] + 1800]
            if _lows and min(_lows) <= _stp3:
                return {"action": "CLOSE_QUICK_EXIT", "price": round(min(price, _stp3), 6), "bar_time": bar_time,
                        "reason": f"常规时段跌破3×ATR止损 {_stp3:.4g}"}
        # 2026-10-18 对齐回测：本轮均线多头里回测已经跌破过3×ATR止损(按规则锁定)，擂台还拿着就平掉''')
sub("strategies/stock_sector_rotation.py", '''    stop_frac = float(p["atr_stop_mult"]) * atr_pct''',
    '''    stop_frac = 10.0 * atr_pct          # 2026-10-19 擂台引擎止损只作宕机兜底(10×ATR)；真正的3×ATR止损在上面按常规时段判断''')
print("ok", ts)

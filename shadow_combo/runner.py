"""Public-market-data-only forward runner; never reads live account secrets."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from strategy_engine import klines

from . import engine, store

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
DB_PATH = os.environ.get("SHADOW_COMBO_DB", str(ROOT / "data" / "shared_shadow_v2.db"))
BASE = "https://fapi.binance.com"
PINNED_FILES = {
    "live_config.py": "41d97f7bd2fd7a48ed7742bb4a6057e1d029628d46ad0a770c25f49fa50947d8",
    "heikin_ashi_strategy.py": "5a667ce7e3e1974a6ea8e9646caf62b0612944a611ce8bdce0aadaa4f3f7aec8",
    "virtual_netting.py": "b2d92f7cc1f0bcca8e4cc17218199ce6d6ba12897b62bf732cc52b8f1cb4fde5",
    "live_guard.py": "8294a1a5530e9c18cdedfffab285260dd56888d602f94a724cfc982dd46b0f4b",
    "live_indicators.py": "981a55b265a718a60317370a1d58092b07397892384f0be3397ceb9f91f4b72b",
}
STRATEGY_FILES = {
    "hma_trend": "135475a59e3a041e766b3ef36392983b205da56e3c4c56b5f7fc24e16dbd127e",
    "ttm_squeeze": "55339a3ffbc9db7c89a756ae594eb7bb81d85737de224d351be2762e0b6c8cf5",
    "keltner_channel": "a46f367477bf686780fec8b88e6937bc69be0460f33e9030e26c53e7c2aaf9fb",
    "turtle_breakout": "ac08cdff6c55f800862ae65780377e34e91e74862c2f03b8bfaffd00ec8cda1c",
    "chanlun_pivot": "48db8d7f452f6140257bef121aaeaf94fd031e45452e79fc4f8a14d1371311f0",
    "mtf_ema_macd_cci": "9f3949b87df0601dcf98e77663eacc125474ca9e2dab64867f2401c43f865846",
    "time_series_momentum": "e5e283abaacc547b4ce8ba2e91eb25f0a43337f9950b3ed974f5fc1ecf1f7cf2",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest() -> dict:
    # Copies differ from source in exactly one import each; prove provenance.
    files = {name: _sha(ROOT / name) for name in PINNED_FILES}
    original_imports = {
        "live_config.py": (b"from . import heikin_ashi_strategy", b"import heikin_ashi_strategy"),
        "live_guard.py": (b"from . import live_indicators as indicators", b"from strategy_engine import indicators"),
    }
    for name, expected in PINNED_FILES.items():
        raw = (ROOT / name).read_bytes()
        if name in original_imports:
            patched, original = original_imports[name]
            if raw.count(patched) != 1:
                raise RuntimeError(f"invalid source patch: {name}")
            raw = raw.replace(patched, original)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise RuntimeError(f"live source snapshot drift: {name}")
    strategy_dir = ROOT.parent / "strategy_engine" / "strategies"
    for name, expected in STRATEGY_FILES.items():
        actual = _sha(strategy_dir / f"{name}.py")
        if actual != expected:
            raise RuntimeError(f"strategy source drift: {name} {actual} != {expected}")
    return {"kind": "shared_shadow_v2", "live_source_sha256": PINNED_FILES,
            "paper_copy_sha256": files, "strategy_sha256": STRATEGY_FILES,
            "simulator_sha256": {name: _sha(ROOT / name) for name in ("engine.py", "store.py", "runner.py")},
            "fee_rate": engine.FEE_RATE, "slippage_rate": engine.SLIPPAGE_RATE,
            "starting_equity": 1000.0, "fill_model": "bid_ask_taker_conservative"}


def _public(path: str, params: dict | None = None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "shared-shadow/1.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


def filters_snapshot() -> dict:
    rows = _public("/fapi/v1/exchangeInfo")["symbols"]
    out = {}
    for row in rows:
        if row.get("symbol") not in engine.SYMBOLS or row.get("status") != "TRADING":
            continue
        rules = {f["filterType"]: f for f in row.get("filters", [])}
        lot = rules.get("MARKET_LOT_SIZE") or rules.get("LOT_SIZE") or {}
        notional = rules.get("MIN_NOTIONAL") or {}
        step = float(lot.get("stepSize") or 0)
        min_qty = float(lot.get("minQty") or 0)
        minimum = float(notional.get("notional") or 0)
        if step > 0 and minimum > 0:
            out[row["symbol"]] = {"step": step, "min_qty": min_qty,
                                  "min_notional": minimum}
    return out


def quotes_snapshot() -> dict:
    rows = _public("/fapi/v1/ticker/bookTicker")
    result = {}
    for row in rows:
        symbol = row.get("symbol")
        if symbol in engine.SYMBOLS:
            bid, ask = float(row["bidPrice"]), float(row["askPrice"])
            if 0 < bid <= ask:
                result[symbol] = (bid, ask)
    return result


def funding_snapshot(db, state: dict, now_ms: int) -> dict:
    out = {}
    start_ms = max(int(state["started_ms"]), now_ms - 72 * 60 * 60 * 1000)
    fills = {}
    for (payload,) in db.execute(
        "SELECT payload_json FROM events WHERE type='fill' ORDER BY ts, id"
    ):
        event = json.loads(payload)
        fills.setdefault(event["symbol"], []).append(event)
    symbols = set(state["positions"]) | {
        symbol for symbol, events in fills.items()
        if any(int(event["ts"]) >= start_ms for event in events)
    }
    for symbol in symbols:
        rows = _public("/fapi/v1/fundingRate", {
            "symbol": symbol, "startTime": start_ms + 1,
            "endTime": now_ms, "limit": 100})
        for row in rows:
            ft = int(row["fundingTime"])
            if f"funding:{symbol}:{ft}" in state.get("funding_applied", {}):
                continue
            row["qty_at_funding"] = sum(
                float(event["delta"]) for event in fills.get(symbol, [])
                if int(event["ts"]) <= ft
            )
            out.setdefault(symbol, []).append(row)
    return out


def data_snapshot() -> dict:
    result = {}
    for symbol in engine.SYMBOLS:
        bars = klines.get_bars(symbol, "4h", limit=200)
        daily = klines.get_bars(symbol, "1d", limit=100)
        if bars and daily:
            result[symbol] = {"base": bars, "1d": daily}
    return result


def run_once(db, filters: dict, source: dict) -> dict:
    now_ms = int(time.time() * 1000)
    state = store.load(db, now_ms, source)
    quotes = quotes_snapshot()
    data = data_snapshot()
    funding = funding_snapshot(db, state, now_ms)
    new_state, events, snapshot = engine.step(state, data, quotes, filters, now_ms, funding)
    store.commit(db, new_state, events, snapshot)
    LOG.info("equity=%.2f gross=%.2f positions=%d events=%d frozen=%d",
             snapshot["equity"], snapshot["gross"], snapshot["positions"],
             len(events), len(snapshot["frozen"]))
    return snapshot


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    source = manifest()
    db = store.connect(DB_PATH)
    filters = filters_snapshot()
    if len(filters) != len(engine.SYMBOLS):
        raise RuntimeError(f"missing exchange filters: {set(engine.SYMBOLS) - set(filters)}")
    while True:
        try:
            run_once(db, filters, source)
        except Exception:
            LOG.exception("shadow tick failed without committing state")
        time.sleep(300)


if __name__ == "__main__":
    main()

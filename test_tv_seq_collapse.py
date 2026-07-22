#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tv_seq 1.0s 缓存折叠：同窗口先 CLOSE 后 OPEN、重复开仓只留最新。"""
import os

os.environ.setdefault("BINANCE_SKIP_BOOTSTRAP", "1")

from tv_seq import collapse_batch_for_execution, reorder_batch_close_then_open


def test_close_then_open_regardless_of_arrival_order():
    """乱序到达：OPEN 先到、CLOSE 后到 → 折叠后仍先平后开。"""
    batch = [
        {"action": "LONG", "symbol": "ETHUSDT", "price": 2000.0, "seq": 2},
        {"action": "CLOSE_QUICK_EXIT", "symbol": "ETHUSDT", "price": 1999.0, "seq": 1},
    ]
    collapsed = collapse_batch_for_execution(batch)
    assert len(collapsed) == 2
    assert collapsed[0]["action"] == "CLOSE_QUICK_EXIT"
    assert collapsed[1]["action"] == "LONG"


def test_duplicate_opens_keep_latest_only():
    batch = [
        {"action": "CLOSE_RSI_EXIT", "symbol": "ETHUSDT", "price": 1900.0},
        {"action": "LONG", "symbol": "ETHUSDT", "price": 1910.0},
        {"action": "SHORT", "symbol": "ETHUSDT", "price": 1915.0},
        {"action": "LONG", "symbol": "ETHUSDT", "price": 1920.0},
    ]
    collapsed = collapse_batch_for_execution(batch)
    assert collapsed[0]["action"] == "CLOSE_RSI_EXIT"
    assert len([m for m in collapsed if m["action"] in ("LONG", "SHORT")]) == 1
    assert collapsed[-1]["action"] == "LONG"
    assert collapsed[-1]["price"] == 1920.0


def test_reorder_close_before_open_with_seq_meta():
    """有 bar_index/seq 时，消费侧重排强制先平后开。"""
    msgs = [
        {
            "action": "SHORT",
            "symbol": "XAUUSDT",
            "price": 2400.0,
            "bar_index": 100,
            "seq": 2,
        },
        {
            "action": "CLOSE_QUICK_EXIT",
            "symbol": "XAUUSDT",
            "price": 2390.0,
            "bar_index": 100,
            "seq": 1,
        },
    ]
    out = reorder_batch_close_then_open(msgs)
    assert out[0]["action"] == "CLOSE_QUICK_EXIT"
    assert out[1]["action"] == "SHORT"


def test_collapse_handles_open_before_close_arrival():
    """无 seq 时仍靠 collapse：OPEN 先到、CLOSE 后到 → 执行仍先平后开。"""
    batch = [
        {"action": "LONG", "symbol": "ETHUSDT", "price": 2001.0},
        {"action": "CLOSE_QUICK_EXIT", "symbol": "ETHUSDT", "price": 2000.0},
    ]
    collapsed = collapse_batch_for_execution(batch)
    assert collapsed[0]["action"] == "CLOSE_QUICK_EXIT"
    assert collapsed[1]["action"] == "LONG"



def test_duplicate_same_exit_collapsed_to_one():
    batch = [
        {"action": "CLOSE_QUICK_EXIT", "symbol": "ETHUSDT", "price": 2000.0},
        {"action": "CLOSE_QUICK_EXIT", "symbol": "ETHUSDT", "price": 2000.0},
        {"action": "LONG", "symbol": "ETHUSDT", "price": 2010.0},
    ]
    collapsed = collapse_batch_for_execution(batch)
    assert len(collapsed) == 2
    assert collapsed[0]["action"] == "CLOSE_QUICK_EXIT"
    assert collapsed[1]["action"] == "LONG"


if __name__ == "__main__":
    test_close_then_open_regardless_of_arrival_order()
    test_duplicate_opens_keep_latest_only()
    test_reorder_close_before_open_with_seq_meta()
    test_collapse_handles_open_before_close_arrival()
    test_duplicate_same_exit_collapsed_to_one()
    print("test_tv_seq_collapse: 5/5 OK")

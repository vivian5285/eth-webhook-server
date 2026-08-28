#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影子引擎独立运行入口。跟四个实盘binance-engine完全隔离的一个进程——
只读 breath_profiles.py / reentry_profiles.py / symbol_config.py 三个
纯配置数据模块(拿品种列表+周期+呼吸参数)，不import position_supervisor_
binance.py，不碰任何账户凭证/API Key/真实下单，符合既定的"不live-import
position_supervisor"规矩。

用法：
    python3 shadow_runner.py            # 常驻循环，每120秒巡检一轮全部品种
    python3 shadow_runner.py --once     # 只跑一轮就退出（cron/手动验证用）
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# repo根目录（breath_profiles.py/reentry_profiles.py/symbol_config.py所在处）
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] ShadowEngine: %(message)s",
)
logger = logging.getLogger(__name__)

TICK_INTERVAL_SEC = 30  # 2026-08-29收紧(原120s)——盘中提前入场就是要抢时间，
# 巡检间隔太长会让"当前还没走完的那根K线"实体已经冲过阈值却隔了一两分钟
# 才发现，失去提前入场的意义；30s对18个品种、纯公开行情查询的负载完全
# 可以接受，仍远小于最短TV周期(45m)，不会错过任何一根新收盘K线


# 2026-08-29：宝贝确认XPDUSDT已经从TV那边撤下了("我的TV没有xpd了")，
# 实盘也没在用。live引擎的active_binance_symbols()里暂时还留着(不影响
# 真实交易——TV既然不会再发信号，留不留都不会触发真实开仓，改这个
# 需要动4个账户的.env/watchdog/dashboard，属于更大范围的清理，先不碰)。
# 但影子引擎不一样：它自己算信号，不看TV有没有发，留着XPD会一直凭空
# 生成假信号，污染"跟TV并排对比"这个核心目的——这里单独排除，不影响
# live交易那边的品种清单。
SHADOW_EXCLUDE_SYMBOLS = {"XPDUSDT"}


def _load_symbol_universe():
    """TV现有18个品种(减去SHADOW_EXCLUDE_SYMBOLS里已经不再实盘使用的) +
    各自真实tv_tf_sec + breath配置 + ADX三档表。直接读配置数据模块，不
    实例化任何position_supervisor对象。"""
    from symbol_config import active_binance_symbols, resolve_binance_symbol
    from reentry_profiles import get_reentry_profile
    from breath_profiles import get_breath_profile

    out = []
    for sym in active_binance_symbols():
        if sym in SHADOW_EXCLUDE_SYMBOLS:
            continue
        try:
            rp = get_reentry_profile(sym)
            bp = get_breath_profile(sym)
            tf_sec = int(rp.get("tv_tf_sec") or 0)
            tiers = rp.get("tiers") or []
            if tf_sec <= 0 or not bp:
                logger.warning(f"[影子] {sym} 缺tv_tf_sec/breath配置，跳过")
                continue
            out.append({"symbol": sym, "tv_tf_sec": tf_sec, "breath": bp, "tiers": tiers})
        except Exception as e:
            logger.warning(f"[影子] {sym} 读取配置失败，跳过: {e}")
    return out


def run_once(universe):
    from strategy_engine import shadow_engine
    for item in universe:
        try:
            shadow_engine.run_symbol_tick(
                item["symbol"], item["tv_tf_sec"], item["breath"], item["tiers"],
            )
        except Exception as e:
            logger.warning(f"[影子] {item['symbol']} 本轮巡检异常，跳过: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只跑一轮就退出")
    args = ap.parse_args()

    universe = _load_symbol_universe()
    logger.info(f"[影子] 品种清单加载完成，共{len(universe)}个: {[u['symbol'] for u in universe]}")

    if args.once:
        run_once(universe)
        return

    while True:
        t0 = time.time()
        run_once(universe)
        elapsed = time.time() - t0
        time.sleep(max(1.0, TICK_INTERVAL_SEC - elapsed))


if __name__ == "__main__":
    main()

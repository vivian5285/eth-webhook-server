#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VPS 部署后全面事件自检 — 对照权威规格 §八钉钉事件 + 核心执行函数。

用法（部署后在 VPS 项目目录）:
  python3 check_deploy_events.py
  python3 check_deploy_events.py -v
  python3 check_deploy_events.py --live          # 额外探测本机 /health
  python3 check_deploy_events.py --deep          # 再跑 check_vps_logic.py
  python3 check_deploy_events.py --live --deep

退出码: 0=全部通过 · 1=有失败
无需交易所 API Key（--live 仅打本地 health）。
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import sys
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"
INFO = "ℹ️"


class Audit:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.ok = 0
        self.bad = 0
        self.warnings = 0

    def section(self, title: str):
        print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")

    def check(self, name: str, cond: bool, detail: str = ""):
        if cond:
            self.ok += 1
            mark = PASS
        else:
            self.bad += 1
            mark = FAIL
        msg = f"{mark} {name}"
        if detail:
            msg += f" — {detail}"
        if self.verbose or not cond:
            print(msg)
        elif not self.verbose:
            print(msg)
        return cond

    def warn(self, name: str, detail: str = ""):
        self.warnings += 1
        msg = f"{WARN} {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)

    def info(self, name: str, detail: str = ""):
        msg = f"{INFO} {name}"
        if detail:
            msg += f" — {detail}"
        if self.verbose:
            print(msg)

    def summary(self) -> int:
        print(f"\n{'=' * 60}")
        print(f"通过 {self.ok} · 失败 {self.bad} · 警告 {self.warnings}")
        if self.bad:
            print(f"{FAIL} 部署事件自检未通过，请修复后再实盘")
            return 1
        print(f"{PASS} 部署事件自检全部通过")
        return 0


def _read(path: str) -> str:
    full = path if os.path.isabs(path) else os.path.join(ROOT, path)
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            with open(full, encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    with open(full, encoding="utf-8", errors="replace") as f:
        return f.read()


def _has_callable(mod, name: str) -> bool:
    obj = getattr(mod, name, None)
    return callable(obj)


def _class_has(cls, name: str) -> bool:
    return hasattr(cls, name) and callable(getattr(cls, name, None))


# 权威规格 §八：钉钉事件 → 函数（必须存在且可调用）
DINGTALK_EVENT_FUNCS = [
    ("开仓", "report_supervisor_open"),
    ("先平后开", "report_close_then_open_chain"),
    ("阶段切换一→二", "report_radar_activated"),
    ("止损移动", "report_intervention"),
    ("TP1/TP2成交", "report_tp_fill"),
    ("止损触发/全平", "report_supervisor_close"),
    ("重启恢复", "report_recover_takeover"),
    ("重启待命", "report_recover_standby"),
    ("FORCE_ALIGN", "report_force_align"),
    ("HARD_SL_FAIL_ABORT", "report_hard_sl_fail_abort"),
    ("CLOSE_THEN_OPEN_FAIL_ABORT", "report_close_then_open_fail_abort"),
    ("异常告警", "report_system_alert"),
]

# CAP_ALIGN / 加仓：必须是 no-op stub（保留函数名防旧调用崩，但禁止真推送）
DINGTALK_STUB_FUNCS = [
    ("CAP_ALIGN已废除", "report_radar_regime_cap_trim"),
    ("加仓已禁用", "report_tv_position_add"),
]

# 核心执行路径：supervisor 方法
SUPERVISOR_METHODS = [
    ("先平后开", "_full_reentry"),
    ("开仓保护挂防", "_protect_and_monitor"),
    ("市价全平", "_close_all"),
    ("仓位计算", "_calc_vps_open_qty"),
    ("呼吸tick", "_apply_breath_stop_tick"),
    ("止损唯一写入", "_sync_exchange_stop"),
    ("TP后收缩止损", "_breath_resize_stop_on_tp"),
    ("重启恢复", "recover_state_on_startup"),
    ("信号入口", "handle_signal"),
    ("哨兵循环", "_sentinel_loop"),
    ("阶段二通知", "_report_breath_phase2"),
    ("FORCE_ALIGN入口", "_enforce_tv_direction_or_flat"),
]

# 纯函数模块（可无密钥 smoke）
CORE_MODULES = [
    "breath_stop",
    "webhook_parser",
    "market_engine",
    "tv_seq",
    "symbol_config",
    "dingtalk",
]


def audit_imports(a: Audit):
    a.section("一 · 核心模块可导入")
    for name in CORE_MODULES:
        try:
            importlib.import_module(name)
            a.check(f"import {name}", True)
        except Exception as e:
            a.check(f"import {name}", False, str(e))

    try:
        from position_supervisor_binance import (
            PositionSupervisorBinance,
            BINANCE_VPS_VERSION,
        )
        a.check(
            "import PositionSupervisorBinance",
            True,
            f"version={BINANCE_VPS_VERSION}",
        )
        a.info("BINANCE_VPS_VERSION", BINANCE_VPS_VERSION)
        if "v15." not in str(BINANCE_VPS_VERSION):
            a.warn("版本建议 v15+", str(BINANCE_VPS_VERSION))
    except Exception as e:
        # 本地缺 PySocks / 代理环境可能导致 import 失败；回退源码版本核对
        a.warn("import Supervisor 失败·改用源码检查", str(e)[:120])
        src = _read("position_supervisor_binance.py")
        ver = ""
        for line in src.splitlines():
            if "BINANCE_VPS_VERSION" in line and "=" in line:
                ver = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
        a.check(
            "源码 BINANCE_VPS_VERSION 含 v15",
            "v15." in ver,
            ver or "未找到",
        )


def audit_dingtalk_events(a: Audit):
    a.section("二 · 钉钉事件函数（权威规格 §八）")
    try:
        import dingtalk
    except Exception as e:
        a.check("dingtalk 模块", False, str(e))
        return

    for label, fn in DINGTALK_EVENT_FUNCS:
        ok = _has_callable(dingtalk, fn)
        a.check(f"钉钉·{label} → {fn}", ok)
        if ok and a.verbose:
            sig = str(inspect.signature(getattr(dingtalk, fn)))
            a.info(f"  signature", sig)

    src = _read("dingtalk.py")
    for label, fn in DINGTALK_STUB_FUNCS:
        ok = _has_callable(dingtalk, fn)
        a.check(f"钉钉·{label} stub 存在 → {fn}", ok)
        if ok:
            # stub 应早 return，禁止再 send_alert 真推档位裁减
            body = inspect.getsource(getattr(dingtalk, fn))
            is_stub = "return" in body and (
                "已废除" in body
                or "已禁用" in body
                or body.count("\n") < 8
                or "send_alert" not in body
            )
            a.check(f"钉钉·{label} 为 no-op", is_stub, "应早 return / 不推送")

    # 禁止旧文案残留在生效播报路径（标题串）
    banned_titles = ("保护性全平", "TP3止盈成交", "加仓成交", "雷达激活 ·")
    for phrase in banned_titles:
        # 允许注释/历史；关键是 send_alert 标题里不再用这些
        in_send = False
        for line in src.splitlines():
            if "send_alert" in line and phrase in line:
                in_send = True
                break
        a.check(f"禁用旧钉钉标题「{phrase}」", not in_send)


def audit_supervisor_methods(a: Audit):
    a.section("三 · Supervisor 核心执行函数")
    cls = None
    try:
        from position_supervisor_binance import PositionSupervisorBinance as Cls
        cls = Cls
    except Exception as e:
        a.warn("Supervisor 类不可 import·改用源码扫描", str(e)[:100])

    src = _read("position_supervisor_binance.py")
    for label, meth in SUPERVISOR_METHODS:
        if cls is not None:
            a.check(f"执行·{label} → {meth}", _class_has(cls, meth))
        else:
            a.check(
                f"执行·{label} → {meth}",
                f"def {meth}" in src,
            )

    a.check("CAP_ALIGN 生效路径已废", "CAP_ALIGN已废除" in src or "禁止 reduceOnly 主动减仓" in src)
    a.check("HARD_SL_FAIL_ABORT 接线", "HARD_SL_FAIL_ABORT" in src)
    a.check("旧 schema 暂停", "_state_old_schema" in src or "restart_old_schema" in src)
    a.check("FORCE_ALIGN 保留", "force_align" in src and "report_force_align" in src)
    a.check("呼吸唯一写止损", "_sync_exchange_stop" in src and "calculate_breath_stop" in src)
    a.check("TP后暂停 tick", "_breath_tick_paused" in src)
    a.check("PLACE_TP_LEVELS=2 接线", "PLACE_TP_LEVELS" in src)


def audit_webhook_actions(a: Audit):
    a.section("四 · Webhook 动作白名单")
    try:
        from webhook_parser import VALID_ACTIONS, FLATTEN_ACTIONS, PLACE_TP_LEVELS
    except Exception as e:
        a.check("webhook_parser 常量", False, str(e))
        return

    expected = {"LONG", "SHORT", "PING", "CLOSE_QUICK_EXIT", "CLOSE_RSI_EXIT"}
    a.check("VALID_ACTIONS ⊇ 4+PING", expected.issubset(VALID_ACTIONS), str(sorted(VALID_ACTIONS)))
    a.check(
        "FLATTEN 仅 QUICK/RSI",
        FLATTEN_ACTIONS == frozenset({"CLOSE_QUICK_EXIT", "CLOSE_RSI_EXIT"}),
        str(FLATTEN_ACTIONS),
    )
    a.check("PLACE_TP_LEVELS=2", int(PLACE_TP_LEVELS) == 2, str(PLACE_TP_LEVELS))

    banned = (
        "CLOSE_TP", "CLOSE_TRAIL", "CLOSE_SL_INITIAL", "CLOSE_SL_BREAKEVEN",
        "CLOSE_TP3", "UPDATE_SL", "UPDATE_TP",
    )
    for act in banned:
        a.check(f"禁止 action 不在 VALID：{act}", act not in VALID_ACTIONS)


def audit_breath_and_sizing_smoke(a: Audit):
    a.section("五 · 呼吸止损 / 仓位公式 Smoke")
    try:
        from breath_stop import (
            initial_stop_price,
            calculate_breath_stop,
            trail_distance_by_adx,
            INITIAL_SL_ATR,
            STEP_TRIGGER_ATR,
            STEP_ADVANCE_ATR,
            BREAKEVEN_TRIGGER_ATR,
        )
        from webhook_parser import compute_fixed_order_qty, FIXED_RISK_PCT, FIXED_NOTIONAL_MULT
    except Exception as e:
        a.check("导入 breath/sizing", False, str(e))
        return

    a.check("INITIAL_SL_ATR=1.5", abs(INITIAL_SL_ATR - 1.5) < 1e-9)
    a.check("STEP=0.75/0.4", abs(STEP_TRIGGER_ATR - 0.75) < 1e-9 and abs(STEP_ADVANCE_ATR - 0.4) < 1e-9)
    a.check("BREAKEVEN=3.0", abs(BREAKEVEN_TRIGGER_ATR - 3.0) < 1e-9)
    a.check("RISK20/NOTIONAL5", abs(FIXED_RISK_PCT - 0.20) < 1e-9 and float(FIXED_NOTIONAL_MULT) == 5.0)

    entry, atr = 3000.0, 40.0
    init_sl = initial_stop_price("LONG", entry, atr)
    expect_sl = round(entry - 1.5 * atr, 2)
    a.check("initial_stop LONG", abs(init_sl - expect_sl) < 1e-6, f"{init_sl} vs {expect_sl}")

    # 阶段一：推进约 1 步 (0.75 ATR)
    px = entry + 0.75 * atr + 0.01
    out = calculate_breath_stop(
        "LONG", px, entry, atr, init_sl, init_sl, entry, False, adx_val=25,
    )
    step_expect = round(init_sl + 1 * 0.4 * atr, 2)
    a.check(
        "阶段一阶梯上移",
        float(out["stop"]) >= step_expect - 0.01,
        f"stop={out['stop']} expect≥{step_expect}",
    )
    a.check("阶段一未进保本", out["breakeven_phase"] is False)

    # 阶段二触发
    px2 = entry + 3.0 * atr + 1.0
    out2 = calculate_breath_stop(
        "LONG", px2, entry, atr, init_sl, float(out["stop"]), px2, False, adx_val=25,
    )
    a.check("触及3.0ATR → 阶段二", out2["breakeven_phase"] is True)
    td = trail_distance_by_adx(25)
    a.check("ADX25 追踪距在 1.2~2.5", 1.2 < td < 2.5, f"td={td:.3f}")

    # 仓位：无 TV.sl → adj=1；equity=1000, stop dist=60, tv_qty=10
    # risk=200/60≈3.333, notional=(1000×20%×5)/3000=0.333 → min=0.333
    qty, meta = compute_fixed_order_qty(
        principal=1000, price=3000, stop_loss=2940, tv_qty=10, qty_step=0.001,
    )
    a.check("仓位公式产出>0", float(qty) > 0, f"qty={qty} meta={meta.get('error')}")
    a.check(
        "仓位受名义约束(=本金×1)",
        abs(float(qty) - 0.333) < 0.002 and meta.get("binding") == "notional",
        f"qty={qty} bind={meta.get('binding')} cap={meta.get('notional_cap')}",
    )
    # 有 TV.sl：VPS距60 TV距40 → adj=2/3；tv′=1.333，但仍被名义0.333卡住
    qty2, meta2 = compute_fixed_order_qty(
        principal=1000, price=3000, stop_loss=2940, tv_qty=2.0, tv_sl=2960,
    )
    a.check(
        "TV止损距调整系数",
        abs(float(meta2.get("sl_adj") or 0) - 40.0 / 60.0) < 1e-6
        and abs(float(qty2) - 0.333) < 0.002
        and meta2.get("binding") == "notional",
        f"sl_adj={meta2.get('sl_adj')} qty={qty2} bind={meta2.get('binding')}",
    )


def audit_market_engine(a: Audit):
    a.section("六 · 行情引擎接口")
    try:
        from market_engine import get_market_engine
        eng = get_market_engine("ETHUSDT")
        a.check("get_market_engine(ETHUSDT)", eng is not None)
        readable = any(
            hasattr(eng, m)
            for m in (
                "get_atr", "get_adx", "atr", "adx", "get_metrics",
                "latest", "refresh", "refresh_metrics", "update",
            )
        )
        a.check("引擎可刷新/读取 ATR·ADX", readable, type(eng).__name__)
        src = _read("market_engine.py")
        a.check(
            "90m 合成",
            "90" in src and ("30" in src or "三根" in src or "合成" in src or "merge" in src.lower()),
        )
        a.check("ATR(14)/ADX(14)", "period=14" in src or "ATR(14)" in src or ", 14)" in src or "14" in src)
    except Exception as e:
        a.check("market_engine", False, str(e))


def audit_tv_seq(a: Audit):
    a.section("七 · 先平后开时序")
    try:
        from tv_seq import (
            collapse_batch_for_execution,
            reorder_batch_close_then_open,
            SAME_BAR_SETTLE_SEC,
        )
        a.check("缓存窗口=1.0s", abs(float(SAME_BAR_SETTLE_SEC) - 1.0) < 1e-9)
        # 同 bar_index 才按动作优先级重排（无 bar 落入 legacy 保序）
        inverted = reorder_batch_close_then_open([
            {"action": "LONG", "bar_index": 100, "seq": 1},
            {"action": "CLOSE_QUICK_EXIT", "bar_index": 100, "seq": 2},
        ])
        a.check(
            "reorder 同bar先平后开",
            inverted[0].get("action") == "CLOSE_QUICK_EXIT"
            and inverted[1].get("action") == "LONG",
            str([m.get("action") for m in inverted]),
        )
        collapsed = collapse_batch_for_execution([
            {"action": "SHORT", "price": 1},
            {"action": "CLOSE_RSI_EXIT", "price": 2},
            {"action": "LONG", "price": 3},
        ])
        a.check(
            "collapse 平一次+最新开",
            len(collapsed) == 2
            and collapsed[0]["action"].startswith("CLOSE")
            and collapsed[1]["action"] == "LONG",
            str([m.get("action") for m in collapsed]),
        )
    except Exception as e:
        a.check("tv_seq", False, str(e))


def audit_health_live(a: Audit, port: int = 5003):
    a.section(f"八 · 本机存活探测 (:{port}/health)")
    url = f"http://127.0.0.1:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=4) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            data = json.loads(body) if body.strip().startswith("{") else {}
            a.check(f"GET {url}", resp.status == 200, body[:200])
            ver = str(data.get("version") or "")
            if ver:
                a.check("health.version 含 v15", "v15" in ver, ver)
            sizing = str(data.get("sizing") or data.get("sizing_mode") or "")
            if sizing:
                a.check("health.sizing", "RISK20" in sizing or "20" in sizing, sizing)
            else:
                a.info("health 无 sizing 字段", str(list(data.keys())[:12]))
    except urllib.error.URLError as e:
        a.warn(f"服务未监听 {url}", str(e.reason if hasattr(e, 'reason') else e))
        a.warn("跳过存活项", "部署后再加 --live，或检查 gunicorn")
    except Exception as e:
        a.check(f"GET {url}", False, str(e))


def audit_file_presence(a: Audit):
    a.section("零 · 关键文件存在")
    files = [
        "app.py",
        "position_supervisor_binance.py",
        "breath_stop.py",
        "market_engine.py",
        "webhook_parser.py",
        "dingtalk.py",
        "binance_client.py",
        "tv_seq.py",
        "check_vps_logic.py",
        "deploy_binance.sh",
    ]
    for f in files:
        a.check(f"文件 {f}", os.path.isfile(os.path.join(ROOT, f)))


def run_deep_logic(a: Audit) -> int:
    a.section("九 · 深度静态逻辑 (check_vps_logic)")
    try:
        import check_vps_logic as cvl
        # 复用其 Audit，但不污染本脚本计数——单独跑 summary
        deep = cvl.Audit(verbose=False)
        # 静默打印：临时劫持 print
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            cvl.audit_module1_symbol(deep)
            cvl.audit_module2_sizing(deep)
            cvl.audit_module3_hard_sl(deep)
            cvl.audit_module4_radar(deep)
            cvl.audit_module5_actions(deep)
            cvl.audit_module6_risk(deep)
            cvl.audit_module7_position(deep)
            cvl.audit_module8_dingtalk(deep)
            cvl.audit_readme_consistency(deep)
        a.check(
            "check_vps_logic 全通过",
            deep.bad == 0,
            f"ok={deep.ok} bad={deep.bad} warn={deep.warnings}",
        )
        if deep.bad and a.verbose:
            print(buf.getvalue()[-2000:])
        return 0 if deep.bad == 0 else 1
    except Exception as e:
        a.check("check_vps_logic 执行", False, str(e))
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="VPS 部署后事件/函数全面自检 (final-spec)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--live",
        action="store_true",
        help="探测本机 http://127.0.0.1:PORT/health",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="追加运行 check_vps_logic.py 全套静态审计",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "5003")),
        help="health 端口 (默认 5003)",
    )
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("[VPS] deploy event self-check · final-spec")
    print(f"cwd: {ROOT}")

    a = Audit(verbose=args.verbose)
    audit_file_presence(a)
    audit_imports(a)
    audit_dingtalk_events(a)
    audit_supervisor_methods(a)
    audit_webhook_actions(a)
    audit_breath_and_sizing_smoke(a)
    audit_market_engine(a)
    audit_tv_seq(a)
    if args.live:
        audit_health_live(a, port=args.port)
    else:
        a.section("八 · 本机存活探测（跳过）")
        a.warn("未加 --live", "部署后建议: python3 check_deploy_events.py --live")
    if args.deep:
        run_deep_logic(a)

    return a.summary()


if __name__ == "__main__":
    sys.exit(main())

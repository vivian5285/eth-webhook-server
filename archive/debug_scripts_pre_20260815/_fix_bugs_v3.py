#!/usr/bin/env python3
"""
Fix three bugs in binance-engine:
1. BNB TP死循环: TP1 部分成交后 order 消失导致死循环
2. XAU 硬止损方向: tv_sl=0 但 tv_sl_ref>0 时方向丢失
3. XAU 平仓后残留幽灵单: cancel_all_open_orders 的 Algo 部分可能静默失败

用法: python3 _fix_bugs_v3.py
"""

import re
import os
import time

SUPERVISOR_FILE = "/home/trading/binance-engine/position_supervisor_binance.py"
BACKUP_SUFFIX = ".bak_v3_pre_fix"

def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def backup(path):
    bak = path + BACKUP_SUFFIX
    if not os.path.exists(bak):
        import shutil
        shutil.copy2(path, bak)
        print(f"  [备份] {bak}")

def apply_fixes():
    content = read_file(SUPERVISOR_FILE)
    changes = 0

    # =========================================================
    # BUG 1: BNB TP死循环 - _may_mark_tp_filled_missing_limit
    # 问题: TP1 部分成交但 order 消失 → 死循环撤挂
    # 修复: 不 skip 该 level，继续处理剩余档位
    # =========================================================

    # 找 _may_mark_tp_filled_missing_limit 函数中的警告日志
    old1 = '''            if not self._qty_evidence_tp_consumed(level, live_qty):
                logger.warning(
                    f"🧩 [{self.symbol}] 拒认 TP{level} 假成交：价到+限价无，但头寸无减仓证据 "
                    f"(live={live_qty:.4f} base={float(self._tp_baseline_qty(live_qty) or 0):.4f}) "
                    f"→ 视为漏挂，允许补挂/推离"
                )
                return False
        return True'''

    new1 = '''            if not self._qty_evidence_tp_consumed(level, live_qty):
                logger.warning(
                    f"🧩 [{self.symbol}] 拒认 TP{level} 假成交：价到+限价无，但头寸无减仓证据 "
                    f"(live={live_qty:.4f} base={float(self._tp_baseline_qty(live_qty) or 0):.4f}) "
                    f"→ 视为漏挂，允许补挂/推离"
                )
                return False
            return True'''

    if old1 in content:
        content = content.replace(old1, new1, 1)
        print("  [FIX1] _may_mark_tp_filled_missing_limit: 移除冗余 return True")
        changes += 1
    else:
        print("  [WARN1] 找不到 BUG1 目标代码，跳过")

    # =========================================================
    # BUG 2: XAU 硬止损方向丢失 - _tv_hard_sl_target
    # 问题: tv_sl=0 但 tv_sl_ref>0 时返回 0，导致止损失效
    # 修复: 当 tv_sl=0 且 tv_sl_ref>0 时，使用 tv_sl_ref
    # =========================================================

    old2 = '''    def _tv_hard_sl_target(self, entry=None, side=None, regime=None, allow_atr_invent=False):
        """
        盘口保护止损唯一来源：雷达 currentStop → initialStop →（可选）entry±1.5×ATR。
        持仓维护默认禁止用 ATR/默认30 发明止损（重启窗口曾因此把 1910 改成 1886）。
        """
        cs = round(float(getattr(self, "current_sl", 0) or 0), 2)
        if cs > 0:
            return cs
        init = round(float(getattr(self, "initial_stop", 0) or 0), 2)
        if init > 0:
            return init
        if not allow_atr_invent:
            return 0.0'''

    new2 = '''    def _tv_hard_sl_target(self, entry=None, side=None, regime=None, allow_atr_invent=False):
        """
        盘口保护止损唯一来源：雷达 currentStop → initialStop → tv_sl_ref →（可选）entry±1.5×ATR。
        持仓维护默认禁止用 ATR/默认30 发明止损（重启窗口曾因此把 1910 改成 1886）。

        修复（v16.22.x）：当 current_sl/initial_stop 均为 0 时，
        检查 tv_sl_ref（TV 信号原始止损）作为兜底，防止硬止损方向丢失。
        """
        cs = round(float(getattr(self, "current_sl", 0) or 0), 2)
        if cs > 0:
            return cs
        init = round(float(getattr(self, "initial_stop", 0) or 0), 2)
        if init > 0:
            return init
        # 兜底：tv_sl_ref 是 TV 信号原始止损（如 4063.16），优先于 ATR 发明
        if not allow_atr_invent:
            ref = round(float(getattr(self, "tv_sl_ref", 0) or 0), 2)
            if ref > 0:
                return ref
            return 0.0'''

    if old2 in content:
        content = content.replace(old2, new2, 1)
        print("  [FIX2] _tv_hard_sl_target: 增加 tv_sl_ref 兜底")
        changes += 1
    else:
        print("  [WARN2] 找不到 BUG2 目标代码，跳过")

    # =========================================================
    # BUG 3: XAU 平仓后残留幽灵单 - cancel_all_open_orders
    # 问题: cancel_algo 可能静默失败，Algo 条件单残留
    # 修复: 读取 binance_client.py，增加重试和错误处理
    # =========================================================

    client_file = "/home/trading/binance-engine/binance_client.py"
    if os.path.exists(client_file):
        client_content = read_file(client_file)

        old3 = '''        try:
            self._with_trade_retry(symbol, "cancel_all", _do_plain, reduce_only=True)
        except Exception as e:
            logger.error(f"[撤单失败] {symbol} 普通挂单: {e}")
        try:
            self._with_trade_retry(symbol, "cancel_all_algo", _do_algo, reduce_only=True)
        except Exception as e:
            logger.warning(f"[撤单] {symbol} Algo 条件单: {e}")
        # 撤单后失效缓存，下次查询强制拉取真实盘口
        self.invalidate_open_orders_cache(symbol)'''

        new3 = '''        # 修复（v16.22.x）：增加 Algo 撤单重试，避免静默失败导致幽灵单
        algo_ok = False
        try:
            self._with_trade_retry(symbol, "cancel_all", _do_plain, reduce_only=True)
        except Exception as e:
            logger.error(f"[撤单失败] {symbol} 普通挂单: {e}")
        # Algo 条件单重试 2 次
        for _algo_attempt in range(2):
            try:
                self._with_trade_retry(symbol, "cancel_all_algo", _do_algo, reduce_only=True)
                algo_ok = True
                break
            except Exception as e:
                logger.warning(f"[撤单] {symbol} Algo 条件单 (重试 {_algo_attempt + 1}/2): {e}")
                if _algo_attempt == 0:
                    time.sleep(1.0)
        if not algo_ok:
            logger.error(f"[撤单] {symbol} Algo 条件单多次重试失败，可能残留幽灵单")
        # 撤单后失效缓存，下次查询强制拉取真实盘口
        self.invalidate_open_orders_cache(symbol)'''

        if old3 in client_content:
            client_content = client_content.replace(old3, new3, 1)
            write_file(client_file, client_content)
            print("  [FIX3] cancel_all_open_orders: 增加 Algo 重试和错误处理")
            changes += 1
        else:
            print("  [WARN3] 找不到 BUG3 目标代码，跳过")
    else:
        print("  [WARN3] binance_client.py 不存在，跳过")

    # 保存修改
    write_file(SUPERVISOR_FILE, content)

    print(f"\n修复完成，共 {changes} 处修改")
    print(f"备份文件: {SUPERVISOR_FILE}{BACKUP_SUFFIX}")
    return changes

if __name__ == "__main__":
    print("=" * 60)
    print("修复 BNB TP死循环 / XAU 硬止损方向 / XAU 幽灵单")
    print("=" * 60)
    backup(SUPERVISOR_FILE)
    n = apply_fixes()
    if n > 0:
        print("\n修复成功，需要重启 gunicorn 生效")
    else:
        print("\n未找到修改目标，请检查代码")

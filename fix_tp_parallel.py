#!/usr/bin/env python3
"""
优化 _place_tp_levels_only：TP1+TP2 并行挂单 + 删除 time.sleep(0.25)
目标：开仓后 TP 挂单时间 19s → ~3s
"""
import re

TARGET = "/home/trading/binance-engine/position_supervisor_binance.py"
BACKUP = TARGET + ".bak_opt_tp_parallel"

def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def main():
    content = read_file(TARGET)

    # ── 1. 备份 ────────────────────────────────────────────────
    write_file(BACKUP, content)
    print(f"[OK] 备份已保存: {BACKUP}")

    # ── 2. 添加 import ────────────────────────────────────────
    # 插在 "import queue" 之后
    if "from concurrent.futures import ThreadPoolExecutor, as_completed" in content:
        print("[SKIP] import 已存在，跳过")
    else:
        content = content.replace(
            "import queue\n",
            "import queue\nfrom concurrent.futures import ThreadPoolExecutor, as_completed\n",
            1
        )
        print("[OK] 添加了 concurrent.futures import")

    # ── 3. 替换 _place_tp_levels_only ────────────────────────
    # 新方法完整替换
    NEW_METHOD = '''    def _place_tp_levels_only(self, live_qty, retries=2):
        """
        只挂 TP1/TP2 限价档（TP3 走雷达守护，不挂）。
        v16.11.0 优化：TP1+TP2 并行挂单，删除串行 time.sleep(0.25)，
        开仓后 TP 挂单时间从 ~19s 降至 ~3s。
        """
        close_side = "SHORT" if self.current_side == "LONG" else "LONG"
        live_qty = self._resolve_live_qty(live_qty)
        if live_qty <= 0:
            return 0
        self._clear_spurious_tp_consumed_if_full_size(
            live_qty, source="place_tp_levels_only",
        )

        # 执行官自检：开仓/补挂均过预算闸（防 TP1 后把余仓堆进 TP2）
        try:
            levels_preview = self._expected_tp_levels(live_qty)
            ok_b, detail_b = self._assert_place_tp_budget(live_qty, levels_preview)
            if not ok_b:
                logger.error(
                    f"🚨 [{self.symbol}] 执行官TP预算闸拒挂 → {detail_b}"
                )
                try:
                    self._pipeline_fail(Role.EXECUTION, f"tp_slice:{detail_b}")
                except Exception:
                    pass
                try:
                    self._pause_symbol_trading(
                        f"tp_slice:{str(detail_b)[:120]}",
                        title=f"TP切片预算拦截 [{self.symbol}]",
                        detail=str(detail_b),
                    )
                except Exception:
                    pass
                return 0
        except Exception as e:
            logger.warning(f"[{self.symbol}] TP预算闸跳过: {e}")

        curr_px = float(binance_client.get_current_price(self.symbol) or 0)

        # 预计算：只取 TP1/TP2，跳过已挂或价到无减仓需记账的档
        prepared = []
        for lv in self._expected_tp_levels(live_qty):
            level_num = int(lv["level"])
            if level_num not in (1, 2):
                continue
            q, px = float(lv["qty"] or 0), float(lv["price"] or 0)
            if q <= 0 or px <= 0:
                continue
            if self._has_tp_limit_at_price(px):
                continue
            # 限价消失处理：价到无减仓 → 记账不挂
            if not self._has_tp_limit_at_price(px):
                reason_skip = ""
                if self._may_mark_tp_filled_missing_limit(
                    level_num, live_qty, curr_px, tp_px=px,
                ):
                    self._mark_tp_levels_consumed([level_num])
                    reason_skip = "（价到无减仓→记账不挂）"
                if reason_skip:
                    logger.warning(
                        f"🧩 [{self.symbol}] TP{level_num}@{px:.2f} "
                        f"限价消失但未核实成交 {reason_skip}，仍尝试补挂"
                    )

            # 穿价处理（直接从原逻辑保留）
            adj_px = px
            if self._tp_is_marketable(self.current_side, px, curr_px):
                self._force_tps_unmarketable(curr_px, self.watched_entry or 0)
                tps = list(self.tv_tps or [])
                idx = level_num - 1
                adj_px = float(tps[idx]) if 0 <= idx < len(tps) else 0.0
                if adj_px <= 0 or self._tp_is_marketable(self.current_side, adj_px, curr_px):
                    logger.warning(
                        f"📈 穿价 TP{level_num} 再推 mark={curr_px:.2f}"
                    )
                    self._force_tps_unmarketable(curr_px, self.watched_entry or 0)
                    tps = list(self.tv_tps or [])
                    adj_px = float(tps[idx]) if 0 <= idx < len(tps) else 0.0
                    if adj_px <= 0 or self._tp_is_marketable(
                        self.current_side, adj_px, curr_px
                    ):
                        logger.error(
                            f"❌ 跳过穿价 TP{level_num}：推离失败 mark={curr_px:.2f}"
                        )
                        continue
                logger.warning(
                    f"📈 穿价 TP{level_num} 已推离 → @{adj_px:.2f} mark={curr_px:.2f}"
                )
            prepared_by_level[level_num] = (q, adj_px)

        if not prepared_by_level:
            return 0

        # ── 并行挂单：TP1 + TP2 同时提交 ────────────────────
        # 用 dict 方便日志回查 qty/price
        prepared_by_level = {}  # {level_num: (q, adj_px)}

        def _place_single(level_num):
            q, px = prepared_by_level[level_num]
            res = self._place_defense_tp_limit(
                close_side, q, px, level_num,
            )
            return level_num, res

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(_place_single, lv_num): lv_num
                for lv_num in prepared_by_level
            }
            placed = 0
            for future in as_completed(futures):
                lv_num, res = future.result()
                q, px = prepared_by_level[lv_num]
                if res:
                    placed += 1
                    logger.info(f"📈 UPDATE_TP 挂 TP{lv_num} {q} @ {px:.2f}")
                else:
                    logger.error(f"❌ UPDATE_TP 挂 TP{lv_num} @ {px:.2f} 失败")
        return placed
'''

    # 用正则精准定位替换（从头定义到 return placed 结尾）
    pattern = r'    def _place_tp_levels_only\(self, live_qty, retries=2\):.*?return placed\n'
    count = len(re.findall(pattern, content, re.DOTALL))
    if count == 1:
        content = re.sub(pattern, NEW_METHOD + '\n', content, count=1, flags=re.DOTALL)
        print(f"[OK] _place_tp_levels_only 已替换（{count} 处）")
    elif count == 0:
        print("[ERROR] 未找到 _place_tp_levels_only 方法定义！")
        return
    else:
        print(f"[WARN] 找到 {count} 处匹配，手动检查！")

    write_file(TARGET, content)
    print(f"[OK] 写入完成: {TARGET}")

if __name__ == "__main__":
    main()

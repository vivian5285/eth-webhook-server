import shutil, time
P = "/home/stratroster/eth-webhook-server/strategy_engine/strategies/stock_sector_rotation.py"
shutil.copy(P, P + f".bak_align_{time.strftime('%Y%m%d_%H%M')}")
s = open(P, encoding="utf-8").read()
# 1) 把"本轮是否已被止损锁定"的推算挪到持仓判断之前，持仓时也用
old_head = """    if position:
        if not up:"""
old_block_start = """    if not rot or not in_rot or not up:
        return None
    # 2026-10-17 对账发现：回测里止损后要等均线重新翻多才能再进场，擂台原来止损后马上又开(CSCO/MSTR)。"""
assert s.count(old_head) == 1 and s.count(old_block_start) == 1
i0 = s.index("    # 2026-10-17 对账发现：回测里止损后要等均线重新翻多才能再进场")
i1 = s.index("    atr_stock = indicators.wilder_atr(stock, int(p[\"atr_len\"]))")
block = s[i0:i1]
# 原来块里 "return None" 改成 设标志
assert block.count("            return None") == 1
block_fn = block.replace("            return None", "            stop_locked = True")
block_fn = "    stop_locked = False\n" + block_fn
s = s[:i0] + s[i1:]                                   # 删掉原位置的块
s = s.replace(old_head, block_fn + """    if position:
        # 2026-10-18 对齐回测：本轮均线多头里回测已经跌破过3×ATR止损(按规则锁定)，擂台还拿着就平掉
        if stop_locked and up:
            return {"action": "CLOSE_QUICK_EXIT", "price": round(price, 6), "bar_time": bar_time,
                    "reason": f"对齐回测：正股{ticker}本轮EMA多头内已跌破3×ATR止损，按规则锁定到均线重新翻多"}
        if not up:""", 1)
s = s.replace("""    if not rot or not in_rot or not up:
        return None
""", """    if not rot or not in_rot or not up or stop_locked:
        return None
""", 1)
open(P, "w", encoding="utf-8").write(s)
import ast; ast.parse(s); print("ok")

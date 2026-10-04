# -*- coding: utf-8 -*-
"""
2026-10-04 修复：indicators.atr_series要求字典K线(bar["h"])，但币安/CoinW的
_has_4h_reversal_signal、_check_pyramid_add把K线转成列表[t,o,h,l,c,v]后直接传入 →
TypeError被外层try/except吞掉 → 4H反转预警和金字塔加仓在实盘一直静默失效。
做法：atr_series的入参就地转回字典；幂等(已修复则跳过)。
用法: fix_atr_dict_bars.py <position_supervisor_*.py>
"""
import re
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
pat = re.compile(r"_sk_ind\.atr_series\(bars, (REVERSAL_4H_ATR_LEN|PYRAMID_ADD_ATR_LEN)\)")
rep = (
    r'_sk_ind.atr_series([{"t": _b[0], "o": _b[1], "h": _b[2], "l": _b[3], "c": _b[4], "v": _b[5]} '
    r"for _b in bars], \1)"
)
n = len(pat.findall(s))
if n == 0:
    print("fix_atr_dict_bars: 无需修改(已修复或无匹配)", p)
else:
    s = pat.sub(rep, s)
    open(p, "w", encoding="utf-8").write(s)
    print(f"fix_atr_dict_bars: 已修复{n}处", p)

import re, shutil, time
U = "/home/stratroster/unicorn_quant"
FILES = ("engine/optimal.py", "engine/universe.py", "engine/plan.py", "static/best.js", "static/best.html", "static/sym.js")
ts = time.strftime("%H%M%S")
for f in FILES:
    shutil.copy(f"{U}/{f}", f"{U}/backups/{f.replace('/', '_')}.pre_equal_{ts}")
def sub(path, old, new, n=1):
    s = open(f"{U}/{path}", encoding="utf-8").read(); assert s.count(old) == n, (path, old[:60], s.count(old))
    open(f"{U}/{path}", "w", encoding="utf-8").write(s.replace(old, new))
sub("engine/optimal.py", 'SUB_W = {"日线EMA8/30": 0.5, "8h超级趋势": 0.25, "8h布林突破": 0.25}',
    '# 2026-10-19 宝贝同意改等权(/tmp/dd_overlays.py、dd_f_nbhd.py)：50/25/25 → 各1/3，比值5.19→5.39、回撤20.1→18.7%，全程/前后半都更好\nSUB_W = {"日线EMA8/30": 1 / 3, "8h超级趋势": 1 / 3, "8h布林突破": 1 / 3}')
sub("engine/universe.py", 'W_SUB = {"日线EMA8/30": 0.5, "8h超级趋势": 0.25, "8h布林突破": 0.25}',
    'W_SUB = {"日线EMA8/30": 1 / 3, "8h超级趋势": 1 / 3, "8h布林突破": 1 / 3}   # 2026-10-19 改等权，跟 optimal.SUB_W 一致')
sub("engine/plan.py", "(各占币圈腿 25%；日线 EMA 那条占 50%)", "(2026-10-19 起三条子腿各占币圈腿 1/3)")
sub("engine/plan.py", '"币圈腿B的25%：', '"币圈腿B的1/3：'); sub("engine/plan.py", '"币圈腿C的25%：', '"币圈腿C的1/3：')
sub("static/best.js", "日线 EMA8/30 <b>50%</b> + 8h 超级趋势 <b>25%</b> + 8h 布林突破 <b>25%</b>", "日线 EMA8/30 + 8h 超级趋势 + 8h 布林突破 <b>各 1/3</b>")
sub("static/best.html", "币圈腿 = 日线EMA8/30 50% + 8h超级趋势 25% + 8h布林突破 25%(2026-10-17 B方案)。", "币圈腿 = 日线EMA8/30 + 8h超级趋势 + 8h布林突破 各 1/3(2026-10-19 由 50/25/25 改等权)。")
sub("static/best.html", "三条子腿各占币圈资金 50%/25%/25%。", "三条子腿各占币圈资金 1/3。")
sub("static/sym.js", "币圈（日线EMA8/30 50% + 8h超级趋势 25% + 8h布林突破 25%）", "币圈（日线EMA8/30 + 8h超级趋势 + 8h布林突破 各 1/3）")
h = open(f"{U}/static/best.html", encoding="utf-8").read()
h = re.sub(r"best\.js\?v=(\w+)", lambda m: f"best.js?v={m.group(1)}e", h)
open(f"{U}/static/best.html", "w", encoding="utf-8").write(h)
for p in ("static/sym.html",):
    s = open(f"{U}/{p}", encoding="utf-8").read(); s = re.sub(r"sym\.js\?v=(\w+)", lambda m: f"sym.js?v={m.group(1)}e", s); open(f"{U}/{p}", "w", encoding="utf-8").write(s)
print("ok")

import shutil, time, os
ts = time.strftime("%H%M%S")
U = "/home/stratroster/unicorn_quant"; E = "/home/stratroster/eth-webhook-server/strategy_engine/strategies"
BK = f"{U}/backups"
def sub(path, old, new, n=1):
    shutil.copy(path, f"{BK}/{os.path.basename(path)}.pre_bb25_{ts}")
    s = open(path, encoding="utf-8").read(); assert s.count(old) == n, (path, old[:60], s.count(old))
    open(path, "w", encoding="utf-8").write(s.replace(old, new))
NOTE = "2026-10-19 宝贝采纳：布林(20,2)→(25,1.25)，/tmp/bb_grid.py+bb_fine.py 周期20~35×倍数1.0~1.5 整片稳定区12/12过关，取区中心；比值5.39→5.57"
sub(f"{U}/engine/optimal.py", "def _bb_states(b, n=20, k=2.0):", f"def _bb_states(b, n=25, k=1.25):          # {NOTE}")
sub(f"{U}/engine/plan.py", "def _bb_state_list(bars, n=20, k=2.0):", f"def _bb_state_list(bars, n=25, k=1.25):   # {NOTE}")
sub(f"{E}/intraday_trend_cycle.py", '"bb_len": 20, "bb_k": 2.0,', f'"bb_len": 25, "bb_k": 1.25,   # {NOTE}\n   ')
sub(f"{E}/intraday_trend_cycle.py", "布林突破(20,2)：", "布林突破(25,1.25，2026-10-19 由(20,2)改)：")
sub(f"{E}/__init__.py", '"8h布林(20,2)突破+BTC减半周期(严格)+山寨跟BTC日线+波动率调仓，主流5个(2026-10-17，方案F币圈腿B的25%)"',
    '"8h布林(25,1.25)突破+BTC减半周期(严格)+山寨跟BTC日线+波动率调仓，主流5个(2026-10-17；10-19起参数(25,1.25)、币圈腿三条子腿各1/3)"')
for p in (f"{U}/static/best.html",):
    s = open(p, encoding="utf-8").read()
    if "布林(20,2)" in s:
        shutil.copy(p, f"{BK}/best.html.pre_bb25_{ts}"); open(p, "w", encoding="utf-8").write(s.replace("布林(20,2)", "布林(25,1.25)"))
print("ok")

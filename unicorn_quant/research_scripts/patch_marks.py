"""2026-10-17 ① 往左拖K线时，屏幕外的开平仓标签被"贴边"挤到右边重叠 → 裁剪到绘图区、屏幕外的不画
② 全屏时用截图快捷键(截图工具用Esc取消/结束)会退出全屏 → Esc 不再退出，只能点"退出全屏"或按 F；浏览器原生全屏被 Esc 退掉时图表仍铺满窗口"""
import os, re
os.chdir("/home/stratroster/unicorn_quant/static")
s = open("app.js", encoding="utf-8").read()
if "// 2026-10-17 只画可见区域" not in s:
    old = "  const last = S.bars[S.bars.length - 1];\n  const items = S.bt.trades.concat("
    assert old in s
    s = s.replace(old, "  // 2026-10-17 只画可见区域：裁剪到绘图区(不含右侧价格轴)，屏幕外的标记不画，避免往左拖时挤到右边重叠\n"
                       "  cx.save(); cx.beginPath(); cx.rect(0, 0, W, H + 30); cx.clip();\n" + old)
    old = "    if (x2 < -80 || x1 > W + 80) continue;"
    assert old in s
    s = s.replace(old, "    if (x2 < 0 || x1 > W) continue;")
    old = "    const n = used[key] = (used[key] || 0) + 1, off = (n - 1) * 27;"
    assert old in s
    s = s.replace(old, "    if (x == null || x < 0 || x > W) return;          // 屏幕外的标签不画(以前会被挤到边上)\n" + old)
    # drawTrades 结尾补 restore：在 renderChips 前面的那个函数结束处
    old = "    if (!t.open) pill(x2, long ? Y(xb.h) - 4 : Y(xb.l) + 4, stop ? '硬止损' : (long ? '平多' : '平空'), t.exit, stop ? ORANGE : PINK, long, t.exit_t + (long ? 'a' : 'b'));\n  }\n}"
    assert old in s
    s = s.replace(old, old[:-1] + "  cx.restore();\n}")
# 全屏：Esc 不退出；原生全屏退出时保留铺满窗口
old_esc = "  if (e.key === 'Escape' && $('#sec-chart').classList.contains('fs-on')) toggleFs(false);\n"
if old_esc in s:
    s = s.replace(old_esc, "")
old_fc = "document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement && $('#sec-chart').classList.contains('fs-on')) toggleFs(false); else fsFit(); });"
if old_fc in s:
    s = s.replace(old_fc, "document.addEventListener('fullscreenchange', fsFit);   // 2026-10-17 截图工具按Esc会退出浏览器原生全屏，但图表继续铺满窗口；退出只用按钮或F键")
s = s.replace("title=\"全屏看K线(快捷键 F，Esc 退出)\"", "title=\"全屏看K线(快捷键 F 进入/退出)\"")
open("app.js", "w", encoding="utf-8").write(s)
h = open("index.html", encoding="utf-8").read()
h = h.replace("title=\"全屏看K线(快捷键 F，Esc 退出)\"", "title=\"全屏看K线(F 键或按钮进入/退出；截图按Esc不会退出)\"")
h = re.sub(r"(app\.js\?v=)([\w]+)", lambda m: m.group(1) + m.group(2) + "m", h)
open("index.html", "w", encoding="utf-8").write(h)
b = open("best.js", encoding="utf-8").read()
old_esc = "  if (e.key === 'Escape' && document.querySelector('#review .rv').classList.contains('fs-on')) kFs(false);\n"
if old_esc in b:
    b = b.replace(old_esc, "")
old_fc = "document.addEventListener('fullscreenchange', () => { if (!document.fullscreenElement && document.querySelector('#review .rv').classList.contains('fs-on')) kFs(false); });"
if old_fc in b:
    b = b.replace(old_fc, "// 2026-10-17 截图工具按Esc会退出浏览器原生全屏，但复盘K线继续铺满窗口；退出只用按钮或F键")
open("best.js", "w", encoding="utf-8").write(b)
bh = open("best.html", encoding="utf-8").read()
bh = re.sub(r"(best\.js\?v=)([\w]+)", lambda m: m.group(1) + m.group(2) + "m", bh)
open("best.html", "w", encoding="utf-8").write(bh)
print("esc removed app:", "e.key === 'Escape'" not in s, "best:", "e.key === 'Escape'" not in b)

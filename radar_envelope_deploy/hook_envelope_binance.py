# -*- coding: utf-8 -*-
"""把radar_envelope.RadarEnvelopeMixin接进币安supervisor(B/E共用)。用法: hook_envelope_binance.py <position_supervisor_binance.py>"""
import sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()


def sub(old, new):
    global s
    assert s.count(old) == 1, (s.count(old), old[:70])
    s = s.replace(old, new)


sub("from pipeline_bridge import PipelineBridgeMixin\n",
    "from pipeline_bridge import PipelineBridgeMixin\nfrom radar_envelope import RadarEnvelopeMixin\n")

sub("class PositionSupervisorBinance(PipelineBridgeMixin, RadarReentryMixin):",
    "class PositionSupervisorBinance(RadarEnvelopeMixin, PipelineBridgeMixin, RadarReentryMixin):")

# 1) 预防：_apply_breath_stop_tick末尾，所有叠加层之后、写阶段状态之前
sub('''        was_phase = phase
        self.breakeven_phase = new_phase
''', '''        # 2026-10-04 雷达最小距离包线(见radar_envelope.py)：叠加层提议的止损比"峰值±2.0×ATR(4H)"
        # 更紧就夹回包线，且不比本tick开始时的账本更宽(棘轮不变)。
        try:
            _prop = float(self.current_sl or 0)
            _fin = self._clamp_to_radar_envelope(_prop, float(cur or 0))
            if _prop > 0 and abs(float(_fin) - _prop) > 1e-9:
                logger.warning(
                    f"🛡️ [{self.symbol}] 雷达包线夹回：叠加层提议@{_prop:.2f} → @{float(_fin):.2f} "
                    f"(峰值{float(self.best_price or 0):.2f}±2.0×ATR4H)"
                )
                self.current_sl = float(_fin)
                self.tv_sl = float(_fin)
        except Exception as e:
            logger.debug(f"[{self.symbol}] 雷达包线夹取跳过: {e}")
        was_phase = phase
        self.breakeven_phase = new_phase
''')

# 2) 预防：_sync_exchange_stop写盘前(覆盖4H预警/加仓保本等直接带radar_sl的调用)
sub('''        target = round(float(target), 2)
        # 账本理论止损；盘口挂单再 ±0.3 执行缓冲（多减空加）
        ledger_target = target
''', '''        target = round(float(target), 2)
        try:
            _clamped = self._clamp_to_radar_envelope(
                target, float(getattr(self, "current_sl", 0) or 0),
            )
            if abs(float(_clamped) - target) > 1e-9:
                logger.warning(
                    f"🛡️ [{self.symbol}] 雷达包线夹回(写盘前)：{target:.2f} → {float(_clamped):.2f} | {reason}"
                )
                target = round(float(_clamped), 2)
        except Exception as e:
            logger.debug(f"[{self.symbol}] 写盘前包线夹取跳过: {e}")
        # 账本理论止损；盘口挂单再 ±0.3 执行缓冲（多减空加）
        ledger_target = target
''')

# 3) 自愈：哨兵每轮调用(内部自带120s节流)
sub('''        self._check_pyramid_add()
        self._check_4h_reversal_tighten()
''', '''        self._check_pyramid_add()
        self._check_4h_reversal_tighten()
        try:
            self._radar_envelope_audit()
        except Exception as _e:
            logger.error(f"[{self.symbol}] 雷达包线自检异常: {_e}", exc_info=True)
''')
open(p, "w", encoding="utf-8").write(s)
print("hooked")

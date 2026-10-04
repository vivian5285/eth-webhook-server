# -*- coding: utf-8 -*-
"""把radar_envelope.CoinWRadarEnvelopeMixin接进CoinW。用法: hook_envelope_coinw.py <breath_stop.py> <position_supervisor_coinw.py>"""
import sys
bs_path, sup_path = sys.argv[1], sys.argv[2]

bs = open(bs_path, encoding="utf-8").read()
old = "    def mark_activated(self, entry_price: float, tp2_price: float,"
assert bs.count(old) == 1
bs = bs.replace(old, '''    def rebase_to(self, sl: float) -> None:
        """包线自愈用：显式把雷达内部current_sl放松/重置到sl(受控纠错入口，不经过只收不放的棘轮)。"""
        with self._lock:
            s = float(sl or 0)
            if s > 0 and self._state.activated:
                self._state.current_sl = s
                self._state.last_update = time.time()

''' + old)
open(bs_path, "w", encoding="utf-8").write(bs)

s = open(sup_path, encoding="utf-8").read()


def sub(old, new):
    global s
    assert s.count(old) == 1, (s.count(old), old[:70])
    s = s.replace(old, new)


sub("class PositionSupervisorCoinW:", "class PositionSupervisorCoinW(CoinWRadarEnvelopeMixin):")
# 导入放在第一个顶层import块之后
sub("import os\nimport time\nimport json\n", "import os\nimport time\nimport json\nfrom radar_envelope import (\n    CoinWRadarEnvelopeMixin, clamp_to_envelope, is_inside_envelope,\n)\n")

# 预防：_update_radar_sl里先夹包线
sub('''            _side = str(self.pipeline.data.get("side") or "").upper()
            _ns = float(new_sl or 0)
            _st = self.radar.get_state()''', '''            _side = str(self.pipeline.data.get("side") or "").upper()
            _ns = float(new_sl or 0)
            _env = self._radar_envelope_stop()
            if _env and _ns > 0:
                _c = clamp_to_envelope(_side, _ns, _env, 0.0)
                if abs(_c - _ns) > 1e-9:
                    logger.warning(
                        f"[{self.symbol}] 雷达包线夹回：提议{_ns:.2f} → {_c:.2f}(峰值±2.0×ATR4H，包线{_env:.2f})"
                    )
                    _ns = _c
                    new_sl = _c
            _st = self.radar.get_state()''')

# 自愈：verify里先把雷达内部越界的状态rebase到包线
sub('''        if not st.activated or want <= 0 or side not in ("LONG", "SHORT") or not pid:
            return
''', '''        if not st.activated or want <= 0 or side not in ("LONG", "SHORT") or not pid:
            return
        env = self._radar_envelope_stop()
        env_tol = max((env or 0) * 0.0003, 0.3)
        if env and is_inside_envelope(side, want, env, env_tol):
            logger.warning(
                f"[{self.symbol}] 雷达包线自愈：雷达内部止损{want:.2f}比包线{env:.2f}更紧 → 内部rebase到包线"
            )
            self.radar.rebase_to(env)
            want = env
''')
sub('''        eff = _effective()
        if not _stale(eff):
            self._radar_verify_fail = 0
            return
''', '''        eff = _effective()
        _inside = bool(env and eff > 0 and is_inside_envelope(side, eff, env, env_tol))
        if _inside:
            _hk = f"{side}|{round(float(self.pipeline.data.get('entry') or 0), 1)}"
            if getattr(self, "_radar_env_heal_key", None) != _hk:
                self._radar_env_heal_key, self._radar_env_heal_count = _hk, 0
            if (
                int(getattr(self, "_radar_env_heal_count", 0) or 0) >= 3
                or now - float(getattr(self, "_radar_env_heal_ts", 0) or 0) < 600
            ):
                _inside = False
            else:
                self._radar_env_heal_ts = now
                self._radar_env_heal_count = int(getattr(self, "_radar_env_heal_count", 0) or 0) + 1
                self._safe_alert(
                    f"[{self.symbol}] 雷达包线自愈#{self._radar_env_heal_count}：交易所止损{eff:.2f}比包线{env:.2f}更紧，重推到{want:.2f}"
                )
        if not _stale(eff) and not _inside:
            self._radar_verify_fail = 0
            return
''')
open(sup_path, "w", encoding="utf-8").write(s)
print("hooked")

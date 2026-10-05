# -*- coding: utf-8 -*-
"""
雷达最小距离包线 + 自检自愈(币安B/E共用，SNDK/ZEC)。2026-10-04。

起因：放开SNDK雷达后，"TV已平仓滞涨刹车"在B系统(没有TV心跳)误触发，把雷达止损从1780一把
收到现价附近5~7点，而"只收不放"的棘轮又不允许系统自己放松，账户持有人也没法手动改单——
出错后无人能修。长久之路是让系统自己核查、自己发现异常、自己撤单重挂。

包线(依据：SNDK/ZEC真实4H事件研究——任何一根4H之后24h逆向极值中位数≈1×ATR(4H)、
P75≈1.7~2.4×ATR(4H)；离价格0.3×ATR(4H)的止损被打到73~92%)：
  雷达止损离"峰值价"(best_price，不是现价——反弹不会改变峰值，所以不会来回抖动)不得近于
  RADAR_ENVELOPE_ATR4H_MULT×ATR(4H)：
      空头：止损 >= best_price + mult×ATR4H     多头：止损 <= best_price - mult×ATR4H
  只约束"比包线更紧"的提议；比包线更宽的(如保本位)原样放行。包线比TV硬止损还远时不启用
  (硬止损已经更近，包线没有意义)。ATR(4H)取不到时不启用(不阻断正常运行)。

两层：
 1) 预防：_clamp_to_radar_envelope由_apply_breath_stop_tick末尾和_sync_exchange_stop调用，
    任何叠加层(刹车/锁盈/4H预警…)提议的止损比包线更紧就夹回包线(且不比提议前的账本更宽)。
 2) 自愈：_radar_envelope_audit每RADAR_ENVELOPE_AUDIT_SEC秒核对交易所上最紧的雷达腿，比包线
    更紧就撤雷达腿(TV硬止损保留)、清棘轮记账、用_sync_exchange_stop重挂到包线、告警。
    频率上限(RADAR_ENVELOPE_HEAL_MIN_GAP_SEC间隔、每笔持仓最多RADAR_ENVELOPE_HEAL_MAX次)，
    超限只告警，避免与其它逻辑来回打架。
"""
import logging
import time

logger = logging.getLogger(__name__)

RADAR_ENVELOPE_SYMBOLS = {"SNDKUSDT", "ZECUSDT"}
RADAR_ENVELOPE_ATR4H_MULT = 2.0
RADAR_ENVELOPE_AUDIT_SEC = 120
RADAR_ENVELOPE_HEAL_MIN_GAP_SEC = 600
RADAR_ENVELOPE_HEAL_MAX = 3
RADAR_ENVELOPE_ATR_CACHE_SEC = 180


def envelope_level(side, best_price, atr4h, hard_px=0.0, mult=RADAR_ENVELOPE_ATR4H_MULT):
    """纯函数：包线价位；不适用返回None。"""
    side = str(side or "").upper()
    best, atr4h, hard_px = float(best_price or 0), float(atr4h or 0), float(hard_px or 0)
    if side not in ("LONG", "SHORT") or best <= 0 or atr4h <= 0:
        return None
    d = mult * atr4h
    if side == "SHORT":
        env = best + d
        if hard_px > 0 and env >= hard_px:
            return None
    else:
        env = best - d
        if hard_px > 0 and env <= hard_px:
            return None
    return round(env, 2)


def clamp_to_envelope(side, proposed, env, prior=0.0):
    """纯函数：比包线更紧的提议夹回包线；且不比prior(提议前账本)更宽。"""
    side = str(side or "").upper()
    proposed, env, prior = float(proposed or 0), env, float(prior or 0)
    if not env or proposed <= 0 or side not in ("LONG", "SHORT"):
        return proposed
    if side == "SHORT":
        out = max(proposed, env)
        if prior > 0:
            out = min(out, prior)
    else:
        out = min(proposed, env)
        if prior > 0:
            out = max(out, prior)
    return round(out, 2)


def is_inside_envelope(side, stop, env, tol):
    """纯函数：stop是否比包线更紧(越界)。"""
    side = str(side or "").upper()
    if not env or float(stop or 0) <= 0:
        return False
    if side == "SHORT":
        return float(stop) < env - tol
    if side == "LONG":
        return float(stop) > env + tol
    return False


class RadarEnvelopeMixin:
    def _radar_env_curr_px(self):
        import binance_client as _bc
        return float(_bc.binance_client.get_current_price(self.symbol) or 0)

    def _radar_env_atr4h(self):
        now = time.time()
        c = getattr(self, "_radar_env_atr_cache", None)
        if c and now - c[0] < RADAR_ENVELOPE_ATR_CACHE_SEC:
            return c[1]
        val = 0.0
        try:
            from strategy_engine import klines as _k
            from strategy_engine import indicators as _i
            raw = _k.get_bars(self.symbol, "4h", limit=60)
            # indicators.atr_series要求字典K线(bar["h"])，传列表会TypeError被下面吞掉→包线静默失效
            atr = _i.atr_series(list(raw or []), 14)
            val = float(atr[-1]) if atr else 0.0
        except Exception as e:
            logger.debug(f"[{self.symbol}] 包线ATR(4H)取数跳过: {e}")
        if val > 0:
            self._radar_env_atr_cache = (now, val)
        return val

    def _radar_envelope_stop(self):
        if self.symbol not in RADAR_ENVELOPE_SYMBOLS:
            return None
        return envelope_level(
            self.current_side, getattr(self, "best_price", 0), self._radar_env_atr4h(),
            hard_px=float(self._frozen_hard_px() or 0),
        )

    def _clamp_to_radar_envelope(self, proposed, prior=0.0):
        env = self._radar_envelope_stop()
        if not env:
            return proposed
        return clamp_to_envelope(self.current_side, proposed, env, prior)

    def _radar_envelope_audit(self):
        """定时自检自愈，见文件头说明。"""
        if self.symbol not in RADAR_ENVELOPE_SYMBOLS:
            return
        now = time.time()
        if now - float(getattr(self, "_radar_env_audit_ts", 0) or 0) < RADAR_ENVELOPE_AUDIT_SEC:
            return
        self._radar_env_audit_ts = now
        if getattr(self, "_open_in_progress", False) or getattr(self, "_defense_align_in_progress", False):
            return
        side = str(self.current_side or "").strip().upper()
        live_qty = float(self._resolve_live_qty(float(getattr(self, "watched_qty", 0) or 0)) or 0)
        if side not in ("LONG", "SHORT") or live_qty <= 0 or self._radar_is_dormant():
            return
        env = self._radar_envelope_stop()
        if not env:
            return
        live = self._radar_live_stops()
        if not live:
            return  # 查询失败或没有雷达腿：交给既有补挂逻辑，不在这里处理
        exch = min(float(p) for p in live) if side == "SHORT" else max(float(p) for p in live)
        tol = max(env * 0.0003, 0.3)
        if not is_inside_envelope(side, exch, env, tol):
            return
        key = f"{side}|{round(float(getattr(self, 'watched_entry', 0) or 0), 1)}"
        if getattr(self, "_radar_env_heal_key", None) != key:
            self._radar_env_heal_key = key
            self._radar_env_heal_count = 0
        n = int(getattr(self, "_radar_env_heal_count", 0) or 0)
        curr_px = self._radar_env_curr_px()
        if curr_px <= 0 or (side == "SHORT" and env <= curr_px) or (side == "LONG" and env >= curr_px):
            return  # 包线已经被价格越过：重挂会立刻触发，交给其它逻辑
        if n >= RADAR_ENVELOPE_HEAL_MAX:
            if now - float(getattr(self, "_radar_env_alert_ts", 0) or 0) > 1800:
                self._radar_env_alert_ts = now
                self._radar_env_notify(
                    f"{side} 雷达止损@{exch:.2f}在包线@{env:.2f}内，已自愈{n}次仍被收紧，停止自动处理，请核查",
                    "紧急",
                )
            return
        if now - float(getattr(self, "_radar_env_heal_ts", 0) or 0) < RADAR_ENVELOPE_HEAL_MIN_GAP_SEC:
            return
        self._radar_env_heal_ts = now
        self._radar_env_heal_count = n + 1
        cur = float(getattr(self, "current_sl", 0) or 0)
        logger.warning(
            f"🔧 [{self.symbol}] 雷达包线自愈#{n + 1}：交易所雷达止损@{exch:.2f}比包线@{env:.2f}更紧"
            f"(峰值{float(getattr(self, 'best_price', 0) or 0):.2f} 现价{curr_px:.2f}) → 撤雷达腿、重挂到包线"
        )
        self._purge_all_protective_stops(preserve_hard=True)
        time.sleep(0.5)
        self.current_sl = 0.0
        self._last_applied_exchange_sl = 0.0
        res = None
        try:
            res = self._sync_exchange_stop(
                live_qty=live_qty, radar_sl=env, reason="雷达包线自愈", force=True,
            )
        except Exception as e:
            logger.error(f"[{self.symbol}] 包线自愈挂单异常: {e}", exc_info=True)
        ok = bool(res and res.get("ok"))
        if not ok:
            try:
                ok = bool(self._ensure_radar_sl(env, live_qty))
            except Exception as e:
                logger.error(f"[{self.symbol}] 包线自愈补挂异常: {e}", exc_info=True)
        self.current_sl = float(env)
        self.tv_sl = float(env)
        try:
            self._save_state()
        except Exception:
            pass
        self._radar_env_notify(
            f"{side} 雷达止损 {exch:.2f}(账本{cur:.2f}) → {env:.2f}，现价{curr_px:.2f}，TV硬止损保留；"
            f"挂单{'成功' if ok else '失败，请核查'}",
            "提示" if ok else "紧急",
        )

    def _radar_env_notify(self, detail, level):
        try:
            import dingtalk
            self._call_dingtalk(
                dingtalk.report_system_alert,
                title=f"雷达包线自愈 [{self.symbol}]", detail=detail, level=level,
            )
        except Exception:
            pass


class CoinWRadarEnvelopeMixin:
    """CoinW版：雷达内部状态在self.radar(BreathStop)，交易所止损用set_sl_tp/get_tp_sl_info。
    预防=_update_radar_sl里夹包线；自愈=_verify_radar_sl_on_exchange里发现内部/交易所比包线更紧时
    把雷达内部rebase到包线并重推(带频率上限)。"""
    RADAR_ENVELOPE_SYMBOLS_COINW = {"SNDK", "ZEC"}

    def _radar_env_atr4h(self):
        now = time.time()
        c = getattr(self, "_radar_env_atr_cache", None)
        if c and now - c[0] < RADAR_ENVELOPE_ATR_CACHE_SEC:
            return c[1]
        val = 0.0
        try:
            from strategy_engine import indicators as _i
            bars = self._get_risk_klines(240, 60)
            # 兼容两种形态：字典K线直接用；列表K线[t,o,h,l,c,v]转字典(atr_series只认字典)
            dbars = [
                b if isinstance(b, dict)
                else {"t": b[0], "o": b[1], "h": b[2], "l": b[3], "c": b[4], "v": b[5]}
                for b in (bars or [])
            ]
            atr = _i.atr_series(dbars, 14) if dbars else []
            val = float(atr[-1]) if atr else 0.0
        except Exception as e:
            logger.debug(f"[{self.symbol}] 包线ATR(4H)取数跳过: {e}")
        if val > 0:
            self._radar_env_atr_cache = (now, val)
        return val

    def _radar_envelope_stop(self):
        if self.symbol not in self.RADAR_ENVELOPE_SYMBOLS_COINW:
            return None
        st = self.radar.get_state()
        side = str(self.pipeline.data.get("side") or "").upper()
        return envelope_level(
            side, float(st.best_price or 0), self._radar_env_atr4h(),
            hard_px=float(self.pipeline.data.get("hard_sl_px") or 0),
        )

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-13：币安B系统("综合硬止损"体系)专属呼吸档 + XPT新品种上线的回归
测试。验证：
  1) get_breath_profile的system参数不传/传"A" => 跟改动前行为完全一致，
     A系统零影响(这是本次改动最重要的安全底线)。
  2) system="B"时，登记过B系统专属档的品种(BNB/XPD/OPENAI/XAU)返回B档，
     没登记的品种(SNDK等)正确落回A档，不报错。
  3) symbol_config.resolve_binance_symbol按SMART_HARD_STOP_ENABLED环境
     变量自动选档：账户在B模式时BNB/XAU等品种自动拿到B档；默认/A模式
     拿到跟改动前完全相同的A档。
  4) XPTUSDT symbol元数据/别名解析正确，且breath_profile非空。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import breath_profiles  # noqa: E402
import symbol_config  # noqa: E402
import reentry_profiles  # noqa: E402


class TestBreathProfileSystemParam(unittest.TestCase):
    def test_default_system_unchanged_for_a_symbols(self):
        """不传system或传"A" => 跟改动前的get_breath_profile(symbol, exchange)完全一样。"""
        for sym in ("BNBUSDT", "XPDUSDT", "OPENAIUSDT", "XAUUSDT", "SNDKUSDT", "ETHUSDT"):
            no_arg = breath_profiles.get_breath_profile(sym, "binance")
            explicit_a = breath_profiles.get_breath_profile(sym, "binance", system="A")
            self.assertEqual(no_arg, explicit_a, f"{sym}: 缺省应等价于system='A'")

    def test_b_system_returns_dedicated_profile_where_registered(self):
        cases = {
            "BNBUSDT": breath_profiles.BREATH_BNB_B,
            "XPDUSDT": breath_profiles.BREATH_XPD_B,
            "OPENAIUSDT": breath_profiles.BREATH_OPENAI_B,
            "XAUUSDT": breath_profiles.BREATH_XAU_B,
            "XPTUSDT": breath_profiles.BREATH_XPT,
        }
        for sym, expected in cases.items():
            got = breath_profiles.get_breath_profile(sym, "binance", system="B")
            self.assertEqual(got, expected, f"{sym}: B系统应返回专属档")

    def test_b_system_falls_back_to_a_when_not_registered(self):
        """SNDK没有B系统专属档(A本来就是75分钟)，system="B"应该原样落回A档。"""
        a_profile = breath_profiles.get_breath_profile("SNDKUSDT", "binance", system="A")
        b_profile = breath_profiles.get_breath_profile("SNDKUSDT", "binance", system="B")
        self.assertEqual(a_profile, b_profile)

    def test_b_profiles_are_actually_different_from_a_for_period_changed_symbols(self):
        """确认B档真的是"不同的数字"，不是复制粘贴A档换了个名字——这几个
        品种A/B周期不同(BNB/XPD/OPENAI/XAU)，数值理应不同。"""
        for sym in ("BNBUSDT", "XPDUSDT", "OPENAIUSDT", "XAUUSDT"):
            a = breath_profiles.get_breath_profile(sym, "binance", system="A")
            b = breath_profiles.get_breath_profile(sym, "binance", system="B")
            self.assertNotEqual(
                (a["breath_tp12"], a["breath_tp23"], a["max_mult"]),
                (b["breath_tp12"], b["breath_tp23"], b["max_mult"]),
                f"{sym}: A/B周期不同，呼吸系数不该完全一样",
            )

    def test_deepcoin_exchange_ignores_system_param(self):
        """deepcoin分支不受影响：不管system传什么，行为跟改动前一样。"""
        a = breath_profiles.get_breath_profile("XAU-USDT-SWAP", "deepcoin", system="A")
        b = breath_profiles.get_breath_profile("XAU-USDT-SWAP", "deepcoin", system="B")
        self.assertEqual(a, b)
        self.assertEqual(a, breath_profiles.BREATH_XAU)


class TestResolveBinanceSymbolModeAware(unittest.TestCase):
    def setUp(self):
        self._orig = os.environ.get("SMART_HARD_STOP_ENABLED")

    def tearDown(self):
        if self._orig is None:
            os.environ.pop("SMART_HARD_STOP_ENABLED", None)
        else:
            os.environ["SMART_HARD_STOP_ENABLED"] = self._orig

    def test_a_mode_default_matches_pre_change_behavior(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)
        meta = symbol_config.resolve_binance_symbol("BNBUSDT.P")
        self.assertEqual(meta["breath_profile"], breath_profiles.BREATH_BNB)

    def test_b_mode_flag_selects_b_profile(self):
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"
        meta = symbol_config.resolve_binance_symbol("BNBUSDT.P")
        self.assertEqual(meta["breath_profile"], breath_profiles.BREATH_BNB_B)

    def test_b_mode_various_truthy_values(self):
        for val in ("1", "true", "True", "yes"):
            os.environ["SMART_HARD_STOP_ENABLED"] = val
            meta = symbol_config.resolve_binance_symbol("XAUUSDT")
            self.assertEqual(meta["breath_profile"], breath_profiles.BREATH_XAU_B, f"flag={val!r}")

    def test_a_mode_falsy_values(self):
        for val in ("0", "false", ""):
            os.environ["SMART_HARD_STOP_ENABLED"] = val
            meta = symbol_config.resolve_binance_symbol("XAUUSDT")
            self.assertEqual(meta["breath_profile"], breath_profiles.BREATH_XAU, f"flag={val!r}")

    def test_b_mode_sndk_unchanged(self):
        """SNDK没有B档，B模式下也应该跟A模式返回相同的breath_profile。"""
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"
        meta_b = symbol_config.resolve_binance_symbol("SNDKUSDT")
        os.environ["SMART_HARD_STOP_ENABLED"] = "0"
        meta_a = symbol_config.resolve_binance_symbol("SNDKUSDT")
        self.assertEqual(meta_a["breath_profile"], meta_b["breath_profile"])


class TestXptOnboarding(unittest.TestCase):
    def test_meta_present(self):
        self.assertIn("XPTUSDT", symbol_config.BINANCE_SYMBOL_META)
        meta = symbol_config.BINANCE_SYMBOL_META["XPTUSDT"]
        self.assertEqual(meta["qty_step"], 0.001)
        self.assertEqual(meta["min_qty"], 0.001)
        self.assertEqual(meta["price_precision"], 2)

    def test_alias_resolution(self):
        for raw in ("XPT", "XPTUSDT", "XPTUSDT.P", "BINANCE:XPTUSDT.P"):
            meta = symbol_config.resolve_binance_symbol(raw, default="")
            self.assertEqual(meta["symbol"], "XPTUSDT", f"raw={raw!r}")

    def test_breath_profile_nonempty_both_modes(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)
        meta_a = symbol_config.resolve_binance_symbol("XPTUSDT")
        self.assertTrue(meta_a["breath_profile"])
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"
        meta_b = symbol_config.resolve_binance_symbol("XPTUSDT")
        self.assertTrue(meta_b["breath_profile"])
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)

    def test_extract_symbol_from_payload_recognizes_xpt(self):
        got = symbol_config.extract_symbol_from_payload({"symbol": "XPTUSDT.P"})
        self.assertEqual(got, "XPTUSDT.P")


class TestActiveBinanceSymbolsModeAware(unittest.TestCase):
    def setUp(self):
        self._orig_flag = os.environ.get("SMART_HARD_STOP_ENABLED")
        self._orig_a = os.environ.get("BINANCE_SYMBOLS")
        self._orig_b = os.environ.get("BINANCE_SYMBOLS_B")

    def tearDown(self):
        for key, val in (
            ("SMART_HARD_STOP_ENABLED", self._orig_flag),
            ("BINANCE_SYMBOLS", self._orig_a),
            ("BINANCE_SYMBOLS_B", self._orig_b),
        ):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_a_mode_default_unchanged(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)
        os.environ.pop("BINANCE_SYMBOLS", None)
        os.environ.pop("BINANCE_SYMBOLS_B", None)
        self.assertEqual(
            symbol_config.active_binance_symbols(),
            ["BNBUSDT", "OPENAIUSDT", "XPDUSDT", "SNDKUSDT"],
        )

    def test_b_mode_default_independent_of_a(self):
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"
        os.environ.pop("BINANCE_SYMBOLS_B", None)
        os.environ["BINANCE_SYMBOLS"] = "ETHUSDT"  # A的清单改了，不该影响B
        self.assertEqual(
            symbol_config.active_binance_symbols(),
            ["BNBUSDT", "XPDUSDT", "SNDKUSDT", "OPENAIUSDT"],
        )

    def test_b_mode_reads_its_own_env_key(self):
        os.environ["SMART_HARD_STOP_ENABLED"] = "1"
        os.environ["BINANCE_SYMBOLS_B"] = "XAUUSDT,XPTUSDT"
        self.assertEqual(symbol_config.active_binance_symbols(), ["XAUUSDT", "XPTUSDT"])

    def test_a_mode_unaffected_by_b_env_key(self):
        os.environ.pop("SMART_HARD_STOP_ENABLED", None)
        os.environ["BINANCE_SYMBOLS_B"] = "XAUUSDT,XPTUSDT"
        os.environ["BINANCE_SYMBOLS"] = "BNBUSDT"
        self.assertEqual(symbol_config.active_binance_symbols(), ["BNBUSDT"])


class TestXptReentryProfileDedicated(unittest.TestCase):
    """XPT不能静默退回REENTRY_ETH——这个坑2026-08-11(BNB/ZEC/BCH)和
    2026-08-15(XMR/SNDK/PAXG)已经踩过两次，这次新增品种必须显式登记。"""

    def test_registered_not_eth_fallback(self):
        profile = reentry_profiles.get_reentry_profile("XPTUSDT")
        self.assertEqual(profile["name"], "XPT")
        self.assertIsNot(profile["tiers"], reentry_profiles.REENTRY_ETH["tiers"])

    def test_tv_tf_is_45m(self):
        profile = reentry_profiles.get_reentry_profile("XPTUSDT")
        self.assertEqual(profile["tv_tf"], "45m")
        self.assertEqual(profile["tv_tf_sec"], 2700)

    def test_tiers_scaled_from_eth_baseline(self):
        """scale=sqrt(2.71/2.37)≈1.0693，三档=1.00/1.20/1.40×scale。"""
        tiers = reentry_profiles.get_reentry_profile("XPTUSDT")["tiers"]
        self.assertEqual(len(tiers), 3)
        self.assertAlmostEqual(tiers[0]["step_trigger_atr"], 1.07, delta=0.01)
        self.assertAlmostEqual(tiers[1]["step_trigger_atr"], 1.28, delta=0.01)
        self.assertAlmostEqual(tiers[2]["step_trigger_atr"], 1.50, delta=0.01)
        for t in tiers:
            self.assertAlmostEqual(t["step_advance_atr"], t["step_trigger_atr"] * 0.5, delta=0.01)

    def test_zone_and_window_bars(self):
        profile = reentry_profiles.get_reentry_profile("XPTUSDT")
        self.assertEqual(profile["reentry_zone_atr"], 0.5)
        self.assertEqual(profile["reentry_window_bars"], 4)

    def test_enabled(self):
        self.assertTrue(reentry_profiles.reentry_enabled("XPTUSDT"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram + 钉钉双通道路由单测（不打真实 API）。"""
import os
import time
import unittest
from unittest.mock import patch, MagicMock


class TestNotifyDual(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
        os.environ.setdefault("TELEGRAM_CHAT_ID", "12345")
        # 避免误连真实钉钉
        os.environ.setdefault("DINGTALK_WEBHOOK", "https://example.invalid/ding")
        import dingtalk
        dingtalk.TELEGRAM_BOT_TOKEN = "test-token"
        dingtalk.TELEGRAM_CHAT_ID = "12345"
        dingtalk.DINGTALK_WEBHOOK = "https://example.invalid/ding"
        dingtalk.WECHAT_WEBHOOK = ""
        dingtalk.DINGTALK_BATCH_DISABLE = True
        cls.dt = dingtalk

    def test_send_telegram_retries(self):
        calls = {"n": 0}

        def fake_post(url, json=None, timeout=8):
            calls["n"] += 1
            r = MagicMock()
            if calls["n"] < 3:
                r.status_code = 500
                r.text = "fail"
                r.json.return_value = {"ok": False}
            else:
                r.status_code = 200
                r.text = '{"ok":true}'
                r.json.return_value = {"ok": True}
            return r

        with patch.object(self.dt.requests, "post", side_effect=fake_post):
            with patch.object(self.dt.time, "sleep", return_value=None):
                ok = self.dt.send_telegram("hello retry")
        self.assertTrue(ok)
        self.assertEqual(calls["n"], 3)

    def test_level1_skips_dingtalk(self):
        ding_calls = []
        tg_texts = []

        def fake_tg(text):
            tg_texts.append(text)
            return True

        def fake_ding(title, md):
            ding_calls.append(title)
            return True

        with patch.object(self.dt, "_fire_telegram_async", side_effect=lambda t: fake_tg(t)):
            with patch.object(self.dt, "_post_with_retry", side_effect=fake_ding):
                self.dt.send_alert(
                    "📈 [ETHUSDT] 开仓 LONG",
                    {"开仓": "LONG"},
                    level=self.dt.NOTIFY_LEVEL_ALL,
                    immediate=True,
                )
        self.assertEqual(len(tg_texts), 1)
        self.assertEqual(ding_calls, [])

    def test_level2_hits_both(self):
        ding_calls = []
        tg_texts = []

        with patch.object(self.dt, "_fire_telegram_async", side_effect=lambda t: tg_texts.append(t)):
            with patch.object(self.dt, "_post_with_retry", side_effect=lambda t, m: ding_calls.append(t) or True):
                self.dt.send_alert(
                    "🚨 硬止损触发",
                    {"说明": "test"},
                    level=self.dt.NOTIFY_LEVEL_CRITICAL,
                    immediate=True,
                )
        self.assertEqual(len(tg_texts), 1)
        self.assertEqual(ding_calls, ["【币安单系统】🚨 硬止损触发"])
        self.assertIn("币安单系统", tg_texts[0])

    def test_hard_sl_close_is_critical(self):
        levels = []

        def capture(title, data, header=None, immediate=False, level=1, _tg_text=None):
            levels.append(level)

        with patch.object(self.dt, "send_alert", side_effect=capture):
            self.dt.report_supervisor_close(
                reason="HARD_SL",
                close_type=self.dt.CLOSE_TYPE_HARD_SL,
                exit_source="vps_hard_sl",
                tv_side="LONG",
            )
        self.assertEqual(levels[-1], self.dt.NOTIFY_LEVEL_CRITICAL)

    def test_open_is_level1(self):
        levels = []

        def capture(title, data, header=None, immediate=False, level=1, _tg_text=None):
            levels.append(level)

        with patch.object(self.dt, "send_alert", side_effect=capture):
            self.dt.report_supervisor_open(
                side="LONG", entry_price=2000, tv_price=2000, qty=0.1,
                tp_pxs=[1, 2, 3], atr=10, regime=2, symbol="ETHUSDT",
            )
        self.assertEqual(levels[-1], self.dt.NOTIFY_LEVEL_ALL)

    def test_reentry_system_alert_is_level1(self):
        levels = []

        def capture(title, data, header=None, immediate=False, level=1, _tg_text=None):
            levels.append(level)

        with patch.object(self.dt, "send_alert", side_effect=capture):
            self.dt.report_system_alert(
                "智能再入场限价已挂", "detail", level="提示",
            )
        self.assertEqual(levels[-1], self.dt.NOTIFY_LEVEL_ALL)

    def test_reload_notify_config(self):
        st = self.dt.reload_notify_config()
        self.assertIn("telegram_configured", st)
        self.assertIn("dingtalk_configured", st)

    def test_brand_title_prefix(self):
        self.assertTrue(self.dt._brand_title("开仓 LONG").startswith("【币安单系统】"))
        # idempotent
        once = self.dt._brand_title("开仓")
        self.assertEqual(self.dt._brand_title(once), once)
        tg = self.dt._build_tg_text("开仓 LONG", {"方向": "LONG"})
        self.assertIn("币安单系统", tg)


if __name__ == "__main__":
    unittest.main()

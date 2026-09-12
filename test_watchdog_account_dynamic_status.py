#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-09-12新增：watchdog/check.py::_account_is_running() 的回归测试。

背景：原来D账户是否被监控靠check.py里写死的一行"monitor": False，宝贝
要求控制面板能随时启停任意账户(不只D)，不能每次手动改watchdog文件才
不误报。改成实时查`systemctl is-active`，这里验证这个判定函数本身的
边界行为：真在跑/真停了/systemctl调用失败三种情况。

不碰任何真实账户，全程mock subprocess.run。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchdog"))

sys.modules.setdefault("dingtalk_notify", MagicMock())

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "watchdog_check",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchdog", "check.py"),
)
wc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wc)


def _mk_completed(stdout: str, returncode: int = 0):
    m = MagicMock()
    m.stdout = stdout.encode("utf-8")
    m.returncode = returncode
    return m


class TestAccountIsRunning(unittest.TestCase):
    def test_active_service_returns_true(self):
        with patch("subprocess.run", return_value=_mk_completed("active\n")):
            self.assertTrue(wc._account_is_running({"service": "binanceC-engine"}))

    def test_inactive_service_returns_false(self):
        """systemctl is-active对已停止服务返回码是3(非0)，stdout仍是
        'inactive'——不能被简单的returncode判断吞掉。"""
        with patch("subprocess.run", return_value=_mk_completed("inactive\n", returncode=3)):
            self.assertFalse(wc._account_is_running({"service": "binanceC-engine"}))

    def test_failed_service_returns_false(self):
        with patch("subprocess.run", return_value=_mk_completed("failed\n", returncode=3)):
            self.assertFalse(wc._account_is_running({"service": "binanceB-engine"}))

    def test_systemctl_query_error_fails_safe_true(self):
        """查询本身失败(比如systemctl不存在/超时)时保守当作"在跑"处理，
        走原有健康检查路径，不能因为查询失败反而放行本该报的异常。"""
        with patch("subprocess.run", side_effect=RuntimeError("boom")):
            self.assertTrue(wc._account_is_running({"service": "binanceB-engine"}))

    def test_monitored_accounts_no_longer_excludes_d_statically(self):
        """2026-09-12改动核心：MONITORED_ACCOUNTS不再基于写死的monitor
        字段过滤，D账户必须仍在清单里(是否检查改成运行时动态判断)。"""
        names = [a["name"] for a in wc.MONITORED_ACCOUNTS]
        self.assertIn("D", names)
        self.assertEqual(sorted(names), ["B", "C", "D", "E"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

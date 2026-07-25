#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全链路日志四级通道（规格第七部分）：
  OPS   — 挂单/改撤/成交等操作
  STATE — 状态快照与变更
  ALERT — 异常/超时/对账不一致
  AUDIT — 资金与风控相关审计
底层仍写入 TimedRotatingFileHandler（保留 30 天）。
"""
from __future__ import annotations

import logging

_log = logging.getLogger("Brain")


def ops(msg: str, *args) -> None:
    _log.info("[OPS] " + str(msg), *args)


def state(msg: str, *args) -> None:
    _log.info("[STATE] " + str(msg), *args)


def alert(msg: str, *args) -> None:
    _log.error("[ALERT] " + str(msg), *args)


def audit(msg: str, *args) -> None:
    _log.info("[AUDIT] " + str(msg), *args)

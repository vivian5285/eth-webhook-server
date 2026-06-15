#!/usr/bin/env python3
import os
import signal
import logging
import queue
import threading
import time
from flask import Flask, request, jsonify
from dotenv import load_dotenv  # 新增：导入加载器

# ==================== 核心：显式加载 .env ====================
# 强制读取项目根目录下的 .env 文件
basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, '.env'))

# 验证环境变量是否加载成功
if not os.getenv("BINANCE_API_KEY") or not os.getenv("BINANCE_API_SECRET"):
    raise ValueError("⚠️ 严重错误：未能在 .env 文件中找到 API 密钥！")

# 内部模块导入
from position_supervisor_binance import position_supervisor
from tp_monitor import tp_monitor
from order_executor import order_executor
from risk_manager import risk_manager
from position_manager import position_manager

# ... 后续逻辑保持不变 ...

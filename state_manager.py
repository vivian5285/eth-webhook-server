#!/usr/bin/env python3
# state_manager.py（完整最终版 - 状态持久化管理，已修复权限路径）
import json
import os
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# 修改为项目目录下的 data 文件夹，避免 root 权限冲突
STATE_FILE = "/home/trading/eth-webhook-server/data/trading_state.json"


class StateManager:
    def __init__(self):
        self.state_file = STATE_FILE
        self._ensure_directory_and_file()

    def _ensure_directory_and_file(self):
        """确保目录和文件存在"""
        directory = os.path.dirname(self.state_file)
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)

        if not os.path.exists(self.state_file):
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
            logger.info(f"[StateManager] 状态文件已创建: {self.state_file}")

    def save_state(self, data: Dict[str, Any]):
        """保存状态到 state.json"""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug(f"[StateManager] 状态已保存")
        except Exception as e:
            logger.error(f"[StateManager] 保存状态失败: {e}")

    def load_state(self) -> Optional[Dict[str, Any]]:
        """从 state.json 加载状态"""
        try:
            if not os.path.exists(self.state_file):
                return None

            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if data and isinstance(data, dict) and data.get("is_monitoring"):
                return data
            return None
        except json.JSONDecodeError:
            logger.warning("[StateManager] state.json 文件损坏，已重置")
            self.clear_state()
            return None
        except Exception as e:
            logger.error(f"[StateManager] 加载状态失败: {e}")
            return None

    def clear_state(self):
        """清空状态（平仓或新信号时调用）"""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
            logger.info("[StateManager] 状态已清

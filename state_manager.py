#!/usr/bin/env python3
# state_manager.py（状态持久化）
import json
import os
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)
STATE_FILE = "/home/workdir/artifacts/trading_state.json"


class StateManager:
    def __init__(self):
        self.state_file = STATE_FILE
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        if not os.path.exists(self.state_file):
            with open(self.state_file, "w") as f:
                json.dump({}, f)

    def save_state(self, data: dict):
        try:
            with open(self.state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"[StateManager] 保存状态失败: {e}")

    def load_state(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
            return data if data else None
        except:
            return None

    def clear_state(self):
        try:
            with open(self.state_file, "w") as f:
                json.dump({}, f)
        except Exception as e:
            logger.error(f"[StateManager] 清空状态失败: {e}")


state_manager = StateManager()

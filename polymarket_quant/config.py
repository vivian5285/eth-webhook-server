#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""polymarket_quant 配置加载。禁止 print/repr 整个模块——私钥等敏感字段一律脱敏展示。"""
import os
from dataclasses import dataclass, field, fields
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(_ENV_PATH)

_SECRET_FIELDS = {
    "polygon_wallet_private_key",
    "telegram_bot_token",
}


def _env_bool(name, default=False):
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


@dataclass(frozen=True)
class Config:
    dry_run: bool = field(default_factory=lambda: _env_bool("DRY_RUN", True))

    # Polymarket CLOB
    clob_host: str = field(
        default_factory=lambda: os.getenv("POLYMARKET_CLOB_HOST", "https://clob.polymarket.com")
    )
    ws_url: str = field(
        default_factory=lambda: os.getenv("POLYMARKET_WS_URL", "wss://ws-live-data.polymarket.com")
    )
    polygon_wallet_private_key: str = field(
        default_factory=lambda: os.getenv("POLYGON_WALLET_PRIVATE_KEY", "")
    )
    polygon_funder_address: str = field(
        default_factory=lambda: os.getenv("POLYGON_FUNDER_ADDRESS", "")
    )
    chain_id: int = field(default_factory=lambda: _env_int("CHAIN_ID", 137))
    signature_type: int = field(default_factory=lambda: _env_int("SIGNATURE_TYPE", 1))

    # 策略范围
    symbols: str = field(default_factory=lambda: os.getenv("SYMBOLS", "BTC").strip())
    window_minutes: int = field(default_factory=lambda: _env_int("WINDOW_MINUTES", 5))

    # 信号阈值
    min_edge_before_fees_pct: float = field(
        default_factory=lambda: _env_float("MIN_EDGE_BEFORE_FEES_PCT", 0.05)
    )
    min_edge_after_fees_pct: float = field(
        default_factory=lambda: _env_float("MIN_EDGE_AFTER_FEES_PCT", 0.02)
    )
    maker_time_buffer_sec: float = field(
        default_factory=lambda: _env_float("MAKER_TIME_BUFFER_SEC", 120)
    )
    early_exit_edge_reversal_pct: float = field(
        default_factory=lambda: _env_float("EARLY_EXIT_EDGE_REVERSAL_PCT", 0.05)
    )
    feed_stale_timeout_sec: float = field(
        default_factory=lambda: _env_float("FEED_STALE_TIMEOUT_SEC", 15)
    )

    # 风控层
    max_stake_per_window_usd: float = field(
        default_factory=lambda: _env_float("MAX_STAKE_PER_WINDOW_USD", 10)
    )
    daily_loss_cap_usd: float = field(default_factory=lambda: _env_float("DAILY_LOSS_CAP_USD", 50))
    max_concurrent_windows: int = field(default_factory=lambda: _env_int("MAX_CONCURRENT_WINDOWS", 1))
    max_trades_per_hour: int = field(default_factory=lambda: _env_int("MAX_TRADES_PER_HOUR", 6))
    max_trades_per_day: int = field(default_factory=lambda: _env_int("MAX_TRADES_PER_DAY", 50))
    kill_switch_auto_reset_daily: bool = field(
        default_factory=lambda: _env_bool("KILL_SWITCH_AUTO_RESET_DAILY", True)
    )

    # Telegram（复用主程序现有渠道变量名）
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", "").strip())
    telegram_parse_mode: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_PARSE_MODE", "HTML").strip() or "HTML"
    )
    telegram_retry_max: int = field(default_factory=lambda: _env_int("TELEGRAM_RETRY_MAX", 3))
    telegram_retry_sec: float = field(default_factory=lambda: _env_float("TELEGRAM_RETRY_SEC", 3))
    heartbeat_interval_hours: float = field(
        default_factory=lambda: _env_float("HEARTBEAT_INTERVAL_HOURS", 4)
    )
    label: str = field(default_factory=lambda: os.getenv("POLYMARKET_LABEL", "Poly量化").strip())

    db_path: str = field(
        default_factory=lambda: os.path.join(BASE_DIR, "data", "polymarket_quant.db")
    )

    def redacted_dict(self):
        out = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if f.name in _SECRET_FIELDS:
                out[f.name] = "已配置" if val else "未配置"
            else:
                out[f.name] = val
        return out

    def secret_values(self):
        return {getattr(self, name) for name in _SECRET_FIELDS if getattr(self, name)}


def load_config():
    return Config()


if __name__ == "__main__":
    cfg = load_config()
    import json
    print(json.dumps(cfg.redacted_dict(), indent=2, ensure_ascii=False, default=str))

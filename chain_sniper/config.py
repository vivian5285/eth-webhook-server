#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chain_sniper 配置加载。禁止 print/repr 整个模块——私钥等敏感字段一律脱敏展示。"""
import os
from dataclasses import dataclass, field, fields
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(_ENV_PATH)

_SECRET_FIELDS = {
    "bsc_hot_wallet_private_key",
    "solana_hot_wallet_private_key",
    "helius_api_key",
    "moralis_api_key",
    "birdeye_api_key",
    "bscscan_api_key",
    "goplus_api_key",
    "goplus_app_secret",
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
    # 模式
    dry_run: bool = field(default_factory=lambda: _env_bool("DRY_RUN", True))

    # 链 RPC
    bsc_rpc_url: str = field(default_factory=lambda: os.getenv("BSC_RPC_URL", ""))
    solana_rpc_url: str = field(default_factory=lambda: os.getenv("SOLANA_RPC_URL", ""))
    # BSC 聪明钱触发用的免费公共节点（逗号分隔，会依次轮换/兜底）。留空用内置默认。
    bsc_rpc_urls: str = field(default_factory=lambda: os.getenv("BSC_RPC_URLS", ""))
    bsc_poll_interval_sec: float = field(default_factory=lambda: _env_float("BSC_POLL_INTERVAL_SEC", 60))
    # 每轮 eth_getLogs 回看的最大区块数。BSC ~3s/块，60s 一轮≈20 块，给到 45
    # 留点余量。太大会让免费 RPC 的 getLogs 慢/超时。
    bsc_max_blocks_per_poll: int = field(default_factory=lambda: _env_int("BSC_MAX_BLOCKS_PER_POLL", 45))
    helius_api_key: str = field(default_factory=lambda: os.getenv("HELIUS_API_KEY", ""))
    jupiter_api_url: str = field(
        default_factory=lambda: os.getenv("JUPITER_API_URL", "https://quote-api.jup.ag/v6")
    )
    use_jito_bundles: bool = field(default_factory=lambda: _env_bool("USE_JITO_BUNDLES", True))
    jito_block_engine_url: str = field(default_factory=lambda: os.getenv("JITO_BLOCK_ENGINE_URL", ""))

    # 钱包活动追踪
    # Moralis：2026-09-04 实测其免费额度已停用(401 "Free usage is paused")，
    # 保留字段但不再依赖它。BSC 聪明钱触发改走 keyless 免费 BSC RPC 轮询。
    moralis_api_key: str = field(default_factory=lambda: os.getenv("MORALIS_API_KEY", ""))
    # Birdeye：免费 30K CU/月，SOL+BSC 都覆盖。用途：发现器 v2 的
    # top_traders(带真实已实现盈亏) + gainers-losers + new_listing +
    # trader/txs 逐钱包成交流水。批量价格接口(multi_price)免费档锁着，
    # 真相账本的价格快照走 keyless 的 DexScreener。
    birdeye_api_key: str = field(default_factory=lambda: os.getenv("BIRDEYE_API_KEY", "").strip())
    # BscScan/Etherscan-V2 免费 key（可选，即时申请）：设了就走干净的
    # account/tokentx 逐钱包轮询；不设则 BscAdapter 用 keyless 扫块兜底(慢)。
    bscscan_api_key: str = field(default_factory=lambda: os.getenv("BSCSCAN_API_KEY", "").strip())

    # 安全过滤
    goplus_api_key: str = field(default_factory=lambda: os.getenv("GOPLUS_API_KEY", ""))
    goplus_app_secret: str = field(default_factory=lambda: os.getenv("GOPLUS_APP_SECRET", ""))

    # 热钱包私钥（VPS环境变量注入，绝不进代码/日志/Telegram）
    bsc_hot_wallet_private_key: str = field(
        default_factory=lambda: os.getenv("BSC_HOT_WALLET_PRIVATE_KEY", "")
    )
    solana_hot_wallet_private_key: str = field(
        default_factory=lambda: os.getenv("SOLANA_HOT_WALLET_PRIVATE_KEY", "")
    )

    # 风控层
    max_position_size_usd: float = field(default_factory=lambda: _env_float("MAX_POSITION_SIZE_USD", 25))
    daily_loss_cap_usd: float = field(default_factory=lambda: _env_float("DAILY_LOSS_CAP_USD", 100))
    max_concurrent_positions: int = field(default_factory=lambda: _env_int("MAX_CONCURRENT_POSITIONS", 3))
    token_cooldown_sec: int = field(default_factory=lambda: _env_int("TOKEN_COOLDOWN_SEC", 3600))
    kill_switch_auto_reset_daily: bool = field(
        default_factory=lambda: _env_bool("KILL_SWITCH_AUTO_RESET_DAILY", True)
    )

    # 轮询节流（2026-09-04：Helius 免费额度扛不住 15s/轮 × N 钱包的完整历史
    # 拉取，日志刷屏 429、事件表 3 周没进新数据。改成更慢的轮询 + 每钱包错峰，
    # webhook 是根治方案但要宝贝在 Helius 后台配 URL，这里先让轮询别再 429。）
    signal_loop_interval_sec: float = field(default_factory=lambda: _env_float("SIGNAL_LOOP_INTERVAL_SEC", 45))
    wallet_poll_interval_sec: float = field(default_factory=lambda: _env_float("WALLET_POLL_INTERVAL_SEC", 90))
    wallet_poll_gap_sec: float = field(default_factory=lambda: _env_float("WALLET_POLL_GAP_SEC", 1.2))

    # 自动发现的聪明钱名单（discovery/wallet_finder.py 产出的 discovered_wallets.json）
    # 里 score >= 此阈值的才灌进 watched_wallets 表一起监控。
    discovery_score_min: float = field(default_factory=lambda: _env_float("DISCOVERY_SCORE_MIN", 45))

    # 信号阈值
    min_smart_wallet_buys: int = field(default_factory=lambda: _env_int("MIN_SMART_WALLET_BUYS", 1))
    sm_window_sec: int = field(default_factory=lambda: _env_int("SM_WINDOW_SEC", 300))
    required_confirmations: int = field(default_factory=lambda: _env_int("REQUIRED_CONFIRMATIONS", 3))
    min_unique_buyers_5min: int = field(default_factory=lambda: _env_int("MIN_UNIQUE_BUYERS_5MIN", 10))
    max_top10_holder_pct: float = field(default_factory=lambda: _env_float("MAX_TOP10_HOLDER_PCT", 50))
    max_buy_tax_pct: float = field(default_factory=lambda: _env_float("MAX_BUY_TAX_PCT", 10))
    max_sell_tax_pct: float = field(default_factory=lambda: _env_float("MAX_SELL_TAX_PCT", 10))
    require_lp_locked: bool = field(default_factory=lambda: _env_bool("REQUIRE_LP_LOCKED", True))
    reject_if_mint_authority_active: bool = field(
        default_factory=lambda: _env_bool("REJECT_IF_MINT_AUTHORITY_ACTIVE", True)
    )
    reject_if_honeypot: bool = field(default_factory=lambda: _env_bool("REJECT_IF_HONEYPOT", True))

    # 离场管理
    exit_poll_interval_sec: float = field(default_factory=lambda: _env_float("EXIT_POLL_INTERVAL_SEC", 4))
    tp_pct: float = field(default_factory=lambda: _env_float("TP_PCT", 0.30))
    sl_pct: float = field(default_factory=lambda: _env_float("SL_PCT", 0.15))

    # ─── 模型B：代币动量模拟盘（2026-09-06）────────────────────────────────
    # 不靠聪明钱钱包，直接盯"趋势/热门代币"（GeckoTerminal + DexScreener，
    # 全免费无 key），满足动量/热度/流动性/新鲜度 + 多源交叉 + GoPlus 安全
    # 才模拟买入。信号/持仓/离场/真相账本全部复用模型A那套。目前只做 BSC
    # (Solana 已经有钱包跟单模型在跑)。
    token_mom_enabled: bool = field(default_factory=lambda: _env_bool("TOKEN_MOM_ENABLED", False))
    token_mom_chains: str = field(default_factory=lambda: os.getenv("TOKEN_MOM_CHAINS", "bsc"))
    token_mom_poll_sec: float = field(default_factory=lambda: _env_float("TOKEN_MOM_POLL_SEC", 180))
    token_mom_min_sources: int = field(default_factory=lambda: _env_int("TOKEN_MOM_MIN_SOURCES", 1))
    token_mom_min_gain_pct: float = field(default_factory=lambda: _env_float("TOKEN_MOM_MIN_GAIN_PCT", 20))
    token_mom_max_gain_pct: float = field(default_factory=lambda: _env_float("TOKEN_MOM_MAX_GAIN_PCT", 250))
    token_mom_min_pc_h1: float = field(default_factory=lambda: _env_float("TOKEN_MOM_MIN_PC_H1", 0))
    token_mom_min_vol_usd: float = field(default_factory=lambda: _env_float("TOKEN_MOM_MIN_VOL_USD", 200000))
    token_mom_min_liq_usd: float = field(default_factory=lambda: _env_float("TOKEN_MOM_MIN_LIQ_USD", 80000))
    token_mom_max_pair_age_h: float = field(default_factory=lambda: _env_float("TOKEN_MOM_MAX_PAIR_AGE_H", 168))
    # 动量交易自己的止盈止损（比模型A宽——追热点波动更大）。留空则回退用
    # tp_pct/sl_pct。exit_manager 的最长持仓 sim_max_hold_hours 两个模型共用。
    token_mom_tp_pct: float = field(default_factory=lambda: _env_float("TOKEN_MOM_TP_PCT", 0.50))
    token_mom_sl_pct: float = field(default_factory=lambda: _env_float("TOKEN_MOM_SL_PCT", 0.25))

    @property
    def token_mom_chains_list(self):
        return [c.strip().lower() for c in str(self.token_mom_chains or "").split(",") if c.strip()]

    # 真相账本 / 复盘（2026-09-04）——每个信号之后追踪该币真实价格轨迹，
    # 这是"跑一段时间总结"唯一的数据来源。价格快照走 keyless DexScreener。
    outcome_poll_sec: float = field(default_factory=lambda: _env_float("OUTCOME_POLL_SEC", 600))
    outcome_track_hours: float = field(default_factory=lambda: _env_float("OUTCOME_TRACK_HOURS", 36))
    outcome_rug_liq_frac: float = field(default_factory=lambda: _env_float("OUTCOME_RUG_LIQ_FRAC", 0.15))

    # 模拟成交折价——不加的话总结出来的收益是假的（确认后跟单本来就慢+有滑点）。
    sim_latency_slip_pct: float = field(default_factory=lambda: _env_float("SIM_LATENCY_SLIP_PCT", 0.015))
    sim_size_slip_k: float = field(default_factory=lambda: _env_float("SIM_SIZE_SLIP_K", 0.5))
    sim_max_slip_pct: float = field(default_factory=lambda: _env_float("SIM_MAX_SLIP_PCT", 0.06))
    sim_max_hold_hours: float = field(default_factory=lambda: _env_float("SIM_MAX_HOLD_HOURS", 24))

    # 通知（复用主程序现有 Telegram 渠道变量名）
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
    label: str = field(default_factory=lambda: os.getenv("CHAINSNIPER_LABEL", "链上狙击").strip())

    # 数据库
    db_path: str = field(
        default_factory=lambda: os.path.join(BASE_DIR, "data", "chain_sniper.db")
    )

    def redacted_dict(self):
        """安全的配置摘要——敏感字段只展示是否已配置，绝不展示原值。"""
        out = {}
        for f in fields(self):
            val = getattr(self, f.name)
            if f.name in _SECRET_FIELDS:
                out[f.name] = "已配置" if val else "未配置"
            else:
                out[f.name] = val
        return out

    def secret_values(self):
        """所有敏感字段的原始值集合，供 notifier 的脱敏过滤器比对使用。绝不用于日志/打印。"""
        return {getattr(self, name) for name in _SECRET_FIELDS if getattr(self, name)}


def load_config():
    return Config()


if __name__ == "__main__":
    cfg = load_config()
    import json
    print(json.dumps(cfg.redacted_dict(), indent=2, ensure_ascii=False, default=str))

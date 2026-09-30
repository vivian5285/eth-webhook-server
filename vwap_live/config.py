#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vwap_live 配置——独立项目，跟 chain_sniper/llm_trader/polymarket_quant
同一个"standalone project"模式，不依赖 strategy_engine 的运行时（跨 VPS，
故意不共享代码，自成一体，逻辑照抄验证过的公式）。

真金专用的两道闸门（缺一不可，都要显式打开才会下真实单）：
  1. binance_api_key/secret 非空
  2. LIVE_TRADING=true
没打开的时候整条链路照常跑（读实时行情、判信号、写决策日志），只是
executor 遇到该下单的时候打日志"（观察模式，不下单）"，不碰交易所。
"""
import os
from dataclasses import dataclass, field, fields

_SECRET = {"binance_api_key", "binance_api_secret", "telegram_bot_token"}


def _load_env_file(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                    v = v[1:-1]
                os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass
    except Exception:
        pass


_load_env_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


def _b(name, default=False):
    v = os.getenv(name)
    return default if v is None else str(v).strip().lower() in ("1", "true", "yes", "on")


def _f(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return float(default)


def _i(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return int(default)


# 2026-09-12：宝贝选定"只跑持续跑赢的那批"——擂台上 vwap_mean_reversion
# 实测 sumR 持续为正、样本 18-40 笔的品种（截至今天）。跟 LINK/BNB/META/
# ASML/ANTHROPIC/HYPE/MU/SKHYNIX(持续跑输)明确分开。
# 2026-09-12 中途一度去掉 OPENAIUSDT：只读核实发现 binanceC 的雷达引擎
# 自己也在跑它(BINANCE_SYMBOLS=OPENAIUSDT,XPDUSDT,SNDKUSDT)，怕两套独立
# 程序共享保证金/杠杆设置。宝贝随后把这个账户的 TV 那条线暂停了、专门
# 腾出来只跑这一套均值回归，冲突已消失，OPENAIUSDT 恢复。
_DEFAULT_SYMBOLS = "GSUSDT,ETHUSDT,PAXGUSDT,UNIUSDT,XMRUSDT,BCHUSDT,LITEUSDT,BTCUSDT,OPENAIUSDT,SOLUSDT"

# 硬顶：不管 .env 怎么配，杠杆永远不会超过这个数——低杠杆是宝贝的固定
# 原则(project_low_leverage_philosophy)，这里做成代码层面的物理限制，
# 不是靠"记得别改"。2026-09-12：宝贝要求测试阶段降到 3x，硬顶同步下调
# （原来是 5x）。
_LEVERAGE_HARD_CAP = 3.0


@dataclass(frozen=True)
class Config:
    live_trading: bool = field(default_factory=lambda: _b("LIVE_TRADING", False))
    binance_api_key: str = field(default_factory=lambda: os.getenv("BINANCE_API_KEY", "").strip())
    binance_api_secret: str = field(default_factory=lambda: os.getenv("BINANCE_API_SECRET", "").strip())
    binance_base_url: str = field(default_factory=lambda: os.getenv("BINANCE_BASE_URL", "https://fapi.binance.com").strip())

    symbols: str = field(default_factory=lambda: os.getenv("SYMBOLS", _DEFAULT_SYMBOLS))
    timeframe: str = field(default_factory=lambda: os.getenv("TIMEFRAME", "15m"))
    loop_check_sec: float = field(default_factory=lambda: _f("LOOP_CHECK_SEC", 300))

    leverage: float = field(default_factory=lambda: min(_f("LEVERAGE", 3.0), _LEVERAGE_HARD_CAP))

    # 2026-09-12：改成"本金百分比"仓位管理，照抄这台 VPS 上真实 TV 币安引擎
    # 的 RISK20 思路(webhook_parser.py::compute_fixed_order_qty，只读核实
    # 过公式)——risk_capital = 权益 × position_size_pct，每次开仓前**实时
    # 查一次账户真实余额**再算，不是开服务那一刻锁死的固定美元数。原来的
    # POSITION_SIZE_USD(固定$50) 不再使用。
    position_size_pct: float = field(default_factory=lambda: _f("POSITION_SIZE_PCT", 0.06))
    # 组合层面总敞口上限——照抄真实引擎的 check_total_notional_cap 思路，
    # 但数值给得远比真实账户的 18x 保守(那边是跑了大半年、有呼吸止损/
    # 分批止盈全套管理的成熟系统；这套今天才第一次接真钱，没有track
    # record，先给窄一点)。所有持仓(不只 vwap_live 自己开的，账户上只要
    # 有仓位就算)名义总值不能超过 权益 × 这个倍数。
    max_total_notional_mult: float = field(default_factory=lambda: _f("MAX_TOTAL_NOTIONAL_MULT", 2.0))
    # 没有 key(纯观察、还没接真实账户)时用这个当"假想权益"算示例仓位，
    # 只是让日志/通知里的数字看着有意义，不影响真实下单(那时候本来就不
    # 会下单)。
    fallback_equity_usd: float = field(default_factory=lambda: _f("FALLBACK_EQUITY_USD", 200.0))

    max_concurrent: int = field(default_factory=lambda: _i("MAX_CONCURRENT", 5))
    daily_loss_limit_usd: float = field(default_factory=lambda: _f("DAILY_LOSS_LIMIT_USD", 30.0))
    max_hold_hours: float = field(default_factory=lambda: _f("MAX_HOLD_HOURS", 48))

    # ── vwap_mean_reversion 原版参数（跟 strategy_engine 2026-09-12 修复后
    # 的 min_session_bars=24 保持一致，不是这里另拍一个数）──
    n_std: float = field(default_factory=lambda: _f("N_STD", 2.0))
    exit_band: float = field(default_factory=lambda: _f("EXIT_BAND", 0.3))
    min_session_bars: int = field(default_factory=lambda: _i("MIN_SESSION_BARS", 24))
    adx_len: int = field(default_factory=lambda: _i("ADX_LEN", 14))
    adx_max: float = field(default_factory=lambda: _f("ADX_MAX", 25.0))
    atr_len: int = field(default_factory=lambda: _i("ATR_LEN", 14))
    atr_stop_mult: float = field(default_factory=lambda: _f("ATR_STOP_MULT", 1.5))

    # 2026-09-12宝贝发现：PAXGUSDT这笔仓位止损止盈距离近到离谱(入场价的
    # 0.01%~0.04%)——查了全部10个品种才知道不是个例，PAXG/GS这两个黄金/
    # 代币化股票品种日内波动率天生比加密货币低一个量级，套加密货币标定
    # 的ATR倍数，算出来的止损止盈距离比手续费成本(真实成交commission
    # 核对过双边约0.10%)还窄——哪怕方向判断完全正确、真等到止盈，赚的都
    # 不够付手续费，止损也窄到跟买卖价差一个量级，随时被噪音打掉，不是
    # 被行情打的。round_trip_fee_pct×min_edge_over_fee_mult=经济性门槛：
    # 这次信号理论最大盈利距离(price到VWAP)/止损距离两者都必须至少覆盖
    # 3倍手续费成本，覆盖不了直接跳过这次信号——不开仓，等哪天这个品种
    # 波动率自己放大到够用了再说。这是信号质量门槛(跟ADX过滤同一类"这
    # 压根不是真机会")，不是仓位管理层面的妥协，不违反"信号来了必须开仓"
    # 那条规则(那条针对的是组合总敞口不能拦截单个有效信号)。只加在这套
    # 真账户代码里，不动擂台那份(擂台要保持不含手续费假设的纯策略对比)。
    round_trip_fee_pct: float = field(default_factory=lambda: _f("ROUND_TRIP_FEE_PCT", 0.10))
    min_edge_over_fee_mult: float = field(default_factory=lambda: _f("MIN_EDGE_OVER_FEE_MULT", 3.0))

    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", "").strip())
    label: str = field(default_factory=lambda: os.getenv("LABEL", "VWAP真账户测试").strip())

    db_path: str = field(default_factory=lambda: os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "vwap_live.db"))

    @property
    def symbol_list(self):
        return [s.strip().upper() for s in str(self.symbols or "").split(",") if s.strip()]

    @property
    def api_key_present(self):
        return bool(self.binance_api_key and self.binance_api_secret)

    @property
    def is_armed(self):
        """两道闸门都打开才算真正武装——少一个都只是观察模式。"""
        return self.api_key_present and self.live_trading

    def redacted(self):
        out = {}
        for f in fields(self):
            v = getattr(self, f.name)
            out[f.name] = ("已配置" if v else "未配置") if f.name in _SECRET else v
        out["is_armed"] = self.is_armed
        return out


def load_config():
    return Config()


if __name__ == "__main__":
    import json
    print(json.dumps(load_config().redacted(), indent=2, ensure_ascii=False, default=str))

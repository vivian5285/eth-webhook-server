#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CoinW仓位监控器 - v16.22.1-coinw-init
唯一生产大脑

流水线阶段：
IDLE -> SIGNAL_RECEIVED -> PENDING_CLEAR -> CLEARED ->
ENTRY_SUBMITTED -> ENTRY_CONFIRMED -> ORDERS_PLACED ->
VERIFIED -> REPORTED -> MONITORING
"""

import os
import math
import time
import json
from radar_envelope import (
    CoinWRadarEnvelopeMixin, clamp_to_envelope, is_inside_envelope,
)

# 2026-10-02：TV 自动 SNDK 单仓最多本金一倍，停用原先的发散加仓。
# 保留下面的参数供历史模拟复现，实盘入口由空名单硬禁用。
# 2026-10-03重新打开(事故后曾被清空禁用整个加仓功能，根因其实是
# 进场阶梯竞态，不是加仓本身的问题，已单独修复见_cancel_and_confirm_
# filled)：
PYRAMID_ADD_SYMBOLS = {"SNDK", "ZEC"}
# 2026-10-03改成按品种查表：SNDK是7/25，ZEC是8/30(都不是Pine源码
# input默认值能直接信的——界面会覆盖源码文本里的默认值，两个品种
# 都踩过这个坑，一个一个让宝贝口头确认过)。
# 2026-10-03修复：key从"SNDKUSDT"/"ZECUSDT"改成"SNDK"/"ZEC"——CoinW
# 这边self.symbol是不带USDT后缀的短代码(跟PYRAMID_ADD_SYMBOLS/
# PYRAMID_ADD_DIVERGENCE_THRESHOLDS一致)，旧key从来没真正命中过，
# ZEC一直在用.get()的默认值(=SNDK的数字)而不是自己的8/30。
PYRAMID_ADD_FAST_LEN = {"SNDK": 7, "ZEC": 8}
PYRAMID_ADD_SLOW_LEN = {"SNDK": 25, "ZEC": 30}
PYRAMID_ADD_ATR_LEN = 14
# 2026-10-03改成按品种查表(原来是单值91，只给SNDK用；ZEC是1小时=60
# 分钟，不能共用)。
PYRAMID_ADD_RISK_KLINE_INTERVAL_MIN = {"SNDK": 91, "ZEC": 60}
PYRAMID_ADD_KLINE_LIMIT = 60
PYRAMID_ADD_FRACTION = 0.5
PYRAMID_ADD_DIVERGENCE_THRESHOLDS = {
    "SNDK": [1.535, 2.109],
    "ZEC": [1.293, 1.872],
}
# 2026-10-04：与币安B/E对齐，加仓后不收紧止损(回测：收紧有害)，见币安同名常量注释
PYRAMID_ADD_RATCHET_ATR_MULT = 0.0
# 2026-10-04：加仓触发方式。"milestone"=浮盈里程碑(开仓ATR的3倍、6倍各加一次)；"divergence"=旧的均线发散度触发(回滚用)。
PYRAMID_ADD_MODE = "milestone"
PYRAMID_ADD_MILESTONE_ATR = [3.0, 6.0]

# 2026-10-04新增：本金等值品种"自检自补"——开仓后若实际持仓不足本金权益(1倍)，
# 系统自己补齐。触发场景：开仓当下基数偏小(旧版用可用余额)、限价只成交一部分、
# 交易所部分成交等。防误补：只在开仓后90秒~12小时内、从没加过金字塔仓、每笔仓位
# 最多补2次、缺口≥10%且≥15U、价格离止损不近于0.5×ATR、保证金够才补。
TOPUP_SYMBOLS = {"SNDK", "ZEC"}
TOPUP_CHECK_INTERVAL_SEC = 120
TOPUP_SETTLE_SEC = 90
TOPUP_MAX_POS_AGE_SEC = 12 * 3600
TOPUP_MAX_ATTEMPTS = 2
TOPUP_MIN_SHORTFALL_FRAC = 0.03
TOPUP_MIN_SHORTFALL_USD = 8.0
# 开仓成交后的"校准冲刺"：刚开仓的前5分钟，每30秒用交易所REST核对一次(最多~10次)，
# 开仓部分成交/取整偏小能在几十秒内补齐，而不是等常规的120秒周期。
TOPUP_BURST_WINDOW_SEC = 300
TOPUP_BURST_INTERVAL_SEC = 30
TOPUP_BURST_SETTLE_SEC = 20

# 2026-10-04新增：仓位/止损完整性审计(与币安同口径，以交易所REST为准；IP限流冷却期
# 整轮跳过)。检查：①超仓②雷达已武装却交易所无止损③止损方向错④止损张数覆盖不足
# (CoinW止损记录按closePiece绑定，仓位张数>止损覆盖张数=有一段裸仓→自动重推止损)
# ⑤账本止损被放松。其中只有④自动修复，其余只告警；同类告警1小时内一次。
INTEGRITY_AUDIT_INTERVAL_SEC = 300
INTEGRITY_ALERT_COOLDOWN_SEC = 3600
INTEGRITY_OVERSIZE_FRAC = 0.15
ADD_QTY_TOLERANCE_FRAC = 0.25
PYRAMID_ADD_CHECK_INTERVAL_SEC = 60
# 2026-10-03新增：量能否决(跟币安B系统同一套校准，CoinW的K线本来就
# 统一拉币安公开数据，不是独立数据源)——只否决不设门槛。
PYRAMID_ADD_VOLUME_VETO_WINDOW = 20
PYRAMID_ADD_VOLUME_VETO_FRAC = {"SNDK": 0.09, "ZEC": 0.40}

# 2026-10-03新增：组合层面加仓总闸(SNDK+ZEC合计不超账户权益3.0倍)
PORTFOLIO_PYRAMID_COMBINED_CAP_FRAC = 3.0

# 2026-10-03新增：4H快线反转预警
# 2026-10-03修复：之前写死7(SNDK自己的快线长度)，ZEC实际是8，改成字典。
REVERSAL_4H_FAST_LEN = {"SNDK": 7, "ZEC": 8}
REVERSAL_4H_ATR_LEN = 14
REVERSAL_4H_KLINE_LIMIT = 60
REVERSAL_4H_DOJI_BODY_FRAC = 0.1
# 2026-10-04按真实4H事件研究重新校准(见coinw_radar_robust说明)：破线收到离现价
# 2.0×ATR(4H)(原0.3倍被打到73~92%等于离场)；十字星无信息量，关掉。
REVERSAL_4H_BREAK_ATR_MULT = 2.0
REVERSAL_4H_DOJI_ATR_MULT = 2.0
REVERSAL_4H_DOJI_ENABLED = False
REVERSAL_4H_CHECK_INTERVAL_SEC = 180
# 2026-10-04新增：雷达止损交易所对账周期(秒)
RADAR_EXCHANGE_VERIFY_SEC = 120
import logging
import threading
from typing import Optional, Dict, Any

from dotenv import load_dotenv
load_dotenv()

from pipeline_ledger import PipelineLedger, get_pipeline, Phase, Role
from symbol_config import ACTIVE_SYMBOLS

# 版本号
COINW_SUPERVISOR_VERSION = "v16.22.1-coinw-init"

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] CoinW: %(message)s'
)
logger = logging.getLogger(__name__)

# 交易暂停标志
trading_paused = False

# ==================== 开仓滑点阶梯（被动限价 -> 可成交限价 -> 市价兜底）====================
# 目标：TV 信号到达后先挂对我们最有利的限价（不劣于 TV 价），给一小段时间做 maker；
# 没成交就让利到滑点上限的可成交限价；仍不成交才市价兜底。现价逆向跑过头则放弃这次信号。
ENTRY_LADDER_ENABLED = os.getenv("ENTRY_LADDER", "1").lower() in ("1", "true", "yes")
ENTRY_PASSIVE_SEC = float(os.getenv("ENTRY_PASSIVE_SEC", "25"))      # 阶段1：TV 价被动限价，做 maker
ENTRY_MARKETABLE_SEC = float(os.getenv("ENTRY_MARKETABLE_SEC", "45"))  # 阶段2：让利到滑点上限的可成交限价
ENTRY_SLIP_CAP_PCT = float(os.getenv("ENTRY_SLIP_CAP_PCT", "0.0012"))  # 阶段2 最多相对 TV 价让 0.12%
ENTRY_ABORT_PCT = float(os.getenv("ENTRY_ABORT_PCT", "0.004"))        # 现价比 TV 价逆向跑超 0.4% -> 放弃信号
ENTRY_POLL_SEC = float(os.getenv("ENTRY_POLL_SEC", "3"))
# 强趋势(tier=2)立即进入：跳过①被动限价（单边突破里等回踩=纯负收益，对齐币安
# _try_better_than_tv_limit_entry 的 tier>=2 跳过），直接可成交限价(短窗)+市价。
ENTRY_STRONG_IMMEDIATE = os.getenv("ENTRY_STRONG_IMMEDIATE", "1").lower() in ("1", "true", "yes")
ENTRY_STRONG_MARKETABLE_SEC = float(os.getenv("ENTRY_STRONG_MARKETABLE_SEC", "8"))

# ==================== 心跳催单 / 启动恢复 / 裸单守护 ====================
HEARTBEAT_CATCHUP_ENABLED = os.getenv("HEARTBEAT_CATCHUP", "1").lower() in ("1", "true", "yes")
CATCHUP_MAX_DRIFT_PCT = float(os.getenv("CATCHUP_MAX_DRIFT_PCT", "0.006"))  # 现价偏离心跳entry超此 -> 不补开
HEARTBEAT_FLAT_CLOSE = os.getenv("HEARTBEAT_FLAT_CLOSE", "0").lower() in ("1", "true", "yes")  # TV空/VPS有仓是否自动补平
CATCHUP_BLOCK_SEC = float(os.getenv("CATCHUP_BLOCK_SEC", "1800"))  # 手动平/TV平后多久内不被心跳补开
STARTUP_RECOVERY_ENABLED = os.getenv("STARTUP_RECOVERY", "1").lower() in ("1", "true", "yes")
NAKED_GUARD_ENABLED = os.getenv("NAKED_GUARD", "1").lower() in ("1", "true", "yes")
# 2026-09-21新增：recover_on_start()查"这个仓位有没有已有止损"偶尔跟
# 交易所侧有竞态(实盘复现4次，XAU/XPD都出现过)，查不到不代表真裸仓，
# 重试几次再下结论——见recover_on_start()内注释。
STARTUP_SL_DETECT_RETRIES = int(os.getenv("STARTUP_SL_DETECT_RETRIES", "3"))
STARTUP_SL_DETECT_RETRY_SEC = float(os.getenv("STARTUP_SL_DETECT_RETRY_SEC", "0.6"))

# 2026-10-01新增："人工仓位不接管"用的溯源窗口——recover_on_start()发现
# 在场持仓时，去open journal里找一条side+entry都匹配、且在这个时间窗内
# 的本引擎自己开仓记录，找到才当"自己的仓位、只是重启丢了内存状态"照常
# 重建监控；找不到就当人工仓位，只告警不接管(见recover_on_start内注释)。
# 48小时是给"周末/长假重启"这类小概率场景留够余量，比正常巡检周期宽松
# 得多，不会误伤真正的自身仓位。
# 2026-10-04: 48小时->14天。SNDK/ZEC这类趋势单会持仓数天，10-02 12:08开的SNDK空单满48小时后被误判为"人工仓位"、停止管理(雷达/补仓/动态追踪全停)。
OPEN_JOURNAL_PROVENANCE_WINDOW_SEC = float(os.getenv("OPEN_JOURNAL_PROVENANCE_WINDOW_SEC", str(14 * 24 * 3600)))
OPEN_JOURNAL_PROVENANCE_TOLERANCE = float(os.getenv("OPEN_JOURNAL_PROVENANCE_TOLERANCE", "0.01"))

# ==================== 止损后冷却 + 智能再入 ====================
COOLDOWN_SEC = float(os.getenv("COOLDOWN_SEC", "1800"))          # 止损后同向 TV 信号冷却
COOLDOWN_IGNORE_TV = os.getenv("COOLDOWN_IGNORE_TV", "1").lower() in ("1", "true", "yes")
REENTRY_ENABLED = os.getenv("REENTRY_ENABLED", "1").lower() in ("1", "true", "yes")
REENTRY_MAX = int(os.getenv("REENTRY_MAX", "1"))
REENTRY_DELAY_SEC = float(os.getenv("REENTRY_DELAY_SEC", "300"))   # 止损后先等这么久才开始找再入机会
REENTRY_WINDOW_SEC = float(os.getenv("REENTRY_WINDOW_SEC", "18000"))  # 再入机会窗口(默认5h=2根150m)
REENTRY_POLL_SEC = float(os.getenv("REENTRY_POLL_SEC", "30"))
REENTRY_SIZE_FACTOR = float(os.getenv("REENTRY_SIZE_FACTOR", "0.6"))
REENTRY_ADX_GATE = float(os.getenv("REENTRY_ADX_GATE", "20"))
REENTRY_MAX_CHASE_PCT = float(os.getenv("REENTRY_MAX_CHASE_PCT", "0.006"))
REENTRY_HARD_SL_ATR = float(os.getenv("REENTRY_HARD_SL_ATR", "2.0"))

# ==================== TV方向+双均线趋势自主重入(2026-09-13新增) ====================
# 宝贝拍板：VPS空仓、TV心跳最后一个非空方向仍然满足"现价站在这个方向的
# 双均线(15/30)之上/之下"(跟TV策略源码自己的开平仓定义完全一致)，就
# 认为趋势还在，允许VPS自己衡量重入——用综合硬止损+ATR估算TP123自己
# 管理这笔仓位，直到TV发出全新真实开仓信号为止(TV判方向为主，VPS是
# 执行层+半辅助)。这跟上面已有的"止损后小区间智能再入"(_start_reentry_
# watcher，只在VPS自己刚被止损出局、价格没跑远时生效)是两套独立机制，
# 互不冲突：这套只在"两边都空"且TV最后方向有据可查时触发。
TREND_REENTRY_ENABLED = os.getenv("TREND_REENTRY_ENABLED", "1").lower() in ("1", "true", "yes")
# 2026-09-15：15/30→8/20，跟DUAL_MA_EXIT(平仓判断"趋势还在不在")用
# 同一套均线定义，不再是"开仓抄TV的15/30、平仓用引擎自己的8/20"两套
# 并存。
# 2026-09-19再改：宝贝反馈8/20慢线太短，正常回撤就能打穿——改成8/30，
# 跟DUAL_MA_EXIT_SLOW_LEN同步改(两者本来就该用同一套"趋势还在不在"定义)。
TREND_REENTRY_FAST_LEN = int(os.getenv("TREND_REENTRY_FAST_LEN", "8"))
TREND_REENTRY_SLOW_LEN = int(os.getenv("TREND_REENTRY_SLOW_LEN", "30"))
TREND_REENTRY_MA_TYPE = os.getenv("TREND_REENTRY_MA_TYPE", "SMA")
# 2026-09-15：30→45分钟(宝贝原话明确指定45分钟双均线，同时把确认口径
# 从单纯dual_ma_trend_ok换成trend_confirmed_with_volume——见
# _maybe_trend_reentry顶部注释)
TREND_REENTRY_KLINE_INTERVAL_MIN = int(os.getenv("TREND_REENTRY_KLINE_INTERVAL_MIN", "45"))
TREND_REENTRY_KLINE_LIMIT = int(os.getenv("TREND_REENTRY_KLINE_LIMIT", "80"))
TREND_REENTRY_CANDLE_RUN = int(os.getenv("TREND_REENTRY_CANDLE_RUN", "3"))
# 每次尝试(不管成功/趋势不确认)之间至少间隔这么久，避免现价刚好贴在均线
# 附近来回穿越时每次心跳都触发一轮开/查
TREND_REENTRY_COOLDOWN_SEC = float(os.getenv("TREND_REENTRY_COOLDOWN_SEC", "300"))
TREND_REENTRY_TP1_ATR = float(os.getenv("TREND_REENTRY_TP1_ATR", "1.35"))
TREND_REENTRY_TP2_ATR = float(os.getenv("TREND_REENTRY_TP2_ATR", "2.5"))
TREND_REENTRY_SIZE_FACTOR = float(os.getenv("TREND_REENTRY_SIZE_FACTOR", "0.6"))
# 2026-09-15新增(宝贝确认)：每个品种每天最多允许这套机制自主开仓几次，
# 冷却+条件复核之外的额外风控上限，防止震荡行情里反复触发磨损
TREND_REENTRY_MAX_PER_DAY = int(os.getenv("TREND_REENTRY_MAX_PER_DAY", "3"))

# ==================== 高潮否决 / 反转锁利 ====================
CLIMAX_VETO_ENABLED = os.getenv("CLIMAX_VETO", "1").lower() in ("1", "true", "yes")
CLIMAX_ATR_MULT = float(os.getenv("CLIMAX_ATR_MULT", "3.0"))
OVEREXT_ATR_MULT = float(os.getenv("OVEREXT_ATR_MULT", "4.0"))
REVLOCK_ENABLED = os.getenv("REVLOCK", "1").lower() in ("1", "true", "yes")
# 2026-09-15对齐币安：判据形状从"实体/ATR"改成"实体/振幅"(body_ratio)，
# 常量改用币安当天事故复盘校准过的值——见market_overlays.py::
# reversal_candle同日期注释。
REVLOCK_BODY_RATIO = float(os.getenv("REVLOCK_BODY_RATIO", "0.55"))
REVLOCK_VOL_MULT = float(os.getenv("REVLOCK_VOL_MULT", "1.15"))
REVLOCK_MIN_PROFIT_ATR = float(os.getenv("REVLOCK_MIN_PROFIT_ATR", "1.0"))  # 至少这么多浮盈才收保本

# ==================== 深盈利保护三层(2026-09-15移植自币安radar_reentry_
# mixin.py，适配CoinW数据模型) ====================
# 宝贝反馈CoinW深盈利仓位保护比币安薄——IMPULSE_EXIT/DUAL_MA_EXIT/REVLOCK
# 三层是"趋势判断"驱动的防线，下面这三层是纯粹"已经赚了多少/回吐了多少/
# TV还认不认这笔仓位"驱动的棘轮，互相独立，谁锁得更紧生效谁的，调用顺序
#不影响最终结果。跟币安一样只朝有利方向棘轮，绝不主动平仓(只收紧止损)。
#
# 1) 大赢家利润地板：不依赖任何K线/指标，纯粹按"峰值浮盈是initial_atr的
#    多少倍"判断，零REST成本，可以每tick都算。
BIG_WIN_ATR_THRESHOLD = float(os.getenv("BIG_WIN_ATR_THRESHOLD", "3.0"))
BIG_WIN_RETAIN_FRAC = float(os.getenv("BIG_WIN_RETAIN_FRAC", "0.65"))
# 2026-09-20新增：CoinW盘口深度流动性缓冲。宝贝实盘复现(BNBUSDT
# 2026-09-19晚)：这条地板公式(系数/门槛/ATR来源)跟币安B系统逐字节一致
# (两边都直接读TV信号自带的atr，不是各自本地算的)，但两边入场价从一
# 开始就不是同一个数(764.11 vs 764.12)——TV是分别投递给两个webhook的
# 两次独立信号，各自后续也各走各的交易所真实盘口。CoinW盘口比币安薄，
# 这次反弹CoinW自己的现价比币安多跳了几分钱，正好踩过两边只差0.07的
# 地板线：币安扛住了，CoinW先被打出。不是代码bug(公式/系数/ATR来源都
# 核对过完全一致)，是两个交易所真实行情噪音的正常差异，在地板线上被
# 放大成了"一个踩线一个没踩线"。这里专门给CoinW的地板线额外留一点缓冲
# 去吸收这种盘口噪音，不动币安那边(两边是独立代码库，互不影响)。
COINW_LIQUIDITY_BUFFER_ATR_MULT = float(os.getenv("COINW_LIQUIDITY_BUFFER_ATR_MULT", "0.2"))
#
# 2) 利润回吐刹车：按品种从breath_profiles.py::giveback_brake读参数，
#    没配置的品种直接不生效(默认关闭)。跟币安B系统当前状态如实对齐——
#    BNB/XPD/SNDK/OPENAI/XAU这5个焦点品种在币安B系统自己的专属档案里
#    目前也没有giveback_brake(该字段只在币安A系统旧档案里按品种回测
#    启用/不启用，比如XAU就因回测证明"提前收紧反而砍断真实趋势"而故意
#    不加)——这里移植的是机制本身，不是凭空发明币安都没有的新校准数值。
#
# 3) TV僵局收紧：TV心跳已转FLAT(判定TV已经平掉这笔仓位)但我们还在持有、
#    且价格滞涨够久，收紧止损。简化版——跳过币安才有的"深度盈利耐心模式
#    让位"分支(CoinW没有这个概念)，统一收紧到现价±TV_EXIT_STALL_TIGHT_ATR。
TV_EXIT_STALL_ENABLED = os.getenv("TV_EXIT_STALL", "1").lower() in ("1", "true", "yes")
TV_EXIT_STALL_MIN_PEAK_ATR = float(os.getenv("TV_EXIT_STALL_MIN_PEAK_ATR", "0.5"))
TV_EXIT_STALL_BARS = int(os.getenv("TV_EXIT_STALL_BARS", "3"))
TV_EXIT_STALL_TIGHT_ATR = float(os.getenv("TV_EXIT_STALL_TIGHT_ATR", "0.3"))

# 2026-09-13新增(宝贝拍板，跟币安B系统同步实施)："双均线破位快速平仓"。
# 背景：币安B系统OPENAI靠ATR跟踪止损雷达在反弹时被打出，同一时刻CoinW的
# OPENAI止损还停在更远处没被打到、仍在持仓——宝贝指出雷达不该只是单一的
# ATR跟踪系数去锁利润，还要主动看这个品种自己真实周期的裸K是否跌破/站上
# 快慢双均线(做空=收盘价还在双均线下方才算趋势仍成立；做多=还在上方才算
# 成立)。跟上面REVLOCK(4h裸K单根反转实体+放量，固定4h不管品种周期，只
# 朝有利方向棘轮到保本价不直接平仓)是两套独立机制，可以同时生效，职责
# 不同：REVLOCK负责"保住不由盈转亏"这条底线，DUAL_MA_EXIT负责"趋势真的
# 走完了就别等ATR慢慢追"。真实放量确认破位时直接清仓；量能没确认(疑似
# 假突破)时不强平，只把止损适度收紧到破位K线收盘价附近，给行情留时间
# 验证是否真反转。
# 2026-09-19再补充(宝贝反馈"本周系统问题总结")：不该开仓/雷达刚激活就
# 立刻评判平仓——TV自己已经决定了这笔交易，硬止损才是真正的安全网；双
# 均线的职责是"保护已经取得的利润"，应该等浮盈真正越过TP1、朝TP2/TP3
# 推进之后，才让双均线破位的判断说了算(不管是强平还是收紧)，见下面
# _dual_ma_exit_profit_gate_open()。慢线也同步20→30(理由同上，太短容易
# 被正常回撤打穿)。
DUAL_MA_EXIT_ENABLED = os.getenv("DUAL_MA_EXIT", "1").lower() in ("1", "true", "yes")
DUAL_MA_EXIT_FAST_LEN = 8
DUAL_MA_EXIT_SLOW_LEN = 30
DUAL_MA_EXIT_KLINE_LIMIT = 80
DUAL_MA_EXIT_REFRESH_SEC = 300.0
DUAL_MA_EXIT_SOFT_TIGHTEN_BUFFER_ATR = 0.3
DUAL_MA_EXIT_NATIVE_BASE_MIN = 15  # CoinW原生支持15/120，45/75靠15合成(×3/×5)
# 每个品种自己真实的TV周期——同一份数值跟币安B系统
# (radar_reentry_mixin.py::DUAL_MA_EXIT_INTERVAL_MIN)保持一致。
# 2026-09-19核对TV警报截图重新校准：宝贝反馈"有的品种的时间周期有做
# 改变"——2026-09-13那版(BNB/XPD/OPENAI/XAU=45分钟, SNDK=75分钟)已经
# 跟TV实际在用的周期对不上，直接拿今天的真实截图数字校正。
DUAL_MA_EXIT_INTERVAL_MIN = {
    "BNB": 65, "XPD": 49, "SNDK": 91, "OPENAI": 65, "XAU": 50,
    "MU": 91,  # 2026-09-20新增：重新上线，TV周期91分钟
    # 下面几个当前已暂停(不在ACTIVE_SYMBOLS白名单)，没有最新TV截图
    # 数据，暂时保留2026-09-13那版旧值——恢复交易前需要重新核对。
    "XPT": 45, "XRP": 45, "SOL": 45,
}
DUAL_MA_EXIT_DEFAULT_INTERVAL_MIN = 45

# 2026-09-14新增(宝贝拍板，跟币安B系统同步实施)："保本激活双均线加宽"。
# 背景：实盘复现(XPTUSDT)——同一笔TV信号币安B/CoinW同时开空，币安B的
# 雷达激活公式(initial_stop_price: entry∓tick∓fee)完全按entry锚定、不看
# 现价/ATR/趋势结构，价格才刚朝有利方向走一点点就摸到激活线，止损立刻
# 锁死在纯手续费保本位，随后一次很正常的回踩就把这条贴着保本的止损打
# 穿，只赚了一点点就出局。CoinW用的是同一个initial_stop_price公式，
# 理论上有一样的风险，只是这次巧合没先摸到自己的激活线才躲过。
# 根源：DUAL_MA_EXIT/IMPULSE_EXIT这两层"聪明判断"目前只管激活*之后*的
# 止损收紧决策，激活那一刻本身是entry锚定的纯保本公式，一旦价格触发
# 激活线立刻实打实挂到交易所，双均线判断没机会介入这"第一次锁"的环节。
# 方案(2026-09-14首版)：只在首次开仓(reentry_count==0)触发激活的那一
# 刻，先检查双均线是否已经确认趋势——上线当天两笔实盘复现(跟币安B系统
# 同一批：BNBUSDT多头、XPTUSDT多头"插针触及TP1后回落"那笔)都显示：
# 双均线用的是45分钟K线8/20周期均线，天然滞后于"价格刚摸到激活线"这个
# 更快的瞬时判据(往往几分钟内就触发)——均线还没来得及跟上，加宽被
# 双均线门槛拦下，等于形同虚设，两笔实盘都验证到最终锁的还是纯保本
# 原值。
# 2026-09-14当天改版：去掉"必须双均线先确认"这个前置门槛，只要触发了
# 激活(本身已经证明价格朝有利方向走出了距离)，就无条件用一个更宽的
# 锚点跟纯保本位取更松的那个——真正的安全阀不是滞后的均线，而是下面
# 两条硬性边界：绝不允许比综合硬止损(hard_sl_px)更松，也绝不允许倒退
# 到比纯保本更紧。
# 2026-09-14当天第三版(跟币安B系统同一批改)：实盘复现(BNBUSDT，10小时
# 内两账户各触发5次同一模式)显示"现价±0.5×ATR"这个缓冲量级根本不够
# 用——激活线本身就是(TP1+TP2)/2算出来的，价格往往"刚摸到激活线就
# 触发"，几乎不会有明显超涨。这种最常见的"刚好到线"情形下，现价-缓冲
# 算出来的锚点反而比纯保本更紧(因为激活线离entry本来就有相当距离，
# 减掉缓冲后仍然比纯保本远)，取更松那个又回退到纯保本，加宽形同虚设。
# 第三版最初的方案是改成锚定"激活线相对entry走的那段距离"(entry±0.5×
# gate_dist)，写完后用BNB真实数字复算才发现同样的方向性漏洞：这个新
# 锚点是跟纯保本完全独立算出来的，"谁更松"取决于两个不相关公式的巧
# 合——当纯保本手续费缓冲(通常远小于1个ATR)本来就比这个新锚点更贴近
# entry时，新锚点反而比纯保本更贴近激活线、更紧，取更松的min/max结果
# 又回退成纯保本，跟前两版殊途同归地形同虚设(BNB正是这种情形)。
# 第三版最终修正：不再独立算锚点去比较，而是直接在纯保本(initial_sl)
# 的基础上做加减法——保本位再往回让出ACTIVATION_RETAIN_FRAC(50%)比例
# 的gate_dist当额外缓冲。由构造保证100%比纯保本更松，不再依赖任何巧合
# 的数值关系，同时依然随激活线的远近(gate_dist)自适应。
DUAL_MA_ACTIVATION_GATE_ENABLED = os.getenv("DUAL_MA_ACTIVATION_GATE", "1").lower() in ("1", "true", "yes")
DUAL_MA_ACTIVATION_RETAIN_FRAC = 0.5  # 在纯保本基础上，额外让出的gate_dist比例当止损缓冲

# 2026-09-13再新增(宝贝实盘截图复盘BNBUSDT.P发现)："突发放量反转K线快速
# 锁保本"——三层防线里最快的一层，跟币安B系统(eth-webhook-server同名
# commit)同步实施。背景：等K线收盘价真正站上/跌破双均线才反应，对一根
# 走势凌厉的反转K线来说已经晚了。宝贝原话："等阳线站上双均线再平仓就
# 已经晚了"。
#   1) IMPULSE_EXIT(本机制，最快，~1分钟轮询一次)：只看最新一根K线自己
#      够不够"决定性"(实体够大+真放量)，够的话立刻锁到保本价，不直接
#      平仓，抢在双均线确认之前先落袋一部分保护。
#   2) DUAL_MA_EXIT(已有，5分钟轮询)：双均线真正被突破+放量确认 →
#      直接清仓。
#   3) REVLOCK(已有，4h裸K)：最慢的兜底安全网。
IMPULSE_EXIT_ENABLED = os.getenv("IMPULSE_EXIT", "1").lower() in ("1", "true", "yes")
IMPULSE_BODY_RATIO = 0.6
IMPULSE_VOL_MULT = 1.5
IMPULSE_VOL_LOOKBACK = 20
IMPULSE_REFRESH_SEC = 60.0
IMPULSE_KLINE_LIMIT = 30

# ==================== 追单确认观察窗 / 孤儿单清扫 ====================
CHASE_WATCH_ENABLED = os.getenv("CHASE_WATCH", "1").lower() in ("1", "true", "yes")
CHASE_CONFIRM_COUNT = int(os.getenv("CHASE_CONFIRM_COUNT", "3"))     # 连续 N 次合格心跳
CHASE_CONFIRM_MIN_SEC = float(os.getenv("CHASE_CONFIRM_MIN_SEC", "120"))  # 且至少观察这么久
CHASE_STALE_SEC = float(os.getenv("CHASE_STALE_SEC", "600"))         # 心跳断这么久 -> 观察窗作废
CHASE_3TF_REQUIRED = int(os.getenv("CHASE_3TF_REQUIRED", "3"))       # 3 个周期里至少几个同向
HOUSEKEEP_SEC = float(os.getenv("HOUSEKEEP_SEC", "300"))

# ==================== regime(ADX档位)自适应雷达 + watchdog ====================
REGIME_ADAPT_ENABLED = os.getenv("REGIME_ADAPT", "1").lower() in ("1", "true", "yes")
# tier 0弱/1中/2强 -> 步进系数 / 呼吸系数（强趋势给跑者更多空间，弱趋势收快点）
REGIME_STEP_MULT = {"0": 0.85, "1": 1.0, "2": 1.20}
REGIME_BREATH_MULT = {"0": 0.90, "1": 1.0, "2": 1.15}
WATCHDOG_STALE_SEC = float(os.getenv("WATCHDOG_STALE_SEC", "120"))   # 监控循环这么久没跳 -> 重启

# ==================== TradFi 品种周末停开仓（默认关闭） ====================
# 2026-09-12新增（宝贝要求）后，当天晚些时候宝贝又改主意撤回："还是都
# 打开吧，有时候行情也就是在周末铺垫的，万一错过趋势更是后悔了"——机制
# 保留（可逆、随时能重新打开），默认开关改成关闭。OPENAI(盘前未上市
# 股权)/XPD(钯金,COMEX)/SNDK(SanDisk股票代理) 这三个品种的底层是传统
# 金融市场，周末现实中休市，但包装成的USDT永续在CoinW仍然7×24挂着——
# 如果以后又想重新拦截，把 WEEKEND_PAUSE_OPEN 环境变量设成 "1" 即可，
# 不用改代码。简单按UTC周几判断（周六0点~周一0点UTC视为周末），只挡
# "新开仓"（LONG/SHORT），不影响CLOSE/HEARTBEAT/已有仓位管理。
# 2026-09-13：新增XPT(铂金,NYMEX/COMEX)——跟XPD同为传统贵金属期货底层，
# 现实中同样周末休市，补进默认清单保持分类一致（机制本身仍默认关闭，
# 加入清单不代表现在真的会拦截新开仓）。
WEEKEND_PAUSE_OPEN_ENABLED = os.getenv("WEEKEND_PAUSE_OPEN", "0").lower() in ("1", "true", "yes")
WEEKEND_PAUSE_SYMBOLS = {
    s.strip().upper()
    for s in os.getenv("WEEKEND_PAUSE_SYMBOLS", "OPENAI,XPD,SNDK,XPT").split(",")
    if s.strip()
}


def _is_weekend_utc(now_ts: Optional[float] = None) -> bool:
    """UTC周六0点 ~ 周一0点视为周末（周六=5，周日=6，Python Monday=0）。"""
    import datetime
    t = now_ts if now_ts is not None else time.time()
    wd = datetime.datetime.utcfromtimestamp(t).weekday()
    return wd in (5, 6)


class PositionSupervisorCoinW(CoinWRadarEnvelopeMixin):
    """
    CoinW唯一生产大脑
    每symbol一实例
    """

    def __init__(self, symbol: str = "ETH"):
        self.symbol = str(symbol or "ETH").upper()
        self._lock = threading.RLock()

        # 状态
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._catchup_blocked_until = 0.0   # 手动平/TV平后，这段时间内心跳不补开
        self._naked_tick = 0                 # 裸单守护节流计数
        self._cooldown_until = 0.0           # 止损后同向信号冷却截止
        self._reentry_count = 0             # 当前 episode 已再入次数
        self._last_exit = {}                # 上次出局: side/entry/atr/tier/reason/ts
        self._intentional_close = False    # True = TV平/手动平（不触发再入/冷却）
        self._reentry_thread: Optional[threading.Thread] = None
        self._reentry_size_factor = 1.0
        self._revlock_bar_ts = 0           # 已查过的最近 4h K线 open_ts（反转锁利节流）
        self._chase_watch = {}            # 追单确认观察窗: {side,first_ts,count,last_ts}
        self._chase_confirmed = False     # 人工 /admin/confirm_catchup 强制放行
        self._loop_beat = 0.0            # 监控循环心跳（watchdog 用）
        self._last_nonflat_hb_side = ""    # TV心跳最后一个非FLAT方向（重启清零，等下次心跳重新填）
        self._last_nonflat_hb_entry = 0.0
        self._trend_reentry_next_try_ts = 0.0  # 冷却：同方向下次允许再评估的时间
        # 2026-09-15新增：趋势确认重入每日次数上限，{"date": "YYYY-MM-DD", "count": N}
        self._trend_reentry_daily = {"date": "", "count": 0}
        # 2026-09-15新增：TV僵局收紧刹车用——_handle_heartbeat每次收到
        # 心跳都会同步这个字段(不止_last_nonflat_hb_side)，记的是"TV
        # 现在这一刻"的方向(含FLAT)，跟_last_nonflat_hb_side(只记非FLAT
        # 方向、FLAT时不更新)语义不同，两个字段都要保留。
        self._tv_heartbeat_side = ""
        self._tv_exit_stall_since_ts = 0.0
        self._tv_exit_stall_best_seen = 0.0
        self._big_win_alerted_best = 0.0
        self._giveback_brake_alerted_best = 0.0

        # 初始化模块
        self._init_modules()

        logger.info(f"CoinW Supervisor {COINW_SUPERVISOR_VERSION} 就绪: {self.symbol}")

    def _init_modules(self):
        """初始化各模块"""
        # 延迟导入避免循环依赖
        from coinw_client import coinw_client, is_position_query_failed
        from pipeline_bridge import PipelineBridge
        from chief_auditor import audit_open_bundle, should_hard_pause
        from risk_manager import get_risk_manager
        from webhook_parser import parse_webhook
        from defense_profiles import get_defense_profile
        from atr_scenario import calc_hard_stop_price
        from breath_stop import get_radar
        from radar_reentry_mixin import get_radar_mixin
        from order_idempotency import get_idempotency
        from api_throttle import get_throttle
        import dingtalk

        self.client = coinw_client
        self.pipeline = get_pipeline(self.symbol, "coinw")
        self.bridge = PipelineBridge(self)
        self.risk_mgr = get_risk_manager()
        self.radar = get_radar(self.symbol)
        self.radar_mixin = get_radar_mixin()
        self.idempotency = get_idempotency()
        # 注意：AccountThrottle.acquire() 目前全仓库无调用方——真正生效的限流是
        # coinw_client.py 的 _throttle_rest()（阻塞式最小间隔）+ IP限流退避，
        # 已在约10处REST调用点接好。self.throttle 是 v16.22.1-coinw-init 那次
        # 整体复刻币安架构时带过来的预算滑动窗口，从初始commit起就没接线，是
        # 冗余 scaffold 不是失效的防护——2026-08-10 评估过，暂不接入（见
        # deepcoin_coinw对齐币安_检查清单.md 第H项）。
        self.throttle = get_throttle("coinw")
        self._dingtalk = dingtalk

    def _get_balance(self, patient: bool = False) -> float:
        """获取余额"""
        # 2026-10-04：所有品种统一按"本金权益"(钱包余额+浮盈，对齐币安marginBalance)
        # 开仓，不再用可用余额(会被其它持仓占用的保证金压小，ZEC多单曾只开到权益的~60%)。
        # patient=True(开仓路径)：读不到就等限流冷却后重试，最多3次，不轻易丢信号；
        # 仍读不到→用24小时内的"最近一次成功本金权益"(落盘)，宁可用略旧的真实值也不丢单。
        eq = 0.0
        for _i in range(3 if patient else 1):
            eq = float(self.client.get_principal_equity() or 0)
            if eq > 0 or not patient or _i == 2:
                break
            _w = min(max(float(self.client.ip_rate_limit_remaining() or 0), 1.5 * (_i + 1)), 10.0)
            logger.warning(f"[{self.symbol}] 本金权益读取失败，{_w:.1f}s后重试(第{_i + 1}/3次)")
            time.sleep(_w)
        _path = "data/last_equity.json"
        if eq > 0:
            try:
                os.makedirs("data", exist_ok=True)
                with open(_path, "w", encoding="utf-8") as f:
                    json.dump({"equity": eq, "ts": time.time()}, f)
            except Exception:
                pass
            return eq
        try:
            with open(_path, encoding="utf-8") as f:
                _d = json.load(f)
            if time.time() - float(_d.get("ts", 0) or 0) <= 24 * 3600 and float(_d.get("equity", 0) or 0) > 0:
                logger.warning(
                    f"[{self.symbol}] 本金权益读取失败 → 沿用{int(time.time() - float(_d['ts']))}秒前的真实值 {float(_d['equity']):.2f}"
                )
                return float(_d["equity"])
        except Exception:
            pass
        if self.symbol in ("SNDK", "ZEC"):
            return 0.0  # 本金等值品种：读不到真实本金宁可不开，也不退回可用余额
        return self.client.get_available_balance()

    def _piece_unit(self) -> float:
        """CoinW合约1张对应的标的数量(ZEC=0.01、SNDK=0.001)。优先读落盘的实测值，
        其次用已知默认；有持仓时用baseSize/currentPiece实测并落盘。"""
        _defaults = {"SNDK": 0.001, "ZEC": 0.01}
        _path = f"data/piece_unit_{self.symbol}.json"
        try:
            with open(_path, encoding="utf-8") as f:
                u = float(json.load(f).get("unit", 0) or 0)
            if u > 0:
                return u
        except Exception:
            pass
        return float(_defaults.get(self.symbol, 0.0))

    def _round_to_piece(self, qty: float, equity: float, price: float) -> float:
        """本金等值品种：数量取整到最近的整张(不是向下取整——ZEC一张≈13U约占374U账户的3.6%，
        向下取整会系统性偏小)；取整后名义不得超过本金权益的102%。"""
        unit = self._piece_unit()
        if unit <= 0 or qty <= 0 or price <= 0:
            return qty
        n = int(round(qty / unit))
        while n > 1 and equity > 0 and n * unit * price > equity * 1.02:
            n -= 1
        return round(max(n, 1) * unit, 6)

    def _get_current_price(self) -> float:
        """获取当前价格"""
        return self.client.get_current_price(self.symbol)

    # ==================== 信号处理入口 ====================

    def handle_signal(self, payload: dict) -> dict:
        """
        处理TV webhook信号
        """
        global trading_paused

        with self._lock:
            # 解析信号
            from webhook_parser import parse_webhook
            signal = parse_webhook(payload)

            if not signal.valid:
                logger.warning(f"信号解析失败: {signal.error}")
                return {"ok": False, "error": signal.error}

            if self.symbol not in ACTIVE_SYMBOLS or signal.symbol not in ACTIVE_SYMBOLS:
                logger.warning(f"拒绝非自动白名单品种信号: {self.symbol}/{signal.symbol}")
                return {"ok": False, "error": "symbol_not_active"}

            logger.info(f"收到信号: {signal.action} {signal.symbol} @ {signal.price}")

            # CLOSE信号
            if signal.action in ("CLOSE", "CLOSE_QUICK_EXIT", "CLOSE_RSI_EXIT"):
                return self._handle_close(signal)

            # LONG/SHORT信号
            if signal.action in ("LONG", "SHORT"):
                return self._handle_open(signal)

            # PING信号 - 心跳检测
            if signal.action == "PING":
                logger.info("收到PING心跳信号")
                return {
                    "ok": True,
                    "status": "pong",
                    "message": "system_alive",
                    "trading_paused": trading_paused,
                }

            # HEARTBEAT - 心跳催单（漏开补开 / 止损漂了补挂 / 反向翻转补救）
            if signal.action == "HEARTBEAT":
                return self._handle_heartbeat(signal)

            return {"ok": False, "error": "unknown_action"}

    def _handle_open(self, signal, is_reentry: bool = False) -> dict:
        """处理开仓信号"""
        global trading_paused
        # 2026-10-04：ZEC也跟SNDK/币安B/E一致——硬止损直接用TV给的值(高波动品种不跑VPS自算的"综合硬止损"，它在CoinW上算出1321.22，比TV 1315.88近得多)；挂单未确认立即净场。
        if self.symbol in ("SNDK", "ZEC"):
            from sndk_live_policy import valid_tv_stop
            current_price = self._get_current_price()
            if not valid_tv_stop(signal.action, signal.price, signal.stop_loss, current_price):
                logger.error("[SNDK] 开仓中止：TV stop_loss缺失或已被现价穿越")
                return {"ok": False, "error": "invalid_tv_stop_loss"}
        # 2026-09-15修复：BreathStop.set_reentry_count()此前从未被调用，
        # 导致重入开仓也一直用首次开仓更近的(TP1+TP2)/2激活线，而不是
        # 设计里更远、更保守的纯TP2激活线(见breath_stop.py
        # _activation_gate_price)。这里记一个瞬时标记，供_place_defense_
        # orders()里的radar.arm()之后据此告诉雷达这是不是重入仓位。
        self._is_reentry_open = bool(is_reentry)

        if trading_paused:
            logger.warning("交易暂停中(全局管理员暂停)，拒绝开仓")
            return {"ok": False, "error": "trading_paused"}

        # 2026-09-14修复(实盘复现：XRPUSDT一笔tp_slice审计drift触发
        # "交易暂停: 督察失败"，从22:32一直卡到第二天凌晨，期间SOL/XRP/
        # BNB/XAU/XPD等其它所有品种的新开仓全部被这个跟它们毫无关系的
        # 审计失败挡在外面——根源是_pause_trading()原来改的是模块级全局
        # trading_paused，一个品种审计出问题就殃及全部品种，且没有任何
        # 自动恢复机制，只能靠人工调/admin/resume才能解开，中间这段时间
        # 造成了大量"漏单"。改成只暂停出问题的这一个品种，不牵连其它
        # 品种；见_pause_trading()。
        if getattr(self, "_symbol_paused", False):
            logger.warning(f"[{self.symbol}] 交易暂停中(本品种督察失败)，拒绝开仓")
            return {"ok": False, "error": "symbol_paused"}

        # TradFi品种周末停开仓（底层现实市场休市，7×24永续仍在挂单，滑点/
        # 手续费不划算）——只挡新开仓，已有仓位/智能再入的平仓管理不受影响。
        if (WEEKEND_PAUSE_OPEN_ENABLED and self.symbol in WEEKEND_PAUSE_SYMBOLS
                and _is_weekend_utc()):
            logger.info(f"[{self.symbol}] 周末休市窗口，跳过新开仓 {signal.action}")
            return {"ok": False, "error": "weekend_market_closed"}

        # 止损后冷却：非再入的同向 TV 信号在冷却期内忽略（防抖，再入走独立通道）
        if (not is_reentry and COOLDOWN_IGNORE_TV
                and time.time() < self._cooldown_until
                and str(signal.action).upper() == str(self._last_exit.get("side") or "").upper()):
            left = self._cooldown_until - time.time()
            logger.warning(f"止损后冷却中({left:.0f}s)，忽略同向信号 {signal.action}")
            return {"ok": False, "error": "cooldown", "cooldown_left": round(left)}

        # 新的真实 TV 信号 = 新 episode：清零再入计数、结束再入看守、解除冷却与心跳冻结
        if not is_reentry:
            self._reentry_count = 0
            self._cooldown_until = 0.0
            self._intentional_close = False
        self._reentry_size_factor = REENTRY_SIZE_FACTOR if is_reentry else 1.0

        # 1) 信号官登记——注意：pipeline.data里的"tp1"/"tp2"是结构化状态
        # ({"px":...,"pieces":...})，这里如果直接传tp1=signal.tp1(一个纯
        # float)会被_apply_fields原样写进self.data["tp1"]，把dict形状覆盖
        # 成float。后面督察官审计取self.data.get("tp1",{}).get(...)时，
        # 如果TP1因仓位太小被跳过(没有重新写回dict)，这个float就会一路存活
        # 到这里，'float' object has no attribute 'get'直接崩溃(2026-08-08
        # 真实测试复现)。这里只是想把TV原始价位记进历史，改用不冲突的字段名。
        self.pipeline.advance(
            Phase.SIGNAL_RECEIVED,
            Role.SIGNAL,
            note=f"{signal.action} @ {signal.price}",
            action=signal.action,
            price=signal.price,
            atr=signal.atr,
            stop_loss=signal.stop_loss,
            tv_tp1=signal.tp1,
            tv_tp2=signal.tp2,
            tier=signal.tier,
        )

        # 2) 仓位稽查员 - 先平后开
        self.pipeline.advance(Phase.PENDING_CLEAR, Role.AUDITOR_POS, note="开始清场")
        cleared = self._clear_position(f"预开仓 {signal.action}")

        if not cleared:
            self.pipeline.mark_failed(Role.AUDITOR_POS, "清场失败")
            return {"ok": False, "error": "clear_failed"}

        self.pipeline.advance(Phase.CLEARED, Role.AUDITOR_POS, note="清场完成")

        # 3) 计算仓位（按 TV 趋势强弱档位 signal.tier 缩放名义，对齐币安）
        balance = self._get_balance(patient=True)
        entry_price = signal.price
        qty = self._calc_position_size(balance, entry_price, signal.tier)

        # 再入仓位缩小
        _rf = float(getattr(self, "_reentry_size_factor", 1.0) or 1.0)
        if _rf != 1.0:
            qty = qty * _rf
            logger.info(f"再入仓位系数 {_rf} -> qty={qty:.6f}")

        # 检查TV qty soft-cap(本金等值品种"永远开本金等量"，不受TV数量压缩)
        if self.symbol not in TOPUP_SYMBOLS and signal.qty and signal.qty > 0 and signal.qty < qty:
            qty = signal.qty

        # 事前拦截(本金等值品种)：取整到最近整张 → 数量/名义合理性 → 保证金预检
        if self.symbol in TOPUP_SYMBOLS:
            qty = self._round_to_piece(qty, balance, entry_price)
            _px_now = float(self._get_current_price() or entry_price or 0)
            _bad = None
            if not (qty > 0 and math.isfinite(qty)):
                _bad = f"数量无效 qty={qty} (本金权益={balance})"
            elif balance > 0 and _px_now > 0 and qty * _px_now > balance * 1.05:
                _bad = f"名义{qty * _px_now:.1f}U 超过本金权益{balance:.1f}U×1.05，拒绝开仓"
            if _bad:
                logger.error(f"[{self.symbol}] 事前拦截: {_bad}")
                self._safe_alert(f"[{self.symbol}] 开仓事前拦截: {_bad}")
                self.pipeline.mark_failed(Role.EXECUTION, f"pretrade_reject:{_bad}")
                return {"ok": False, "error": f"pretrade_reject:{_bad}"}
            try:
                _lev = float(self.client.get_symbol_leverage(self.symbol) or 5.0)
                _avail = float(self.client.get_available_balance() or 0)
                _need = qty * _px_now / max(_lev, 1.0)
                if _avail > 0 and _need > _avail * 0.95:
                    _unit = self._piece_unit() or 0.0001
                    _fit = math.floor((_avail * 0.95 * _lev / _px_now) / _unit) * _unit
                    logger.warning(f"[{self.symbol}] 开仓保证金预检：需{_need:.1f}>可用{_avail:.1f}×95% → 数量{qty}缩到{_fit:.4f}")
                    self._integrity_alert("开仓保证金不足缩量", f"目标{qty}→可开{_fit:.4f} (可用{_avail:.1f} 杠杆{_lev:.0f}x)")
                    qty = round(_fit, 6)
                    if qty <= 0:
                        self.pipeline.mark_failed(Role.EXECUTION, "pretrade_margin_insufficient")
                        return {"ok": False, "error": "pretrade_margin_insufficient"}
            except Exception as _e:
                logger.debug(f"[{self.symbol}] 保证金预检跳过: {_e}")
            self._open_target_qty = float(qty)
            self._open_target_side = str(signal.action).upper()
            self._open_target_ts = time.time()

        # 3.5) 高潮插针否决 / 过度延伸告警
        if CLIMAX_VETO_ENABLED:
            veto, warn, det = self._climax_check(signal.action, entry_price)
            if warn:
                logger.warning(f"进场过度延伸告警: {det}")
            if veto:
                self._safe_alert(f"高潮插针否决进场 {signal.action}: {det}")
                self.pipeline.mark_failed(Role.EXECUTION, f"climax_veto:{det}")
                return {"ok": False, "error": f"climax_veto:{det}"}

        # 4) 开仓
        self.pipeline.advance(Phase.ENTRY_SUBMITTED, Role.EXECUTION, note="提交开仓")
        entry_result = self._execute_open(signal.action, entry_price, qty, signal)

        if not entry_result.get("ok"):
            self.pipeline.mark_failed(Role.EXECUTION, entry_result.get("error", "开仓失败"))
            return entry_result

        # 5) 同步持仓
        # 事后校准：成交量 vs 目标量，开"校准冲刺"(前5分钟每30秒交易所核对补齐)；顺便学习每张换算
        if self.symbol in TOPUP_SYMBOLS:
            try:
                _got = float(entry_result.get("qty") or 0)
                _tp = float(entry_result.get("total_pieces") or 0)
                if _got > 0 and _tp > 0:
                    os.makedirs("data", exist_ok=True)
                    with open(f"data/piece_unit_{self.symbol}.json", "w", encoding="utf-8") as _f:
                        json.dump({"unit": round(_got / _tp, 8), "ts": time.time()}, _f)
                _tgt = float(getattr(self, "_open_target_qty", 0) or 0)
                if _tgt > 0:
                    if _got < _tgt * (1 - TOPUP_MIN_SHORTFALL_FRAC):
                        logger.warning(f"[{self.symbol}] 开仓成交{_got} < 目标{_tgt} → 启动校准冲刺自动补齐")
                    self._topup_burst_until = time.time() + TOPUP_BURST_WINDOW_SEC
                    self._topup_last_ts = 0
            except Exception as _e:
                logger.debug(f"[{self.symbol}] 开仓后校准登记跳过: {_e}")
        self.pipeline.sync_position(
            side=signal.action,
            qty=entry_result.get("qty", qty),
            entry=entry_result.get("entry_price", entry_price),
            position_id=entry_result.get("position_id", ""),
            allow_initial=True,
        )

        self.pipeline.advance(Phase.ENTRY_CONFIRMED, Role.EXECUTION,
                           note=f"开仓确认 {entry_result.get('entry_price')} x {entry_result.get('qty')}")

        # 6) 设置防线
        orders_placed = self._place_defense_orders(signal, entry_result)

        if not orders_placed:
            self.pipeline.mark_failed(Role.EXECUTION, "防线设置失败")
            if self.symbol in ("SNDK", "ZEC"):
                self._safe_alert("[SNDK] 硬止损挂单未确认，立即净场")
                if not self._clear_position("SNDK止损挂单失败"):
                    self._pause_trading("SNDK止损挂单失败且净场未确认")
            return {"ok": False, "error": "defense_failed"}

        self.pipeline.advance(Phase.ORDERS_PLACED, Role.EXECUTION, note="防线就绪")

        # 7) 督察官复查
        audit_ok = self._run_audit(signal)

        if not audit_ok:
            self._pause_trading("督察失败")
            self.pipeline.mark_failed(Role.CHIEF, "审计失败")
            return {"ok": False, "error": "audit_failed"}

        self.pipeline.advance(Phase.VERIFIED, Role.CHIEF, note="督察通过")

        # 8) 通讯官通知
        self._report_open(signal, entry_result)
        self.pipeline.advance(Phase.REPORTED, Role.COMMS, note="已通知")

        # 9) 进入监控
        self.pipeline.advance(Phase.MONITORING, Role.RADAR, note="开始监控")
        self._start_monitoring()

        return {
            "ok": True,
            "entry_price": entry_result.get("entry_price"),
            "qty": entry_result.get("qty"),
            "position_id": entry_result.get("position_id"),
        }

    def _handle_close(self, signal) -> dict:
        """处理平仓信号"""
        logger.info(f"收到平仓信号: {signal.action}")

        # TV 主动平仓：不触发再入 / 不算止损冷却，结束再入看守
        self._intentional_close = True
        self._cooldown_until = 0.0
        self._reentry_count = 0

        # 停止监控
        self._stop_monitoring()

        # 清仓
        success = self._clear_position(f"TV平仓 {signal.action}")

        # 重置流水线
        self.pipeline.reset_idle("tv_close")

        # 刚平的仓，一段时间内不让滞后的心跳把它又补开
        self._catchup_blocked_until = time.time() + CATCHUP_BLOCK_SEC

        return {
            "ok": success,
            "action": signal.action,
        }

    # ==================== 心跳催单 ====================

    def _synthesize_open_signal(self, hb_signal, side: str, entry: float, px: float):
        """把心跳 payload 拼成一个开仓信号，走 _handle_open。"""
        from webhook_parser import ParsedSignal
        raw = hb_signal.raw or {}

        def _f(k, d=0.0):
            try:
                return float(raw.get(k) or d)
            except (TypeError, ValueError):
                return d

        return ParsedSignal(
            valid=True, action=side, symbol=self.symbol,
            price=(entry or px), stop_loss=(hb_signal.stop_loss or _f("stop_loss")),
            atr=(hb_signal.atr or _f("atr")),
            tp1=(hb_signal.tp1 or _f("tp1")), tp2=(hb_signal.tp2 or _f("tp2")),
            tp3=(hb_signal.tp3 or _f("tp3")),
            qty=None, tier=(hb_signal.tier or ""), leverage=20, error="",
            side=side, raw=raw,
        )

    def _hard_sl_present(self) -> bool:
        """交易所是否已挂着一张有效硬止损（stopType!=1、未触发、stopLossPrice>0）。"""
        pid = self.pipeline.data.get("position_id")
        if not pid:
            return False
        try:
            for r in self.client.get_tp_sl_info(pid):
                if int(r.get("stopType") or 0) != 1 and float(r.get("stopLossPrice") or 0) > 0 \
                        and int(r.get("triggerStatus") or 0) == 0:
                    return True
        except Exception:
            return True  # 查不到就别乱补，避免重复挂
        return False

    def _live_side(self, pos: dict) -> str:
        s = str((pos or {}).get("direction") or "").upper()
        if s in ("LONG", "BUY"):
            return "LONG"
        if s in ("SHORT", "SELL"):
            return "SHORT"
        return ""

    def _handle_heartbeat(self, signal) -> dict:
        """心跳催单：以 TV 上报的应有持仓意图为准，补齐 VPS 的漏动作。"""
        if not HEARTBEAT_CATCHUP_ENABLED:
            return {"ok": True, "status": "heartbeat", "catchup": "disabled"}

        hb_side = (signal.side or "FLAT").upper()
        # 2026-09-15新增：TV僵局收紧刹车用，记"TV现在这一刻"的方向(含
        # FLAT)——跟下面_last_nonflat_hb_side(只记非FLAT、FLAT时不更新)
        # 是两个不同语义的字段，都要维护。
        self._tv_heartbeat_side = hb_side
        raw = signal.raw or {}
        try:
            hb_entry = float(raw.get("entry") or raw.get("entry_price") or signal.price or 0)
        except (TypeError, ValueError):
            hb_entry = 0.0

        # 2026-09-13新增：记住TV心跳最后一个非FLAT方向——心跳一旦变成
        # FLAT，hb_side这个局部变量就再也看不出"之前是哪个方向"了，供
        # 下面"两边都空"分支里的自主重入机制用(TV最后方向未变+趋势仍在
        # 才允许重入，不是每次心跳都乱猜)。只在真的是LONG/SHORT时更新，
        # FLAT/UNKNOWN都不覆盖，保留"上一次真实方向"的记忆。
        if hb_side in ("LONG", "SHORT"):
            self._last_nonflat_hb_side = hb_side
            if hb_entry > 0:
                self._last_nonflat_hb_entry = hb_entry

        with self._lock:
            from coinw_client import is_position_query_failed
            if self.client.ip_rate_limit_remaining() > 0:
                return {"ok": False, "status": "heartbeat", "error": "position_unverified"}
            pos = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
            if is_position_query_failed(pos):
                return {"ok": False, "status": "heartbeat", "error": "position_unverified"}
            have = float((pos or {}).get("baseSize") or (pos or {}).get("positionAmt")
                         or (pos or {}).get("quantity") or 0)
            live = self._live_side(pos) if have != 0 else ""

            # 心跳没带 side（TV 心跳没配持仓意图）：只做存活/裸单核对，不当 TV 空仓处理
            if hb_side == "UNKNOWN":
                if have != 0 and NAKED_GUARD_ENABLED and not self.radar.get_state().activated \
                        and not self._hard_sl_present():
                    sl = float(self.pipeline.data.get("hard_sl_px") or 0)
                    pid = self.pipeline.data.get("position_id")
                    entry_px = float(self.pipeline.data.get("entry") or 0)
                    # 2026-10-01：现算并补挂一个全新硬止损前，先确认这笔
                    # 仓位真的是本引擎自己开的(open journal溯源)——宝贝
                    # 明确要求人工仓位系统不要管，这条裸单守护原来会对
                    # 任何"没有有效硬止损"的在场持仓无条件补一个，等于
                    # 变相开始管理一笔可能是人工开的仓位。
                    if sl <= 0 and entry_px > 0 and live and pid \
                            and not self._position_has_algo_provenance(live, entry_px):
                        self._safe_alert(
                            f"⚠️ [{self.symbol}] 心跳(无side)裸单守护发现无止损的在场"
                            f"持仓 {live} {have} @{entry_px}，open journal溯源判定为人工"
                            f"仓位——不补挂止损，由你自己管理。"
                        )
                        return {"ok": True, "status": "heartbeat", "state": "manual_position_skipped"}
                    if sl <= 0 and entry_px > 0 and live and pid:
                        sl = self._compute_fresh_hard_stop(
                            side=live, entry=entry_px, amt=have, pid=pid,
                            source="心跳(无side)裸单守护",
                        )
                        return {"ok": True, "status": "heartbeat",
                                "action": "computed_sl" if sl > 0 else "computed_sl_failed", "sl": sl}
                    if sl > 0 and pid:
                        self.client.set_sl_tp(position_id=pid, instrument=self.symbol,
                                              stop_loss_price=round(sl, 2))
                        logger.warning(f"心跳(无side)裸单守护：补挂硬止损 @{sl}")
                        return {"ok": True, "status": "heartbeat", "action": "reattach_sl", "sl": sl}
                return {"ok": True, "status": "heartbeat", "state": "no_side"}

            # 两边都空——TV最后方向如果还站得住(双均线趋势确认)，交给
            # 自主重入机制评估要不要自己开仓(见_maybe_trend_reentry)
            if hb_side == "FLAT" and have == 0:
                trend_result = self._maybe_trend_reentry()
                if trend_result is not None:
                    return trend_result
                return {"ok": True, "status": "heartbeat", "state": "both_flat"}

            # TV 空、VPS 有仓：TV 平了我们漏了
            if hb_side == "FLAT" and have != 0:
                self._safe_alert("心跳：TV已空但VPS仍持仓")
                if HEARTBEAT_FLAT_CLOSE:
                    self._stop_monitoring()
                    ok = self._clear_position("心跳催单-补平(TV已空)")
                    self.pipeline.reset_idle("hb_flat_close")
                    self._catchup_blocked_until = time.time() + CATCHUP_BLOCK_SEC
                    return {"ok": ok, "status": "heartbeat", "action": "flat_close"}
                return {"ok": True, "status": "heartbeat", "state": "tv_flat_vps_open",
                        "note": "仅告警，未自动平（HEARTBEAT_FLAT_CLOSE=0）"}

            # 同向已持仓：核对裸单
            if have != 0 and live == hb_side:
                if NAKED_GUARD_ENABLED and not self.radar.get_state().activated \
                        and not self._hard_sl_present():
                    sl = float(self.pipeline.data.get("hard_sl_px") or 0)
                    pid = self.pipeline.data.get("position_id")
                    entry_px = float(self.pipeline.data.get("entry") or hb_entry or 0)
                    # 2026-10-01：同上——现算+补挂新硬止损前先做open journal
                    # 溯源，人工仓位不接管，只告警。
                    if sl <= 0 and entry_px > 0 and pid \
                            and not self._position_has_algo_provenance(hb_side, entry_px):
                        self._safe_alert(
                            f"⚠️ [{self.symbol}] 心跳裸单守护发现无止损的在场持仓 "
                            f"{hb_side} {have} @{entry_px}，open journal溯源判定为人工"
                            f"仓位——不补挂止损，由你自己管理。"
                        )
                        return {"ok": True, "status": "heartbeat", "state": "manual_position_skipped"}
                    if sl <= 0 and entry_px > 0 and pid:
                        # 2026-09-14修复(实盘复现XRPUSDT，止损被TV自己的
                        # stop_loss参考带成过紧的1.35，entry=1.3547只有
                        # 0.35%距离)：这里原来查不到hard_sl_px就直接退回
                        # signal.stop_loss(TV心跳自带的止损参考)当成我们
                        # 自己的综合硬止损去挂——TV这个字段是它自己Pine
                        # 脚本内部逻辑算出来的，跟我们结构摆动点+ATR分档
                        # 的方法论完全不同，可能过紧也可能过松，不该被
                        # 当成综合硬止损直接采信。改成现算，详见
                        # _compute_fresh_hard_stop顶部注释。
                        sl = self._compute_fresh_hard_stop(
                            side=hb_side, entry=entry_px, amt=have, pid=pid,
                            source="心跳裸单守护",
                        )
                        return {"ok": True, "status": "heartbeat",
                                "action": "computed_sl" if sl > 0 else "computed_sl_failed", "sl": sl}
                    if sl > 0 and pid:
                        self.client.set_sl_tp(position_id=pid, instrument=self.symbol,
                                              stop_loss_price=round(sl, 2))
                        logger.warning(f"心跳裸单守护：补挂硬止损 @{sl}")
                        return {"ok": True, "status": "heartbeat", "action": "reattach_sl", "sl": sl}
                if not self._monitoring:
                    self._start_monitoring()
                return {"ok": True, "status": "heartbeat", "state": "in_sync"}

            # 反向持仓：TV 翻转我们漏了 -> 先平
            if have != 0 and live and live != hb_side:
                logger.warning(f"心跳催单：反向翻转 {live} -> {hb_side}，先平旧仓")
                self._stop_monitoring()
                if not self._clear_position(f"心跳催单-反向翻转 {live}->{hb_side}"):
                    self._safe_alert(f"[{self.symbol}] 心跳反手净场未确认，拒绝补开 {hb_side}")
                    return {"ok": False, "status": "heartbeat", "error": "clear_failed"}
                self.pipeline.reset_idle("hb_flip")
                have = 0

            # TV 有仓、VPS 空：补开（经追单确认观察窗）
            if have == 0:
                if self.symbol == "SNDK":
                    return {"ok": True, "status": "heartbeat", "state": "await_new_tv_open"}
                if time.time() < self._catchup_blocked_until:
                    self._chase_watch = {}
                    return {"ok": True, "status": "heartbeat", "state": "catchup_blocked",
                            "until": round(self._catchup_blocked_until, 0)}
                # 2026-09-15新增：更强的技术确认(双均线+3根同向+放量)可以
                # 抢在_chase_watch_step的多周期持续确认窗口跑完之前直接
                # 入场——今天OPENAI在B账户被误判止损后TV其实还在持有，
                # 就是这里要补的口子。trend_reentry标记只在真的尝试过
                # 开仓(成功或失败)时才出现，"未确认/冷却中/日上限"等
                # 中间状态原样落回下面既有的追单确认流程，不打断它。
                fast_path = self._maybe_trend_reentry(override_side=hb_side)
                if fast_path and fast_path.get("trend_reentry"):
                    return fast_path
                px = float(self._get_current_price() or 0)
                hb_sig = self._synthesize_open_signal(signal, hb_side, hb_entry, px)
                if float(hb_sig.atr or 0) <= 0:
                    self._safe_alert("心跳催单放弃：缺 ATR，无法管理")
                    return {"ok": True, "status": "heartbeat", "state": "no_atr"}
                if hb_entry > 0 and px > 0 and abs(px - hb_entry) / hb_entry > CATCHUP_MAX_DRIFT_PCT:
                    self._chase_watch = {}
                    self._safe_alert(f"心跳催单放弃：现价{px} 偏离 entry{hb_entry} 超 "
                                     f"{CATCHUP_MAX_DRIFT_PCT:.2%}")
                    return {"ok": True, "status": "heartbeat", "state": "drift_too_large"}

                ready, why = self._chase_watch_step(hb_side)
                if not ready:
                    return {"ok": True, "status": "heartbeat", "state": "chase_watch",
                            "detail": why, "count": self._chase_watch.get("count", 0)}

                logger.warning(f"追单确认通过（{why}）：补开 {hb_side} @现价{px}")
                self._chase_watch = {}
                self._chase_confirmed = False
                res = self._handle_open(hb_sig)
                res["catchup"] = True
                return res

            return {"ok": True, "status": "heartbeat"}

    # ==================== 追单确认观察窗 ====================

    def _trend_confirm_3tf(self, side: str) -> tuple:
        """30m / 60m / 4h 三周期方向一致性。返回 (同向数, 详情)。"""
        from smart_reentry_engine import _adx, _donchian
        agree = 0
        parts = []
        for tf, code, n in (("30m", 30, 400), ("60m", 60, 400), ("4h", 240, 200)):
            try:
                bars = self._get_risk_klines(code, n)
            except Exception:
                bars = []
            if not bars or len(bars) < 25:
                parts.append(f"{tf}:?")
                continue
            hh, ll, mid = _donchian(bars, 20)
            close = bars[-1][4]
            ok = (side == "LONG" and close > mid and close > ll) or \
                 (side == "SHORT" and close < mid and close < hh)
            agree += 1 if ok else 0
            parts.append(f"{tf}:{'✓' if ok else '✗'}")
        return agree, " ".join(parts)

    def _chase_watch_step(self, hb_side: str) -> tuple:
        """推进观察窗。返回 (是否放行, 详情)。"""
        now = time.time()
        if self._chase_confirmed:
            return True, "人工确认"
        if not CHASE_WATCH_ENABLED:
            agree, det = self._trend_confirm_3tf(hb_side)
            return (agree >= CHASE_3TF_REQUIRED), f"3TF {agree}/3 {det}"

        cw = self._chase_watch
        if cw and (cw.get("side") != hb_side or now - cw.get("last_ts", 0) > CHASE_STALE_SEC):
            cw = {}
        agree, det = self._trend_confirm_3tf(hb_side)
        if agree < CHASE_3TF_REQUIRED:
            self._chase_watch = {}
            return False, f"3TF不足 {agree}/{CHASE_3TF_REQUIRED} {det}"

        if not cw:
            self._chase_watch = {"side": hb_side, "first_ts": now, "count": 1, "last_ts": now}
            self._safe_alert(f"追单确认观察窗开启 {hb_side}（3TF {agree}/3）— "
                             f"需连续{CHASE_CONFIRM_COUNT}次/{CHASE_CONFIRM_MIN_SEC:.0f}s 或 /admin/confirm_catchup")
            return False, f"watch_started 3TF{agree}/3"
        cw["count"] = cw.get("count", 0) + 1
        cw["last_ts"] = now
        self._chase_watch = cw
        if cw["count"] >= CHASE_CONFIRM_COUNT and now - cw["first_ts"] >= CHASE_CONFIRM_MIN_SEC:
            return True, f"连续{cw['count']}次/{now-cw['first_ts']:.0f}s 3TF{agree}/3"
        return False, f"观察中 {cw['count']}/{CHASE_CONFIRM_COUNT}"

    def _safe_alert(self, msg: str):
        logger.warning(msg)
        try:
            self._dingtalk.report_coinw_tp(msg, 0)
        except Exception:
            pass

    def _position_has_algo_provenance(self, side: str, entry: float) -> bool:
        """2026-10-01新增：在open journal里查最近一条side+entry都匹配、
        且在OPEN_JOURNAL_PROVENANCE_WINDOW_SEC时间窗内的本引擎自己的开仓
        记录，找到才认定这笔仓位是本引擎自己通过TV信号开出来的。

        提取成独立方法，供recover_on_start()(重启接管)和两处心跳裸单
        守护共用同一套判定——宝贝明确要求"我自己开的单，系统不要管，
        不要平仓我的"，这几处原来都会对"没有有效硬止损的在场持仓"
        无条件现算+补挂一个全新硬止损，等于变相开始管理一笔可能是
        人工开的仓位。查无匹配记录时保守返回False(当人工仓位处理)，
        查询本身异常也保守返回False。"""
        try:
            side_u = str(side or "").upper()
            entry_f = float(entry or 0)
            if side_u not in ("LONG", "SHORT") or entry_f <= 0:
                return False
            for rec in reversed(self._iter_journal_entries("open")):
                rec_side = str(rec.get("side") or "").upper()
                rec_entry = float(rec.get("entry_px") or 0)
                rec_ts_str = str(rec.get("ts") or "")
                try:
                    rec_ts = time.mktime(time.strptime(rec_ts_str, "%Y-%m-%d %H:%M:%S")) if rec_ts_str else 0
                except ValueError:
                    rec_ts = 0
                if time.time() - rec_ts > OPEN_JOURNAL_PROVENANCE_WINDOW_SEC:
                    break
                if rec_side != side_u or rec_entry <= 0:
                    continue
                if abs(rec_entry - entry_f) / max(rec_entry, 1e-9) <= OPEN_JOURNAL_PROVENANCE_TOLERANCE:
                    return True
            return False
        except Exception as e:
            logger.warning(f"[{self.symbol}] open journal溯源检查异常(保守当人工仓位): {e}")
            return False

    # ==================== 开仓执行 ====================

    def _calc_position_size(self, balance: float, entry_price: float, tier=None) -> float:
        """计算仓位（tier=0/1/2 时按趋势强弱缩放名义，对齐币安）"""
        from defense_profiles import get_defense_profile
        profile = get_defense_profile(self.symbol)

        return profile.calc_position_size(balance, entry_price, tier)

    def _is_retryable_open_rejection(self, code, error_msg) -> bool:
        """只有短暂性拒单(如资金费结算窗口)才值得用限价单重试；保证金不足/
        参数错误等重试也没用，直接判失败更清楚，避免无意义地占用限价单
        名额(交易所挂单硬上限5笔)。"""
        try:
            if int(code) == 9010:
                return True
        except (TypeError, ValueError):
            pass
        msg = str(error_msg or "")
        return "funding" in msg.lower() or "结算" in msg

    def _retry_open_with_limit(self, action: str, tv_price: float, qty: float,
                                timeout_sec: float = 60.0, poll_interval: float = 3.0) -> dict:
        """市价开仓被拒(如资金费结算窗口)的兜底：改挂限价单，价格不劣于TV
        信号价——多单最高只出TV价的99.7%，空单最低只收TV价的100.3%，
        绝不比TV当时想要的价格更差。限定时间内轮询是否成交，超时未成交
        就撤单放弃，不留孤儿挂单长期追单(2026-08-08 实盘：BNB因资金费
        结算窗口被拒单后直接放弃，错过一次开仓信号)。"""
        from coinw_client import is_orders_query_failed

        side_u = str(action).upper()
        if side_u == "LONG":
            limit_price = round(float(tv_price) * 0.997, 2)
        else:
            limit_price = round(float(tv_price) * 1.003, 2)

        logger.warning(
            f"限价重试开仓: {side_u} {qty} @{limit_price} "
            f"(TV价{tv_price}，让利0.3%，不追差价)"
        )

        order = self.client.place_limit_order(
            side=action, quantity=qty, price=limit_price,
            instrument=self.symbol, reduce_only=False,
        )
        if not order or order.get("code") != 0:
            return {
                "ok": False,
                "error": f"限价重试提交失败: {(order or {}).get('msg', '无响应')}",
            }

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            time.sleep(poll_interval)
            pos = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
            if pos and float(pos.get("positionAmt") or pos.get("quantity") or 0) != 0:
                logger.info(f"限价重试成交: {side_u} @{limit_price}")
                return {"ok": True}

        logger.warning(f"限价重试超时({timeout_sec:.0f}s)未成交，撤单放弃")
        orders = self.client.get_open_orders(self.symbol, position_type="plan")
        if not is_orders_query_failed(orders):
            for o in orders:
                try:
                    opx = round(float(o.get("openPrice", 0) or o.get("orderPrice", 0)), 2)
                except (TypeError, ValueError):
                    continue
                if abs(opx - limit_price) <= 0.02 and o.get("id"):
                    self.client.cancel_order(o.get("id"), self.symbol)
                    break
        return {"ok": False, "error": "限价重试超时未成交"}

    # ---------- 开仓滑点阶梯 ----------

    _MIN_ORDER_ETH = 0.011  # < 1 张(0.01 ETH)的残量无法再挂单，视为已完成

    def _entry_residual_floor(self, qty: float) -> float:
        """2026-09-19修复(实盘复现XAUUSDT)：_MIN_ORDER_ETH是ETH时代遗留
        的绝对量(0.011)，当品种自己的下单量(qty，比如XAU这次仅
        0.009135，金价太高导致基础货币数量天然很小)本身就小于这个绝对
        量时，_wait_fill()里"want_qty - _MIN_ORDER_ETH"会算出负数，
        "have>=负数"对have=0也恒成立——刚挂单还没等任何成交，第一次
        轮询就被判定"①成交(被动限价,0滑点)"，之后按"已经开仓成功"的
        分支往下走，查真实持仓却"持仓未找到"，整个开仓流程当场报错
        中止，真正挂在交易所上的限价单反而没人管，一分钟后才被
        housekeep当孤儿单撤掉——这笔TV信号完全没开成仓，这正是"CoinW
        经常漏开单"的一个实锤根因。
        改成"绝对量跟相对量取更小者"：min(_MIN_ORDER_ETH, qty×10%)，
        用调用方传入的原始qty(不是会不断缩水的remaining)算，ETH等大
        额下单场景数值上更保守(不会变得更松)，XAU这类基础货币数量天生
        很小的品种就不会再出现"残量阈值比整笔下单量还大"这种荒谬结果。
        """
        try:
            q = float(qty or 0)
        except (TypeError, ValueError):
            q = 0.0
        if q <= 0:
            return self._MIN_ORDER_ETH
        return min(self._MIN_ORDER_ETH, q * 0.1)

    def _pos_qty(self) -> float:
        """当前实盘持仓的标的货币数量（强制 REST，绕缓存）。"""
        pos = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
        if not pos:
            return 0.0
        return float(pos.get("baseSize") or pos.get("positionAmt") or pos.get("quantity") or 0)

    def _cancel_open_limits(self):
        from coinw_client import is_orders_query_failed
        orders = self.client.get_open_orders(self.symbol, position_type="plan")
        if is_orders_query_failed(orders) or not orders:
            return
        for o in orders:
            if o.get("id"):
                self.client.cancel_order(o.get("id"), self.symbol)

    def _cancel_and_confirm_filled(self, want_qty: float) -> float:
        """撤单后主动核实：重试撤单+查询直到确认挂单真的清空了(最多3
        次)，再用强制REST读到的真实持仓量(_pos_qty)当权威的'已成交'
        值——不信任调用方上一次_wait_fill轮询到的have，那可能是撤单
        生效之前的陈旧快照。2026-10-02 SNDK实盘2倍头寸事故的根因就是
        信了这个陈旧快照：阶段1限价单在超时判定和撤单生效之间的窗口期
        偷偷成交，阶段2又按'未成交'补了一次全量，两腿都成交导致翻倍。
        仍有遗留挂单未确认清空时报警(不阻塞，至少这次查到的真实持仓量
        是准的，调用方用它重新算remaining不会凭空多算或少算)。"""
        from coinw_client import is_orders_query_failed
        confirmed_clear = False
        for _ in range(3):
            orders = self.client.get_open_orders(self.symbol, position_type="plan")
            if is_orders_query_failed(orders):
                time.sleep(0.5)
                continue
            if not orders:
                confirmed_clear = True
                break
            for o in orders:
                if o.get("id"):
                    self.client.cancel_order(o.get("id"), self.symbol)
            time.sleep(0.5)
        if not confirmed_clear:
            logger.error(f"[{self.symbol}] 开仓阶梯撤单后仍有遗留挂单未确认清空")
            try:
                self._safe_alert(f"[{self.symbol}] 开仓阶梯撤单未确认清空，请核查是否重复成交")
            except Exception:
                pass
        return self._pos_qty()


    def _adverse_gap_pct(self, action: str, tv_price: float) -> float:
        """现价相对 TV 价的【逆向】偏离比例（>0 表示对我们不利：多单买贵/空单卖便宜）。"""
        last = float(self._get_current_price() or 0)
        if last <= 0 or tv_price <= 0:
            return 0.0
        if str(action).upper() == "LONG":
            return (last - tv_price) / tv_price
        return (tv_price - last) / tv_price

    def _wait_fill(self, action: str, tv_price: float, deadline: float, want_qty: float):
        """轮询到成交 / deadline / 逆向放弃。返回 (状态, 已成交量)。"""
        floor = self._entry_residual_floor(want_qty)
        while time.time() < deadline:
            time.sleep(ENTRY_POLL_SEC)
            have = self._pos_qty()
            # have>0是硬性前提——2026-09-19修复：残量阈值floor不能让
            # have=0(压根没成交)也被判定成"filled"，见_entry_residual_
            # floor顶部注释。
            if have > 0 and have >= want_qty - floor:
                return "filled", have
            if self._adverse_gap_pct(action, tv_price) > ENTRY_ABORT_PCT:
                return "abort", have
        return "timeout", self._pos_qty()

    def _open_sndk_single_limit(self, action: str, tv_price: float, qty: float, adding: bool = False) -> dict:
        """One bounded order only: a second ladder leg can double-fill a late first leg.
        adding=True(2026-10-05)：给"加仓/本金补仓"用——此前这条路径要求开仓前必须空仓，所以SNDK
        的加仓和补仓永远下不出去(日志：补仓下单未成交: SNDK开仓前未确认净场)。加仓模式要求已有同向
        持仓，数量按"已有+本笔"校验，总量封顶用1.98倍(capped_add_qty)。"""
        from coinw_client import is_orders_query_failed, is_position_query_failed
        from sndk_live_policy import capped_add_qty, capped_entry_qty, coinw_base_qty

        if self.client.ip_rate_limit_remaining() > 0:
            return {"ok": False, "error": "SNDK持仓核查限流"}
        before = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
        if is_position_query_failed(before):
            return {"ok": False, "error": "SNDK开仓前未确认净场"}
        side = str(action).upper()
        base_before = 0.0
        if adding:
            if not before or self._live_side(before) != side:
                return {"ok": False, "error": "SNDK加仓前未确认同向持仓"}
            base_before = coinw_base_qty(before)
        elif before:
            return {"ok": False, "error": "SNDK开仓前未确认净场"}

        limit_price = round(float(tv_price) * (1 + ENTRY_SLIP_CAP_PCT), 2) if side == "LONG" \
            else round(float(tv_price) * (1 - ENTRY_SLIP_CAP_PCT), 2)
        equity = self.client.get_futures_usdt_balance()
        if adding:
            order_qty = capped_add_qty(equity, base_before, max(float(tv_price), limit_price), qty, 0.001)
        else:
            order_qty = capped_entry_qty(equity, qty, max(float(tv_price), limit_price), 0.001)
        if order_qty <= 0:
            return {"ok": False, "error": "SNDK一倍本金订单数量无效"}

        order = self.client.place_limit_order(
            side=action, quantity=order_qty, price=limit_price,
            instrument=self.symbol, reduce_only=False,
        )
        if not order or order.get("code") != 0:
            return {"ok": False, "error": "SNDK单笔限价开仓未被接受"}
        order_id = str((order.get("data") or {}).get("value") or order.get("id") or "")
        if not order_id:
            logger.error("[SNDK] 限价单已提交但没有订单ID，禁止补挂")
            self._symbol_paused = True
            self._safe_alert("[SNDK] 开仓订单ID缺失，已禁止追单；请核查持仓和止损")
            return {"ok": False, "error": "SNDK订单ID缺失"}

        deadline = time.time() + max(8.0, ENTRY_MARKETABLE_SEC)
        while time.time() < deadline:
            time.sleep(ENTRY_POLL_SEC)
            pos = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
            if not is_position_query_failed(pos) and pos and coinw_base_qty(pos) >= base_before + order_qty - 0.001:
                break
            if self._adverse_gap_pct(action, float(tv_price)) > ENTRY_ABORT_PCT:
                break

        # Cancel the exact order ID and inspect the exchange again. Never submit
        # a second order or market fallback for SNDK, even if cancellation is uncertain.
        settled = False
        for _ in range(3):
            orders = self.client.get_open_orders(self.symbol, position_type="plan", prefer_cache=False)
            if is_orders_query_failed(orders):
                time.sleep(0.5)
                continue
            if not any(str(item.get("id")) == order_id for item in orders):
                settled = True
                break
            self.client.cancel_order(order_id, self.symbol)
            time.sleep(0.5)
        if not settled:
            self._symbol_paused = True
            self._safe_alert("[SNDK] 限价单撤销状态未确认，禁止后续补单")

        pos = None
        for _ in range(3):
            pos = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
            if not is_position_query_failed(pos):
                break
            time.sleep(0.5)
        if is_position_query_failed(pos) or not pos:
            if is_position_query_failed(pos):
                self._symbol_paused = True
                self._safe_alert("[SNDK] 开仓后持仓查询失败，禁止重试；请核查交易所止损")
            return {"ok": False, "error": "SNDK开仓后未确认持仓；没有补单"}
        live_qty = coinw_base_qty(pos)
        if live_qty <= 0 or self._live_side(pos) != side:
            self._symbol_paused = True
            return {"ok": False, "error": "SNDK成交方向或数量异常"}
        if adding and live_qty <= base_before + 1e-9:
            return {"ok": False, "error": "SNDK加仓限价未成交(持仓未增加)"}
        if live_qty > base_before + order_qty + 0.001:
            self._safe_alert(f"[SNDK] 持仓超过单笔委托量: {live_qty} > {base_before + order_qty}，停止新增")
            self._symbol_paused = True
        return {"ok": True, "via": "sndk_single_limit", "pending_order": not settled}

    def _open_with_ladder(self, action: str, tv_price: float, qty: float,
                          tier: str = None, adding: bool = False) -> dict:
        """
        TV 信号进场：被动限价(TV价, maker) -> 可成交限价(让利≤滑点上限) -> 市价兜底。
        强趋势 tier=2：跳过①被动限价，直接②可成交限价(短窗)+③市价 —— 立即进入。
        现价逆向跑过 ENTRY_ABORT_PCT 则撤单放弃这次信号。返回 {ok, via, error, aborted}。
        """
        if self.symbol == "SNDK":
            return self._open_sndk_single_limit(action, tv_price, qty, adding=adding)
        side_u = str(action).upper()
        strong = ENTRY_STRONG_IMMEDIATE and str(tier or "").strip() == "2"
        if not ENTRY_LADDER_ENABLED:
            r = self.client.place_market_order(side=action, quantity=qty, instrument=self.symbol)
            if r and r.get("code") == 0:
                return {"ok": True, "via": "market_direct"}
            code = (r or {}).get("code")
            msg = (r or {}).get("msg", "无响应")
            if self._is_retryable_open_rejection(code, msg):
                rr = self._retry_open_with_limit(action, tv_price, qty)
                return {"ok": bool(rr.get("ok")), "via": "limit_retry", "error": rr.get("error", "")}
            return {"ok": False, "via": "market_direct", "error": msg}

        t0 = time.time()
        tvp = float(tv_price)
        remaining = float(qty)

        # 阶段1：TV 价被动限价（做 maker，绝不劣于 TV 价）—— 强趋势档跳过
        if strong:
            logger.info(f"[开仓阶梯] 强趋势档 tier=2 → 跳过①被动限价，立即可成交限价+市价")
        else:
            px1 = round(tvp, 2)
            logger.info(f"[开仓阶梯] ①被动限价 {side_u} {qty} @{px1} (TV价 · {ENTRY_PASSIVE_SEC:.0f}s)")
            o1 = self.client.place_limit_order(side=action, quantity=qty, price=px1,
                                               instrument=self.symbol, reduce_only=False)
            if o1 and (o1.get("code") == 0 or o1.get("id")):
                st, have = self._wait_fill(action, tvp, t0 + ENTRY_PASSIVE_SEC, qty)
                remaining = max(0.0, float(qty) - have)
                if st == "filled":
                    logger.info("[开仓阶梯] ①成交(被动限价, 0 滑点)")
                    return {"ok": True, "via": "passive_limit"}
                if st == "abort":
                    self._cancel_open_limits()
                    return {"ok": False, "via": "passive_limit", "aborted": True,
                            "error": f"信号逆向跑过 {ENTRY_ABORT_PCT:.2%}，放弃进场"}
            remaining = max(0.0, float(qty) - self._cancel_and_confirm_filled(qty))

        # 阶段2：让利到滑点上限的可成交限价（强趋势档用短窗）
        mk_deadline = (t0 + ENTRY_STRONG_MARKETABLE_SEC) if strong else (t0 + ENTRY_MARKETABLE_SEC)
        # 2026-09-19修复：用_entry_residual_floor(qty)(相对原始下单量的
        # 阈值)替代绝对量_MIN_ORDER_ETH——XAU这类基础货币数量天生很小的
        # 品种，原来的0.011绝对量比整笔下单量还大，见_entry_residual_
        # floor顶部注释。
        if remaining >= self._entry_residual_floor(qty):
            px2 = round(tvp * (1.0 + ENTRY_SLIP_CAP_PCT), 2) if side_u == "LONG" \
                else round(tvp * (1.0 - ENTRY_SLIP_CAP_PCT), 2)
            logger.info(f"[开仓阶梯] ②可成交限价 {side_u} {remaining:.6f} @{px2} "
                        f"(让利≤{ENTRY_SLIP_CAP_PCT:.2%} · 到{mk_deadline - t0:.0f}s)")
            o2 = self.client.place_limit_order(side=action, quantity=remaining, price=px2,
                                               instrument=self.symbol, reduce_only=False)
            if o2 and (o2.get("code") == 0 or o2.get("id")):
                st, have = self._wait_fill(action, tvp, mk_deadline, qty)
                remaining = max(0.0, float(qty) - have)
                if st == "filled":
                    logger.info("[开仓阶梯] ②成交(可成交限价, 滑点已封顶)")
                    return {"ok": True, "via": "marketable_limit"}
                if st == "abort":
                    self._cancel_open_limits()
                    return {"ok": False, "via": "marketable_limit", "aborted": True,
                            "error": f"信号逆向跑过 {ENTRY_ABORT_PCT:.2%}，放弃进场"}
            remaining = max(0.0, float(qty) - self._cancel_and_confirm_filled(qty))

        # 阶段3：市价兜底（仅剩余量）
        if remaining < self._entry_residual_floor(qty):
            return {"ok": True, "via": "limit_all"}
        if self._adverse_gap_pct(action, tvp) > ENTRY_ABORT_PCT:
            return {"ok": False, "via": "market_fallback", "aborted": True,
                    "error": f"兜底前信号已逆向跑过 {ENTRY_ABORT_PCT:.2%}，放弃进场"}
        logger.warning(f"[开仓阶梯] ③市价兜底 {side_u} {remaining:.6f}")
        r = self.client.place_market_order(side=action, quantity=remaining, instrument=self.symbol)
        if r and r.get("code") == 0:
            return {"ok": True, "via": "market_fallback"}
        code = (r or {}).get("code")
        msg = (r or {}).get("msg", "无响应")
        if self._is_retryable_open_rejection(code, msg):
            rr = self._retry_open_with_limit(action, tvp, remaining, timeout_sec=20.0)
            return {"ok": bool(rr.get("ok")), "via": "market_fallback_retry", "error": rr.get("error", "")}
        return {"ok": False, "via": "market_fallback", "error": msg}

    def _execute_open(self, action: str, price: float, qty: float, signal) -> dict:
        """执行开仓"""
        try:
            # 获取当前价格作为参考
            current_price = self._get_current_price()

            # 进场滑点阶梯：被动限价 -> 可成交限价 -> 市价兜底
            led = self._open_with_ladder(action, price, qty, tier=getattr(signal, "tier", None))
            if not led.get("ok"):
                logger.error(f"开仓未成交: {led.get('error')} (via={led.get('via')})")
                return {"ok": False, "error": led.get("error", "开仓未成交"),
                        "aborted": bool(led.get("aborted"))}
            logger.info(f"进场方式: {led.get('via')}")

            # 获取持仓信息——开仓前_clear_position()已经把本地持仓缓存写成
            # "无持仓"(POSITION_CACHE_TTL_SEC=8s内有效)，这里如果沿用默认的
            # prefer_ws=True/force_rest=False，大概率直接读到那份下单前的
            # 陈旧缓存，把刚成交的仓位误判成"没有"。必须force_rest=True绕开
            # 缓存；交易所侧成交回报也可能有轻微延迟，所以做几次重试。
            pos = None
            for _ in range(3):
                time.sleep(0.5)  # 等待成交/交易所侧持仓数据落地
                pos = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
                if pos and float(pos.get("positionAmt") or pos.get("quantity") or 0) != 0:
                    break

            # get_position()可能返回两种形状：REST原始行(quantity字段)或
            # WS缓存行(positionAmt字段)——都要检查，否则缓存了旧的"已归零"
            # 记录时not pos判断不出来(非空dict恒真)
            if not pos or float(pos.get("positionAmt") or pos.get("quantity") or 0) == 0:
                return {"ok": False, "error": "持仓未找到"}

            position_id = pos.get("id", "")
            entry_price = float(pos.get("openPrice", current_price))
            # pos["quantity"]不是真实ETH数量——真实持仓验证过(2026-08-08)：
            # 那是张数相关的内部值(quantityUnit=1场景下的原始委托量)，实际
            # 持有的标的货币数量就是baseSize本身(已经是持仓总量，不是"每张
            # 面值"，实测1张=0.01 ETH时baseSize=0.01，5张=0.05 ETH时
            # baseSize=0.05，乘以totalPiece会再放大成倍——之前那版乘了
            # totalPiece，把5张仓位算成0.25 ETH，比真实的0.05 ETH大5倍)。
            base_size = float(pos.get("baseSize", 0) or 0)
            total_pieces = float(pos.get("totalPiece", pos.get("currentPiece", 0)) or 0)
            actual_qty = base_size if base_size else float(pos.get("quantity", qty))

            logger.info(f"开仓成功: {action} {actual_qty} @ {entry_price} ({total_pieces}张)")

            # 2026-10-01新增："open"kind journal——_journal_path/_append_journal
            # 这套基础设施2026-09-19就建好了，当时注释明说"目前只用close这
            # 一种，先不做币安那边tv/open/exchange的全套"，今天补上open这
            # 一种：宝贝要求"我自己开的单，系统不要管"，需要在recover_on_
            # start()重启接管时区分"这笔仓位是不是这个引擎自己通过TV信号
            # 开出来的"——没有这条open journal就永远做不到这个区分，只能
            # 无条件接管。纯记录，不影响这次开仓本身的任何后续步骤。
            try:
                self._append_journal(self._journal_path("open"), {
                    "side": action, "entry_px": float(entry_price or 0),
                    "qty": float(actual_qty or 0),
                })
            except Exception as e:
                logger.debug(f"[{self.symbol}] open journal写入跳过: {e}")

            return {
                "ok": True,
                "entry_price": entry_price,
                "qty": actual_qty,
                "total_pieces": total_pieces,
                "position_id": position_id,
                "atr": signal.atr,
            }

        except Exception as e:
            logger.error(f"开仓异常: {e}")
            return {"ok": False, "error": str(e)}

    # ==================== 防线设置 ====================

    def _place_defense_orders(self, signal, entry_result: dict) -> bool:
        """设置三层防线"""
        from atr_scenario import calc_smart_hard_stop_price, STRUCT_LOOKBACK_BARS, ATR_PERIOD, _atr_last

        try:
            position_id = entry_result.get("position_id", "")
            entry_price = entry_result.get("entry_price", 0)
            direction = signal.action

            # 1) 硬止损——2026-09-12改为"综合硬止损"（宝贝拍板：不理会TV自己
            # 算的stop_loss，太木讷；VPS自己拉K线判断趋势更智慧）。TV只给
            # 方向，止损完全由VPS独立算：结构摆动点(fractal pivot) + 分档
            # ATR保护带，取更保守者。klines用VPS自己拉的30m原生K线（不依赖
            # TV那边用的图表周期，VPS自主判断，周期choice见atr_scenario.py
            # 模块docstring）。
            # 2026-09-20新增breath_atr：MU/XPD/XAU实盘复现止损距entry仅
            # 0.1~0.2%(tight模式无下限，30分钟ATR/结构位本身比品种真实
            # 呼吸周期短很多)，额外拉一次品种自己原生TV周期的K线算ATR，
            # 传给calc_smart_hard_stop_price做tight模式下限参考——拉取
            # 失败时传None，函数内部退回用30分钟K线自己的ATR做下限。
            # 2026-10-01新增：SNDK(EMA7/30策略，Pine脚本固定2.5×ATR硬止损
            # 作参考)要求"硬止损原策略给了参考价，思考如何设置比较合适"——
            # tier缺失/弱档时k=1.5×ATR，比TV自己的2.5×ATR参考紧40%，只靠
            # breath_atr那道下限(是从MU/XPD/XAU"止损太紧"事故反推的比例，
            # 不是针对"跟TV比"设计的)未必兜得住。把TV自己给的stop_loss
            # 参考距离传进去，函数内部会跟breath_atr那道下限取更宽的一个，
            # 用project_radar_tv_stop_floor_20260901已经实盘验证过的50%
            # 比例，跟币安B系统(atr_scenario.py同一天同步改)保持一致。
            # signal.stop_loss<=0(TV没传这个字段)时tv_reference_dist=None，
            # 不生效，不影响其它品种原有行为。
            _tv_sl_ref_now = float(getattr(signal, "stop_loss", 0) or 0)
            klines = []
            if self.symbol in ("SNDK", "ZEC"):
                from sndk_live_policy import valid_tv_stop
                hard_sl_price = valid_tv_stop(
                    direction, entry_price, _tv_sl_ref_now, self._get_current_price(),
                )
                if hard_sl_price <= 0:
                    logger.error("[SNDK] 成交后TV止损价无效，拒绝使用30分钟ATR代替")
                    return False
                hard_sl_meta, ok, err = {"source": "tv_stop_loss"}, True, ""
            else:
                _need_bars = STRUCT_LOOKBACK_BARS + ATR_PERIOD + 10
                klines = self._get_risk_klines(30, _need_bars)
                breath_atr = None
                try:
                    breath_bars = self._fetch_dual_ma_exit_klines()
                    breath_atr = _atr_last(breath_bars or [], ATR_PERIOD) or None
                except Exception as e:
                    logger.debug(f"[{self.symbol}] 综合硬止损：呼吸周期ATR拉取失败(退回30m自算下限): {e}")
                tv_reference_dist = (
                    abs(float(entry_price or 0) - _tv_sl_ref_now)
                    if _tv_sl_ref_now > 0 and entry_price else None
                )
                hard_sl_price, hard_sl_meta, ok, err = calc_smart_hard_stop_price(
                    side=direction,
                    entry_price=entry_price,
                    klines=klines,
                    tier=signal.tier,
                    breath_atr=breath_atr,
                    tv_reference_dist=tv_reference_dist,
                )

            if not ok:
                logger.error(
                    f"[{self.symbol}] 综合硬止损计算失败: {err} "
                    f"(klines={len(klines or [])}根)"
                )
                return False
            logger.info(f"[{self.symbol}] 综合硬止损 @{hard_sl_price} | {hard_sl_meta}")

            # 设置止损（通过TPSL接口）
            sl_result = self.client.set_sl_tp(
                position_id=position_id,
                instrument=self.symbol,
                stop_loss_price=hard_sl_price,
                stop_from=2,  # 市价触发
                price_type=3,  # 标记价格
            )
            if not sl_result or sl_result.get("code") != 0:
                logger.error(f"硬止损挂单失败: {sl_result}")
                if self.symbol in ("SNDK", "ZEC"):
                    return False

            if self.symbol in ("SNDK", "ZEC"):
                stop_confirmed = False
                for _ in range(4):
                    records = self.client.get_tp_sl_info(position_id) or []
                    stop_confirmed = any(
                        int(r.get("stopType") or 0) != 1
                        and int(r.get("triggerStatus") or 0) == 0
                        and abs(float(r.get("stopLossPrice") or 0) - hard_sl_price)
                        <= max(0.01, hard_sl_price * 0.0001)
                        for r in records
                    )
                    if stop_confirmed:
                        break
                    time.sleep(0.5)
                if not stop_confirmed:
                    logger.error("[SNDK] 交易所未返回匹配TV价格的有效止损单")
                    return False

            self.pipeline.data["hard_sl_px"] = hard_sl_price

            # 2) TP1 + TP2 —— 改用CoinW原生分批止盈接口(/v1/perpum/addTpsl,
            # stopType=1分批)，按position_id+张数(closePiece)设置，由交易所
            # 在触发价自动平掉对应张数。之前用"同方向限价单模拟reduce_only"
            # 的方式是错的：CoinW下单接口没有reduce_only语义，方向填的是
            # signal.action(跟开仓同向)，等于挂了两个"买入"限价单，价格又都
            # 在开仓价上方——一旦挂上就被当成可成交的买单直接成交，变成两次
            # 加仓而不是止盈(2026-08-08真实下单验证复现，仓位被从0.01 ETH
            # 加到0.29 ETH)。原有qty计算还用错了字段，进一步放大了这个问题。
            total_pieces = float(entry_result.get("total_pieces", 0) or 0)
            total_qty = float(entry_result.get("qty", 0) or 0)
            per_piece_qty = (total_qty / total_pieces) if total_pieces > 0 else 0.0
            tp1_pieces = round(total_pieces * 0.10)
            tp2_pieces = round(total_pieces * 0.20)
            # 2026-09-14修复(实盘复现XRPUSDT一笔tp_slice审计误判触发全局
            # 暂停)：_run_audit()读facts["contract_unit"]时此前一直硬编码
            # 0.01(注释写着"CoinW ETH 1张=0.01 ETH")，但每个品种的真实
            # 1张换算成币量完全不同(比如这笔XRP实盘是80.0 XRP=8.0张，
            # 1张=10 XRP，不是0.01)——审计的取整容差算错了尺度，10%/20%
            # 取整产生的正常drift被误判成真实异常，触发不必要的交易暂停。
            # 这里直接用这笔仓位自己刚算出来的per_piece_qty，每个品种、
            # 每次开仓都用当下真实值，不再猜一个全品种通用常数。
            if per_piece_qty > 0:
                self.pipeline.data["contract_unit"] = per_piece_qty

            if signal.tp1 > 0 and self.symbol not in TOPUP_SYMBOLS:  # 2026-10-04: ZEC/SNDK不挂分批止盈(全程靠雷达+TV离场)
                if tp1_pieces > 0:
                    tp1_result = self.client.set_batch_sl_tp(
                        position_id=position_id,
                        instrument=self.symbol,
                        stop_profit_price=signal.tp1,
                        close_piece=tp1_pieces,
                        stop_type=1,  # 分批止盈
                        stop_from=2,  # 市价触发
                        price_type=3,  # 标记价格
                    )
                    if not tp1_result or tp1_result.get("code") != 0:
                        logger.error(f"TP1挂单失败: {tp1_result}")
                    # qty字段保留给_run_audit的tp_slice一致性检查用(ETH等值)，
                    # pieces是CoinW原生张数，两个都存，缺一个督察官那边就读不到
                    self.pipeline.data["tp1"] = {"px": signal.tp1, "pieces": tp1_pieces, "qty": tp1_pieces * per_piece_qty}
                else:
                    logger.warning(f"TP1跳过: 仓位仅{total_pieces}张，10%不足1张最小单位")
                    self.pipeline.data["tp1"] = {"px": signal.tp1, "pieces": 0, "qty": 0.0}

            if signal.tp2 > 0 and self.symbol not in TOPUP_SYMBOLS:
                if tp2_pieces > 0:
                    tp2_result = self.client.set_batch_sl_tp(
                        position_id=position_id,
                        instrument=self.symbol,
                        stop_profit_price=signal.tp2,
                        close_piece=tp2_pieces,
                        stop_type=1,
                        stop_from=2,
                        price_type=3,
                    )
                    if not tp2_result or tp2_result.get("code") != 0:
                        logger.error(f"TP2挂单失败: {tp2_result}")
                    self.pipeline.data["tp2"] = {"px": signal.tp2, "pieces": tp2_pieces, "qty": tp2_pieces * per_piece_qty}
                else:
                    logger.warning(f"TP2跳过: 仓位仅{total_pieces}张，20%不足1张最小单位")
                    self.pipeline.data["tp2"] = {"px": signal.tp2, "pieces": 0, "qty": 0.0}

            # 3) 雷达初始化（休眠状态）——武装TP1/TP2/entry/ATR/tier供
            # should_activate算激活线(2026-09-20起改成entry沿盈利方向
            # 推进min(0.8×TP1距离, ATR_MULT×ATR)，不再是(TP1+TP2)中点，
            # 见breath_stop.py::_activation_gate_price顶部注释)，不依赖
            # TP1/TP2是否真的成交
            self.radar.set_atr(signal.atr)
            self._save_tv_open_atr(direction, entry_price, signal.atr)
            self.radar.reset()
            self.radar.arm(
                tp1_price=signal.tp1, tp2_price=signal.tp2, direction=direction,
                entry_price=entry_price, tier=str(signal.tier),
            )
            self.radar.set_reentry_count(1 if getattr(self, "_is_reentry_open", False) else 0)

            logger.info(f"防线就绪: SL={hard_sl_price}, TP1={signal.tp1}, TP2={signal.tp2}")

            return True

        except Exception as e:
            logger.error(f"防线设置异常: {e}")
            return False

    # ==================== 监控循环 ====================

    def _start_monitoring(self):
        """启动监控"""
        if self._monitoring:
            return

        self._monitoring = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name=f"coinw-monitor-{self.symbol}"
        )
        self._monitor_thread.start()
        logger.info(f"监控启动: {self.symbol}")

    def _stop_monitoring(self):
        """停止监控"""
        self._monitoring = False

    def _monitor_loop(self):
        """监控循环"""
        import breath_profiles

        while self._monitoring:
            try:
                self._loop_beat = time.time()  # watchdog 心跳
                # 1) 检查持仓
                pos = self.client.get_position(self.symbol)
                current_price = self._get_current_price()

                if not pos or float(pos.get("positionAmt") or pos.get("quantity") or 0) == 0:
                    # 仓位归零
                    self._on_position_zero(curr_px=float(current_price or 0))
                    break

                # 2) 检查TP成交——仅用于账本记录/日志核对，不再作为雷达激活
                # 的前提条件(对齐币安v2.1规格：TP是否成交只是核对项，不是
                # 激活门槛)。CoinW的/v1/perpum/TPSL查询接口直接给
                # triggerStatus(0未触发/1已触发)，比自己猜靠谱，仍然查一下
                # 存进pipeline方便审计/复盘。
                self._check_tp_fills()

                # 2.5) 裸单守护：持仓在场但交易所没有有效硬止损、雷达也没接管
                #      -> 补挂硬止损（每 ~24s 查一次，限流）
                self._naked_tick += 1
                if NAKED_GUARD_ENABLED and self._naked_tick % 6 == 0 \
                        and not self.radar.get_state().activated:
                    try:
                        if not self._hard_sl_present():
                            sl = float(self.pipeline.data.get("hard_sl_px") or 0)
                            pid = self.pipeline.data.get("position_id")
                            if sl > 0 and pid:
                                self.client.set_sl_tp(position_id=pid, instrument=self.symbol,
                                                      stop_loss_price=round(sl, 2))
                                logger.warning(f"裸单守护：补挂硬止损 @{sl}")
                    except Exception as _e:
                        logger.debug(f"裸单守护检查异常: {_e}")

                # 3) 雷达激活检查——纯价格判断：现价到没到激活线
                # ((TP1+TP2)/2中点，首次开仓)，不等TP1/TP2真的成交。CoinW
                # 小仓位下TP1经常因不足1张最小单位被跳过，继续拿"TP成交"当
                # 前提会导致雷达永远激活不了(2026-08-08真实测试发现此问题)。
                if not self.radar.get_state().activated:
                    should, reason = self.radar.should_activate(current_price)
                    if should:
                        self._activate_radar(current_price)

                # 4) 雷达止损更新（币安 v2.1 价格分区 + regime 自适应 + 三条硬地板 + tp2_patience）
                profile = self._regime_profile(breath_profiles.get_breath_profile(self.symbol))
                tp1_px = float((self.pipeline.data.get("tp1") or {}).get("px", 0) or 0)
                tp2_px = float((self.pipeline.data.get("tp2") or {}).get("px", 0) or 0)
                _tp3 = self.pipeline.data.get("tp3")
                tp3_px = float(_tp3.get("px", 0) or 0) if isinstance(_tp3, dict) else float(_tp3 or 0)
                # TV 止损空间 = |entry − 硬止损|，喂给「雷达最多比 TV 紧 35%」硬地板
                _entry = float(self.pipeline.data.get("entry") or 0)
                _hsl = float(self.pipeline.data.get("hard_sl_px") or 0)
                tv_stop_dist = abs(_entry - _hsl) if (_entry > 0 and _hsl > 0) else 0.0
                _prev_sl = float(self.radar.get_state().current_sl or 0)
                new_sl = self.radar.update(
                    current_price, profile,
                    tp1_px=tp1_px, tp2_px=tp2_px, tp3_px=tp3_px,
                    tv_stop_dist=tv_stop_dist,
                )
                if self.symbol in TOPUP_SYMBOLS:
                    new_sl = self._dyn_trail_radar_step(_prev_sl)

                if new_sl:
                    self._update_radar_sl(new_sl)
                try:
                    self._verify_radar_sl_on_exchange()
                except Exception as _e:
                    logger.debug(f"[{self.symbol}] 雷达止损交易所对账异常: {_e}")

                # 4.5) 突发放量反转K线快速锁保本——见IMPULSE_EXIT_*常量顶部
                # 注释。三层防线里最快的一层，排在双均线破位检查之前：宝贝
                # 实盘截图复盘发现"等阳线站上双均线再平仓就已经晚了"，这个
                # 检查不等双均线正式突破确认，只看最新一根K线自己够不够
                # "决定性"，够的话立刻锁到保本价。
                if IMPULSE_EXIT_ENABLED:
                    try:
                        self._maybe_fast_lock_on_impulse_candle(current_price)
                    except Exception as _e:
                        logger.debug(f"突发反转K线快速锁保本异常: {_e}")

                # 5) 双均线破位快速平仓——见DUAL_MA_EXIT_*常量顶部注释。故意
                # 排在反转锁利**之前**调用——宝贝原话"双均线权重大于反转
                # 锁利，因为它是最快速反应市场趋势的、最敏捷的一个"，优先
                # 让双均线先判断。真放量确认破位时直接清仓；没确认时收紧到
                # 至少不低于保本线(内部顺带算了一次跟反转锁利同一个保本
                # 公式，取更紧的那个)。
                if DUAL_MA_EXIT_ENABLED:
                    try:
                        self._maybe_fast_exit_on_dual_ma_break(current_price)
                    except Exception as _e:
                        logger.debug(f"双均线破位快速平仓异常: {_e}")

                # 5.5) 反转锁利：4h 逆向放量反转 + 浮盈中 -> 收保本
                if REVLOCK_ENABLED:
                    try:
                        self._apply_reversal_lock(current_price)
                    except Exception as _e:
                        logger.debug(f"反转锁利异常: {_e}")

                # 5.6~5.8) 深盈利保护三层(2026-09-15移植自币安)：大赢家
                # 利润地板/利润回吐刹车/TV僵局收紧。三个都只朝有利方向
                # 棘轮止损，互相独立，谁锁得更紧生效谁的。
                try:
                    self._maybe_lock_profit_on_big_win(current_price)
                except Exception as _e:
                    logger.debug(f"大赢家利润地板异常: {_e}")
                try:
                    self._maybe_tighten_on_profit_giveback(current_price)
                except Exception as _e:
                    logger.debug(f"利润回吐刹车异常: {_e}")
                try:
                    self._maybe_tighten_on_tv_exit_stall(current_price)
                except Exception as _e:
                    logger.debug(f"TV僵局收紧异常: {_e}")
                try:
                    self._check_principal_topup(current_price)
                except Exception as _e:
                    logger.warning(f"[{self.symbol}] 本金自检补仓异常: {_e}")
                try:
                    self._audit_integrity(current_price)
                except Exception as _e:
                    logger.warning(f"[{self.symbol}] 仓位/止损完整性审计异常: {_e}")
                try:
                    self._check_pyramid_add(current_price)
                except Exception as _e:
                    logger.debug(f"均线发散确认加仓检查异常: {_e}")
                try:
                    self._check_4h_reversal_tighten()
                except Exception as _e:
                    logger.debug(f"4H快线反转预警检查异常: {_e}")

                # 每 ~60s 打一条监控存活/状态日志（冒烟/复盘可见）
                if self._naked_tick % 15 == 0:
                    _rs = self.radar.get_state()
                    _e = float(self.pipeline.data.get("entry") or 0)
                    _pnl = ((current_price - _e) if str(self.pipeline.data.get("side") or "").upper() == "LONG"
                            else (_e - current_price)) if _e else 0.0
                    logger.info(f"[监控] {self.symbol} px={current_price} 浮盈={_pnl:+.2f} "
                                f"雷达={'激活' if _rs.activated else '待命'} SL={_rs.current_sl or self.pipeline.data.get('hard_sl_px')} "
                                f"step={_rs.step_count} phase={_rs.phase}")

                # 等待
                time.sleep(4)  # 雷达间隔

            except Exception as e:
                logger.error(f"监控异常: {e}")
                time.sleep(5)

    def _check_tp_fills(self) -> tuple:
        """
        查交易所原生SL/TP记录，核对TP1/TP2是否真的成交(triggerStatus==1)。
        按挂单时记的px匹配对应记录(浮点直接比较——CoinW原样回显我们发送的
        stopProfitPrice，不存在自己内部再做四舍五入的问题)。跳过的档位
        (pieces==0，没真挂过)不查、直接当未成交处理。
        """
        position_id = self.pipeline.data.get("position_id", "")
        tp1_state = self.pipeline.data.get("tp1") or {}
        tp2_state = self.pipeline.data.get("tp2") or {}
        tp1_filled = bool(tp1_state.get("filled"))
        tp2_filled = bool(tp2_state.get("filled"))

        if not position_id or (tp1_filled and tp2_filled):
            return tp1_filled, tp2_filled
        if not tp1_state.get("pieces") and not tp2_state.get("pieces"):
            return tp1_filled, tp2_filled

        records = self.client.get_tp_sl_info(position_id)
        for r in records:
            if int(r.get("stopType") or 0) != 1:  # 只看分批止盈(TP1/TP2)，不是整仓硬止损
                continue
            px = float(r.get("stopProfitPrice") or 0)
            triggered = int(r.get("triggerStatus") or 0) == 1
            if not tp1_filled and tp1_state.get("pieces") and abs(px - float(tp1_state.get("px", 0))) < 1e-6:
                tp1_filled = triggered
            if not tp2_filled and tp2_state.get("pieces") and abs(px - float(tp2_state.get("px", 0))) < 1e-6:
                tp2_filled = triggered

        if tp1_filled and not tp1_state.get("filled"):
            self.pipeline.data["tp1"]["filled"] = True
            logger.info(f"TP1成交确认: {self.symbol} @{tp1_state.get('px')}")
        if tp2_filled and not tp2_state.get("filled"):
            self.pipeline.data["tp2"]["filled"] = True
            logger.info(f"TP2成交确认: {self.symbol} @{tp2_state.get('px')}")

        return tp1_filled, tp2_filled

    def _dual_ma_activation_anchor(self, side: str, curr_px: float, init_breakeven: float = 0.0):
        """见上方DUAL_MA_ACTIVATION_GATE_*常量顶部注释："保本激活加宽"。
        只在首次开仓触发激活的那一刻调用一次(非每tick)，返回一个更宽的
        锚点供调用方跟纯保本位取更松的那个，无效时返回None(调用方原样
        使用现有纯保本公式，不改变默认行为)。
        2026-09-14第三版修正(跟币安B系统同一批改)：不再独立算一个锚点去
        跟纯保本比"谁更松"(那样谁更松取决于两个不相关公式的巧合，BNB
        实盘复算证明会巧合失效)，而是直接在纯保本(init_breakeven)基础
        上，按gate_dist(激活线((TP1+TP2)/2中点)相对entry走了多远)的
        ACTIVATION_RETAIN_FRAC比例再让出一段缓冲——由构造保证100%比纯
        保本更松，同时随行情走出的距离自适应。"""
        if not DUAL_MA_ACTIVATION_GATE_ENABLED:
            return None
        side = str(side or "").upper()
        if side not in ("LONG", "SHORT"):
            return None
        entry = float(self.pipeline.data.get("entry") or 0)
        init_breakeven = float(init_breakeven or 0)
        try:
            gate_px = float(self.radar._activation_gate_price() or 0)
        except Exception as e:
            logger.info(f"[{self.symbol}] 保本激活加宽跳过(激活线取值异常): {e} → 走现有纯保本")
            return None
        if entry <= 0 or gate_px <= 0 or init_breakeven <= 0:
            logger.info(
                f"[{self.symbol}] 保本激活加宽跳过(entry={entry} gate={gate_px} "
                f"init={init_breakeven} 无效) → 走现有纯保本"
            )
            return None
        gate_dist = abs(gate_px - entry)
        if gate_dist <= 0:
            return None
        if side == "LONG":
            return init_breakeven - DUAL_MA_ACTIVATION_RETAIN_FRAC * gate_dist
        return init_breakeven + DUAL_MA_ACTIVATION_RETAIN_FRAC * gate_dist

    def _activate_radar(self, curr_px: float = 0.0):
        """激活雷达"""
        import breath_profiles
        entry_price = float(self.pipeline.data.get("entry", 0) or 0)
        position_id = self.pipeline.data.get("position_id", "")
        tp2_px = self.pipeline.data.get("tp2", {}).get("px", 0)
        tier = self.pipeline.data.get("tier", "1")
        direction = self.pipeline.data.get("side", "LONG")

        profile = breath_profiles.get_breath_profile(self.symbol)
        self.radar.activate(
            entry_price=entry_price,
            tp2_price=tp2_px,
            tier=tier,
            direction=direction,
            profile=profile,
        )

        # 初始止损 = 雷达算出的保本起步位（entry ± tick ± entry×fee_cover_pct）
        initial_sl = self.radar.get_state().current_sl
        if not initial_sl or initial_sl <= 0:
            initial_sl = entry_price - 0.01 if direction == "LONG" else entry_price + 0.01

        # 2026-10-04：ZEC/SNDK武装雷达时不再把止损一把收到保本位——实盘ZEC浮盈到1.8×ATR武装后止损被收到
        # 保本价(1331.57≈entry+手续费)，一次正常回调(峰值1366→1331)就把B/E/CoinW三边全部打掉，而TV还持有。
        # 武装只表示"动态追踪开始接管"：止损保持武装前的TV硬止损，由动态追踪(峰值∓k×ATR，只收不放)逐步收紧。
        _arm_no_be = self.symbol in TOPUP_SYMBOLS
        if _arm_no_be:
            _wide = float(self.pipeline.data.get("hard_sl_px") or 0)
            if _wide > 0:
                logger.info(f"[{self.symbol}] 雷达武装不收保本：沿用TV硬止损 {_wide:.4f}(保本位{initial_sl:.4f}弃用)，交给动态追踪逐步收紧")
                initial_sl = _wide
                self.radar.seed_stop(_wide)

        # 2026-09-14新增：见上方DUAL_MA_ACTIVATION_GATE_*常量顶部注释"保本
        # 激活双均线加宽"。只对首次开仓生效(重入沿用现有更严格的纯保本，
        # 不做加宽)。
        if int(self.radar.get_state().reentry_count or 0) == 0 and not _arm_no_be:
            try:
                widened = self._dual_ma_activation_anchor(direction, curr_px, init_breakeven=initial_sl)
            except Exception as e:
                widened = None
                logger.debug(f"[{self.symbol}] 保本激活加宽异常跳过: {e}")
            if widened is not None and widened > 0:
                hard_ceiling = float(self.pipeline.data.get("hard_sl_px") or 0)
                if direction == "LONG":
                    final_sl = min(initial_sl, widened)
                    if hard_ceiling > 0:
                        final_sl = max(final_sl, hard_ceiling)
                else:
                    final_sl = max(initial_sl, widened)
                    if hard_ceiling > 0:
                        final_sl = min(final_sl, hard_ceiling)
                if abs(final_sl - initial_sl) > 1e-9:
                    logger.info(
                        f"[{self.symbol}] 保本激活加宽 {initial_sl:.4f}→{final_sl:.4f} "
                        f"(保留激活线距entry {DUAL_MA_ACTIVATION_RETAIN_FRAC:.0%}距离当缓冲)"
                    )
                initial_sl = final_sl
                self.radar.seed_stop(final_sl)

        self.client.set_sl_tp(
            position_id=position_id,
            instrument=self.symbol,
            stop_loss_price=round(initial_sl, 2),
        )

        logger.info(f"雷达激活: 方向={direction}, TP2={tp2_px}, 初始SL={initial_sl}")

    def _pyramid_state_path(self) -> str:
        return f"data/pyramid_stage_{self.symbol}.json"

    def _load_pyramid_stage(self) -> int:
        try:
            with open(self._pyramid_state_path(), encoding="utf-8") as f:
                d = json.load(f)
            return int(d.get("stage", 0) or 0)
        except Exception:
            return 0

    def _save_pyramid_stage(self, stage: int) -> None:
        try:
            os.makedirs("data", exist_ok=True)
            with open(self._pyramid_state_path(), "w", encoding="utf-8") as f:
                json.dump({"stage": int(stage), "ts": time.time()}, f)
        except Exception as e:
            logger.debug(f"[{self.symbol}] pyramid_stage保存跳过: {e}")

    def _portfolio_pyramid_notional_except_self(self) -> float:
        """PYRAMID_ADD_SYMBOLS里除自己之外，其它品种当前真实持仓名义
        合计——组合总闸用。"""
        total = 0.0
        for sym in PYRAMID_ADD_SYMBOLS:
            if sym == self.symbol:
                continue
            try:
                pos = self.client.get_position(sym, prefer_ws=True, force_rest=False)
                if pos:
                    qty = float(pos.get("quantity") or pos.get("baseSize") or 0)
                    if qty > 0:
                        px = self.client.get_ticker(sym)
                        if px > 0:
                            total += qty * px
            except Exception as e:
                logger.debug(f"[{self.symbol}] 组合加仓总闸查询{sym}异常: {e}")
        return total

    def _has_4h_reversal_signal(self, side: str):
        """4H结构反转信号判断(共享)：收盘破线或十字星。返回(has_signal,
        trigger_name, mult, atr_4h)。_check_4h_reversal_tighten用这个
        结果去收紧止损，_check_pyramid_add用这个结果去否决加仓。"""
        try:
            from strategy_engine import indicators as _sk_ind
            fast_len = REVERSAL_4H_FAST_LEN.get(self.symbol, 7) if isinstance(REVERSAL_4H_FAST_LEN, dict) else int(REVERSAL_4H_FAST_LEN)
            bars = self._get_risk_klines(240, REVERSAL_4H_KLINE_LIMIT)
            if not bars or len(bars) < fast_len + 2:
                return False, "", None, 0.0
            closes = [float(b[4]) for b in bars]
            fast = _sk_ind.ema(closes, fast_len)
            atr_ser = _sk_ind.atr_series([{"t": _b[0], "o": _b[1], "h": _b[2], "l": _b[3], "c": _b[4], "v": _b[5]} for _b in bars], REVERSAL_4H_ATR_LEN)
            if not fast or not atr_ser:
                return False, "", None, 0.0
            last = bars[-1]
            o4, h4, l4, c4 = float(last[1]), float(last[2]), float(last[3]), float(last[4])
            fast_4h = fast[-1]
            atr_4h = float(atr_ser[-1])
            if atr_4h <= 0:
                return False, "", None, 0.0
            rng = max(h4 - l4, 1e-9)
            is_doji = abs(c4 - o4) / rng <= REVERSAL_4H_DOJI_BODY_FRAC
        except Exception as e:
            logger.debug(f"[{self.symbol}] 4H结构检查跳过: {e}")
            return False, "", None, 0.0

        mult = None
        trigger_name = ""
        if side == "LONG":
            if c4 < fast_4h:
                mult, trigger_name = REVERSAL_4H_BREAK_ATR_MULT, f"4H收盘跌破EMA{fast_len}"
            elif is_doji and REVERSAL_4H_DOJI_ENABLED:
                mult, trigger_name = REVERSAL_4H_DOJI_ATR_MULT, "4H十字星"
        elif side == "SHORT":
            if c4 > fast_4h:
                mult, trigger_name = REVERSAL_4H_BREAK_ATR_MULT, f"4H收盘站上EMA{fast_len}"
            elif is_doji and REVERSAL_4H_DOJI_ENABLED:
                mult, trigger_name = REVERSAL_4H_DOJI_ATR_MULT, "4H十字星"
        if mult is None:
            return False, "", None, 0.0
        return True, trigger_name, mult, atr_4h

    def _check_4h_reversal_tighten(self) -> None:
        """4H快线反转预警：只对PYRAMID_ADD_SYMBOLS生效，已有仓位时才
        检查，180秒节流一次。多头：4H收盘跌破EMA7或那根K线是十字星，
        止损收紧到贴近市价；空头反向。"""
        if self.symbol not in PYRAMID_ADD_SYMBOLS:
            return
        side = str(self.pipeline.data.get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            return
        now = time.time()
        if now - float(getattr(self, "_4h_reversal_last_ts", 0) or 0) < REVERSAL_4H_CHECK_INTERVAL_SEC:
            return
        self._4h_reversal_last_ts = now

        has_signal, trigger_name, mult, atr_4h = self._has_4h_reversal_signal(side)
        if not has_signal:
            return

        curr_px = self._get_current_price()
        if curr_px <= 0:
            return
        direction = 1.0 if side == "LONG" else -1.0
        target_sl = curr_px - direction * atr_4h * mult
        logger.warning(
            f"[REVERSAL_4H] [{self.symbol}] {trigger_name} -> 雷达收紧目标@{target_sl:.4f} "
            f"(现价{curr_px:.4f} ATR(4H)={atr_4h:.4f})"
        )
        try:
            self._update_radar_sl(target_sl)
        except Exception as e:
            logger.error(f"[{self.symbol}] 4H反转预警收紧止损失败: {e}")

    def _check_pyramid_add(self, current_price: float) -> None:
        """均线发散确认加仓检查——见模块顶部PYRAMID_ADD_*常量注释。
        只对PYRAMID_ADD_SYMBOLS生效，只在已有仓位、还没加到封顶时检查，
        60秒节流一次。"""
        if self.symbol not in PYRAMID_ADD_SYMBOLS:
            return
        side = str(self.pipeline.data.get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            return
        stage = int(getattr(self, "_pyramid_stage_cache", None) or self._load_pyramid_stage())
        self._pyramid_stage_cache = stage
        if stage >= 2:
            return
        now = time.time()
        if now - float(getattr(self, "_pyramid_last_check_ts", 0) or 0) < PYRAMID_ADD_CHECK_INTERVAL_SEC:
            return
        self._pyramid_last_check_ts = now

        try:
            from strategy_engine import indicators as _sk_ind
            _interval_min = PYRAMID_ADD_RISK_KLINE_INTERVAL_MIN.get(self.symbol, 91)
            bars = self._get_risk_klines(_interval_min, PYRAMID_ADD_KLINE_LIMIT)
            _slow_n = PYRAMID_ADD_SLOW_LEN.get(self.symbol, 25)
            if not bars or len(bars) < _slow_n + 2:
                return
            closes = [float(b[4]) for b in bars]
            _fast_n = PYRAMID_ADD_FAST_LEN.get(self.symbol, 7)
            fast = _sk_ind.ema(closes, _fast_n)
            slow = _sk_ind.ema(closes, _slow_n)
            atr_ser = _sk_ind.atr_series([{"t": _b[0], "o": _b[1], "h": _b[2], "l": _b[3], "c": _b[4], "v": _b[5]} for _b in bars], PYRAMID_ADD_ATR_LEN)
            if not fast or not slow or not atr_ser:
                return
            atr_now = float(atr_ser[-1])
            if atr_now <= 0:
                return
            divergence = abs(fast[-1] - slow[-1]) / atr_now
            # 连续2根(原生周期自己)都站稳阈值之上才算真触发，过滤单根
            # 噪音K线，不引入跨周期滞后。
            divergence_prev = None
            if len(fast) >= 2 and len(slow) >= 2 and len(atr_ser) >= 2 and atr_ser[-2] > 0:
                divergence_prev = abs(fast[-2] - slow[-2]) / float(atr_ser[-2])
            vol_ratio = None
            vw = PYRAMID_ADD_VOLUME_VETO_WINDOW
            if len(bars) >= vw + 1:
                vol_now = float(bars[-1][5] or 0)
                recent_vols = [float(b[5] or 0) for b in bars[-(vw + 1):-1]]
                recent_avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 0.0
                if recent_avg_vol > 0:
                    vol_ratio = vol_now / recent_avg_vol
        except Exception as e:
            logger.debug(f"[{self.symbol}] 加仓发散度检查跳过: {e}")
            return

        if PYRAMID_ADD_MODE == "milestone":
            # 2026-10-04：浮盈里程碑加仓(取代发散度触发)，理由/回测见币安B/E同名常量注释。
            ms = PYRAMID_ADD_MILESTONE_ATR
            if stage >= len(ms):
                return
            _d = 1.0 if side == "LONG" else -1.0
            if not (_d * (fast[-1] - slow[-1]) > 0 and _d * (closes[-1] - fast[-1]) > 0):
                return  # 趋势已不在(快慢线不同向排列/收盘跌回快线另一侧)不加
            _e0 = self._pyramid_first_entry(stage)
            _a0 = float(self.radar.get_state().initial_atr or 0)
            if _e0 <= 0 or _a0 <= 0:
                return
            _prof = _d * (closes[-1] - _e0) / _a0
            if _prof < ms[stage]:
                return
            threshold = ms[stage]
            divergence = _prof  # 仅用于下面的日志展示
            divergence_prev = _prof
        else:
            thresholds = PYRAMID_ADD_DIVERGENCE_THRESHOLDS.get(self.symbol, [1.535, 2.109])
            threshold = thresholds[stage] if stage < len(thresholds) else None
            if threshold is None or divergence < threshold:
                return
            if divergence_prev is None or divergence_prev < threshold:
                logger.info(
                    f"[{self.symbol}] 发散度本根达标({divergence:.3f}>={threshold})但上一根"
                    f"未达标({divergence_prev}) -> 等连续确认，暂不加仓"
                )
                return

        # 量能否决：发散度达标但这根K线量能明显偏薄，没有真实资金推动
        # 的发散更容易反转打回，跟4H否决同一哲学——只否决不设门槛。
        vol_veto_frac = PYRAMID_ADD_VOLUME_VETO_FRAC.get(self.symbol)
        if vol_veto_frac is not None and vol_ratio is not None and vol_ratio < vol_veto_frac:
            logger.warning(
                f"[PYRAMID] [{self.symbol}] 发散度已确认但量能偏薄"
                f"(比值={vol_ratio:.3f}<门槛={vol_veto_frac}) -> 否决本次加仓，等下一根"
            )
            return

        # 4H一票否决：均线发散不等4H点头，但4H已经明确亮出反转信号时
        # 否决这次加仓。
        has_4h_signal, trigger_name_4h, _mult_4h, _atr_4h = self._has_4h_reversal_signal(side)
        if has_4h_signal:
            logger.warning(
                f"[PYRAMID] [{self.symbol}] 发散度已确认但4H亮出反转信号"
                f"({trigger_name_4h}) -> 否决本次加仓"
            )
            return

        if PYRAMID_ADD_MODE == "milestone":
            logger.warning(
                f"[PYRAMID] [{self.symbol}] 浮盈里程碑确认(stage{stage}->{stage+1}): "
                f"浮盈={divergence:.2f}×ATR 阈值={threshold}×ATR(开仓ATR) -> 触发加仓"
            )
        else:
            logger.warning(
                f"[PYRAMID] [{self.symbol}] 均线发散度确认(stage{stage}->{stage+1}): "
                f"发散={divergence:.3f}(连续2根) 阈值={threshold} ATR={atr_now:.4f} -> 触发加仓"
            )
        self._execute_pyramid_add(stage, atr_now, side, current_price)

    def _pyramid_first_entry(self, stage: int) -> float:
        """首仓开仓价(加仓后entry会变成加权均价，里程碑要用首仓价算浮盈)。按"方向|持仓id"落盘；
        stage==0时当前entry就是首仓价，首次检查时记下；stage>0却没记录→返回0(不加)。"""
        try:
            side = str(self.pipeline.data.get("side") or "").upper()
            pid = str(self.pipeline.data.get("position_id") or "")
            key = f"{side}|{pid}"
            path = f"data/pyramid_e0_{self.symbol}.json"
            try:
                with open(path, encoding="utf-8") as f:
                    _d = json.load(f)
                if _d.get("key") == key and float(_d.get("e0", 0) or 0) > 0:
                    return float(_d["e0"])
            except Exception:
                pass
            if int(stage) == 0 and pid:
                e0 = float(self.pipeline.data.get("entry") or 0)
                if e0 > 0:
                    os.makedirs("data", exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump({"key": key, "e0": e0}, f)
                return e0
        except Exception as e:
            logger.debug(f"[{self.symbol}] 首仓价读取失败: {e}")
        return 0.0

    def _reassert_stop_after_add(self, position_id) -> None:
        """加仓/补仓成交后，把"现有最紧止损"重新推一遍，让交易所止损记录覆盖到
        新的全部仓位(CoinW止损记录按张数closePiece绑定)。取雷达内部止损与交易所
        当前生效止损里更紧的一个，只收不放。"""
        try:
            pid = str(position_id or "")
            side = str(self.pipeline.data.get("side") or "").upper()
            if not pid or side not in ("LONG", "SHORT"):
                return
            st = self.radar.get_state()
            want = float(st.current_sl or 0) if st.activated else float(
                self.pipeline.data.get("hard_sl_px") or 0
            )
            recs = self.client.get_tp_sl_info(pid, stop_from=2) or []
            sls = [float(r.get("stopLossPrice") or 0) for r in recs
                   if float(r.get("stopLossPrice") or 0) > 0 and int(r.get("triggerStatus") or 0) == 0]
            if sls:
                eff = min(sls) if side == "SHORT" else max(sls)
                if want <= 0 or (side == "SHORT" and eff < want) or (side == "LONG" and eff > want):
                    want = eff
            if want <= 0:
                logger.warning(f"[{self.symbol}] 加仓后补止损覆盖：找不到有效止损价，跳过")
                return
            self.client.set_sl_tp(position_id=pid, instrument=self.symbol, stop_loss_price=round(want, 2))
            logger.info(f"[{self.symbol}] 加仓后止损覆盖已补推 @{want:.2f}(覆盖全部新仓位)")
        except Exception as e:
            logger.error(f"[{self.symbol}] 加仓后补止损覆盖失败: {e}")

    def _dyn_trail_radar_step(self, prev_sl: float):
        """2026-10-04：ZEC/SNDK的追踪距离改用动态系数(按均线发散度+动能放宽/收紧，见dyn_trail.py)，
        取代TP分区追踪。radar.update()只用来维护峰值/阶段状态，它算出的分区止损不采用：雷达内部
        止损强制回到"原止损 与 动态候选 里更紧的那个"；取不到动态值(限流/K线不足)就保持原止损不动。
        返回需要推送到交易所的新止损(没变化返回None)；包线夹取+只收不放仍在_update_radar_sl里生效。"""
        try:
            side = str(self.pipeline.data.get("side") or "").upper()
            best = float(self.radar.get_state().best_price or 0)
            cand = self._dyn_trail_candidate(side, best)
            # 先夹包线(峰值±2×ATR4H)再跟原止损比较——否则候选比包线更紧时会把雷达内部止损拉到包线内，
            # 随后包线自愈又把它抬回去，来回打架
            try:
                _env = self._radar_envelope_stop()
                if cand and _env:
                    cand = clamp_to_envelope(side, cand, _env, 0.0)
            except Exception:
                pass
            desired = float(prev_sl or 0)
            if cand and desired > 0:
                if (side == "LONG" and cand > desired) or (side == "SHORT" and cand < desired):
                    desired = float(cand)
            if desired > 0:
                self.radar.rebase_to(desired)
            return desired if (desired > 0 and abs(desired - float(prev_sl or 0)) > 1e-9) else None
        except Exception as e:
            logger.warning(f"[{self.symbol}] 动态雷达追踪异常(保持原止损): {e}")
            return None

    def _dyn_trail_candidate(self, side: str, best: float):
        """ZEC/SNDK动态雷达追踪候选止损(见dyn_trail.py顶部说明)。K线走_get_risk_klines(币安公开K线
        优先、限流时30分钟缓存兜底)；取不到/数据不足返回None，调用方保持现有止损不动。"""
        try:
            from dyn_trail import dynamic_trail_stop, DYN_PARAMS, norm_symbol
            p = DYN_PARAMS.get(norm_symbol(self.symbol))
            if not p or float(best or 0) <= 0:
                return None
            raw = self._get_risk_klines(int(p["tf"]), 60)
            bars = [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]} for r in (raw or [])]
            stop, meta = dynamic_trail_stop(side, float(best), bars, self.symbol)
            if stop is None:
                return None
            now = time.time()
            if now - float(getattr(self, "_dyn_trail_log_ts", 0) or 0) > 120:
                self._dyn_trail_log_ts = now
                logger.info(
                    f"🧭 [{self.symbol}] 动态雷达追踪 峰值{float(best):.2f} k={meta['k']} "
                    f"发散度={meta['div']} 动能={meta['mom']} ATR={meta['atr']} → 候选止损{stop:.2f}"
                )
            return float(stop)
        except Exception as e:
            logger.warning(f"[{self.symbol}] 动态雷达追踪计算失败(保持现有止损): {e}")
            return None

    def _integrity_alert(self, kind: str, detail: str) -> bool:
        """同一品种同一类问题INTEGRITY_ALERT_COOLDOWN_SEC内只告警一次。"""
        now = time.time()
        seen = self.__dict__.setdefault("_integrity_alert_ts", {})
        k = (self.symbol, kind)
        if now - float(seen.get(k, 0) or 0) < INTEGRITY_ALERT_COOLDOWN_SEC:
            return False
        seen[k] = now
        logger.error(f"[AUDIT] [{self.symbol}] {kind}: {detail}")
        try:
            self._safe_alert(f"仓位/止损审计 [{self.symbol}] {kind}: {detail}")
        except Exception:
            pass
        return True

    def _verify_add_fill(self, kind: str, planned: float, before_qty: float, after_qty: float) -> bool:
        """加仓/补仓成交后核对：交易所实际增量是否≈计划量。偏差大只告警(不回滚)。"""
        actual = float(after_qty) - float(before_qty)
        if planned > 0 and abs(actual - planned) > max(planned * ADD_QTY_TOLERANCE_FRAC, 0.0002):
            self._integrity_alert(
                f"{kind}数量偏差",
                f"计划{planned:.4f} 交易所实际增量{actual:.4f} (加前{before_qty:.4f}→加后{after_qty:.4f})",
            )
            return False
        return True

    def _audit_integrity(self, current_price: float) -> None:
        """仓位/止损完整性审计(以交易所REST为准)——见顶部INTEGRITY_*常量注释。"""
        if self.symbol not in TOPUP_SYMBOLS or current_price <= 0:
            return
        side = str(self.pipeline.data.get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            return
        now = time.time()
        if now - float(getattr(self, "_integrity_last_ts", 0) or 0) < INTEGRITY_AUDIT_INTERVAL_SEC:
            return
        self._integrity_last_ts = now
        if getattr(self, "_topup_busy", False):
            return
        if self.client.ip_rate_limit_remaining() > 0:
            return  # 限流冷却：不抢REST额度
        from coinw_client import is_position_query_failed
        from sndk_live_policy import coinw_base_qty
        live_pos = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
        if is_position_query_failed(live_pos) or not live_pos or self._live_side(live_pos) != side:
            return
        live_qty = coinw_base_qty(live_pos)
        if live_qty <= 0:
            return
        px = float(current_price)
        # ① 超仓
        if int(getattr(self, "_pyramid_stage_cache", None) or self._load_pyramid_stage()) == 0:
            eq = float(self._get_balance() or 0)
            target = eq / px if eq > 0 else 0.0
            _a = float(getattr(self, "_open_target_qty", 0) or 0)
            if _a > 0 and getattr(self, "_open_target_side", None) == side:
                target = max(target, _a)  # 亏损使权益缩水时，以开仓目标量为准，避免假"超仓"
            if target > 0 and live_qty > target * (1 + INTEGRITY_OVERSIZE_FRAC) and (live_qty - target) * px >= TOPUP_MIN_SHORTFALL_USD:
                self._integrity_alert(
                    "超仓", f"交易所持仓{live_qty:.4f}(≈{live_qty * px:.1f}U) > 本金目标{target:.4f}(≈{target * px:.1f}U)×{1 + INTEGRITY_OVERSIZE_FRAC:.2f}，且未加过金字塔仓"
                )
        # ② ③ ④ 止损：存在性/方向/张数覆盖
        pid = str(live_pos.get("id") or "")
        recs = self.client.get_tp_sl_info(pid, stop_from=2) if pid else None
        if recs is not None:
            live_recs = [r for r in recs if float(r.get("stopLossPrice") or 0) > 0 and int(r.get("triggerStatus") or 0) == 0]
            st = self.radar.get_state()
            if st.activated and not live_recs:
                self._integrity_alert("雷达已武装但交易所无止损", f"{side} qty={live_qty} 现价{px}")
            wrong = [float(r["stopLossPrice"]) for r in live_recs
                     if (side == "LONG" and float(r["stopLossPrice"]) >= px) or (side == "SHORT" and float(r["stopLossPrice"]) <= px)]
            if wrong:
                self._integrity_alert("止损方向错误", f"{side} 现价{px} 止损{wrong}(多单止损应在现价下方/空单在上方)")
            pieces = int(float(live_pos.get("currentPiece") or 0))
            if live_recs and pieces > 0:
                covered = max(int(float(r.get("closePiece") or 0)) for r in live_recs)
                if covered < pieces:
                    logger.warning(f"[AUDIT] [{self.symbol}] 止损张数覆盖不足 {covered}/{pieces} → 重推止损覆盖全部仓位")
                    self._reassert_stop_after_add(pid)
                    self._integrity_alert("止损张数覆盖不足(已自动重推)", f"覆盖{covered}张 < 持仓{pieces}张")
        # ⑤ 账本止损单调
        st = self.radar.get_state()
        cur_sl = float(st.current_sl or 0) if st.activated else 0.0
        key = f"{side}|{int(float(live_pos.get('createdDate') or 0) / 1000)}"
        ext = self.__dict__.setdefault("_integrity_sl_extreme", {})
        if cur_sl > 0:
            prev = ext.get(key)
            tol = max(px * 0.0003, 0.3)
            if prev is not None and ((side == "LONG" and cur_sl < prev - tol) or (side == "SHORT" and cur_sl > prev + tol)):
                self._integrity_alert("账本止损被放松", f"{side} 已达最紧{prev:.2f} → 现{cur_sl:.2f}")
            else:
                ext[key] = cur_sl if prev is None else (max(prev, cur_sl) if side == "LONG" else min(prev, cur_sl))

    def _topup_state_path(self) -> str:
        return f"data/topup_{self.symbol}.json"

    def _check_principal_topup(self, current_price: float) -> None:
        """本金自检自补——见顶部TOPUP_*常量注释。每120秒核对一次：交易所真实持仓
        名义 < 本金权益(1倍)的90%时，自己补到1倍。"""
        if self.symbol not in TOPUP_SYMBOLS or current_price <= 0:
            return
        side = str(self.pipeline.data.get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            return
        now = time.time()
        _burst = now < float(getattr(self, "_topup_burst_until", 0) or 0)
        _interval = TOPUP_BURST_INTERVAL_SEC if _burst else TOPUP_CHECK_INTERVAL_SEC
        _settle = TOPUP_BURST_SETTLE_SEC if _burst else TOPUP_SETTLE_SEC
        if now - float(getattr(self, "_topup_last_ts", 0) or 0) < _interval:
            return
        self._topup_last_ts = now
        if getattr(self, "_topup_busy", False):
            return
        stage = int(getattr(self, "_pyramid_stage_cache", None) or self._load_pyramid_stage())
        if stage > 0:
            return
        if self.client.ip_rate_limit_remaining() > 0:
            return
        from coinw_client import is_position_query_failed
        from sndk_live_policy import coinw_base_qty
        live_pos = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
        if is_position_query_failed(live_pos) or not live_pos or self._live_side(live_pos) != side:
            return
        created = float(live_pos.get("createdDate") or 0) / 1000.0
        if created <= 0 or not (_settle <= now - created <= TOPUP_MAX_POS_AGE_SEC):
            return
        live_qty = coinw_base_qty(live_pos)
        if live_qty <= 0:
            return
        # 目标量锚定：本笔开仓时算出的目标量(不随浮盈漂移，否则赢的仓位会被越补越大)；
        # 重启后锚点丢失→退回按当前权益算+10%容差(防漂移)。
        _anchor = float(getattr(self, "_open_target_qty", 0) or 0)
        _anchored = (
            _anchor > 0
            and getattr(self, "_open_target_side", None) == side
            and 0 < now - float(getattr(self, "_open_target_ts", 0) or 0) <= TOPUP_MAX_POS_AGE_SEC
        )
        _frac = TOPUP_MIN_SHORTFALL_FRAC if _anchored else 0.10
        equity = self._get_balance()
        if equity <= 0:
            return
        target_qty = _anchor if _anchored else equity / current_price
        shortfall = target_qty - live_qty
        _unit = self._piece_unit()
        if _anchored and _unit > 0:
            shortfall = round(shortfall / _unit) * _unit  # 只补整张
        if shortfall < target_qty * _frac or shortfall * current_price < TOPUP_MIN_SHORTFALL_USD:
            return

        key = f"{side}|{int(created)}"
        attempts = 0
        try:
            with open(self._topup_state_path(), encoding="utf-8") as f:
                _s = json.load(f)
            if _s.get("key") == key:
                attempts = int(_s.get("n", 0) or 0)
        except Exception:
            pass
        if attempts >= TOPUP_MAX_ATTEMPTS:
            return

        st = self.radar.get_state()
        stop_ref = float(st.current_sl or 0) if st.activated else float(self.pipeline.data.get("hard_sl_px") or 0)
        atr = float(st.initial_atr or 0)
        if stop_ref > 0 and atr > 0 and abs(current_price - stop_ref) < 0.5 * atr:
            logger.info(f"[{self.symbol}] 本金补仓跳过：现价离止损不足0.5×ATR")
            return
        lev = float(self.client.get_symbol_leverage(self.symbol) or 5.0)
        avail = float(self.client.get_available_balance() or 0)
        add_qty = math.floor(shortfall / 0.0001) * 0.0001
        if add_qty <= 0 or avail < add_qty * current_price / max(lev, 1.0) * 1.2:
            logger.warning(f"[{self.symbol}] 本金补仓跳过：可用保证金不足(需约{add_qty * current_price / max(lev, 1.0) * 1.2:.1f}，可用{avail:.1f})")
            return

        if not self._lock.acquire(blocking=False):
            return  # TV信号/开平仓正在处理，下个周期再核对，不抢锁
        self._topup_busy = True
        try:
            os.makedirs("data", exist_ok=True)
            with open(self._topup_state_path(), "w", encoding="utf-8") as f:
                json.dump({"key": key, "n": attempts + 1, "ts": now}, f)
            logger.warning(
                f"[TOPUP] [{self.symbol}] 持仓{live_qty}(≈{live_qty * current_price:.1f}U) < 本金权益{equity:.1f}U"
                f" → 自动补仓 {add_qty} (第{attempts + 1}/{TOPUP_MAX_ATTEMPTS}次)"
            )
            led = self._open_with_ladder(side, current_price, add_qty, tier=None, adding=True)
            if not led.get("ok"):
                logger.error(f"[TOPUP] [{self.symbol}] 补仓下单未成交: {led.get('error')}")
                return
            pos = None
            for _ in range(4):
                time.sleep(0.7)
                pos = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
                if pos and not is_position_query_failed(pos) and coinw_base_qty(pos) > live_qty:
                    break
            if is_position_query_failed(pos) or not pos or coinw_base_qty(pos) <= live_qty:
                logger.warning(f"[TOPUP] [{self.symbol}] 补仓未确认成交，下个周期再核对")
                return
            new_qty = coinw_base_qty(pos)
            self._verify_add_fill("补仓", add_qty, live_qty, new_qty)
            self.pipeline.sync_position(side=side, qty=new_qty, entry=float(pos.get("openPrice") or current_price))
            self._reassert_stop_after_add(pos.get("id"))
            try:
                self._safe_alert(
                    f"本金自检补仓 [{self.symbol}] {side} {live_qty}→{new_qty} "
                    f"(目标本金权益{equity:.1f}U≈{target_qty:.4f}) 止损覆盖已补推"
                )
            except Exception:
                pass
            logger.warning(f"[TOPUP] [{self.symbol}] 补仓完成 {live_qty}→{new_qty}")
        finally:
            self._topup_busy = False
            self._lock.release()

    def _execute_pyramid_add(self, from_stage: int, atr_now: float, side: str, current_price: float) -> None:
        """执行一次加仓(+0.5倍本金等值)——复用_open_with_ladder(限价
        阶梯+市价兜底，跟开仓同一套)，不触碰已有止损/止盈挂单。加仓
        成交后从交易所baseSize核对真实币数，同步进pipeline；SNDK继续
        使用本笔TV硬止损，不因加仓收紧。"""
        try:
            balance = self._get_balance()
            if balance <= 0 or current_price <= 0:
                logger.warning(f"[{self.symbol}] 加仓中止：本金或现价不可用")
                return
            add_qty = (balance * PYRAMID_ADD_FRACTION) / current_price
            if self.client.ip_rate_limit_remaining() > 0:
                logger.warning(f"[{self.symbol}] 加仓中止：无法新鲜核查持仓")
                return
            from coinw_client import is_position_query_failed
            live_pos = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
            if is_position_query_failed(live_pos) or not live_pos or self._live_side(live_pos) != side:
                logger.warning(f"[{self.symbol}] 加仓中止：交易所持仓或方向未确认")
                return
            from sndk_live_policy import capped_add_qty, coinw_base_qty
            live_base_qty = coinw_base_qty(live_pos)
            if live_base_qty <= 0:
                logger.error(f"[{self.symbol}] 加仓中止：交易所baseSize缺失")
                return
            add_qty = capped_add_qty(
                balance, live_base_qty, current_price, add_qty, 0.0001,
            )
            other_notional = self._portfolio_pyramid_notional_except_self()
            self_notional = live_base_qty * current_price
            portfolio_budget = max(
                balance * PORTFOLIO_PYRAMID_COMBINED_CAP_FRAC - other_notional - self_notional,
                0.0,
            )
            budget_qty = math.floor((portfolio_budget / current_price) / 0.0001) * 0.0001 \
                if current_price > 0 else 0.0
            if budget_qty < add_qty:
                logger.warning(
                    f"[{self.symbol}] 加仓受组合总闸限制: 其它品种占用{other_notional:.2f} "
                    f"本品种{self_notional:.2f} 权益{balance:.2f} -> add_qty {add_qty}->{budget_qty}"
                )
                add_qty = max(0.0, budget_qty)
            if add_qty <= 0:
                logger.info(f"[{self.symbol}] 加仓跳过：1倍名义上限已满")
                return

            led = self._open_with_ladder(side, current_price, add_qty, tier=None, adding=True)
            if not led.get("ok"):
                logger.error(f"[PYRAMID] [{self.symbol}] 加仓下单未成交: {led.get('error')}")
                return

            pos = None
            for _ in range(3):
                time.sleep(0.5)
                pos = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
                if pos and not is_position_query_failed(pos) and coinw_base_qty(pos) > 0:
                    break
            if is_position_query_failed(pos) or not pos:
                logger.error(f"[PYRAMID] [{self.symbol}] 加仓后查不到真实持仓，停止本次加仓收尾")
                return
            new_qty = coinw_base_qty(pos)
            if new_qty <= live_base_qty:
                logger.warning(f"[PYRAMID] [{self.symbol}] 加仓未确认成交，阶段保持不变")
                return
            new_entry = float(pos.get("openPrice") or current_price)
            self._verify_add_fill("加仓", add_qty, live_base_qty, new_qty)
            self.pipeline.sync_position(side=side, qty=new_qty, entry=new_entry)

            new_stage = from_stage + 1
            self._pyramid_stage_cache = new_stage
            self._save_pyramid_stage(new_stage)

            direction = 1.0 if side == "LONG" else -1.0
            new_sl = current_price - direction * atr_now * PYRAMID_ADD_RATCHET_ATR_MULT
            # 保本安全线：加仓后的止损不能劣于新加权均价的保本位。
            try:
                from breath_stop import initial_stop_price
                import breath_profiles as _bp
                _profile = _bp.get_breath_profile(symbol=self.symbol)
                be_floor = initial_stop_price(side, new_entry, profile=_profile)
                if be_floor > 0:
                    new_sl = max(new_sl, be_floor) if side == "LONG" else min(new_sl, be_floor)
            except Exception as e:
                logger.debug(f"[{self.symbol}] 加仓保本安全线计算跳过: {e}")
            if PYRAMID_ADD_RATCHET_ATR_MULT > 0:
                try:
                    self._update_radar_sl(new_sl)
                except Exception as e:
                    logger.error(f"[{self.symbol}] 加仓后止损收紧失败: {e}")
            else:
                logger.info(f"[PYRAMID] [{self.symbol}] 加仓后不收紧止损(RATCHET_MULT=0)，沿用现有雷达止损")
                self._reassert_stop_after_add(pos.get("id"))

            try:
                self._safe_alert(
                    f"均线发散确认加仓 [{self.symbol}] {side} 加仓{PYRAMID_ADD_FRACTION:.0%} "
                    f"@约{current_price:.4f} (stage{from_stage}->{new_stage}，"
                    f"累计{1.0+new_stage*PYRAMID_ADD_FRACTION:.1f}倍) 继续使用TV硬止损"
                )
            except Exception:
                pass
            logger.warning(
                f"[PYRAMID] [{self.symbol}] 加仓完成 stage{from_stage}->{new_stage} "
                f"新qty={new_qty}，SNDK继续使用TV硬止损"
            )
        except Exception as e:
            logger.error(f"[{self.symbol}] 加仓执行异常: {e}")

    def _update_radar_sl(self, new_sl: float):
        """更新雷达止损。2026-10-04加棘轮：只收不放，外部更紧的值同步回雷达内部。"""
        position_id = self.pipeline.data.get("position_id", "")
        if not position_id:
            return
        try:
            _side = str(self.pipeline.data.get("side") or "").upper()
            _ns = float(new_sl or 0)
            _env = self._radar_envelope_stop()
            if _env and _ns > 0:
                _c = clamp_to_envelope(_side, _ns, _env, 0.0)
                if abs(_c - _ns) > 1e-9:
                    logger.warning(
                        f"[{self.symbol}] 雷达包线夹回：提议{_ns:.2f} → {_c:.2f}(峰值±2.0×ATR4H，包线{_env:.2f})"
                    )
                    _ns = _c
                    new_sl = _c
            _st = self.radar.get_state()
            _ref = float(_st.current_sl or 0) if _st.activated else float(
                self.pipeline.data.get("hard_sl_px") or 0
            )
            if _ns > 0 and _ref > 0:
                _looser = (_side == "SHORT" and _ns > _ref + 1e-9) or (
                    _side == "LONG" and _ns < _ref - 1e-9
                )
                if _looser:
                    logger.info(
                        f"[{self.symbol}] 雷达止损棘轮拦截放松: 目标{_ns:.2f} 比当前{_ref:.2f}更松，不推送"
                    )
                    return False
                if _st.activated:
                    self.radar.ratchet_to(_ns)
        except Exception as _e:
            logger.debug(f"[{self.symbol}] 雷达棘轮判断跳过: {_e}")

        self.client.set_sl_tp(
            position_id=position_id,
            instrument=self.symbol,
            stop_loss_price=new_sl,
        )

        logger.info(f"雷达止损更新: {new_sl}")

    def _verify_radar_sl_on_exchange(self) -> None:
        """2026-10-04新增：每RADAR_EXCHANGE_VERIFY_SEC秒核对交易所上实际生效的止损
        (所有未触发止损记录里最紧的一条)是否跟雷达内部止损一致；落后(更松)就重推并
        复核，连续3次没推上去就告警。set_sl_tp一次返回None/被限流/记录被部分平仓
        重建时，雷达内部已经收紧但交易所还停在旧价，之前没有任何环节会再补推。"""
        now = time.time()
        if now - float(getattr(self, "_radar_verify_last_ts", 0) or 0) < RADAR_EXCHANGE_VERIFY_SEC:
            return
        self._radar_verify_last_ts = now
        st = self.radar.get_state()
        want = float(st.current_sl or 0)
        side = str(self.pipeline.data.get("side") or "").upper()
        pid = self.pipeline.data.get("position_id", "")
        if not st.activated or want <= 0 or side not in ("LONG", "SHORT") or not pid:
            return
        env = self._radar_envelope_stop()
        env_tol = max((env or 0) * 0.0003, 0.3)
        if env and is_inside_envelope(side, want, env, env_tol):
            logger.warning(
                f"[{self.symbol}] 雷达包线自愈：雷达内部止损{want:.2f}比包线{env:.2f}更紧 → 内部rebase到包线"
            )
            self.radar.rebase_to(env)
            want = env

        def _effective():
            recs = self.client.get_tp_sl_info(str(pid), stop_from=2) or []
            sls = [
                float(r.get("stopLossPrice") or 0) for r in recs
                if float(r.get("stopLossPrice") or 0) > 0 and int(r.get("triggerStatus") or 0) == 0
            ]
            if not sls:
                return 0.0
            return min(sls) if side == "SHORT" else max(sls)

        def _stale(eff):
            tol = max(want * 0.0004, 0.05)
            return eff <= 0 or (side == "SHORT" and eff > want + tol) or (side == "LONG" and eff < want - tol)

        eff = _effective()
        _inside = bool(env and eff > 0 and is_inside_envelope(side, eff, env, env_tol))
        if _inside:
            _hk = f"{side}|{round(float(self.pipeline.data.get('entry') or 0), 1)}"
            if getattr(self, "_radar_env_heal_key", None) != _hk:
                self._radar_env_heal_key, self._radar_env_heal_count = _hk, 0
            if (
                int(getattr(self, "_radar_env_heal_count", 0) or 0) >= 3
                or now - float(getattr(self, "_radar_env_heal_ts", 0) or 0) < 600
            ):
                _inside = False
            else:
                self._radar_env_heal_ts = now
                self._radar_env_heal_count = int(getattr(self, "_radar_env_heal_count", 0) or 0) + 1
                self._safe_alert(
                    f"[{self.symbol}] 雷达包线自愈#{self._radar_env_heal_count}：交易所止损{eff:.2f}比包线{env:.2f}更紧，重推到{want:.2f}"
                )
        if not _stale(eff) and not _inside:
            self._radar_verify_fail = 0
            return
        logger.warning(
            f"[{self.symbol}] 雷达止损交易所对账：雷达内部{want:.2f} 交易所生效{eff:.2f}(0=未查到) → 重推"
        )
        self.client.set_sl_tp(position_id=pid, instrument=self.symbol, stop_loss_price=round(want, 2))
        time.sleep(1.5)
        eff2 = _effective()
        if not _stale(eff2):
            self._radar_verify_fail = 0
            logger.warning(f"[{self.symbol}] 雷达止损已补推到交易所 @{eff2:.2f}")
            return
        self._radar_verify_fail = int(getattr(self, "_radar_verify_fail", 0) or 0) + 1
        if self._radar_verify_fail >= 3 and now - float(getattr(self, "_radar_verify_alert_ts", 0) or 0) > 1800:
            self._radar_verify_alert_ts = now
            self._safe_alert(
                f"[{self.symbol}] 雷达止损连续{self._radar_verify_fail}次没能推到交易所："
                f"雷达{want:.2f} 交易所{eff2:.2f}，请核查"
            )

    def _journal_path(self, kind: str) -> str:
        """2026-09-19新增(本周问题总结item6"控制面板显示每笔平仓原因")：
        品种隔离journal路径，跟币安B系统position_supervisor_binance.py::
        _journal_path同一套命名/思路(两仓库保持结构一致)。目前只用
        kind="close"这一种，先不做币安那边tv/open/exchange的全套。"""
        return f"logs/coinw_{kind}_journal_{self.symbol}.jsonl"

    def _append_journal(self, path: str, record: dict) -> None:
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            record = dict(record)
            record.setdefault("symbol", self.symbol)
            record["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[{self.symbol}] journal写入跳过: {e}")

    def _iter_journal_entries(self, kind: str) -> list:
        """按时间正序读取本品种journal。单条解析失败跳过，不中断整体。"""
        path = self._journal_path(kind)
        if not os.path.exists(path):
            return []
        entries = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            return entries
        return entries

    def _journal_close(self, exit_side: str, exit_entry: float, exit_px: float,
                        exit_source: str, tier: str = "") -> None:
        """每次平仓落一条"close"kind journal——exit_source此前只进了
        _safe_alert告警文案，从没被持久化成可查询的结构化记录。纯记录，
        不影响任何交易决策，跟币安B系统_journal_close同一份设计。"""
        self._append_journal(self._journal_path("close"), {
            "side": exit_side,
            "entry_px": float(exit_entry or 0),
            "exit_px": float(exit_px or 0),
            "exit_source": exit_source or "",
            "tier": tier or "",
        })

    def _resolve_exit_source_coinw(self, curr_px: float) -> str:
        """2026-09-15新增：离场原因分类，简化版对齐币安
        position_supervisor_binance.py::_resolve_exit_source的优先级
        思路(TV平仓 > 硬止损 > 雷达保本 > 其余)，适配CoinW自己的数据
        模型。必须在self.radar.reset()/self.pipeline.reset_idle()**之前**
        调用——这两步会清空雷达状态和pipeline.data，晚了就读不到了。

        不细分sl_initial/sl_breakeven(CoinW没有breakeven_phase这个字段)，
        统一归到radar_be——够用于下面的重入门槛判断即可。TP3/QUICK/RSI/
        TV_PROTECT这几个币安独有的TV CLOSE细分类，CoinW的_intentional_
        close不区分子类型，先不加没有数据支撑的空字段。
        """
        from webhook_parser import (
            EXIT_SOURCE_TV_CLOSE, EXIT_SOURCE_VPS_HARD_SL,
            EXIT_SOURCE_RADAR_BE, EXIT_SOURCE_MANUAL,
        )
        if self._intentional_close:
            return EXIT_SOURCE_TV_CLOSE

        px = float(curr_px or 0)
        if px <= 0:
            return EXIT_SOURCE_MANUAL

        hard_sl_px = float(self.pipeline.data.get("hard_sl_px") or 0)
        if hard_sl_px > 0:
            tol = max(2.5, hard_sl_px * 0.002)
            if abs(px - hard_sl_px) <= tol:
                return EXIT_SOURCE_VPS_HARD_SL

        try:
            st = self.radar.get_state()
        except Exception:
            st = None
        if st is not None and bool(getattr(st, "activated", False)):
            radar_sl = float(getattr(st, "current_sl", 0) or 0)
            if radar_sl > 0:
                tol = max(2.5, radar_sl * 0.002)
                if abs(px - radar_sl) <= tol:
                    return EXIT_SOURCE_RADAR_BE

        return EXIT_SOURCE_MANUAL

    def _on_position_zero(self, curr_px: float = 0.0):
        """仓位归零"""
        logger.info(f"仓位归零: {self.symbol}")
        self._pyramid_stage_cache = 0
        self._save_pyramid_stage(0)

        # 出局分类：非 TV平/手动平 -> 视为止损出局，记录并启动冷却 + 再入看守
        stopped_out = not self._intentional_close
        exit_side = str(self.pipeline.data.get("side") or "").upper()
        exit_entry = float(self.pipeline.data.get("entry") or 0)
        # 必须在radar.reset()/pipeline.reset_idle()清空状态之前算出分类
        exit_source = self._resolve_exit_source_coinw(curr_px)
        # 2026-09-19新增(本周问题总结item6)：不管是TV平仓还是止损出局，
        # 只要这确实是一笔真实持仓的收尾(entry>0)，都落一条journal——
        # 跟_last_exit只记录"非TV平仓"不同，控制面板要看到每一笔的原因，
        # 包括TV自己平掉的那些(exit_source=tv_close)。
        if exit_side in ("LONG", "SHORT") and exit_entry > 0:
            self._journal_close(
                exit_side, exit_entry, float(curr_px or 0), exit_source,
                tier=str(self.pipeline.data.get("tier") or ""),
            )

        # 停止监控
        self._monitoring = False

        # 清除挂单
        self.client.cancel_all_orders(self.symbol)

        # 清除雷达
        self.radar.reset()

        # 通知
        self._dingtalk.report_coinw_tp("仓位归零", 0)

        # 重置流水线
        self.pipeline.reset_idle("position_zero")

        if stopped_out and exit_side in ("LONG", "SHORT") and exit_entry > 0:
            self._last_exit = {
                "side": exit_side, "entry": exit_entry, "reason": exit_source,
                "tier": str(self.pipeline.data.get("tier") or ""), "ts": time.time(),
            }
            self._cooldown_until = time.time() + COOLDOWN_SEC
            logger.warning(f"止损出局 {exit_side}@{exit_entry} source={exit_source}；"
                           f"冷却 {COOLDOWN_SEC:.0f}s"
                           f"{'，启动再入看守' if REENTRY_ENABLED else ''}")
            # 2026-09-15新增：综合硬止损出局永久禁止重入，跟币安
            # can_smart_reenter的"exit_source in (vps_hard_sl,hard_sl) →
            # 永久拒绝"规则对齐——此前CoinW对雷达保本出局/硬止损出局一视
            # 同仁都允许重入，是个真实的正确性缺口。
            from webhook_parser import EXIT_SOURCE_VPS_HARD_SL
            if exit_source == EXIT_SOURCE_VPS_HARD_SL:
                logger.info(f"[{self.symbol}] 综合硬止损出局，不启动重入看守")
            elif REENTRY_ENABLED and self._reentry_count < REENTRY_MAX:
                self._start_reentry_watcher()
        self._intentional_close = False

    # ==================== 智能再入看守 ====================

    def _start_reentry_watcher(self):
        if self._reentry_thread and self._reentry_thread.is_alive():
            return
        self._reentry_thread = threading.Thread(
            target=self._reentry_watch_loop, daemon=True, name=f"coinw-reentry-{self.symbol}")
        self._reentry_thread.start()

    def _reentry_watch_loop(self):
        from smart_reentry_engine import reentry_gate
        ex = dict(self._last_exit or {})
        if not ex:
            return
        t_end = time.time() + REENTRY_WINDOW_SEC
        time.sleep(REENTRY_DELAY_SEC)
        while time.time() < t_end:
            try:
                # 已有仓 / 新 TV 信号进来了 / 冷却被清了 -> 结束看守
                if self._pos_qty() != 0 or self._last_exit is not ex and self._last_exit != ex:
                    return
                if self.pipeline.phase.value not in ("IDLE", "position_zero", ""):
                    # 有新信号在处理
                    if self._pos_qty() != 0:
                        return
                if self._reentry_count >= REENTRY_MAX:
                    return
                bars = self._get_risk_klines(60, 400)
                px = float(self._get_current_price() or 0)
                if bars and px > 0:
                    ok, reason = reentry_gate(
                        bars, ex["side"], ex["entry"], px, self._reentry_count,
                        cfg={"max_reentry": REENTRY_MAX, "adx_gate": REENTRY_ADX_GATE,
                             "max_chase_pct": REENTRY_MAX_CHASE_PCT},
                    )
                    if ok:
                        logger.warning(f"智能再入触发：{ex['side']} @现价{px} ({reason})")
                        self._do_reentry(ex, px, bars)
                        return
                    logger.debug(f"再入未达标: {reason}")
            except Exception as e:
                logger.error(f"再入看守异常: {e}")
            time.sleep(REENTRY_POLL_SEC)
        logger.info("再入机会窗口结束，未再入")

    def _synth_150(self, raw):
        f = 5
        if not raw or len(raw) < f:
            return []
        n = len(raw) - (len(raw) % f)
        out = []
        for i in range(0, n, f):
            ch = raw[i:i + f]
            out.append([ch[0][0], ch[0][1], max(c[2] for c in ch),
                        min(c[3] for c in ch), ch[-1][4], sum(c[5] for c in ch)])
        return out

    # ==================== regime 自适应雷达 ====================

    def _regime_profile(self, base: dict) -> dict:
        """按开仓锁定的 tier(0/1/2) 微调雷达步进/呼吸系数。"""
        if not REGIME_ADAPT_ENABLED or not isinstance(base, dict):
            return base
        tier = str(self.pipeline.data.get("tier") or "1")
        sm = REGIME_STEP_MULT.get(tier, 1.0)
        bm = REGIME_BREATH_MULT.get(tier, 1.0)
        if sm == 1.0 and bm == 1.0:
            return base
        p = dict(base)
        p["step_trigger_atr"] = round(float(base.get("step_trigger_atr", 0.96)) * sm, 3)
        p["step_advance_atr"] = round(float(base.get("step_advance_atr", 0.62)) * sm, 3)
        p["breath_tp12"] = round(float(base.get("breath_tp12", 2.56)) * bm, 3)
        p["breath_tp23"] = round(float(base.get("breath_tp23", 3.45)) * bm, 3)
        return p

    # ==================== 高潮否决 / 反转锁利 ====================

    def _climax_check(self, side: str, price: float):
        """进场前：(veto, warn, detail)。"""
        try:
            bars = self._get_risk_klines(60, 400)
            if not bars:
                return False, False, "no_bars"
            from market_overlays import climax_check
            return climax_check(bars, side, float(price or 0),
                                cfg={"climax_atr_mult": CLIMAX_ATR_MULT,
                                     "overext_atr_mult": OVEREXT_ATR_MULT})
        except Exception as e:
            logger.debug(f"climax_check异常: {e}")
            return False, False, "err"

    def _apply_reversal_lock(self, current_price: float):
        """持仓中：4h 逆向放量反转K线 + 有浮盈 -> 止损收到保本（只进不退）。"""
        if not REVLOCK_ENABLED:
            return
        st = self.radar.get_state()
        side = str(self.pipeline.data.get("side") or "").upper()
        entry = float(self.pipeline.data.get("entry") or 0)
        atr = float(st.initial_atr or getattr(self.radar, "_atr", 0) or 0)
        px = float(current_price or 0)
        if side not in ("LONG", "SHORT") or entry <= 0 or atr <= 0 or px <= 0:
            return
        mfe = (px - entry) if side == "LONG" else (entry - px)
        if mfe < REVLOCK_MIN_PROFIT_ATR * atr:
            return
        try:
            raw4h = self._get_risk_klines(240, 120)
        except Exception:
            return
        if not raw4h or len(raw4h) < 20:
            return
        last_closed_ts = raw4h[-2][0] if len(raw4h) >= 2 else raw4h[-1][0]
        if last_closed_ts <= self._revlock_bar_ts:
            return
        self._revlock_bar_ts = last_closed_ts
        from market_overlays import reversal_candle
        hit, det = reversal_candle(raw4h, side, cfg={"body_ratio": REVLOCK_BODY_RATIO,
                                                     "vol_mult": REVLOCK_VOL_MULT})
        if not hit:
            return
        from breath_stop import initial_stop_price
        from breath_profiles import get_breath_profile
        be = initial_stop_price(side, entry, profile=get_breath_profile(self.symbol))
        cur = float(st.current_sl or 0)
        new_sl = None
        if side == "LONG":
            if be > cur:
                new_sl = round(be, 2)
        else:
            if cur <= 0 or be < cur:
                new_sl = round(be, 2)
        if new_sl:
            self.radar.seed_stop(new_sl)
            self._update_radar_sl(new_sl)
            self._safe_alert(f"反转锁利：4h逆向放量反转({det})，止损收到保本 {new_sl}")

    # ==================== 深盈利保护三层(2026-09-15移植自币安) ====================

    def _maybe_lock_profit_on_big_win(self, current_price: float):
        """大赢家利润地板——见BIG_WIN_ATR_THRESHOLD/BIG_WIN_RETAIN_FRAC
        顶部注释。不依赖任何K线/指标信号，纯粹按"峰值浮盈是initial_atr的
        多少倍"判断，零额外REST成本，可以每tick都算。"""
        if self.symbol in TOPUP_SYMBOLS:
            return  # 2026-10-04: ZEC/SNDK追踪距离由动态系数(dyn_trail.py)决定，大赢家65%留存地板会比动态追踪更紧，让位
        if not BIG_WIN_ATR_THRESHOLD or not BIG_WIN_RETAIN_FRAC:
            return
        st = self.radar.get_state()
        side = str(self.pipeline.data.get("side") or "").upper()
        entry = float(self.pipeline.data.get("entry") or 0)
        atr = float(st.initial_atr or getattr(self.radar, "_atr", 0) or 0)
        if side not in ("LONG", "SHORT") or entry <= 0 or atr <= 0:
            return
        best = float(st.best_price or 0) or entry
        peak_profit = abs(best - entry)
        peak_profit_atr = peak_profit / atr
        if peak_profit_atr < BIG_WIN_ATR_THRESHOLD:
            return

        retain_profit = peak_profit * BIG_WIN_RETAIN_FRAC
        # 见COINW_LIQUIDITY_BUFFER_ATR_MULT顶部注释(2026-09-20)：地板线
        # 额外让出一点CoinW自己盘口噪音的缓冲，只朝"更松"方向调，不改变
        # 门槛/棘轮判断本身。
        liquidity_buffer = COINW_LIQUIDITY_BUFFER_ATR_MULT * atr
        cur = float(st.current_sl or 0)
        if side == "LONG":
            floor_px = entry + retain_profit - liquidity_buffer
            improved = floor_px > cur
        else:
            floor_px = entry - retain_profit + liquidity_buffer
            improved = cur <= 0 or floor_px < cur
        if not improved:
            return
        new_sl = round(floor_px, 2)

        if abs(self._big_win_alerted_best - best) > 1e-9:
            self._big_win_alerted_best = best
            self._safe_alert(
                f"大赢家利润地板触发：峰值浮盈{peak_profit_atr:.2f}×ATR"
                f"(≥{BIG_WIN_ATR_THRESHOLD:.1f}倍门槛) → 止损顶至保住峰值"
                f"{BIG_WIN_RETAIN_FRAC*100:.0f}% {cur}→{new_sl}"
            )
        self.radar.seed_stop(new_sl)
        self._update_radar_sl(new_sl)

    def _maybe_tighten_on_profit_giveback(self, current_price: float):
        """利润回吐刹车——见常量顶部注释。按品种从breath_profiles.py::
        giveback_brake读参数，没配置的品种(目前BNB/XPD/SNDK/OPENAI/XAU
        都没有)直接原样跳过。"""
        if self.symbol in TOPUP_SYMBOLS:
            return  # 2026-10-04: 同上，动态追踪取代回吐刹车
        from breath_profiles import get_breath_profile
        profile = get_breath_profile(self.symbol)
        cfg = profile.get("giveback_brake") if isinstance(profile, dict) else None
        if not isinstance(cfg, dict):
            return
        min_peak_atr = float(cfg.get("min_peak_atr") or 0)
        trigger_frac = float(cfg.get("trigger_frac") or 0)
        retain_frac = float(cfg.get("retain_frac") or 0)
        if min_peak_atr <= 0 or trigger_frac <= 0 or retain_frac <= 0:
            return

        st = self.radar.get_state()
        side = str(self.pipeline.data.get("side") or "").upper()
        entry = float(self.pipeline.data.get("entry") or 0)
        atr = float(st.initial_atr or getattr(self.radar, "_atr", 0) or 0)
        if side not in ("LONG", "SHORT") or entry <= 0 or atr <= 0:
            return
        best = float(st.best_price or 0) or entry
        px = float(current_price or 0) or best

        peak_profit = (best - entry) if side == "LONG" else (entry - best)
        if peak_profit <= 0 or peak_profit / atr < min_peak_atr:
            return
        current_profit = (px - entry) if side == "LONG" else (entry - px)
        giveback = peak_profit - current_profit
        if giveback <= 0 or giveback / peak_profit < trigger_frac:
            return

        retain_profit = peak_profit * retain_frac
        cur = float(st.current_sl or 0)
        if side == "LONG":
            floor_px = entry + retain_profit
            improved = floor_px > cur
        else:
            floor_px = entry - retain_profit
            improved = cur <= 0 or floor_px < cur
        if not improved:
            return
        new_sl = round(floor_px, 2)

        if abs(self._giveback_brake_alerted_best - best) > 1e-9:
            self._giveback_brake_alerted_best = best
            giveback_frac_now = giveback / peak_profit
            self._safe_alert(
                f"利润回吐刹车触发：峰值浮盈{peak_profit/atr:.2f}×ATR已回吐"
                f"{giveback_frac_now*100:.0f}%(≥{trigger_frac*100:.0f}%门槛) → "
                f"止损顶至保住峰值{retain_frac*100:.0f}% {cur}→{new_sl}"
            )
        self.radar.seed_stop(new_sl)
        self._update_radar_sl(new_sl)

    def _maybe_tighten_on_tv_exit_stall(self, current_price: float):
        """TV僵局收紧——见TV_EXIT_STALL_*常量顶部注释。简化版：跳过币安
        才有的"深度盈利耐心模式让位"分支(CoinW没有这个概念)，达到滞涨
        条件统一收紧到现价±TV_EXIT_STALL_TIGHT_ATR。"""
        if not TV_EXIT_STALL_ENABLED:
            return
        # 2026-10-04：SNDK/ZEC的TV平仓走明确的CLOSE_* webhook，不用心跳推断(同币安B/E)。
        if self.symbol in ("SNDK", "ZEC"):
            self._tv_exit_stall_since_ts = 0.0
            return
        if str(self._tv_heartbeat_side or "FLAT").upper() != "FLAT":
            # TV心跳还在跟我们同方向(或压根还没收到过心跳)，没有"TV已
            # 平仓"这个前提，滞涨计时器归零，不触发。
            self._tv_exit_stall_since_ts = 0.0
            return

        side = str(self.pipeline.data.get("side") or "").upper()
        entry = float(self.pipeline.data.get("entry") or 0)
        if side not in ("LONG", "SHORT") or entry <= 0:
            return
        # 心跳曾经非FLAT时记的方向必须等于这段持仓自己的方向，才能把
        # "心跳现在是FLAT"解读成"TV已经平掉了我们这一笔"——CoinW没有
        # last_tv_signal这个字段，用_last_nonflat_hb_side代替同一个判断。
        if str(self._last_nonflat_hb_side or "").upper() != side:
            return

        st = self.radar.get_state()
        atr = float(st.initial_atr or getattr(self.radar, "_atr", 0) or 0)
        if atr <= 0:
            return
        best = float(st.best_price or 0) or entry
        peak_profit = (best - entry) if side == "LONG" else (entry - best)
        if peak_profit <= 0 or peak_profit / atr < TV_EXIT_STALL_MIN_PEAK_ATR:
            return

        now = time.time()
        if abs(self._tv_exit_stall_best_seen - best) > 1e-9:
            # best比上次检查又创了新高/新低，还没真滞涨，计时器归零重开
            self._tv_exit_stall_best_seen = best
            self._tv_exit_stall_since_ts = now
            return
        since = float(self._tv_exit_stall_since_ts or 0)
        if since <= 0:
            self._tv_exit_stall_since_ts = now
            return

        tv_tf_min = DUAL_MA_EXIT_INTERVAL_MIN.get(self.symbol, DUAL_MA_EXIT_DEFAULT_INTERVAL_MIN)
        stall_window_sec = tv_tf_min * 60 * TV_EXIT_STALL_BARS
        if now - since < stall_window_sec:
            return

        px = float(current_price or 0) or best
        tight_dist = atr * TV_EXIT_STALL_TIGHT_ATR
        cur = float(st.current_sl or 0)
        if side == "LONG":
            floor_px = px - tight_dist
            improved = floor_px > cur
        else:
            floor_px = px + tight_dist
            improved = cur <= 0 or floor_px < cur
        if not improved:
            return
        new_sl = round(floor_px, 2)

        self._safe_alert(
            f"TV僵局收紧触发：TV心跳已转FLAT，峰值浮盈{peak_profit/atr:.2f}×ATR后"
            f"滞涨超过{TV_EXIT_STALL_BARS}个TV周期 → 止损收紧至现价±"
            f"{TV_EXIT_STALL_TIGHT_ATR}×ATR {cur}→{new_sl}"
        )
        self.radar.seed_stop(new_sl)
        self._update_radar_sl(new_sl)

    _COINW_NATIVE_GRAN = {1, 3, 5, 15, 30, 60, 120, 240, 360, 480, 1440, 10080}

    def _coinw_native_source_period(self, interval_min: int) -> int:
        """从CoinW原生粒度集合里，挑能整除目标周期、且尽量粗的那个当
        合成源周期——跟binance_klines.py::resolve_source_interval同一
        个思路(2026-09-19新增，配合当天把DUAL_MA_EXIT_INTERVAL_MIN从
        旧的45/75重新校准成49/50/65/91这些真实TV周期后，原来写死的
        "只处理45/75"特例已经不够用——这些新周期没有一个是15的整数倍，
        必须像币安那边一样按能整除的最粗原生粒度动态挑源周期，不能再
        硬编码只认45/75)。找不到能整除的(理论上不会，1永远整除任何
        整数分钟数)才退化到1分钟。"""
        for g in sorted(self._COINW_NATIVE_GRAN, reverse=True):
            if g < interval_min and interval_min % g == 0:
                return g
        return 1

    def _synth_klines_via_coinw_native(self, interval_min: int, limit: int) -> list:
        """CoinW原生K线合成任意目标周期——挑_coinw_native_source_period()
        选出的最粗能整除源周期，按窗口桶合并(open取首根/high取窗口
        最高/low取窗口最低/close取末根/volume求和)。只在_get_risk_
        klines()币安公开K线也拉不到时才会被调用到，是"兜底的兜底"。"""
        src = self._coinw_native_source_period(interval_min)
        factor = interval_min // src
        if factor <= 0:
            return []
        try:
            raw = self.client.get_klines(self.symbol, src, limit * factor + factor)
        except Exception:
            return []
        if not raw or len(raw) < factor:
            return []
        n = len(raw) - (len(raw) % factor)
        out = []
        for i in range(0, n, factor):
            ch = raw[i:i + factor]
            out.append([ch[0][0], ch[0][1], max(c[2] for c in ch),
                        min(c[3] for c in ch), ch[-1][4], sum(c[5] for c in ch)])
        return out

    def _get_risk_klines(self, interval_min: int, limit: int) -> list:
        """2026-09-19新增：所有"风险计算"类K线(综合硬止损/双均线破位/
        反转锁利/趋势确认重入/追单多周期确认/突发反转K线)统一改成优先
        拉币安公开K线，CoinW自己的K线只做兜底——CoinW自己的行情深度比
        币安薄，偶尔一笔稍大的单子就能在CoinW自己的K线上制造一个"假
        摆动点"，算出来的止损/趋势判断跟币安同一笔信号对不上号(实盘
        复现：OPENAI/XPD/XAU止损距离两边对不上，XPD一度贴着entry几个
        点就被CoinW自己打平，币安同一笔信号算出来的止损宽了好几倍)。
        只换这一层"算风险参数用的K线"，仓位查询/下单/止盈止损这些
        执行类操作完全不变，还是CoinW自己的coinw_client.py——跟宝贝
        早就确认过的"币赢那边自己查仓位还是归CoinW自己管"一致。

        顺带修复一个当前就在生效的bug：_maybe_trend_reentry此前直接用
        self.client.get_klines(self.symbol, 45或75, ...)拉K线，但
        CoinW原生get_klines只支持{1,3,5,15,30,60,120,240,360,480,
        1440,10080}这些粒度，45/75根本不在其中——这个调用从
        TREND_REENTRY_KLINE_INTERVAL_MIN改成45分钟那天起就一直在默默
        拉空列表，"趋势确认多次重入"+"TV心跳追回快速通道"这两个机制
        在CoinW上从未真正跑起来过。币安公开K线接口原生支持任意周期
        合成，这次改造自然带着把这个也修好。
        """
        interval_min = int(interval_min or 0)
        limit = int(limit or 0)
        try:
            from binance_klines import get_bars
            binance_symbol = f"{self.symbol}USDT"
            bars = get_bars(binance_symbol, f"{interval_min}m", limit=limit)
        except Exception as e:
            logger.debug(f"[{self.symbol}] 风险K线：币安公开K线拉取异常 {e}")
            bars = []
        _ck = (interval_min, limit)
        _cache = self.__dict__.setdefault("_risk_kline_cache", {})
        if bars:
            _out = [[b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in bars]
            _cache[_ck] = (time.time(), _out)
            return _out

        # 2026-10-04：币安公开K线被429限流时，先用30分钟内的上一份成功结果
        # (K线周期>=45分钟，几分钟陈旧不影响判断)，不退回CoinW自己偏薄的K线——
        # 否则CoinW的4H ATR/包线/预警输入会和币安不一致，止损对不上号。
        _hit = _cache.get(_ck)
        if _hit and time.time() - _hit[0] < 1800:
            logger.info(f"[{self.symbol}] 风险K线：币安拉取失败，沿用{int(time.time() - _hit[0])}秒前的币安K线缓存")
            return _hit[1]

        logger.warning(
            f"[{self.symbol}] 风险K线：币安公开K线拉取失败/为空(周期{interval_min}m)，"
            f"退回CoinW自己的K线兜底"
        )
        if interval_min in self._COINW_NATIVE_GRAN:
            try:
                return self.client.get_klines(self.symbol, interval_min, limit)
            except Exception:
                return []
        if interval_min > 0:
            return self._synth_klines_via_coinw_native(interval_min, limit)
        logger.warning(f"[{self.symbol}] 风险K线：兜底也无法处理的周期{interval_min}m，返回空")
        return []

    def _fetch_dual_ma_exit_klines(self) -> list:
        """双均线破位检查专用取K线——品种自己真实TV周期，经
        _get_risk_klines()统一入口(币安公开K线优先，CoinW自己的K线
        兜底)。"""
        interval_min = DUAL_MA_EXIT_INTERVAL_MIN.get(self.symbol, DUAL_MA_EXIT_DEFAULT_INTERVAL_MIN)
        return self._get_risk_klines(interval_min, DUAL_MA_EXIT_KLINE_LIMIT)

    def _maybe_fast_lock_on_impulse_candle(self, current_price: float):
        """突发放量反转K线快速锁保本——见上方IMPULSE_EXIT_*常量顶部注释。
        三层防线里速度最快的一层：不等双均线正式突破确认，只看最新一根
        K线自己够不够"决定性"(实体够大+真放量)，够的话立刻把止损棘轮到
        保本价——不清仓(单根K线可能只是插针)，真正决定性的清仓交给
        _maybe_fast_exit_on_dual_ma_break。"""
        if not IMPULSE_EXIT_ENABLED:
            return
        side = str(self.pipeline.data.get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            return
        now = time.time()
        last_check = float(getattr(self, "_impulse_exit_last_check_ts", 0) or 0)
        if last_check > 0 and (now - last_check) < IMPULSE_REFRESH_SEC:
            return
        self._impulse_exit_last_check_ts = now

        bars = self._fetch_dual_ma_exit_klines()
        if not bars or len(bars) < IMPULSE_VOL_LOOKBACK + 2:
            return

        last = bars[-1]
        try:
            bar_time = int(last[0])
            o, h, l, c, v = (float(last[i]) for i in (1, 2, 3, 4, 5))
        except (TypeError, ValueError, IndexError):
            return
        rng = max(h - l, 1e-9)
        body_ratio = abs(c - o) / rng
        decisive_bear = c < o and body_ratio >= IMPULSE_BODY_RATIO
        decisive_bull = c > o and body_ratio >= IMPULSE_BODY_RATIO
        against_position = (side == "LONG" and decisive_bear) or (side == "SHORT" and decisive_bull)
        if not against_position:
            return

        prior = bars[-(IMPULSE_VOL_LOOKBACK + 1):-1]
        if len(prior) < IMPULSE_VOL_LOOKBACK:
            return
        vol_avg = sum(float(b[5]) for b in prior) / len(prior)
        if not (vol_avg > 0 and v >= vol_avg * IMPULSE_VOL_MULT):
            return

        entry = float(self.pipeline.data.get("entry") or 0)
        if entry <= 0:
            return
        st = self.radar.get_state()
        atr = float(st.initial_atr or getattr(self.radar, "_atr", 0) or 0)
        if atr <= 0:
            return
        try:
            from breath_stop import initial_stop_price
            from breath_profiles import get_breath_profile
            breakeven = float(initial_stop_price(
                side, entry, atr, profile=get_breath_profile(self.symbol),
            ) or 0)
        except Exception:
            return
        if breakeven <= 0:
            return
        cur = float(st.current_sl or 0)
        if side == "LONG":
            improved = breakeven > cur
        else:
            improved = cur <= 0 or breakeven < cur
        if not improved:
            return

        self.radar.seed_stop(round(breakeven, 2))
        self._update_radar_sl(round(breakeven, 2))
        already = int(getattr(self, "_impulse_exit_alerted_bar", 0) or 0)
        if already != bar_time:
            self._impulse_exit_alerted_bar = bar_time
            self._safe_alert(
                f"突发放量反转K线快速锁保本：{side} 实体比={body_ratio:.2f} "
                f"量能={(v / vol_avg if vol_avg > 0 else 0):.2f}倍 → "
                f"止损顶至保本价 {cur}→{round(breakeven, 2)}(双均线一旦真的被突破+放量"
                f"确认会直接清仓)"
            )

    def _dual_ma_exit_profit_gate_open(self, current_price: float) -> bool:
        """2026-09-19新增：DUAL_MA_EXIT只在浮盈已经"越过TP1"之后才生效
        ——TV自己已经决定了这笔交易，硬止损才是真正的安全网；双均线的
        职责是保护已经取得的利润，不该开仓/雷达刚激活就立刻评判平仓，
        需要先给一点呼吸空间。TP1数据不可得时默认放行(不静默关掉这层
        保护)。"""
        side = str(self.pipeline.data.get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            return True
        st = self.radar.get_state()
        tp1 = float(getattr(st, "tp1_price", 0) or 0)
        if tp1 <= 0:
            return True
        if side == "LONG":
            return current_price >= tp1
        return current_price <= tp1

    def _maybe_fast_exit_on_dual_ma_break(self, current_price: float):
        """双均线破位快速平仓——见上方DUAL_MA_EXIT_*常量顶部注释。跟
        _apply_reversal_lock同一个位置调用、同一种"自己处理副作用、不
        返回值"的写法，但这个检查更决断：真实放量确认破位时直接
        self._clear_position()清仓；没放量确认时只顺着_update_radar_sl
        同款写法适度收紧止损，不强平。"""
        # 2026-10-04: ZEC/SNDK的离场完全交给TV，不做提前双均线破位判断
        if self.symbol in ("SNDK", "ZEC"):
            return
        if not DUAL_MA_EXIT_ENABLED:
            return
        side = str(self.pipeline.data.get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            return
        if not self._dual_ma_exit_profit_gate_open(current_price):
            return
        now = time.time()
        last_check = float(getattr(self, "_dual_ma_exit_last_check_ts", 0) or 0)
        if last_check > 0 and (now - last_check) < DUAL_MA_EXIT_REFRESH_SEC:
            return
        self._dual_ma_exit_last_check_ts = now

        bars = self._fetch_dual_ma_exit_klines()
        if not bars or len(bars) < DUAL_MA_EXIT_SLOW_LEN + 2:
            return

        try:
            from dual_ma_trend import dual_ma_trend_ok
            ok, meta = dual_ma_trend_ok(
                side, bars, fast_len=DUAL_MA_EXIT_FAST_LEN, slow_len=DUAL_MA_EXIT_SLOW_LEN,
            )
        except Exception as e:
            logger.debug(f"[{self.symbol}] 双均线破位判断跳过: {e}")
            return
        if ok:
            return  # 空头仍在双均线下方/多头仍在双均线上方，趋势仍成立

        close_px = float(bars[-1][4])
        bar_time = int(bars[-1][0])
        try:
            from atr_scenario import _volume_confirmed
            vol_ok = _volume_confirmed(bars)
        except Exception:
            vol_ok = False

        if vol_ok:
            already = int(getattr(self, "_dual_ma_exit_closed_bar", 0) or 0)
            if already == bar_time:
                return  # 同一根K线已经处理过，不重复触发
            self._dual_ma_exit_closed_bar = bar_time
            interval_min = DUAL_MA_EXIT_INTERVAL_MIN.get(self.symbol, DUAL_MA_EXIT_DEFAULT_INTERVAL_MIN)
            self._safe_alert(
                f"双均线破位快速平仓：{side} {interval_min}分钟K线"
                f"{'跌破' if side == 'LONG' else '站上'}双均线"
                f"({DUAL_MA_EXIT_FAST_LEN}/{DUAL_MA_EXIT_SLOW_LEN})，真实放量确认(非假突破)，"
                f"close={close_px:.4f} fast_ma={meta.get('ma_fast', 0):.4f} "
                f"slow_ma={meta.get('ma_slow', 0):.4f} → 市价清仓"
            )
            try:
                self._clear_position("双均线破位+放量确认快速平仓")
            except Exception as e:
                logger.error(f"[{self.symbol}] 双均线破位快速平仓执行异常: {e}")
            return

        # 放量没确认——疑似假突破，不强平，只适度收紧止损到破位K线收盘价
        # 附近，给行情一点时间验证是否真反转。
        # 2026-09-13再补充(宝贝拍板："双均线权重大于反转锁盈，因为它是
        # 最快速反应市场趋势的、最敏捷的一个")：顺带把保本价(跟
        # _apply_reversal_lock同一个initial_stop_price公式)也算进来，两个
        # 候选取更紧的那个——哪怕这次放量没确认，双均线自己给出的破位
        # 判断也至少要收紧到反转锁盈本来会给的保本线，不能因为"没放量
        # 确认"就比反转锁盈更保守。
        st = self.radar.get_state()
        atr = float(st.initial_atr or getattr(self.radar, "_atr", 0) or 0)
        if atr <= 0:
            return
        cur = float(st.current_sl or 0)
        if side == "LONG":
            tighter = close_px - DUAL_MA_EXIT_SOFT_TIGHTEN_BUFFER_ATR * atr
        else:
            tighter = close_px + DUAL_MA_EXIT_SOFT_TIGHTEN_BUFFER_ATR * atr
        entry = float(self.pipeline.data.get("entry") or 0)
        if entry > 0:
            try:
                from breath_stop import initial_stop_price
                from breath_profiles import get_breath_profile
                breakeven = float(initial_stop_price(
                    side, entry, atr, profile=get_breath_profile(self.symbol),
                ) or 0)
            except Exception:
                breakeven = 0.0
            if breakeven > 0:
                # 更紧(更保护)的那个胜出：多头更高的价更紧，空头更低的价更紧。
                tighter = max(tighter, breakeven) if side == "LONG" else min(tighter, breakeven)
        tighter = round(tighter, 2)
        if side == "LONG":
            improved = tighter > cur
        else:
            improved = cur <= 0 or tighter < cur
        if not improved:
            return
        self.radar.seed_stop(tighter)
        self._update_radar_sl(tighter)
        logger.info(
            f"[{self.symbol}] 双均线破位但放量未确认(疑似假突破) | {side} | "
            f"close={close_px:.4f} → 止损适度收紧 {cur}→{tighter}(不低于保本线)，暂不强平"
        )

    def _do_reentry(self, ex: dict, px: float, bars_150: list):
        if self.symbol == "SNDK":
            return
        from webhook_parser import ParsedSignal
        atr = self._recompute_atr_150m()
        if atr <= 0:
            logger.warning("再入放弃：ATR 不可用")
            return
        side = ex["side"]
        sl = px - REENTRY_HARD_SL_ATR * atr if side == "LONG" else px + REENTRY_HARD_SL_ATR * atr
        tp1 = px + 1.35 * atr if side == "LONG" else px - 1.35 * atr
        tp2 = px + 2.5 * atr if side == "LONG" else px - 2.5 * atr
        # 再入档位沿用上次；缺失则按 ADX 粗分
        tier = ex.get("tier") or ""
        sig = ParsedSignal(
            valid=True, action=side, symbol=self.symbol, price=px,
            stop_loss=round(sl, 2), atr=round(atr, 4),
            tp1=round(tp1, 2), tp2=round(tp2, 2), tp3=0.0,
            qty=None, tier=tier, leverage=20, error="",
            side=side, raw={"_reentry": True},
        )
        res = self._handle_open(sig, is_reentry=True)
        if res.get("ok"):
            self._reentry_count += 1
            self._safe_alert(f"智能再入成功 #{self._reentry_count}：{side} @{px} "
                             f"(仓位×{REENTRY_SIZE_FACTOR})")
        else:
            logger.warning(f"再入开仓失败: {res.get('error')}")

    # ==================== TV方向+双均线趋势自主重入 ====================

    def _trend_reentry_daily_allow(self) -> bool:
        """2026-09-15新增：每个品种每天最多TREND_REENTRY_MAX_PER_DAY次
        (宝贝确认的风控上限，独立于冷却+条件复核之外)。返回True时顺带
        把计数+1；调用方应只在真的要开仓前调用一次。"""
        import datetime
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        rec = self._trend_reentry_daily
        if rec.get("date") != today:
            rec = {"date": today, "count": 0}
        if int(rec.get("count") or 0) >= TREND_REENTRY_MAX_PER_DAY:
            self._trend_reentry_daily = rec
            return False
        rec["count"] = int(rec.get("count") or 0) + 1
        self._trend_reentry_daily = rec
        return True

    def _maybe_trend_reentry(self, override_side: str = "") -> Optional[dict]:
        """
        2026-09-13新增(宝贝拍板)：只在VPS空仓、TV心跳当前也是FLAT时被
        调用(见_handle_heartbeat"两边都空"分支)。TV最后一个非FLAT方向
        (self._last_nonflat_hb_side)如果仍然满足趋势确认，就自主开仓——
        用综合硬止损+ATR估算TP123自己管理这笔仓位，直到TV发出全新真实
        开仓信号为止(那条路径走_handle_open的is_reentry=False分支，
        自动接管权威)。

        2026-09-15增强(宝贝反馈OPENAI连续3根阳线放量上涨且站上45分钟
        双均线)："趋势确认"从单纯dual_ma_trend_ok换成更严格的
        trend_confirmed_with_volume(双均线+最近3根同向实体+放量三者
        都满足)，并新增override_side参数——TV心跳仍非FLAT但VPS已空仓
        (今天OPENAI在B账户被误判止损后TV其实还在持有的真实场景)时，
        由_handle_heartbeat的"TV有仓VPS空"分支传入hb_side，作为比现有
        _chase_watch_step多周期持续确认窗口更快的平行判据——同一套
        开仓/仓位/止损逻辑，只是方向来源不同。override_side为空时沿用
        原有"两边都空"场景，从self._last_nonflat_hb_side取方向。
        同时新增每日次数上限(TREND_REENTRY_MAX_PER_DAY)。

        跟本类既有的"止损后小区间智能再入"(_start_reentry_watcher，只
        在VPS自己刚被止损、价格没跑远时生效)是两套独立机制：那套要求
        "现价已收复原entry"，这套完全不要求，只看"TV最后方向+当前趋势"
        ——TV是主方向判断，VPS是执行层+半辅助，允许VPS在更大范围内
        自己做主，仓位缩小(TREND_REENTRY_SIZE_FACTOR)对冲这份自主性
        带来的额外风险。

        返回None表示"这次没有可评估的东西"，调用方按原有逻辑处理；
        返回dict表示已经处理过，直接作为心跳响应返回。
        """
        if self.symbol == "SNDK":
            return None
        if not TREND_REENTRY_ENABLED:
            return None
        side = str(override_side or self._last_nonflat_hb_side or "").upper()
        if side not in ("LONG", "SHORT"):
            return None  # 从没见过TV给过方向，没什么好评估的

        now = time.time()
        if now < self._catchup_blocked_until:
            # 人工/手动平仓后的冻结窗口——跟既有心跳催单共用同一个开关，
            # 宝贝刚手动干预过，这段时间内不该被这套机制自作主张重开
            return {"ok": True, "status": "heartbeat", "state": "trend_reentry_blocked"}
        if now < self._trend_reentry_next_try_ts:
            return {"ok": True, "status": "heartbeat", "state": "trend_reentry_cooldown", "side": side}

        self._trend_reentry_next_try_ts = now + TREND_REENTRY_COOLDOWN_SEC

        try:
            bars = self._get_risk_klines(TREND_REENTRY_KLINE_INTERVAL_MIN, TREND_REENTRY_KLINE_LIMIT)
        except Exception as e:
            logger.debug(f"自主重入拉K线失败: {e}")
            return {"ok": True, "status": "heartbeat", "state": "trend_reentry_kline_failed"}

        from dual_ma_trend import trend_confirmed_with_volume
        ok, meta = trend_confirmed_with_volume(
            side, bars, fast_len=TREND_REENTRY_FAST_LEN, slow_len=TREND_REENTRY_SLOW_LEN,
            ma_type=TREND_REENTRY_MA_TYPE, candle_run=TREND_REENTRY_CANDLE_RUN,
        )
        if not ok:
            logger.debug(f"自主重入：趋势未确认 {side} {meta}")
            return {"ok": True, "status": "heartbeat", "state": "trend_not_confirmed",
                    "side": side, "meta": meta}

        if not self._trend_reentry_daily_allow():
            logger.warning(f"自主重入：今日次数已达上限({TREND_REENTRY_MAX_PER_DAY}) {side}")
            return {"ok": True, "status": "heartbeat", "state": "trend_reentry_daily_cap",
                    "side": side}

        px = float(self._get_current_price() or 0)
        if px <= 0:
            return {"ok": True, "status": "heartbeat", "state": "trend_reentry_no_price"}

        from atr_scenario import calc_smart_hard_stop_price
        hard_sl, sl_meta, sl_ok, sl_err = calc_smart_hard_stop_price(
            side=side, entry_price=px, klines=bars, tier=None,
        )
        atr = float(sl_meta.get("atr") or 0) if sl_ok else self._recompute_atr_150m()
        if atr <= 0:
            logger.warning("自主重入放弃：ATR不可用")
            return {"ok": True, "status": "heartbeat", "state": "trend_reentry_no_atr"}
        if not sl_ok:
            # 综合硬止损算不出来(K线不够/摆动点异常)时退回ATR倍数应急止损，
            # 跟_do_reentry既有的ATR应急止损同一套安全网，不整体放弃这次
            # 重入——VPS自主开仓这条路径尤其不能因为一次算不出智能止损
            # 就裸奔或者干脆放弃，安全网要比TV正常开仓路径更保守。
            hard_sl = (
                px - REENTRY_HARD_SL_ATR * atr if side == "LONG"
                else px + REENTRY_HARD_SL_ATR * atr
            )
            logger.warning(f"自主重入：综合硬止损失败({sl_err})，回退ATR应急止损 @{hard_sl:.2f}")

        tp1 = px + TREND_REENTRY_TP1_ATR * atr if side == "LONG" else px - TREND_REENTRY_TP1_ATR * atr
        tp2 = px + TREND_REENTRY_TP2_ATR * atr if side == "LONG" else px - TREND_REENTRY_TP2_ATR * atr

        from webhook_parser import ParsedSignal
        sig = ParsedSignal(
            valid=True, action=side, symbol=self.symbol, price=px,
            stop_loss=round(float(hard_sl), 2), atr=round(atr, 4),
            tp1=round(tp1, 2), tp2=round(tp2, 2), tp3=0.0,
            qty=None, tier="", leverage=20, error="",
            side=side, raw={"_trend_reentry": True},
        )
        old_factor = self._reentry_size_factor
        self._reentry_size_factor = TREND_REENTRY_SIZE_FACTOR
        try:
            res = self._handle_open(sig, is_reentry=True)
        finally:
            self._reentry_size_factor = old_factor

        if res.get("ok"):
            self._safe_alert(
                f"TV方向+双均线自主重入：{side} @{px} "
                f"(综合硬止损@{hard_sl:.2f}{'' if sl_ok else '·ATR兜底'}，"
                f"TV上次方向entry≈{self._last_nonflat_hb_entry or '-'})"
            )
            logger.warning(
                f"自主重入成功：{side} @{px} sl={hard_sl:.2f} tp1={tp1:.2f} tp2={tp2:.2f}"
            )
        else:
            logger.warning(f"自主重入开仓失败: {res.get('error')}")

        res_out = dict(res)
        res_out.setdefault("status", "heartbeat")
        res_out["trend_reentry"] = True
        return res_out

    # ==================== 清仓 ====================

    def _clear_position(self, reason: str) -> bool:
        """Fail closed until REST confirms no position and no open orders."""
        from coinw_client import is_orders_query_failed, is_position_query_failed
        logger.info(f"清仓: {reason}")
        for attempt in range(5):
            if self.client.ip_rate_limit_remaining() > 0:
                logger.error(f"[{self.symbol}] 清仓核查遇到IP冷却，拒绝反手开仓")
                return False
            before = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
            if is_position_query_failed(before):
                logger.error(f"[{self.symbol}] 清仓前REST持仓不可读，拒绝反手开仓")
                return False
            self.client.cancel_all_orders(self.symbol)
            if before and not self.client.close_all_positions(self.symbol):
                logger.warning(f"[{self.symbol}] 第{attempt + 1}次平仓未确认，重新核对")
            time.sleep(1)
            if self.client.ip_rate_limit_remaining() > 0:
                return False
            after = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
            plan = self.client.get_open_orders(self.symbol, position_type="plan", prefer_cache=False)
            execute = self.client.get_open_orders(self.symbol, position_type="execute", prefer_cache=False)
            if is_position_query_failed(after) or is_orders_query_failed(plan) or is_orders_query_failed(execute):
                logger.error(f"[{self.symbol}] 清仓后持仓/挂单不可读，拒绝反手开仓")
                return False
            if not after and not plan and not execute:
                return True
            logger.warning(
                f"[{self.symbol}] 第{attempt + 1}次净场未完成: "
                f"position={bool(after)} plan={len(plan)} execute={len(execute)}"
            )
        return False

    # ==================== 审计 ====================

    def _run_audit(self, signal) -> bool:
        """运行督察官审计"""
        from chief_auditor import audit_open_bundle, should_hard_pause

        # pipeline.data["tp1"]/["tp2"]理应恒为dict，但曾经被其它advance()调用
        # 误传同名标量字段直接覆盖成float(2026-08-08修过一次)。这里读取时
        # 兜底类型检查，避免同类问题再次出现时变成崩溃而不是审计不通过。
        tp1_state = self.pipeline.data.get("tp1")
        tp2_state = self.pipeline.data.get("tp2")
        if not isinstance(tp1_state, dict):
            tp1_state = {}
        if not isinstance(tp2_state, dict):
            tp2_state = {}

        # check_tp_slice_budget默认按10%/20%全额期望TP1+TP2——但CoinW最小
        # 交易单位是1张(0.01 ETH)，账户小的时候10%(甚至20%)会被四舍五入成
        # 0张而合理跳过(见_place_defense_orders)。跳过的那一档就不该再被
        # 审计要求凑齐，否则小账户每次开仓都会被这条硬性审计判失败、误触发
        # 交易暂停(2026-08-08真实测试复现)。按实际挂没挂出来动态给ratios。
        tp1_ratio = 0.10 if tp1_state.get("pieces", 0) else 0.0
        tp2_ratio = 0.20 if tp2_state.get("pieces", 0) else 0.0

        facts = {
            "symbol": self.symbol,
            "signal_side": signal.action,
            "live_side": signal.action,
            "live_qty": self.pipeline.data.get("qty", 0),
            "initial_qty": self.pipeline.data.get("initial_qty", 0),
            "entry": self.pipeline.data.get("entry", 0),
            "tp1_qty": tp1_state.get("qty", 0),
            "tp2_qty": tp2_state.get("qty", 0),
            "ratios": [tp1_ratio, tp2_ratio, 1.0 - tp1_ratio - tp2_ratio],
            "hard_sl_px": self.pipeline.data.get("hard_sl_px", 0),
            "hard_sl_live": True,
            "tier": signal.tier,
            # 2026-09-14修复(实盘复现XRPUSDT审计误判触发全局暂停，详见
            # _place_defense_orders顶部注释)：不再硬编码ETH的0.01，改用
            # 这笔仓位自己在_place_defense_orders里刚算出来的真实
            # per_piece_qty(存在pipeline.data["contract_unit"])；没有的
            # 品种(理论上不该发生，但保留兜底避免崩溃)退回旧的0.01。
            "contract_unit": float(self.pipeline.data.get("contract_unit") or 0.01),
        }

        result = audit_open_bundle(facts)
        self.pipeline.set_audit(
            result.ok,
            result.hard_fails,
            result.warns,
        )

        if result.hard_fails:
            logger.warning(f"审计失败: {result.hard_fails}")

        return result.ok

    # ==================== 通知 ====================

    def _report_open(self, signal, entry_result: dict):
        """发送开仓通知"""
        try:
            self._dingtalk.report_coinw_open(
                action=signal.action,
                entry_price=entry_result.get("entry_price"),
                qty=entry_result.get("qty"),
                tp_dict={"tp1": signal.tp1, "tp2": signal.tp2},
                margin=0,
            )
        except Exception as e:
            logger.error(f"通知失败: {e}")

    # ==================== 暂停 ====================

    def _pause_trading(self, reason: str):
        """暂停交易(仅本品种)。

        2026-09-14修复：这里此前改的是模块级全局trading_paused，一个
        品种的审计失败(比如CoinW按"张"取整产生的正常TP切片drift被误判)
        会连带把所有其它品种的新开仓一起挡住，而且没有任何自动恢复，
        只能靠人工调/admin/resume——实盘复现过一次从晚上10点半卡到
        第二天凌晨，中间大量品种的TV信号被这条跟它们无关的暂停拒收，
        造成"漏单"。改成只标记本品种(self._symbol_paused)，不牵连
        其它品种；对应的解除见/admin/resume/<symbol>。全局管理员级别
        的暂停(pause_all_trading/resume_all_trading，/admin/pause端点)
        不受这次改动影响，仍然是显式的全局熔断开关。"""
        self._symbol_paused = True
        logger.warning(f"[{self.symbol}] 交易暂停(仅本品种): {reason}")

        self._dingtalk.send_alert(
            f"[{self.symbol}] 交易暂停(仅本品种，其它品种不受影响): {reason} "
            f"→ 需要调用 /admin/resume/{self.symbol} 手动恢复"
        )

    # ==================== 启动恢复 ====================

    def _tv_open_atr_path(self) -> str:
        return f"logs/coinw_tv_open_atr_{self.symbol}.json"

    def _save_tv_open_atr(self, side, entry, atr) -> None:
        """PYRAMID_ADD_SYMBOLS(SNDK/ZEC)开仓时把TV给的ATR落盘：重启后TV锁定的
        ATR会丢，只能用60m ATR(14)兜底(约为TV的1/3)，雷达按它算会过紧。"""
        if self.symbol not in PYRAMID_ADD_SYMBOLS:
            return
        try:
            a = float(atr or 0)
            if a <= 0:
                return
            os.makedirs("logs", exist_ok=True)
            with open(self._tv_open_atr_path(), "w", encoding="utf-8") as f:
                json.dump({"side": str(side or "").upper(), "entry": float(entry or 0),
                           "atr": a, "ts": time.time()}, f)
        except Exception as e:
            logger.debug(f"[{self.symbol}] 落盘TV开仓ATR跳过: {e}")

    def _load_tv_open_atr(self, side, entry) -> float:
        if self.symbol not in PYRAMID_ADD_SYMBOLS:
            return 0.0
        try:
            with open(self._tv_open_atr_path(), "r", encoding="utf-8") as f:
                d = json.load(f)
            e0 = float(d.get("entry") or 0)
            e1 = float(entry or 0)
            if (
                str(d.get("side") or "").upper() == str(side or "").upper()
                and e0 > 0 and e1 > 0 and abs(e1 - e0) / e0 <= 0.03
                and time.time() - float(d.get("ts") or 0) <= 14 * 86400
            ):
                return float(d.get("atr") or 0)
        except Exception:
            pass
        return 0.0

    def _recompute_atr_150m(self) -> float:
        """重启后 TV 锁定的 ATR 已丢，用 60m K线重算 ATR(14) 兜底（≈59m TV 周期）。"""
        try:
            bars = self._get_risk_klines(60, 400)
            if len(bars) < 16:
                return 0.0
            trs = []
            for i in range(1, len(bars)):
                h, l, pc = bars[i][2], bars[i][3], bars[i - 1][4]
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))
            p = 14
            atr = sum(trs[:p]) / p
            for tr in trs[p:]:
                atr = (atr * (p - 1) + tr) / p
            return round(atr, 4)
        except Exception as e:
            logger.warning(f"重算ATR失败: {e}")
            return 0.0

    def _compute_fresh_hard_stop(self, side, entry, amt, pid, source=""):
        """2026-09-14新增：查不到任何已有止损(addTpsl记录+position自身
        stopLossPrice标量都没有)时的统一兜底——现拉真实K线，用综合硬
        止损公式(结构摆动点+ATR分档，固定tier=1中档估算)现算一个并
        立刻挂到交易所。recover_on_start()和_handle_heartbeat()的裸单
        守护共用这一份，不再各自为政、各写各的口径。

        2026-09-14实盘复现(XRPUSDT)：_handle_heartbeat()原来查不到
        hard_sl_px时会直接退回`signal.stop_loss`(TV心跳自带的止损参考)
        当成我们自己的综合硬止损去挂——TV这个字段是它自己Pine脚本内部
        的止损逻辑算出来的，跟我们综合硬止损(结构摆动点+ATR分档)是两套
        完全不同的方法论，可能松可能紧，这次实盘就复现了一次明显过紧
        (entry=1.3547，TV给的止损只有1.35，距离仅0.35%，一个正常波动
        就会打穿)。改成两条路都统一现算综合硬止损，不再把TV自己的
        止损参考直接当成我们的硬止损用。

        返回计算成功挂上的止损价，失败返回0(调用方据此判断是否仍是
        裸仓)。"""
        try:
            bars = self._fetch_dual_ma_exit_klines()
            from atr_scenario import calc_smart_hard_stop_price
            computed, meta, calc_ok, err = calc_smart_hard_stop_price(
                side, entry, bars or [], tier=1,
            )
        except Exception as e:
            computed, calc_ok, err = 0.0, False, str(e)
            meta = {}
        if calc_ok and computed > 0:
            try:
                self.client.set_sl_tp(
                    position_id=pid, instrument=self.symbol,
                    stop_loss_price=round(computed, 2),
                )
                logger.warning(
                    f"{source}：交易所无任何止损记录，现算综合硬止损并"
                    f"挂上 @{computed:.2f} | {meta}"
                )
                self._safe_alert(
                    f"检测到无保护仓位：{side} {amt} @{entry} → 已自动"
                    f"计算并挂上综合硬止损 @{computed:.2f} | {source}"
                )
                return float(computed)
            except Exception as e:
                logger.error(f"{source}：现算硬止损后挂单失败 {e}")
                self._safe_alert(
                    f"⚠️ 检测到无保护仓位且自动挂止损失败({e})，请立即人工"
                    f"核查！建议止损价 @{computed:.2f} | {source}"
                )
                return 0.0
        logger.error(f"{source}：交易所无止损且综合硬止损计算失败({err})，仍为裸仓")
        self._safe_alert(
            f"⚠️ 检测到无保护仓位且自动计算硬止损失败({err})，请立即人工"
            f"核查！| {source}"
        )
        return 0.0

    def recover_on_start(self):
        """引擎重启时，如交易所仍有在场持仓，重建 pipeline + 雷达并恢复监控。
        止损锚定交易所现值（只进不退），绝不因恢复而放松。"""
        if not STARTUP_RECOVERY_ENABLED:
            return
        try:
            pos = self.client.get_position(self.symbol, prefer_ws=False, force_rest=True)
        except Exception as e:
            logger.warning(f"启动恢复：查持仓失败 {e}")
            return
        amt = 0.0
        if pos:
            amt = float(pos.get("baseSize") or pos.get("positionAmt") or pos.get("quantity") or 0)
        if not pos or amt == 0:
            logger.info("启动恢复：无在场持仓")
            return

        side = self._live_side(pos) or "LONG"
        entry = float(pos.get("openPrice") or 0)
        pid = str(pos.get("id") or "")
        logger.warning(f"启动恢复：发现在场持仓 {side} {amt} @{entry} id={pid}，重建监控")

        # 2026-10-01：宝贝明确要求"我自己开的单，系统不要管，不要平仓
        # 我的"——用_position_has_algo_provenance(open journal溯源)判断
        # "是自己的仓位，只是重启丢了内存状态"(照常往下走完整的止损/
        # 止盈/雷达重建) vs "人工仓位"(只告警，不挂任何防线，不做任何
        # 后续操作，完全交给用户自己管理)。原来这里没有这层区分，任何
        # 在场持仓都会被无条件接管。
        if not self._position_has_algo_provenance(side, entry):
            self._safe_alert(
                f"⚠️ [{self.symbol}] 启动恢复发现在场持仓 {side} {amt} @{entry}，"
                f"open journal里找不到匹配记录(±{OPEN_JOURNAL_PROVENANCE_TOLERANCE:.1%}/"
                f"{OPEN_JOURNAL_PROVENANCE_WINDOW_SEC / 3600:.0f}小时窗内)——当成人工"
                f"仓位处理，不挂止损/止盈/雷达，不做任何操作，由你自己管理。"
            )
            return

        atr = self._recompute_atr_150m()
        _tv_atr = self._load_tv_open_atr(side, entry)
        if _tv_atr > 0:
            logger.info(f"[{self.symbol}] 恢复用落盘的TV开仓ATR={_tv_atr}(替代60m重算{atr})")
            atr = _tv_atr
        hard_sl = tp1_px = tp2_px = 0.0
        tp1_f = tp2_f = False
        # 2026-09-21新增：宝贝实盘复现(过去一周4次，XAU/XPD都出现过)——
        # 下面这两条查询(addTpsl记录 + 持仓自身stopLossPrice标量)偶尔会
        # 在重启瞬间跟交易所侧有竞态，明明止损真实挂在交易所上，这一次
        # 查询却返回"没有"，导致误判裸仓、现算一个全新止损直接覆盖挂
        # 上——如果雷达之前已经把止损移到盈利区间，这一覆盖会把已经安全
        # 的仓位重新打回亏损区间的止损，比"真裸仓"更隐蔽也更危险。改成
        # 查不到时不立刻下结论，间隔重试几次(STARTUP_SL_DETECT_RETRIES)，
        # 真的连续几次都查不到才认定裸仓、走下面的现算兜底。
        _radar_seed = 0.0  # 交易所现有止损里"最紧"的一条(=雷达当前止损)；hard_sl取"最宽"的一条(=TV硬止损)
        for _attempt in range(STARTUP_SL_DETECT_RETRIES):
            hard_sl = tp1_px = tp2_px = 0.0
            tp1_f = tp2_f = False
            _sls = []
            try:
                for r in (self.client.get_tp_sl_info(pid) or []):
                    spx = float(r.get("stopProfitPrice") or 0)
                    slx = float(r.get("stopLossPrice") or 0)
                    trg = int(r.get("triggerStatus") or 0) == 1
                    if int(r.get("stopType") or 0) == 1 and spx > 0:
                        if not tp1_px or abs(spx - entry) < abs(tp1_px - entry):
                            tp2_px, tp2_f = tp1_px, tp1_f
                            tp1_px, tp1_f = spx, trg
                        else:
                            tp2_px, tp2_f = spx, trg
                    elif slx > 0 and not trg:
                        hard_sl = slx
                    # 止损价也可能挂在"分批止盈"类型的记录上(同一条记录同时带TP和SL，例如宝贝手动挂的
                    # 整仓止盈1518.11那条记录上就带着TV硬止损1797.19)，不能只在elif分支里收集
                    if slx > 0 and not trg:
                        _sls.append(slx)
                # 2026-10-04：交易所常有多条止损记录(雷达腿+TV硬止损)。原来取"最后扫到的一条"，
                # 重启后可能把更紧的雷达止损当成硬止损，导致包线(硬止损比包线更近就不启用)失效、
                # 动态追踪不受包线约束。改成：硬止损=最宽的一条，雷达止损=最紧的一条。
                if _sls:
                    hard_sl = max(_sls) if side == "SHORT" else min(_sls)
                    _radar_seed = min(_sls) if side == "SHORT" else max(_sls)
            except Exception as e:
                logger.warning(f"启动恢复：查TPSL失败 {e}")

            # 2026-09-14新增：addTpsl记录里没查到硬止损，不代表真的裸仓——
            # 持仓自身还有一个独立的止损标量字段(stopLossPrice，跟addTpsl是
            # 两套不同的止损系统，见coinw_client.py::get_tp_sl_info顶部注释)，
            # 先认这个，避免把"用另一套机制挂过止损"的仓位误判成裸仓、重复
            # 现算一遍。每次重试都重新拉一遍持仓(不复用函数开头那次快照)，
            # 跟重试get_tp_sl_info同一个道理——都是为了绕开同一次竞态。
            if hard_sl <= 0:
                try:
                    _pos_retry = pos if _attempt == 0 else (
                        self.client.get_position(self.symbol, prefer_ws=False, force_rest=True) or pos
                    )
                    scalar_sl = float(_pos_retry.get("stopLossPrice") or 0)
                    if scalar_sl > 0:
                        hard_sl = scalar_sl
                except (TypeError, ValueError):
                    pass

            if hard_sl > 0:
                if _attempt > 0:
                    logger.info(f"启动恢复：第{_attempt + 1}次重试查到已有止损@{hard_sl}，不是真裸仓")
                break
            if _attempt < STARTUP_SL_DETECT_RETRIES - 1:
                time.sleep(STARTUP_SL_DETECT_RETRY_SEC)

        # 2026-09-14新增：实盘复现(XAUUSDT/XPTUSDT，宝贝手工在CoinW APP
        # 补开仓位)——两套止损记录都查不到时，此前直接原样记0，形同裸仓
        # 交给下游"裸单守护"，但裸单守护只会重挂一个已经算好、缓存在
        # 账本里的止损价，对手工开的仓位从没算过，等于什么都不做。这里
        # 改成：查不到任何已有止损时，现拉真实K线、用综合硬止损公式现算
        # 一个，立刻挂到交易所，不再指望下游"重挂缓存值"这条路补上——
        # 抽成_compute_fresh_hard_stop()共用方法，_handle_heartbeat()的
        # 裸单守护也复用同一份(见其顶部注释)，不再各自为政。上面重试
        # STARTUP_SL_DETECT_RETRIES次仍查不到才会走到这里，真裸仓才现算。
        if hard_sl <= 0 and entry > 0:
            hard_sl = self._compute_fresh_hard_stop(
                side=side, entry=entry, amt=amt, pid=pid, source="启动恢复",
            )

        self.pipeline.sync_position(side=side, qty=amt, entry=entry,
                                    position_id=pid, allow_initial=True)
        self.pipeline.data["hard_sl_px"] = hard_sl
        self.pipeline.data["tp1"] = {"px": tp1_px, "pieces": 1 if tp1_px else 0,
                                     "filled": tp1_f, "qty": 0.0}
        self.pipeline.data["tp2"] = {"px": tp2_px, "pieces": 1 if tp2_px else 0,
                                     "filled": tp2_f, "qty": 0.0}
        self.pipeline.data.setdefault("tier", "")

        self.radar.reset()
        if atr > 0:
            self.radar.set_atr(atr)
        # 交易所没有 TP 记录（小仓位 TP1/TP2 被跳过）时，用 ATR 倍数估出 TP1/TP2，
        # 让恢复后的激活时机跟全新开仓一致（等到 (TP1+TP2)/2 中点），而不是一
        # 恢复就按 entry 立刻激活。
        _bp = None
        try:
            from breath_profiles import get_breath_profile
            _bp = get_breath_profile(self.symbol)
        except Exception:
            _bp = {}
        _t1a = float((_bp or {}).get("tp1_atr") or 1.35)
        _t2a = float((_bp or {}).get("tp2_atr") or 2.5)
        _sgn = 1 if side == "LONG" else -1
        tp1_est = tp1_px or (entry + _sgn * _t1a * atr if atr > 0 else 0.0)
        tp2_est = tp2_px or (entry + _sgn * _t2a * atr if atr > 0 else 0.0)
        self.radar.arm(
            tp1_price=tp1_est, tp2_price=(tp2_est or entry), direction=side,
            entry_price=entry, tier=str(self.pipeline.data.get("tier") or ""),
        )
        px = float(self._get_current_price() or entry)
        # 2026-09-20起激活线公式改了(见breath_stop.py::_activation_gate_
        # price顶部注释)，这里直接问雷达自己刚arm()好的门槛，不再自己
        # 重复算一遍(TP1+TP2)/2——避免这条重启对账路径跟雷达内部用的
        # 公式各算各的、悄悄跑偏。
        gate = self.radar._activation_gate_price()
        # 2026-09-13修复(BNB实盘复现)：原来只靠"现价有没有过激活线(用tp1_est/
        # tp2_est现算的gate)"这一条判断"重启前雷达是不是已经激活过"——雷达真的
        # 已经激活过、把交易所止损从开仓时的hard_sl移到了盈利区间(比如
        # 738.06→733.87)，但重启这一刻价格恰好又回落到gate以下一点点(或者
        # tp1_est/tp2_est用ATR估算跟当初真实激活时用的TP1/TP2对不上)，这条
        # 判断就会误判"还没激活"，把已经进入trail/dynamic阶段的雷达打回
        # activated=False——而update()一开头就是"if not st.activated: return
        # None"，之后雷达再也不会推进，止损永远锁死在733.87不会再收紧，
        # 白白丢失"趋势强度系数不断锁住利润"这个雷达最核心的能力。
        # 交易所现有止损本身就是最权威的"有没有激活过"证据：全新开仓的硬
        # 止损必然在亏损方向(多头entry上方/空头entry下方)，只要止损已经
        # 越过entry进到盈利方向，就百分百证明雷达之前真的动过手——不需要
        # 再靠现价对gate的瞬时快照去猜，直接信这个更可靠的信号。
        _seed0 = _radar_seed or hard_sl
        already_moved_into_profit = _seed0 > 0 and (
            (side == "LONG" and _seed0 > entry)
            or (side == "SHORT" and _seed0 < entry)
        )
        if already_moved_into_profit:
            self.radar.mark_activated(entry_price=entry, tp2_price=(tp2_est or entry), direction=side)
            logger.warning(
                f"启动恢复：交易所止损{hard_sl}已在盈利方向(entry={entry})，"
                f"判定雷达重启前已激活，恢复为已激活状态(而非重新等待现价过gate)"
            )
        elif atr > 0 and gate > 0 and (
            (side == "LONG" and px >= gate) or (side == "SHORT" and px <= gate)
        ):
            if self.symbol in PYRAMID_ADD_SYMBOLS:
                # 2026-10-04：SNDK/ZEC放开雷达后，恢复时交易所止损还停在TV硬止
                # 损(没进盈利方向)，说明雷达从没真正武装过。直接mark_activated
                # 会把雷达锚在硬止损上绕过"保本起步"——不抢先标记，留给下一个
                # tick的should_activate走_activate_radar正常武装(保本+双均线
                # 加宽+推到交易所)，跟币安B/E同口径。
                logger.info(
                    f"[{self.symbol}] 启动恢复：现价已过激活线但交易所止损仍是TV硬止损"
                    f"，不抢先标记激活，交给下一tick按保本起步正常武装"
                )
            else:
                self.radar.mark_activated(entry_price=entry, tp2_price=(tp2_est or entry), direction=side)
        # 无论是否激活，都把雷达止损锚到交易所现有硬止损（只进不退守卫在 update 里）
        if _seed0 > 0:
            self.radar.seed_stop(_seed0)
        # 2026-10-04：恢复峰值——best_price重启后若从当前价重新起算，包线/动态追踪的"峰值"就偏了
        # (SNDK空单真实最低1714.4，重启后记成1719.8，止损被算得过紧)。用开仓以来的15m K线真实极值。
        try:
            _created = float(pos.get("createdDate") or 0)
            if _created > 0 and self.radar.get_state().activated:
                _bars15 = self._get_risk_klines(15, 800) or []
                _seg = [r for r in _bars15 if float(r[0]) >= _created - 15 * 60000]
                if _seg:
                    _peak = max(float(r[2]) for r in _seg) if side == "LONG" else min(float(r[3]) for r in _seg)
                    self.radar.seed_best(_peak)
                    logger.info(f"[{self.symbol}] 启动恢复：峰值恢复为开仓以来真实极值 {_peak}")
        except Exception as _e:
            logger.debug(f"[{self.symbol}] 启动恢复峰值恢复跳过: {_e}")

        # IDLE -> MONITORING 是跳阶段，正常 advance 会被 can_advance 拒；恢复路径 force 跳过
        try:
            self.pipeline.advance(Phase.MONITORING, Role.RADAR,
                                  note="启动恢复-重建监控", force=True)
        except Exception:
            pass
        self._start_monitoring()
        self._catchup_blocked_until = 0.0
        self._safe_alert(f"启动恢复：{side} {amt}@{entry} 已重建监控 "
                         f"(ATR≈{atr:.2f} SL={hard_sl or '?'} 雷达={'已激活' if self.radar.get_state().activated else '待命'})")

    # ==================== 健康检查 ====================

    def get_health(self) -> dict:
        """获取健康状态"""
        return {
            "version": COINW_SUPERVISOR_VERSION,
            "symbol": self.symbol,
            "pipeline": self.pipeline.phase.value,
            "trading_paused": trading_paused,
            "symbol_paused": bool(getattr(self, "_symbol_paused", False)),
            "monitoring": self._monitoring,
            "radar": self.radar.get_state().activated,
            "position_id": self.pipeline.data.get("position_id", ""),
        }


# ==================== 全局暂停控制 ====================

def pause_all_trading(reason: str):
    """暂停所有交易"""
    global trading_paused
    trading_paused = True
    logger.warning(f"全局暂停: {reason}")


def resume_all_trading():
    """恢复所有交易"""
    global trading_paused
    trading_paused = False
    logger.info("交易恢复")


def is_trading_paused() -> bool:
    """检查是否暂停"""
    return trading_paused


def block_catchup(symbol: str = "ETH", seconds: float = None):
    """人工中止心跳催单：这段时间内心跳不再把该品种补开。"""
    from app import get_supervisor  # 延迟导入避免循环
    sup = get_supervisor(symbol)
    sec = float(seconds if seconds is not None else CATCHUP_BLOCK_SEC)
    sup._catchup_blocked_until = time.time() + sec
    logger.warning(f"[{symbol}] 心跳催单已人工中止 {sec:.0f}s")
    return sup._catchup_blocked_until


def _symbols_with_orphaned_live_positions() -> list:
    """检测(不接管)不在ACTIVE_SYMBOLS白名单、但交易所上真实有非零仓位
    的品种。

    2026-09-19新增，同日內即被宝贝叫停自动接管(实盘复现：ZEC/ETH等宝贝
    自己手工开的非白名单仓位，一补建supervisor就被硬止损/雷达碰到，
    今天被强平了好几次)。宝贝原话："我自己手工开的其他品种不要管我的，
    不要平仓我的……白名单内的走系统健康开仓和雷达、硬止损，白名单外的
    我自己知道设置止盈止损"。这个函数现在只做**检测+返回列表**，不再
    被拿去补建supervisor(那样会让硬止损/雷达/催单等所有机制碰到这笔
    仓位)——调用方(recover_all_on_start/app.py housekeep巡检)只把结果
    用来发一条告警，提醒宝贝自己去管，不会实际接管。

    用一次账户级批量REST(coinw_client.get_all_positions，跟watchdog/
    housekeep同一套节流缓存机制)找全部持仓，不逐品种查。"""
    from coinw_client import coinw_client
    from symbol_config import SymbolConfig
    try:
        rows = coinw_client.get_all_positions(force=True)
    except Exception as e:
        logger.error(f"🚨 [孤儿仓位扫描] 账户级持仓核对失败，跳过: {e}")
        return []
    if not rows:
        return []
    whitelisted = set(ACTIVE_SYMBOLS)
    known = set(SymbolConfig.SYMBOL_MAP.keys())
    orphaned = []
    for sym, row in rows.items():
        sym = str(sym or "").upper()
        if not sym or sym in whitelisted or sym not in known:
            continue
        try:
            amt = abs(float(row.get("quantity", 0) or row.get("positionAmt", 0) or 0))
        except (TypeError, ValueError):
            continue
        if amt <= 0:
            continue
        orphaned.append(sym)
    if orphaned:
        logger.warning(
            f"⚠️ [孤儿仓位扫描] 发现{len(orphaned)}个不在ACTIVE_SYMBOLS、但交易所"
            f"仍有真实仓位的品种(不再自动接管，仅告警，由宝贝自己管理止盈"
            f"止损): {orphaned}"
        )
        try:
            from dingtalk import send_alert
            send_alert(
                f"⚠️ 白名单外发现仓位(VPS不再接管): {orphaned}。这些仓位不在"
                f"当前ACTIVE_SYMBOLS里，VPS不会再自动建supervisor接管硬止损/"
                f"雷达/强平逻辑——如果是你自己手工开的，请自行管理止盈止损；"
                f"如果是遗留的旧仓位，请人工核查。"
            )
        except Exception as e:
            logger.debug(f"白名单外仓位告警发送跳过: {e}")
    return orphaned


def recover_all_on_start():
    """引擎启动时对所有活跃品种做一次在场持仓恢复。"""
    # 2026-09-12修复：此前硬编码只恢复"ETH"一个品种——BNB当时已经在
    # webhook_parser.VALID_SYMBOLS里"名义支持"却漏在这里，等于重启后
    # BNB万一有仓位完全不会被recover_on_start接管，是个既存的孤儿仓
    # 风险缺口。现在跟其它5处品种清单一样改用symbol_config.ACTIVE_SYMBOLS
    # 统一权威来源。
    from app import get_supervisor
    # 2026-09-19：白名单外品种只检测+告警，不再补建supervisor接管——见
    # _symbols_with_orphaned_live_positions()顶部注释(同一天被宝贝叫停)。
    # 检测本身出意外(非函数内部已处理的REST失败，而是更底层的bug)也
    # 不该拖垮白名单品种自己的启动恢复。
    try:
        _symbols_with_orphaned_live_positions()
    except Exception as e:
        logger.error(f"孤儿仓位检测异常(不影响白名单品种启动恢复): {e}")
    for sym in ACTIVE_SYMBOLS:
        try:
            get_supervisor(sym).recover_on_start()
        except Exception as e:
            logger.error(f"[{sym}] 启动恢复异常: {e}")


def confirm_catchup(symbol: str = "ETH"):
    """人工确认追单：下一次合格心跳立即补开。"""
    from app import get_supervisor
    sup = get_supervisor(symbol)
    sup._chase_confirmed = True
    logger.warning(f"[{symbol}] 追单已人工确认")
    return True


def cancel_chase_watch(symbol: str = "ETH"):
    """人工中止追单确认观察窗，并冻结补开一段时间。"""
    from app import get_supervisor
    sup = get_supervisor(symbol)
    sup._chase_watch = {}
    sup._chase_confirmed = False
    sup._catchup_blocked_until = time.time() + CATCHUP_BLOCK_SEC
    logger.warning(f"[{symbol}] 追单观察窗已中止，冻结补开 {CATCHUP_BLOCK_SEC:.0f}s")
    return sup._catchup_blocked_until


def housekeep(symbol: str = "ETH"):
    """周期巡检：空仓清孤儿单；有仓但无人管 -> 兜底恢复。"""
    from app import get_supervisor
    from coinw_client import is_orders_query_failed
    sup = get_supervisor(symbol)
    try:
        have = sup._pos_qty()
        if have == 0:
            if not sup._monitoring:
                orders = sup.client.get_open_orders(symbol, position_type="plan")
                if not is_orders_query_failed(orders) and orders:
                    n = sup.client.cancel_all_orders(symbol)
                    logger.warning(f"[{symbol}] housekeep：清孤儿单 {n} 笔")
        elif not sup._monitoring:
            logger.warning(f"[{symbol}] housekeep：发现无人管持仓，触发恢复")
            sup.recover_on_start()
        elif sup._loop_beat and (time.time() - sup._loop_beat) > WATCHDOG_STALE_SEC:
            stale = time.time() - sup._loop_beat
            logger.error(f"[{symbol}] watchdog：监控循环 {stale:.0f}s 未跳，重启监控线程")
            sup._safe_alert(f"watchdog：监控循环停跳 {stale:.0f}s，已重启")
            sup._monitoring = False
            time.sleep(1)
            sup._start_monitoring()
    except Exception as e:
        logger.error(f"[{symbol}] housekeep 异常: {e}")

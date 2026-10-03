# -*- coding: utf-8 -*-
"""
2026-10-03 擂台策略去重归档记录。

宝贝的要求原话："保留有收益的就行，擂台太多雷同且负收益严重的，我们记录
在案，以后绝不再用，懒得浪费精力和内存"。

判定方法(基于 data/shadow_v2.db 当时的全量 strategy_equity + 已平仓
shadow_positions_v2 统计，起始虚拟资金各$1000):
  1. equity(含未实现浮动) > 1000 即"有收益" -> 保留，不在此列表。
  2. ttm_squeeze_* 系列(加仓/止损变体实验，基础ttm_squeeze本身除外)：
     全部为负 -> 整族归档，只留基础版。
  3. 其余：已平仓样本 n>=10 且 pnl<=-$15 -> "负收益严重" -> 归档。
  4. donchian_reversal/ichimoku_cloud/td_sequential 三个样本量本来薄
     (n_closed<10)，但未实现浮亏已经很大($87~$224)，不能用"样本太薄"当
     保护伞，一并归档。
  5. 剩下样本太薄或仅轻微亏损(<$15)的，证据不足，暂不归档，继续跑攒数据
     (包括刚修正Pine逻辑/刚上线的 ema7_25_micro_breakout 全家族和
     ema8_30/ema7_30_micro_breakout ZEC变体，样本几乎为0，豁免)。

这份名单只影响擂台(纯模拟盘，不动真实资金)的"当前要不要继续跑"，不是
删代码——comparison_roster.py里的原始定义和注释历史保持不动，只在
multi_strategy_runner.py::main_loop实际组装"这一轮要巡检谁"时按这份
名单过滤掉。以后任何人想重新验证某个策略，照样能在roster定义里找到它
的参数配置，解除这里的名单即可重新启用，不用重写。

绝不再用的理由总结(供人类复查，不是代码逻辑用)：
  - 单一最差: time_series_momentum_v2 (-$491.61, n=40)
  - 样本量巨大仍稳定亏损(非噪音): opening_range_breakout(n=675,-$161.55)、
    mtf_ema_pullback(n=518,-$100.22)、ehlers_fisher_transform(n=892,-$32.30)、
    vwap_mean_reversion_45m(n=668,-$52.27)、vwap_ema_regime(n=271,-$44.92)
  - 雷同家族，基础版留/变体淘汰:
    hma_trend(留) vs hma_trend_v2/_crypto/_2h/_150m/_reverse_strong/
      _reverse_tiered/_reversal_control/_agile(淘汰8个)
    chanlun_pivot(留) vs _duan/_mtf_div/_sentinel(淘汰3个)
    heikin_ashi_trend(留) vs _v2/_v3/_agile/_ema7_25(淘汰4个)
    cross_momentum/_v2(留) vs _agile/_runwin(淘汰2个)
    dual_momentum/_v2/_crypto(留) vs _agile/_runwin/_stocks(淘汰3个)
    eth_pingkai_buhuchi_narrow(留) vs 基础版/_narrow_v2(淘汰2个)
    eth_kdj_exempt_narrow(留) vs _v2(淘汰1个)
    mtf_ema_macd_cci(留) vs _v2/_agile(淘汰2个)
    kaufman_ama(留) vs _v2/_v3(淘汰2个)
    cci_mean_reversion家族(留base/6h/8h) vs _12h(淘汰1个)
    turtle_breakout(留) vs turtle_system2(淘汰1个)
    dual_ema_band_7_25全系列(radar_1h/radar_90m/1h/90m)+dual_ema_spot_7_25
      (1h/90m)：6个全部亏损，整族淘汰，无保留版本
    ttm_squeeze(留base) vs _2h/_150m/_tp/_trail/_pyramid/_tight_stop/
      _wide_stop(淘汰7个)
  - 未实现浮亏拖累严重: donchian_reversal(-$223.98)、td_sequential(-$87.91)、
    ichimoku_cloud(-$87.26)

共归档 76 个策略名(包含品种×策略×周期粒度合并后的 strategy 字段值)。
"""

PRUNED_STRATEGIES = frozenset({
    "time_series_momentum_v2",
    "volatility_breakout",
    "oi_price_confirm",
    "breakout_retest",
    "parabolic_sar_flip",
    "wyckoff_spring",
    "kaufman_ama_v3",
    "opening_range_breakout",
    "schaff_trend_cycle",
    "darvas_box",
    "kaufman_ama_v2",
    "dual_ema_band_7_25_radar_1h",
    "mtf_ema_macd_cci_agile",
    "dual_ema_band_7_25_radar_90m",
    "dual_ema_band_7_25_1h",
    "hma_trend_agile",
    "wavetrend",
    "dual_ema_band_7_25_90m",
    "heikin_ashi_trend_v3",
    "cci_mean_reversion_12h",
    "mtf_ema_pullback",
    "vortex_indicator",
    "cvd_divergence",
    "adx_efficiency_zscore",
    "raschke_adx_pullback",
    "hma_trend_150m",
    "cross_momentum_agile",
    "ttm_squeeze_2h",
    "hma_trend_reversal_control",
    "eth_pingkai_buhuchi",
    "ttm_squeeze_150m",
    "heikin_ashi_trend_agile",
    "time_series_momentum",
    "williams_alligator",
    "hma_trend_2h",
    "hma_trend_reverse_strong",
    "clenow_momentum",
    "trend_ensemble_crypto",
    "mtf_ema_macd_cci_v2",
    "dual_ema_spot_7_25_1h",
    "hma_trend_v2",
    "cross_momentum_runwin",
    "heikin_ashi_trend_ema7_25",
    "chanlun_pivot_mtf_div",
    "vwap_mean_reversion_45m",
    "ttm_squeeze_tp",
    "ttm_squeeze_trail",
    "ttm_squeeze_pyramid",
    "dual_ema_spot_7_25_90m",
    "us_stock_rth_momentum",
    "eth_pingkai_buhuchi_narrow_v2",
    "vwap_ema_regime",
    "ttm_squeeze_tight_stop",
    "tv_multiscore_v1",
    "trend_ensemble_stocks",
    "heikin_ashi_trend_v2",
    "chanlun_pivot_sentinel",
    "fibonacci_retracement",
    "hma_trend_reverse_tiered",
    "ehlers_fisher_transform",
    "tsmom_agile",
    "hma_trend_crypto",
    "dual_momentum_agile",
    "dual_momentum_runwin",
    "ttm_squeeze_wide_stop",
    "chanlun_pivot_duan",
    "bnb_heartbeat_real_reversal",
    "eth_kdj_exempt_narrow_v2",
    "vwap_mean_reversion_v2",
    "crypto_combo_coordinated_4h",
    "crypto_combo_blend_4h",
    "turtle_system2",
    "dual_momentum_stocks",
    "donchian_reversal",
    "ichimoku_cloud",
    "td_sequential",
})

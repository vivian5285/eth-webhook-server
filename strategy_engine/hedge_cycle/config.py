"""2026-10-05: 用户提出的EMA趋势对冲循环，固定参数先模拟，不按收益调参。"""
SYMBOLS = ('ETHUSDT', 'SNDKUSDT', 'XAUUSDT')
HEDGE_NAME = 'ema_hedge_cycle_4h_1d'
CONTROL_NAME = 'ema_hedge_net_control_4h_1d'
NAMES = (HEDGE_NAME, CONTROL_NAME)
PARAMS = {
    'ema_fast': 7, 'ema_slow': 25, 'slope_lookback': 3, 'atr_period': 14,
    'atr_percentile_lookback': 100, 'low_atr_quantile': 0.30,
    'ema_gap_atr': 0.30, 'trend_confirm_bars': 2,
    'max_gross_mult': 3.0, 'margin_leverage': 10.0,
    'max_margin_frac': 0.30, 'max_direction_margin_frac': 0.20,
    'max_stop_risk_frac': 0.025, 'initial_leg_risk_frac': 0.005,
    'add_risk_frac': 0.005, 'max_drawdown': 0.15,
    'main_stop_atr': 2.0, 'hedge_stop_atr': 1.0,
    'hedge_ratio': 0.30, 'hedge_ratio_limit': 0.50,
    'take_profit_ratio': 0.25, 'add_decay': [1.0, 0.5, 0.25],
    'cycle_distance_atr': 1.0, 'breakout_period': 20,
    'min_cycle_cost_multiple': 2.0, 'slippage_bps': 2.0,
    # Gate三个合约当前普通taker费率0.075%，不假定VIP折扣。
    'fee_rate': 0.00075, 'max_symbol_main_mult': 0.50,
    'poll_seconds': 60,
}
DESCRIPTIONS = {
    HEDGE_NAME: ('ETH/SNDK/XAU共用$1000的双向循环实验。4h+1d EMA7/25方向和斜率一致、'
        '4h连续2根确认后主仓顺势，对冲目标主仓30%、绝对上限50%。每走1ATR且组合'
        '扣成本浮盈达标，兑现主仓25%，用释放资金递减再入场，旧对冲先平后换；最多3轮。'
        '10倍保证金假设下，总多空名义≤权益3倍、保证金≤30%、单方向≤20%、'
        '总止损风险≤2.5%、回撤15%平仓停机。盘整减半、反转先退出旧主仓。'
        '手续费/滑点/各合约实际资金费率均计入；排名看盯市净值，不能只看兑现浮盈。'),
    CONTROL_NAME: ('对冲循环的净敞口镜像对照。ETH/SNDK/XAU共用独立$1000，每次同步实验组'
        '的多减空净数量，只持净方向，去掉同品种相互抵消的多空；使用同一Gate价、'
        '手续费、滑点和资金费率。用它检验双仓是否带来额外价值或只增加换手成本。'
        '受同样风险硬上限约束；若被限额而无法精确跟踪，会在原因字段记录。')
}

# EMA microstructure research

As of: 2026-09-30T16:10:49.146358+00:00. Paper research only; no live orders or arena-ranking changes.
Signals use completed candles; fills occur at the next candle open. A reversal closes
the old position and opens the new one at that open, charging both legs.
Position size is 1x current equity; Pine stops use 2.5 ATR, channel stops trail at 3 ATR.
Fees are 5 bp per leg plus 2 bp adverse slippage; stress adds 7 bp per leg.
Protective stops are active immediately on entry, which can differ from Pine historical fills.
Funding, liquidation, bid/ask queue, partial fills and intrabar order sequence are not modeled.
The first 65% of available bars are training; final 35% are a single chronological holdout.
Pine variants were fixed before the first evaluation. Channel variants were added after
seeing the first holdout results and are exploratory, not independently validated.

## ETHUSDT 45m

Bars: 7765; 2026-01-31T00:00:00+00:00 to 2026-09-30T15:00:00+00:00; holdout from 2026-07-07T17:15:00+00:00; gaps: 0 (max missing 0 bars). Buy-and-hold net: train -9.395%, holdout 48.405% (DD 8.619%).

| Variant | Train net % | Holdout net % | Holdout max DD % | Trades | PF | Turnover x | Fees $ | Stress net % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| pine_7_30 | -47.403 | -26.268 | 28.967 | 177 | 0.682 | 302.5 | 1512.48 | -42.564 |
| pine_7_25 | -47.461 | -21.89 | 26.497 | 181 | 0.743 | 319.62 | 1598.11 | -39.49 |
| 7_25_breakout3 | -50.99 | -23.805 | 30.189 | 211 | 0.737 | 363.4 | 1817.01 | -43.399 |
| 7_25_body0 | -47.357 | -22.147 | 26.738 | 182 | 0.741 | 320.67 | 1603.35 | -39.773 |
| 7_25_no_breakout | -57.275 | -32.616 | 37.001 | 290 | 0.693 | 481.73 | 2408.66 | -55.197 |
| 7_25_band_only | -60.058 | -33.019 | 37.645 | 297 | 0.691 | 490.54 | 2452.71 | -55.899 |
| channel20_10_ema200 | -16.861 | -7.635 | 19.882 | 66 | 0.871 | 130.9 | 654.51 | -15.834 |
| channel40_20_ema200 | -13.622 | 0.106 | 17.291 | 48 | 1.002 | 98.25 | 491.26 | -6.453 |
| channel20_10_ema100 | -3.423 | -10.284 | 22.769 | 71 | 0.833 | 140.3 | 701.48 | -18.827 |
| channel40_20_no_ema | -6.598 | -5.684 | 21.473 | 53 | 0.894 | 107.23 | 536.14 | -12.497 |
| channel40_20_longonly | -6.475 | 20.317 | 10.804 | 29 | 1.697 | 67.37 | 336.86 | 15.528 |

## ETHUSDT 1h

Bars: 5845; 2026-01-30T02:00:00+00:00 to 2026-09-30T14:00:00+00:00; holdout from 2026-07-07T09:00:00+00:00; gaps: 0 (max missing 0 bars). Buy-and-hold net: train -10.299%, holdout 51.051% (DD 9.096%).

| Variant | Train net % | Holdout net % | Holdout max DD % | Trades | PF | Turnover x | Fees $ | Stress net % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| pine_7_30 | -34.927 | -23.565 | 28.855 | 127 | 0.683 | 224.0 | 1119.98 | -36.134 |
| pine_7_25 | -31.577 | -22.945 | 28.364 | 138 | 0.701 | 243.81 | 1219.04 | -36.6 |
| 7_25_breakout3 | -37.635 | -33.866 | 36.36 | 170 | 0.593 | 272.74 | 1363.68 | -47.985 |
| 7_25_body0 | -32.415 | -22.352 | 27.948 | 142 | 0.705 | 249.39 | 1246.94 | -36.467 |
| 7_25_no_breakout | -40.811 | -41.996 | 42.509 | 259 | 0.562 | 381.33 | 1906.67 | -59.73 |
| 7_25_band_only | -42.184 | -43.972 | 44.15 | 268 | 0.55 | 390.73 | 1953.67 | -61.591 |
| channel20_10_ema200 | -6.92 | 11.261 | 11.883 | 43 | 1.3 | 92.09 | 460.47 | 4.705 |
| channel40_20_ema200 | 5.849 | -6.731 | 21.461 | 37 | 0.834 | 72.44 | 362.18 | -11.501 |
| channel20_10_ema100 | -3.482 | 5.53 | 13.795 | 51 | 1.128 | 107.36 | 536.81 | -1.812 |
| channel40_20_no_ema | 2.817 | -7.16 | 21.829 | 41 | 0.831 | 79.89 | 399.44 | -12.407 |
| channel40_20_longonly | 1.345 | 17.809 | 12.744 | 21 | 1.88 | 46.47 | 232.36 | 14.394 |

## SNDKUSDT 1h

Bars: 4226; 2026-04-07T13:00:00+00:00 to 2026-09-30T14:00:00+00:00; holdout from 2026-07-30T23:00:00+00:00; gaps: 0 (max missing 0 bars). Buy-and-hold net: train 43.403%, holdout 29.222% (DD 21.123%).

| Variant | Train net % | Holdout net % | Holdout max DD % | Trades | PF | Turnover x | Fees $ | Stress net % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| pine_7_30 | 0.425 | -14.103 | 37.565 | 81 | 0.853 | 153.9 | 769.48 | -23.414 |
| pine_7_25 | -15.4 | -19.665 | 36.853 | 91 | 0.796 | 163.04 | 815.21 | -29.368 |
| 7_25_breakout3 | -33.653 | -27.264 | 38.757 | 108 | 0.736 | 180.1 | 900.51 | -37.564 |
| 7_25_body0 | -14.72 | -18.203 | 37.086 | 92 | 0.811 | 167.94 | 839.72 | -28.178 |
| 7_25_no_breakout | -25.782 | -43.926 | 49.291 | 139 | 0.626 | 204.15 | 1020.74 | -53.936 |
| 7_25_band_only | -27.905 | -42.643 | 51.303 | 145 | 0.652 | 220.2 | 1100.98 | -53.276 |
| channel20_10_ema200 | 41.85 | -8.094 | 24.785 | 36 | 0.876 | 68.93 | 344.64 | -12.637 |
| channel40_20_ema200 | 26.352 | -0.322 | 21.749 | 27 | 0.994 | 54.93 | 274.65 | -4.072 |
| channel20_10_ema100 | 49.084 | -4.239 | 27.126 | 41 | 0.939 | 79.11 | 395.54 | -9.619 |
| channel40_20_no_ema | 3.708 | -9.49 | 23.118 | 34 | 0.867 | 65.85 | 329.23 | -13.773 |
| channel40_20_longonly | 15.237 | 19.248 | 16.435 | 16 | 1.53 | 37.37 | 186.83 | 16.605 |

## SNDKUSDT 90m

Bars: 2817; 2026-04-07T13:30:00+00:00 to 2026-09-30T13:30:00+00:00; holdout from 2026-07-31T00:00:00+00:00; gaps: 0 (max missing 0 bars). Buy-and-hold net: train 13.664%, holdout 28.745% (DD 20.256%).

| Variant | Train net % | Holdout net % | Holdout max DD % | Trades | PF | Turnover x | Fees $ | Stress net % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| pine_7_30 | 69.712 | 4.326 | 28.771 | 50 | 1.055 | 95.16 | 475.82 | -2.827 |
| pine_7_25 | 98.548 | -5.074 | 29.466 | 55 | 0.929 | 101.97 | 509.84 | -12.209 |
| 7_25_breakout3 | 81.043 | -8.855 | 32.559 | 66 | 0.891 | 121.78 | 608.92 | -16.999 |
| 7_25_body0 | 100.009 | -6.315 | 30.047 | 58 | 0.914 | 107.23 | 536.14 | -13.722 |
| 7_25_no_breakout | 30.842 | -30.58 | 48.267 | 92 | 0.688 | 143.32 | 716.58 | -39.071 |
| 7_25_band_only | 33.914 | -30.953 | 47.278 | 100 | 0.684 | 155.82 | 779.12 | -40.074 |
| channel20_10_ema200 | -2.995 | 6.232 | 21.891 | 23 | 1.139 | 45.85 | 229.27 | 2.825 |
| channel40_20_ema200 | -24.21 | -5.634 | 25.947 | 19 | 0.891 | 34.61 | 173.07 | -8.177 |
| channel20_10_ema100 | 1.441 | 3.015 | 26.173 | 26 | 1.056 | 51.92 | 259.62 | -0.732 |
| channel40_20_no_ema | -15.652 | -8.862 | 23.55 | 23 | 0.862 | 42.7 | 213.49 | -11.836 |
| channel40_20_longonly | -26.893 | 19.53 | 18.678 | 11 | 1.632 | 23.9 | 119.51 | 17.703 |

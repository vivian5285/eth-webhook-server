# MTF strategy research

As of 2026-09-30T16:31:10.996200+00:00. Research only: no live orders or arena ranking changes.
Only confirmed higher-timeframe candles are mapped to each completed base candle.
Signals fill at the next base candle open; reversals pay both closing and opening costs.
1x equity exposure, 5 bp taker fee plus 5 bp adverse slippage per leg; stress uses 15 bp slip.
Settled public funding is included conservatively but its sequence versus an intrabar stop is approximate.
Intrabar stop fills use the worse of stop and gap-open. Liquidation/queue/partial fills are not modeled.
Initial breakout/pullback/mean-reversion candidates were fixed before the first MTF run.
The hold family was added after inspecting that run and is exploratory, not independent validation.
Prior ETH/SNDK data were also inspected in an earlier study. Forward paper trading is required.

## ETHUSDT 30m

Bars 11770: 2026-01-28T11:00:00+00:00 .. 2026-09-30T15:30:00+00:00; common train start 2026-01-29T17:00:00+00:00; validation 2026-06-12T07:30:00+00:00; test 2026-07-31T08:30:00+00:00; gaps 0; funding events 1096.
Buy-and-hold net train/validation/test: -41.456% / 13.402% / 40.485%.

| Candidate | Train % | Validation % | Test % | Test DD % | Closed | Funding $ | Stress % |
|---|---:|---:|---:|---:|---:|---:|---:|
| breakout20_4h | 11.408 | -13.685 | -1.821 | 14.338 | 35 | -22.29 | -8.486 |
| breakout20_4h_strong | -4.261 | -8.3 | -6.438 | 12.277 | 22 | -18.37 | -10.478 |
| breakout40_4h | 11.19 | -11.781 | 5.597 | 11.155 | 22 | -21.04 | 1.034 |
| breakout20_12h | 9.338 | -14.636 | 8.536 | 11.685 | 29 | -23.82 | 2.401 |
| pullback_4h | -8.148 | -18.621 | -0.611 | 19.809 | 54 | -22.63 | -10.813 |
| pullback_12h | 7.234 | -16.103 | 5.719 | 15.524 | 45 | -24.27 | -3.39 |
| breakout20_ungated | -12.106 | -5.163 | -19.63 | 21.263 | 88 | -23.94 | -32.682 |
| breakout40_4h_long | 2.463 | -5.394 | 9.216 | 11.155 | 18 | -23.02 | 5.352 |
| hold_4h_both | 1.056 | -11.02 | -21.432 | 22.639 | 26 | -15.29 | -25.437 |
| hold_12h_both | -3.699 | -5.931 | -13.331 | 13.331 | 15 | -12.0 | -15.923 |
| hold_4h_long | -9.298 | -2.324 | -15.674 | 16.617 | 19 | -23.14 | -18.82 |
| hold_12h_long | -7.883 | -0.656 | -5.95 | 7.029 | 10 | -15.56 | -7.813 |

## ETHUSDT 1h

Bars 6010: 2026-01-23T06:00:00+00:00 .. 2026-09-30T15:00:00+00:00; common train start 2026-01-25T18:00:00+00:00; validation 2026-06-09T23:00:00+00:00; test 2026-07-30T01:00:00+00:00; gaps 0; funding events 1096.
Buy-and-hold net train/validation/test: -43.174% / 15.131% / 40.001%.

| Candidate | Train % | Validation % | Test % | Test DD % | Closed | Funding $ | Stress % |
|---|---:|---:|---:|---:|---:|---:|---:|
| breakout20_4h | 6.328 | -4.965 | 1.812 | 15.848 | 17 | -35.19 | -1.616 |
| breakout20_4h_strong | 1.713 | -3.199 | -0.903 | 14.272 | 11 | -22.59 | -3.075 |
| breakout40_4h | -3.033 | -4.234 | 1.804 | 17.312 | 14 | -28.63 | -1.023 |
| breakout20_12h | 2.333 | -10.007 | 16.821 | 10.399 | 12 | -36.47 | 14.045 |
| pullback_4h | -24.283 | -20.641 | 0.767 | 17.86 | 37 | -23.89 | -6.449 |
| pullback_12h | -19.728 | -17.589 | 3.004 | 16.515 | 32 | -26.26 | -3.402 |
| breakout20_ungated | 11.068 | 3.169 | -6.97 | 18.457 | 43 | -27.52 | -14.729 |
| breakout40_4h_long | -4.473 | -1.966 | 5.466 | 17.312 | 12 | -31.46 | 2.964 |
| hold_4h_both | -9.928 | -13.461 | -9.76 | 24.785 | 27 | -32.77 | -14.628 |
| hold_12h_both | -0.334 | -9.303 | -2.287 | 18.716 | 16 | -30.29 | -5.408 |
| hold_4h_long | -13.83 | -4.123 | -0.148 | 21.989 | 20 | -44.96 | -4.161 |
| hold_12h_long | -14.75 | -1.448 | 8.733 | 16.421 | 11 | -39.4 | 6.365 |

## SNDKUSDT 1h

Bars 4227: 2026-04-07T13:00:00+00:00 .. 2026-09-30T15:00:00+00:00; common train start 2026-05-22T23:00:00+00:00; validation 2026-07-13T09:00:00+00:00; test 2026-08-17T15:00:00+00:00; gaps 0; funding events 529.
Buy-and-hold net train/validation/test: 18.499% / 0.215% / -4.092%.

| Candidate | Train % | Validation % | Test % | Test DD % | Closed | Funding $ | Stress % |
|---|---:|---:|---:|---:|---:|---:|---:|
| breakout20_4h | 2.803 | 3.921 | -10.344 | 14.167 | 13 | -17.88 | -12.669 |
| breakout20_4h_strong | -2.606 | -0.82 | -3.348 | 8.861 | 9 | -18.23 | -5.073 |
| breakout40_4h | -3.896 | 0.351 | -13.069 | 16.045 | 12 | -22.19 | -15.157 |
| breakout20_12h | 4.765 | 15.32 | -7.634 | 10.889 | 11 | -8.97 | -9.67 |
| pullback_4h | -0.641 | -13.96 | -14.819 | 17.541 | 17 | 17.94 | -17.692 |
| pullback_12h | -2.111 | -6.925 | -12.4 | 12.4 | 12 | 12.03 | -14.491 |
| breakout20_ungated | -6.468 | 28.055 | -16.628 | 27.853 | 34 | -12.08 | -22.208 |
| breakout40_4h_long | -2.544 | 14.55 | -6.615 | 7.877 | 8 | -9.13 | -8.098 |
| hold_4h_both | -12.616 | -3.047 | -10.305 | 19.217 | 11 | 17.21 | -12.367 |
| hold_12h_both | -3.741 | 8.091 | -8.514 | 17.714 | 7 | 32.43 | -9.811 |
| hold_4h_long | 14.5 | 10.617 | -4.146 | 12.492 | 7 | -0.19 | -5.48 |
| hold_12h_long | 2.067 | 15.269 | -2.5 | 14.745 | 5 | 7.04 | -3.47 |

## SNDKUSDT 90m

Bars 2817: 2026-04-07T13:30:00+00:00 .. 2026-09-30T13:30:00+00:00; common train start 2026-05-22T22:30:00+00:00; validation 2026-07-13T09:00:00+00:00; test 2026-08-17T13:30:00+00:00; gaps 0; funding events 529.
Buy-and-hold net train/validation/test: 18.547% / -5.669% / 1.869%.

| Candidate | Train % | Validation % | Test % | Test DD % | Closed | Funding $ | Stress % |
|---|---:|---:|---:|---:|---:|---:|---:|
| breakout20_4h | -3.959 | 9.51 | -9.554 | 18.792 | 10 | -7.27 | -11.377 |
| breakout20_4h_strong | -4.972 | 12.99 | -3.642 | 11.948 | 8 | -9.83 | -5.174 |
| breakout40_4h | -17.332 | 4.476 | -2.153 | 14.454 | 7 | 5.16 | -3.524 |
| breakout20_12h | -4.054 | 4.772 | -3.957 | 11.449 | 8 | -0.98 | -5.513 |
| pullback_4h | -17.012 | -22.102 | 1.137 | 8.28 | 12 | 21.69 | -1.275 |
| pullback_12h | -4.767 | 4.964 | -3.642 | 10.26 | 12 | 15.56 | -5.947 |
| breakout20_ungated | -4.961 | 69.318 | -12.165 | 23.142 | 23 | 1.13 | -16.182 |
| breakout40_4h_long | -12.582 | 10.982 | 0.323 | 10.402 | 5 | 4.13 | -0.676 |
| hold_4h_both | -3.924 | -16.722 | -15.897 | 20.386 | 11 | 18.88 | -17.838 |
| hold_12h_both | -9.233 | -3.363 | -12.891 | 19.053 | 7 | 18.0 | -14.127 |
| hold_4h_long | 11.05 | 4.242 | -8.063 | 12.67 | 7 | 1.13 | -9.342 |
| hold_12h_long | -2.402 | 8.548 | -6.781 | 16.856 | 5 | 4.3 | -7.709 |

## XAUUSDT 1h

Bars 6010: 2026-01-23T06:00:00+00:00 .. 2026-09-30T15:00:00+00:00; common train start 2026-01-25T18:00:00+00:00; validation 2026-06-09T23:00:00+00:00; test 2026-07-30T01:00:00+00:00; gaps 0; funding events 1760.
Buy-and-hold net train/validation/test: -20.053% / -4.698% / 0.488%.

| Candidate | Train % | Validation % | Test % | Test DD % | Closed | Funding $ | Stress % |
|---|---:|---:|---:|---:|---:|---:|---:|
| breakout20_4h | -11.515 | 1.833 | 0.908 | 7.022 | 23 | 42.61 | -3.636 |
| breakout20_4h_strong | -10.915 | 0.302 | -2.032 | 7.616 | 20 | 34.55 | -5.886 |
| breakout40_4h | -12.525 | 0.131 | -2.389 | 9.507 | 22 | 40.41 | -6.604 |
| breakout20_12h | -7.86 | 0.402 | 0.983 | 7.33 | 21 | 40.77 | -3.179 |
| pullback_4h | -11.042 | -8.315 | -4.286 | 9.651 | 45 | 30.73 | -12.555 |
| pullback_12h | -11.505 | -8.521 | -3.009 | 7.137 | 42 | 33.35 | -10.85 |
| breakout20_ungated | -13.914 | -0.188 | -8.975 | 15.699 | 49 | 44.09 | -17.511 |
| breakout40_4h_long | -7.555 | -2.964 | 0.006 | 4.685 | 11 | -3.41 | -2.171 |
| gold_meanrev_4h | -8.896 | -2.241 | -4.626 | 5.39 | 15 | -4.7 | -7.456 |
| gold_meanrev_12h | -12.979 | -6.311 | -2.058 | 3.752 | 29 | -3.39 | -7.58 |
| hold_4h_both | -13.366 | -1.517 | 1.057 | 6.445 | 17 | 32.12 | -2.338 |
| hold_12h_both | -19.331 | -0.769 | 8.514 | 4.421 | 10 | 34.72 | 6.375 |
| hold_4h_long | -11.289 | -2.288 | 4.454 | 2.482 | 7 | -8.27 | 3.001 |
| hold_12h_long | -7.067 | -2.623 | 5.242 | 3.135 | 5 | -9.56 | 4.194 |

## XAUUSDT 90m

Bars 4090: 2026-01-18T00:00:00+00:00 .. 2026-09-30T13:30:00+00:00; common train start 2026-01-25T10:30:00+00:00; validation 2026-06-07T13:30:00+00:00; test 2026-07-28T16:30:00+00:00; gaps 0; funding events 1760.
Buy-and-hold net train/validation/test: -18.046% / -7.21% / 1.506%.

| Candidate | Train % | Validation % | Test % | Test DD % | Closed | Funding $ | Stress % |
|---|---:|---:|---:|---:|---:|---:|---:|
| breakout20_4h | -13.262 | 1.074 | 2.892 | 4.763 | 16 | 43.92 | -0.354 |
| breakout20_4h_strong | -13.579 | 1.804 | 1.437 | 4.685 | 14 | 41.47 | -1.372 |
| breakout40_4h | -13.698 | -0.445 | -1.421 | 6.893 | 14 | 44.11 | -4.17 |
| breakout20_12h | -10.383 | 1.921 | 1.729 | 6.403 | 15 | 43.86 | -1.291 |
| pullback_4h | -8.745 | -5.108 | -5.412 | 8.242 | 27 | 35.0 | -10.398 |
| pullback_12h | -4.942 | -5.276 | -4.986 | 7.541 | 29 | 44.72 | -10.354 |
| breakout20_ungated | -16.0 | 2.616 | -10.399 | 15.4 | 35 | 39.17 | -16.58 |
| breakout40_4h_long | -10.995 | -3.269 | 4.627 | 2.966 | 5 | -1.0 | 3.586 |
| gold_meanrev_4h | -5.641 | -0.785 | -2.849 | 3.767 | 11 | -6.07 | -4.97 |
| gold_meanrev_12h | -11.926 | -7.182 | 0.762 | 3.879 | 21 | 0.53 | -3.376 |
| hold_4h_both | -19.307 | -5.737 | 3.572 | 5.34 | 18 | 65.16 | -0.097 |
| hold_12h_both | -16.565 | -3.322 | 5.411 | 5.499 | 11 | 31.87 | 3.116 |
| hold_4h_long | -13.806 | -3.426 | 4.07 | 2.715 | 7 | -10.38 | 2.622 |
| hold_12h_long | -7.575 | -3.251 | 5.124 | 2.909 | 5 | -13.31 | 4.077 |

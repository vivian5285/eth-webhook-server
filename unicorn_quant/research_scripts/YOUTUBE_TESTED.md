# 已回测的油管博主 / 视频登记表

**用途：避免重复测。** 宝贝发来新的博主或视频时，先在这里搜：频道名、频道ID（UC…）、视频ID、交易员名字。
已测过的就直接引用当时的结论（8890 研究结论页同名条目），只测这里没有的新规则。每测完一批就在这里追加。

统一口径（除非另写）：币安永续主流5币（BTC/ETH/SOL/BNB/XRP），1m 合成任意周期，2022-01..2026-09；
吃单 5bp + 滑点 1.5bp 每边，止损再加 2bp；样本内 2022-2024 / 样本外 2025-2026；同时报零成本 R 判断"有没有真优势"。
日线/8h 子腿类按"接进方案F逐日盯市后比值是否超过方案F"判定。

## 频道（整频道或多期测过）

| 频道 | 频道ID / 链接 | 测了什么 | 日期 | 结论 | 脚本 |
|---|---|---|---|---|---|
| 投机实验室 (@speculation) | youtube.com/@speculation | 全频道 115 期逐期（视频清单见 speculation_channel/catalog.tsv，含半木夏、Vegas隧道、Rob Hoffman、BNF、Minervini、Qullamaggie、Kristjan、Flight、Umar、Alex Temiz、Darek、Bit浪浪、Pradeep Bonde、Ted Zhang、Patrick Nill、David MACD三背离、Martin、Tony、Adhikari、Kyle、Lance、Wyckoff、Lorentzian、AHR999、PEAD 等） | 2026-10-30 | 方案F不变；急跌反弹两条上擂台纸面 | speculation_channel/batch01-18 |
| Chris Creamer（订单流） | 视频 fklnLclljFQ | 吸收→再砸失败 | 2026-10-30 | 否决 | orderflow_absorption/ |
| Fabio Valentini（订单流） | 视频 EucLjn0Wdnw | IVB开盘区间突破 / 趋势+AAA / 伦敦盘均值回归 | 2026-10-30 | 否决 | orderflow_absorption/fabio.py |
| Rayner Teo (TradingwithRayner) | — | 200MA+回调50MA、趋势内布林、200MA+50根突破、布林收缩、美股52周新高 | 2026-10-30 | 否决 | youtube_top/ |
| ICT (Inner Circle Trader) | — | 银弹FVG、Judas、海龟汤、OTE、2022模型、Unicorn；另 10-19 测过 4H→15m→3m CISD | 2026-10-30 | 否决 | youtube_top/ |
| Ross Cameron (Warrior Trading) | — | gap&go、首次回调、MACD | 2026-10-30 | 否决 | youtube_top/cameron_daily.py |
| The Trading Channel | — | 突破回踩、pin bar、EMA20/50回调、RSI背离 | 2026-10-30 | 否决 | youtube_top/ |
| Trading with DaviddTech | — | UTBot+STC 等（最接近：8h 5.40 vs 5.41） | 2026-10-30 | 否决 | youtube_top/ |
| Patrick Wieland / SMB Capital / TheSecretMindset | — | 各招牌策略 | 2026-10-30 | 否决 | youtube_top/ |
| DeepSeek 推荐清单（约20个，含 TradingView 高分享） | — | EMA9/21+RSI、SSL、超级趋势变体、AO、Dynamic Breakout II、Dual Thrust、多时段ORB、VWAP+EMA9、RSI(2)+200MA、配对、马丁 等 | 2026-10-30 | 否决 | add_deepseek_list.py |
| TV 文档 11 份 | — | 万亿战神v6.5、SuperTrend+QQE+TrendA、雷达追踪 | 2026-10-20 | 否决 | — |
| 交易伦 - AT | UCGTDHRerNo4eIyaliexDxtA | 见下方"2026-10-30/31 四博主"一节 | 2026-10-30 | 进行中 | youtube_4ch/ |
| 已泰 | UCLIFrck8EUhjRySvJAALa9Q | 67期中交易类约15期：EMA+威廉分型、双RSI、锚定VWAP、TD九转、Impulse MACD、RSI布林带、热力图+SMA200+AO、SuperTrend+HMA200、分型突破+放量（布林3σ/HMA+ROC/均线误区=已测同类）；0NhDZCA6urE、fc-LnP8mwiA 无字幕待转写 | 2026-10-30 | 否决（研究页 四博主②） | youtube_4ch/yitai_hist.py |
| 幣哥備份 | UCdAf7idaznVssBMHhjS3EvA | 同上（频道只有3期，全是双均线系统） | 2026-10-30 | 进行中 | youtube_4ch/ |
| mio来了 | UCnKF3a8uDDkLBc9V5hTTM6Q | 同上 | 2026-10-30 | 进行中 | youtube_4ch/ |
| 大黑美股（中低频实盘自动交易） | UCvZznN0wCMZaP2VdtLyxPTw | QQQ/TQQQ：EMA5/30、牛熊均线切换TQQQ、动态风险分配、MACD（日线+1h近3年；他主打30m无长历史） | 2026-10-30 | 否决（研究页 七博主①） | youtube_b2/qqq_tqqq.py |
| Trader小Q | UCtNPrRw5ovVLlOlODBJpNJw | 加密参数RSI9/MACD(8,21,5)/布林3σ、Twin Range+Supertrend+放量、Donchian+B-Xtrender | 2026-10-30 | Twin Range日线候选观察，其余否决（七博主②） | youtube_b2/tv_combos.py |
| Yuchi Trader | UCW1KHRAydlyBrX7nz7bz26g | Halftrend+RSI；Rob Hoffman=已测；其余(SSL/Supertrend/宙斯/5m剥头皮)同类 | 2026-10-30 | 否决（七博主②③） | youtube_b2/tv_combos.py |
| 交易聰明錢 | UCD9HUxu-a1Eug7Z61gDf6-g | Supertrend+KD；均线SOP(YsYFrWVwAq4 待转写)；其余裸K/SMC/FVG=已测同类 | 2026-10-30 | 否决（七博主②） | youtube_b2/tv_combos.py |
| 威廉 交易頻道 | UCZzqkJj6vv_0M2GBu3H-pOA | 吞没4分战法(吞没+均线+MACD+量)；AI 5m策略(2CUvhvcjo1w 待转写)；威科夫/现货网格=已测同类 | 2026-10-30 | 否决（七博主②） | youtube_b2/tv_combos.py |
| 蛋仔 CryptoEGG | UCKnA-uQNw6fErPxZEXdFMlg | SMC/ICT 入门(eWDVA9zKB-I)；频道多为1分K剥头皮/SNR/ICT | 2026-10-30 | 已测同类（七博主③） | — |
| 暗夜飞行 | UCUzrqf93vnhgqJ-CbsTJQBw | 合约网格多空双开(3KFtMhNeRY4)；频道多为网格/币安Alpha | 2026-10-30 | 已测同类（七博主③） | — |

## 常见"同类已测"速查（新视频大概率落在这些里）

- 均线顺势/金叉/多均线排列/顾比GMMA/均线密集：10-19 12种指标、10-18 美股均线网格、批次19 币哥双均线
- 5分钟/15分钟日内顺势回调、多周期确认：10-19 小周期多周期、10-20 ETH 5m 平台突破、批次19 已泰/交易伦
- SMC/ICT/订单块/流动性/海龟汤/OTE：ICT 一节 + 投机实验室多期
- 供需区：投机实验室 dOoDBhX9sx8、KgN97lPYFy8
- 订单流/CVD/吸收：Chris、Fabio
- 均值回归（RSI/布林/超跌反弹）：10-18、10-19、DeepSeek④
- 季节性/宏观/衍生品/周期顶/PEAD：新信息源 A~F

## 单个视频明细（按测试批次）

### 2026-10-30 宝贝发的4个视频（speculation_channel/batch19.py）
| 视频ID | 博主 | 规则 | 结论 |
|---|---|---|---|
| fLN20X1dP48 | 交易伦 | 1h 结构/偏向 + 5m EMA21/50/200 强动能K穿双均线 + RSI过50，目标下一个1h关键位 | 否决：5m/15m 含费 -0.2~-0.67R，零成本 +0.00~+0.07R |
| f-YB3Qdw_EU | 已泰 | EMA20/50/100 排列 + 回调过EMA20 + 威廉分型(2)，止损EMA50/100，1.5R | 否决：5m~4h 零成本 ≈0，含费 -0.1~-0.8R |
| a6kCJroORaI | 幣哥備份 | MA+EMA 20/60/120 均线密集突破 / 首次回踩20线 | 候选观察：4h 宽1.5~2.5×ATR 整片小正；6组平均接进方案F 5.26→5.51~5.71，单组不稳（研究页 四博主①） |
| IdZ-dyck27c | mio来了 | Fabio Valentini 订单流（转述） | 已测（Fabio），不重复 |

### 2026-10-30 已泰历史（youtube_4ch/yitai_hist.py，研究页 四博主②）
| 视频ID | 规则 | 结论 |
|---|---|---|
| 3Kooa9SlKz0 | 双RSI：日线RSI7>60/<40定势 + 1h RSI7 回到40/60 进场 | 否决（零成本-0.03R） |
| wuRQwu3fLa8 | 锚定VWAP（最近摆动点）回踩阳线 | 否决（2R为负；追踪版单独正，接进方案F 1.99） |
| abNcySkV9eI | TD九转 setup9 完美状态 + RSI | 否决（零成本就为负） |
| KZQMfBj6xFs | Impulse MACD 超买超卖交叉 / 横盘后突破 | 否决 |
| ARS7EP4o_LA | RSI布林带 + SMA250 | 否决 |
| pQLnlghXqbg | 热力图追踪(ATR) + SMA200 + AO | 否决（单独+0.17R，接进方案F 3.29） |
| 7foqLkodFps | SuperTrend(10,3) + HMA200 | 否决（单独+0.21R，接进方案F 0.5%时5.43、1%时4.84） |
| WCIP09ISi6Y | 分型结构突破 + MA200向上 + 放量 | 否决（接进方案F 4.57） |
| 9zXnhSqG91w / 4gVLowpz9eY / swldmJKPP_o | 布林3σ反转 / HMA+ROC / 均线三误区 | 已测同类，未单独测 |

### 2026-10-30 第二批7个视频（youtube_b2/，研究页 七博主①②③）
| 视频ID | 博主 | 规则 | 结论 |
|---|---|---|---|
| Sog09AOV9GE（+qSJXMU6grbg、hxrIirI--sQ、P2JYc8oPXfg、iLg-zGeAqrs） | 大黑美股 | QQQ EMA5/30 → TQQQ、牛熊切换、动态分配、MACD | 否决（接进方案F全降；他的最好版本比值1.22 < 我们美股腿2.27） |
| J3jBN_qzi1Q | Trader小Q | RSI9>70 + MACD(8,21,5)金叉 + 回踩3σ布林中/下轨 | 否决（4h +0.28R 但接进方案F 4.23） |
| （频道其余） | Trader小Q | Twin Range Filter + Supertrend + 放量 | **候选观察**：日线放量1.2~2.0整片正，5档平均接进方案F 5.52~5.60 |
| （频道其余） | Trader小Q | Donchian + B-Xtrender | 否决（4.48） |
| （频道其余） | Yuchi | Halftrend + RSI | 否决（2.70） |
| 2DfBuY1kaAw（标题） | 交易聰明錢 | Supertrend + KD | 否决（零成本为负） |
| Ow30lEj7uHA（标题） | 威廉 | 吞没4分战法 | 否决 |
| 2CUvhvcjo1w / YsYFrWVwAq4 | 威廉 / 交易聰明錢 | AI 5m 策略 / 均线SOP | 转写排队中 |
| eWDVA9zKB-I / 3KFtMhNeRY4 / IspHGootQ84 | 蛋仔 / 暗夜飞行 / Yuchi | SMC入门 / 网格多空双开 / Rob Hoffman | 已测同类 |

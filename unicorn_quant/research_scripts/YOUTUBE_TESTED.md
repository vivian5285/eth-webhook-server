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
| 交易伦 - AT | UCGTDHRerNo4eIyaliexDxtA | 无字幕，语音转写：5m EMA21/50/200强动能K(fLN20X1dP48)、破位清单RSI过滤(fTRv2HhTJUk)、伦敦破位(QZNzq-BunA4)；其余13期(谐波/供需/13种价格行为/ICI/动能交易/多时框等)待转写，ICT系列=已测同类 | 2026-10-30 | 已测3期全否决（四博主①③） | youtube_4ch/lun_hist.py |
| 已泰 | UCLIFrck8EUhjRySvJAALa9Q | 67期中交易类约15期：EMA+威廉分型、双RSI、锚定VWAP、TD九转、Impulse MACD、RSI布林带、热力图+SMA200+AO、SuperTrend+HMA200、分型突破+放量（布林3σ/HMA+ROC/均线误区=已测同类）；0NhDZCA6urE、fc-LnP8mwiA 无字幕待转写 | 2026-10-30 | 否决（研究页 四博主②） | youtube_4ch/yitai_hist.py |
| 幣哥備份 | UCdAf7idaznVssBMHhjS3EvA | 同上（频道只有3期，全是双均线系统） | 2026-10-30 | 进行中 | youtube_4ch/ |
| mio来了 | UCnKF3a8uDDkLBc9V5hTTM6Q | 布林带最完整教学(4QE8YfsrWzA，11-01 转写)：带宽6个月最低=擠壓→收盘收在带外进场→收回中轨离场；往期 K线4型态/均线/RSI/MACD/斐波那契/J Law META/PTJ 200日线；i-1q0TOuI9s RSI 50%法=会员专属打不开 | 2026-11-01 | 布林擠壓 8h **候选，11-01 已上擂台纸面(mio_bb_squeeze_8h)** | youtube_b4/b4d.py |
| 大黑美股（中低频实盘自动交易） | UCvZznN0wCMZaP2VdtLyxPTw | QQQ/TQQQ：EMA5/30、牛熊均线切换TQQQ、动态风险分配、MACD（日线+1h近3年；他主打30m无长历史） | 2026-10-30 | 否决（研究页 七博主①） | youtube_b2/qqq_tqqq.py |
| Trader小Q | UCtNPrRw5ovVLlOlODBJpNJw | 加密参数RSI9/MACD(8,21,5)/布林3σ、Twin Range+Supertrend+放量、Donchian+B-Xtrender | 2026-10-30 | Twin Range日线候选观察，其余否决（七博主②） | youtube_b2/tv_combos.py |
| Yuchi Trader | UCW1KHRAydlyBrX7nz7bz26g | Halftrend+RSI；Rob Hoffman=已测；3gp_2F2vN48 EMA50/200+VuManChu Swing Free(Range Filter)(11-01)；其余(SSL/Supertrend/宙斯/5m剥头皮)同类 | 2026-11-01 | 否决（七博主②③、四批②） | youtube_b2/tv_combos.py, youtube_b4/b4.py |
| 交易聰明錢 | UCD9HUxu-a1Eug7Z61gDf6-g | Supertrend+KD；均线SOP(YsYFrWVwAq4，11-01 转写：EMA16/64+Impulse MACD 同向，回踩/金叉进场，三条件共振离场)；其余裸K/SMC/FVG=已测同类 | 2026-11-01 | 否决（七博主②、四批③） | youtube_b2/tv_combos.py, youtube_b4/b4d.py |
| 威廉 交易頻道 | UCZzqkJj6vv_0M2GBu3H-pOA | 吞没4分战法(吞没+均线+MACD+量)；AI 5m策略(2CUvhvcjo1w 11-01否决)；威科夫/现货网格=已测同类 | 2026-10-30 | 否决（七博主②） | youtube_b2/tv_combos.py |
| 蛋仔 CryptoEGG | UCKnA-uQNw6fErPxZEXdFMlg | SMC/ICT 入门(eWDVA9zKB-I)；频道多为1分K剥头皮/SNR/ICT | 2026-10-30 | 已测同类（七博主③） | — |
| 暗夜飞行 | UCUzrqf93vnhgqJ-CbsTJQBw | 合约网格多空双开(3KFtMhNeRY4)；频道多为网格/币安Alpha | 2026-10-30 | 已测同类（七博主③） | — |

| 卫央Neo - 币圈短线交易 | UCXy7cYTLsZtux-i5AVqtiGw | 8oXyRhBKpnM 风林火山 + 趋势七步系列(j7lIZgOhBN8 区间突破 / ub_W-ofEhVg 横盘回调 / 4GHcmoLTVu4 收敛突破二波 / oBsuSubiweo 出场 等)：区间突破、横盘回调、区间顶部挤压、二波，15m~1d | 2026-11-01 | 区间突破 4h/6h 3ATR追踪 **候选，11-01 已上擂台纸面(neo_box_4h/6h)**；其余否决（研究页 十博主②） | youtube_b3/b3.py, b3_nb.py |
| 币需暴富 | UCJQgr-XyEELFhdY1Dmi7uSQ | Vegas 改进版 EMA144/169+288/338+EMA12过滤、金叉后首次回踩(auAnQXtiNNo)；HY-iU803qec 同主题无字幕 | 2026-11-01 | 否决（4h 单独+0.7R 但接进方案F 2.8~3.8；大众版576/676 见投机实验室批次1） | youtube_b3/b3.py |
| Lone Wolf | UCU17HnJXYCa_yFM7Py-7RHA | Phenom 远离均线+反转点 1.5R(_rNN1Xscm6c)、相对趋势指数RTI+Market Bias(u9_G-I-ZIyM)；其余多为行情分析 | 2026-11-01 | 否决 | youtube_b3/b3.py |
| 番人说 | UCLTfYhkXTnzRBWduVt_djZg | 纽约首根4H区间 5m 假突破回收 2R(u-A6BTmVXpk，无字幕已转写)；频道其余(平均K线/斐波那契/布林突破/均线交叉/MACD/SMC/FVG/订单块/123法则/2B)=已测同类 | 2026-11-01 | 否决（零成本+0.01~0.06R） | youtube_b3/b3_extra.py |
| 威廉 交易頻道(续) | UCZzqkJj6vv_0M2GBu3H-pOA | 2CUvhvcjo1w AI 5m RSI70/30 反向 1%/1% + z2YNY3QBuXE 4招(放量/长影线/持续超买超卖不做/1h阶段高低点扫流动性) | 2026-11-01 | 否决（任何过滤组合零成本都为负） | youtube_b3/b3_extra.py |
| BitGuy小比 | UCbBZuXDPkPYn4t0FhCGnrAw | EU5TqXN2R4E Claude+Hermes 自我优化交易代理：没有具体规则，是"自动复盘改参数"的流程；频道其余=工具教程(TV/ATAS/IBKR/3Commas) | 2026-11-01 | 无可测规则（自动改参数=过拟合风险，见前视检验/选择偏差条目） | — |
| 交易研究院 | UCktqp7siztf5IWcMiVA9KbA | CGZ8GYwqkms T3+Trendilo+Stiffness；往期 VIDYA/JMA/珊瑚/线性回归斜率/估波曲线/ALMA/RMI/平均力量/袋鼠尾/机构级突破(pivot180)/EMA三层+K线评分，各 30m~1d；其余(超级趋势/MACD/TTM/一目/斐波那契/网格/DCA/Holt+ADX/KusKus)=同类模板 | 2026-11-01 | Stiffness 6h/8h 追踪 **候选，11-01 已上擂台纸面(stiff_trendilo_6h/8h)**；线性回归斜率 4h 单组5.88但邻域平均4.74否决；其余否决（研究页 十博主③） | youtube_b3/b3_yj.py, b3_nb.py |
| JiaSheng 嘉成 TradingLife | UCBEVhAB8QF9IhUr5g-bGZxQ | 173期(多为黄金/BTC盘面+vlog)：ITgGLKWSXeY 斐波那契0.618~0.705、nBooQWVjTw0 TSS日线EMA12/24(自称5年32倍)、OLm9w1vbLmU MACD+EMA200(30m)、nowxuPXru3Q 真假突破(大K/三连K+分批+吊灯止损)、6KCk4-9FyJA MACD背离×SMC扫低点；0Vr4xJoESXM / 3WCAsU19S2U 胜率70%/80%策略=会员专属打不开；其余SMC/SNR/资金管理=同类 | 2026-11-01 | 真假突破 4h **候选，11-01 已上擂台纸面(jiasheng_brk_tp_4h / jiasheng_brk_trail_4h)**；其余否决（研究页 四批①） | youtube_b4/b4.py, b4c.py, tss.py, b4c_nb.py |
| 诗魂 Shi Hun | UCsb7kzrjIWCFR0FCkk7l7rg | 279期，自制指标系列：tMZky1ZpFBY RJ指标(J线×RSI)、B-38PKINIFA MJ指标(MACD 0轴+J>50)、P02AUNYdKA8 2560战法、-fZZHMpfuHQ CIS双均线(5/25，前两段)；未测：双平滑KD/OBV起涨/神奇均线(自适应=KAMA/VIDYA已测同类)/TD9/Qullamaggie/BNF/Bit浪浪=已测 | 2026-11-01 | MJ 8h 只多 观察(2025后拉低方案F)；其余否决（研究页 四批②） | youtube_b4/b4.py, b4b.py, b4_nb.py |
| Louis Trading | UCJJ2W0jG0SQPV4_z0wMjL8Q | 323期多为外汇/黄金：G5bFcWoHCR8 结构突破+斐波那契0.5回调+流动性(SMC)、kE6TepxrrzE M15+M1 超短线；其余 H4+M15 SMC 机械化系列=同类 | 2026-11-01 | 已测同类(SMC/斐波那契/1m超短线) | — |
| Mike Yu 聰明錢 | UCTRgIiw2c7qtBrqiG1Wq8nQ | 55期 全是 SMC/ICT/TPO 订单流：s0VD85XpAKs SMC日内(偏见+多周期) | 2026-11-01 | 已测同类(ICT/SMC/订单流) | — |
| 论文 Zarattini/Pagani/Barbon 2025「Catching Crypto Trends」 | SSRN/RePEc rp2580 | 唐奇安 9 周期(5~360天)集成+中点追踪止损+25%波动率目标，只做多(网上找的新来源，不是油管) | 2026-11-01 | 否决（单独2025后≈0，接进/替换方案F都降） | youtube_b4/donchian_ens.py |
| 指標與策略 | UCE-pQE8a8O2-99ZOFL4rqKw | 12期全测：ifWUYXnWJtY 布林进肯特纳擠壓+KDJ(25)逆势、8FwaLBJ8Uss 双肯特纳(50,2.75/3.75)、tqUxGBKRQVk HMA+EMA200+KDJ、PFYA-WSwWh8 CCI(18/54)±200对翻、0SRBFMnMkR4 吊灯止损+RSI、BaZ3I9Za86w MACD+EMA200+SAR；三兵/SuperTrend+QQE/5m 动量/剥头皮/高斯通道=已测同类 | 2026-11-01 | 擠壓+KDJ逆势 4h/6h **候选**(180组平均让新基准7.75→9.59，但2026年为负)；其余否决（研究页 五批①） | youtube_b5/b6.py, b7.py, b6_nb.py |
| LEI | UCZyTcQHJGKkGeotf0vWA7Rg | MTmz6OLCykc 均线高度密集(6条线差<2%、≥6个月)后张嘴突破、Pe04VShLE1g/K2OiWFYyP7Y EMA20+MA20双线系统；其余投资理念/ETF/Livermore 无规则 | 2026-11-01 | 否决（币圈日线均线很少<2%密集，放宽到4%只有个位数笔；双线系统接新基准全降） | youtube_b5/b6.py, b8.py |
| 量化熊猫实验室 | UCdGAmAPo1oEEFR_ByawrkGw | cxG3lOrGqkM 结构突破EA(DMI交叉+ZigZag摆动点挂突破单)；其余4期黄金海龟/唐奇安/波段突破EA拆解=已测同类 | 2026-11-01 | 否决（初测+0.36R是突破单成交价前视，修正后+0.03~0.10R，接新基准全降） | youtube_b5/b8.py |
| 大數據Trader | UCuHgcMy4HG5XAOtI0sFyHPQ | jr6_zKLCmLM RSI(10)<30+SMA200(转述Rayner)、rOFL8IJW5P8 同规则做空；SSL/MACD背离/VWAP/DEMA/网格/QFL=已测同类 | 2026-11-01 | 否决（胜率65%但含费为负） | youtube_b5/b6.py |
| Bitptp | UC_wobMwj_hl0aqNAvH0634A | KvUkDo12O1M EMA金叉死叉25万笔≈0→LWMA/SMMA+高周期EMA+ADX>30、fFNOp3EZ3wM 黄金5m高频EA(无具体规则) | 2026-11-01 | 否决（LWMA 4h 单独+0.33R但4组只1组让新基准变好） | youtube_b5/b8.py |
| Master Technical Analysis(MTA) / 方方土PriceAction / 已轉移至新頻道 | UCUeSB9j3tcN2ZztRnfjGwSg / UC-CckeotGQCZigg3nETreCQ / UCoGIQCN1Slae8C8tbdEsJNw | IGn4AGzzbRo 支撑阻力+吞没、1fHd9Ycbc5Y 美股早盘价格行为、-VZGKdDDtuM 1分钟 TTM Squeeze Pro+Andean+ADX | 2026-11-01 | 已测同类(价格行为/开盘区间/1m超短线) | — |

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

### 2026-11-01 第五批（youtube_b5/，研究页 五批①②；对比基准=方案F+叠加2% 比值7.75）
| 视频ID | 博主 | 结论 |
|---|---|---|
| J3jBN_qzi1Q / _rNN1Xscm6c / 7foqLkodFps | Trader小Q / Lone Wolf / 已泰 | 已测（第二批/第三批/四博主②），不重复 |
| ifWUYXnWJtY | 指標與策略 | **候选**：擠壓+KDJ逆势 4h/6h 不要擠壓过滤更稳；180组平均 +1% 新基准 7.75→9.59(6.97/18.83)，回撤20.0→17.8%，相关方案F -0.10；但2026年这一腿为负 |
| cxG3lOrGqkM / MTmz6OLCykc / jr6_zKLCmLM / fFNOp3EZ3wM | 量化熊猫 / LEI / 大數據Trader / Bitptp | 否决 |
| IGn4AGzzbRo / -VZGKdDDtuM / 1fHd9Ycbc5Y | MTA / 已轉移 / 方方土 | 已测同类 |

### 2026-11-01 第四批（youtube_b4/，研究页 四批①②③④）
| 视频ID | 博主 | 规则 | 结论 |
|---|---|---|---|
| ITgGLKWSXeY | JiaSheng | 斐波那契 0.618~0.705 回调挂单，止损1.0，目标前高/1.618 | 否决（120组仅11组含费为正，都<0.1R） |
| nBooQWVjTw0 | JiaSheng | TSS 日线EMA12/24 整根K脱离两线开仓、大阳8%/大阴5%直接成立、不设止损、减半牛熊过滤 | 否决（BTC 2020~25 我们测 2.5~14倍、回撤58~67%，不是他说的32倍/26%；比方案F币圈腿差很多） |
| OLm9w1vbLmU | JiaSheng | MACD 交叉+EMA200 顺势+"能量小"过滤，前低止损 1.5R | 否决（30m~8h 都在 ±0.06R） |
| nowxuPXru3Q | JiaSheng | 箱体突破那根大K/三连K=真突破，止损箱内，一半1.5R+一半吊灯止损 | **候选**：4h 18组16组>方案F，平均+1% 6.11(4.90/11.97)，已上擂台纸面 |
| 6KCk4-9FyJA | JiaSheng | MACD 底背离 + 扫前低收回(SMC) | 否决（1h~4h 为负） |
| 3gp_2F2vN48 | Yuchi | EMA50/200 + VuManChu Swing Free(Range Filter) | 否决（邻域接进方案F 3.3~4.7） |
| tMZky1ZpFBY / B-38PKINIFA / P02AUNYdKA8 / -fZZHMpfuHQ | 诗魂 | RJ / MJ / 2560 / CIS | MJ 8h 只多 14/18组>方案F 但后半 10.02→8.71，观察；其余否决 |
| 4QE8YfsrWzA | mio来了 | 布林擠壓(带宽6个月最低)→收盘带外进场→收回中轨离场 | **候选**：8h 24组20组>方案F，平均+1% 5.99(4.03/15.31)，收益集中2024/2026，已上擂台纸面 |
| YsYFrWVwAq4 | 交易聰明錢 | EMA16/64 + Impulse MACD 同向 | 否决（邻域 3.7~5.1） |
| G5bFcWoHCR8 / kE6TepxrrzE / s0VD85XpAKs | Louis / Louis / Mike Yu | SMC结构+斐波那契 / M15+M1 / SMC日内 | 已测同类 |
| CGZ8GYwqkms / Sog09AOV9GE | 交易研究院 / 大黑美股 | — | 已测（第三批 / 第二批） |

### 2026-11-01 第三批10个视频（youtube_b3/，研究页 十博主①②③④）
| 视频ID | 博主 | 规则 | 结论 |
|---|---|---|---|
| 8oXyRhBKpnM | 卫央Neo | 风林火山周期 + 趋势七步①区间突破/②横盘回调 | 区间突破 4h/6h 追踪候选；横盘回调/顶部挤压/二波否决 |
| 2CUvhvcjo1w / z2YNY3QBuXE | 威廉 | 5m RSI 70/30 反向 1%/1% + 4招 | 否决 |
| _rNN1Xscm6c | Lone Wolf | Phenom 远离均线 + 反转点 | 否决（5m~1h 含费 -0.16~-0.45R） |
| J3jBN_qzi1Q | Trader小Q | 加密参数 | 已测（七博主②） |
| u-A6BTmVXpk | 番人说 | 纽约首根4H区间假突破 | 否决 |
| f-YB3Qdw_EU | 已泰 | EMA+威廉分型 | 已测（批次19） |
| auAnQXtiNNo | 币需暴富 | Vegas 288/338+EMA12 | 否决 |
| EU5TqXN2R4E | BitGuy小比 | AI 自我优化代理 | 无可测规则 |
| CGZ8GYwqkms | 交易研究院 | T3+Trendilo+Stiffness | 15m 否决；6h/8h 追踪候选 |

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
| 2CUvhvcjo1w / YsYFrWVwAq4 | 威廉 / 交易聰明錢 | AI 5m 策略 / 均线SOP | 2CUvhvcjo1w 11-01 已测否决(第三批)；YsYFrWVwAq4 仍待转写 |
| eWDVA9zKB-I / 3KFtMhNeRY4 / IspHGootQ84 | 蛋仔 / 暗夜飞行 / Yuchi | SMC入门 / 网格多空双开 / Rob Hoffman | 已测同类 |

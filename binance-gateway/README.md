# 广播网关（Binance Gateway）

TradingView 订阅上限 20 条警报。每个品种若要覆盖 B/C/D 三个币安账户，直连方式需要 3 条独立警报
（`/binance-b/webhook`、`/binance-c/webhook`、`/binance-d/webhook` 各一条），品种一多就超限。

这个网关让部分品种在 TV 只需配 **1 条**警报，指向 `http://<VPS_IP>/binance-all/webhook`；网关收到后
并发原样转发给三个账户各自的 `/webhook`，账户自己的 secret 校验 / 解析 / 去重 / 风控逻辑完全不变。

## 设计

- 纯转发哑管道：不做任何交易业务判断，不持有任何 API 密钥，不 import 任何账户代码
- 无第三方依赖，纯 Python 标准库（`http.server` + `urllib`），不需要 venv
- 三账户**并发**转发（各自独立线程），单账户超时 8s；一个慢/挂（比如正在 deploy 重启）不阻塞另外两个
- 收到请求后原样透传 body + headers（跳过 `Host`/`Content-Length`/`Connection`，由 urllib 重新计算）
- 返回：至少一个账户成功即 200；三个全失败才 502。每次转发的逐账户明细（状态码/耗时/错误）都记日志
- HTTP server 设置 `timeout=15`（socket级读超时），防止慢速/不完整请求把处理线程挂住

## 单点加固（2026-08-16，用户把全部TV警报切到本网关后单点风险升高）

网关现在是所有TV信号的唯一入口，挂了 = 全部品种同时失联，比单个账户异常严重得多，因此单独加固：

1. **转发失败即时告警**：部分/全部账户转发失败时立即推钉钉（复用watchdog同一个群，`WATCHDOG_DINGTALK_WEBHOOK`/`_SECRET`，纯stdlib HMAC签名实现不依赖requests），60秒内同类问题不重复刷屏。原来只写日志，容易错过。
2. **systemd重启策略加固**：`Restart=always`（不只是on-failure，任何退出方式都拉起）、`RestartSec=1`（无状态纯转发，重启即用）、`StartLimitIntervalSec=60`+`StartLimitBurst=30`兜底崩溃循环。
3. **独立快速探活**（`gateway-heartbeat.timer`，每60秒）：不依赖主监督狗10分钟一轮的周期。健康就安静退出；探活失败先尝试`systemctl restart`自愈，2秒后复查；复查恢复了推一条"短暂失联已自动恢复"提醒（非紧急，但要留痕）；复查仍失败则推🚨紧急告警。这一层专门补`Restart=`系统本身补不到的坑——systemd只能抓进程崩溃，抓不住"进程还活着但卡死不响应"。
4. 已实测验证：手动停服模拟故障 → heartbeat在60秒内探活失败 → 自动`systemctl restart` → 2秒后复查恢复 → 全程9秒左右完成自愈，验证通过。

## 路由

- 部署位置：VPS `/root/binance-gateway/`，独立于 `binanceB/C/D` 三套账户目录
- 监听：`127.0.0.1:5006`（只本机可达，靠 nginx 对外暴露）
- nginx：`/etc/nginx/conf.d/binance-webhook.conf` 里的 `/binance-all/webhook` 和 `/binance-all/health`
  location 反代到本服务
- systemd：`binance-gateway.service`（见同目录 `binance-gateway.service`），`Type=simple` + `Restart=on-failure`，
  无状态、无凭证，不需要像 B/C/D 那样在重启前轮询 `deploy_safe`

## 当前转发目标

2026-08-15：D账户暂停使用（未接TV、未放资金），`BACKENDS`里D那一行先注释掉，
网关目前只转发给B（自己账户）和C（妈妈账户）。D重新启用时取消注释即可，
不用改其它逻辑。

## 与直连方式共存

现有 `/binance-b(-c/-d)/webhook` 三条独立路由**保持不变**。两种方式按品种混用——哪些品种走直连、
哪些品种走网关，由 TV 那端各个品种警报配的 URL 决定，网关对 payload 内容完全无感知，谁发来就转发
给谁，不做任何品种级别的区分或过滤。

## 监控

`watchdog.service` 每轮（10分钟）探活 `http://127.0.0.1:5006/health`（见 `watchdog/check.py` 的
`check_gateway()`）。网关若挂了不会体现在三个账户各自的 `/health` 里——走网关的品种会静默漏单，
比普通账户异常更隐蔽，所以单独探测、单独告警。

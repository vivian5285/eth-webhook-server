# 独立监控项目（Watchdog）

跟三个币安账户（B/C/D）的交易主程序完全独立、只读——目的是持续核对"TV信号 ↔ 实盘执行"是否一致，
发现真异常发独立钉钉群机器人（跟主程序已停用的钉钉通知`dingtalk.py`/`DINGTALK_DISABLE=True`完全
无关，是单独的`dingtalk_notify.py` + `WATCHDOG_DINGTALK_WEBHOOK`）。

## 设计

- 部署位置：VPS `/root/watchdog/`，跟 `binanceB/C/D` 三套主程序完全分开的目录
- 运行方式：systemd timer `watchdog.timer`，每 10 分钟跑一次 `check.py`
- 只读：持仓/挂单走账户级批量 REST（`futures_position_information` 全量持仓 +
  `futures_get_open_orders` 全量挂单 + `openAlgoOrders` 全量条件单，均不带 symbol
  参数，本地按 symbol 分组），TV信号走 `journalctl`，不导入 `position_supervisor_*`，
  不下任何单、不撤任何单（跟一直坚持的"只做client-layer/read-only calls against
  running VPS accounts"原则一致）
- 2026-08-15：品种数涨到10个后，原来的写法是每个品种循环发 REST（持仓+挂单最多2次/品种），
  实测10品种规模下耗时42.3s，超过 subprocess 40s 超时，B/C/D 三账户短暂出现"持仓查询
  失败"误报（真实交易未受影响，只是watchdog自检链路本身超时，钉钉确实推送了3条真实告警）。
  已改为账户级批量REST（不管品种数多少都固定3次REST），实测<1s，品种数再涨不会再变慢。

## 检查项（每轮）

1. 三个端口 `/health` 是否正常、`deploy_safe`/`trading_paused` 有没有意外
2. 每个账户每个活跃品种：TV最近一条webhook信号 vs 实盘持仓方向/数量是否对得上
3. 幽灵单：仓位空但挂单还在
4. 裸奔仓位：持仓存在但零STOP类型挂单（**最高优先级**，5分钟去重）
5. 雷达激活卡死：持仓存在、雷达未激活，但实时REST markPrice已经越过本地状态文件记录的
   激活线（跟引擎自身 `_sentinel_loop` 里那道120秒强制REST交叉核对是同一类问题的独立
   backstop，10分钟去重，第二高优先级）
6. 真实 ERROR 日志（已排除"良性收尾杂音"：Client session、NoneType sock、穿价TP1推离
   市价、止损单-4509等，跟dashboard共用同一套`self_healed`相关性过滤逻辑——ERROR行
   90秒窗口内若能匹配到对应的"确认空仓/平仓"确认行，判定为自愈噪音，不告警）

## 通知规则

- **真异常** → 立刻发钉钉（不同异常类型各自的去重窗口：裸奔仓位5分钟、雷达卡死10分钟，
  其余默认30分钟，避免同一个问题反复刷屏）
- **心跳** → 每天 08:00 / 20:00（UTC）各发一条汇总（不管有没有异常都发，证明监控本身
  还活着，没有心跳本身就是一种异常信号）

## 当前状态

- [x] 钉钉群机器人 webhook + 加签密钥已写入 `.env`（`WATCHDOG_DINGTALK_WEBHOOK`/`_SECRET`）
- [x] 已部署到 VPS，`watchdog.timer` 常驻，`enabled`+`active`
- [x] 实盘跑了多天，误报来源（closing chatter日志噪音、TV信号查询未合并、持仓查询未
  批量化）陆续发现并修复；当前12个活跃品种下每轮稳定在3秒内完成

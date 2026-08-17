# 三账户统一监控面板（Binance Dashboard）

只读汇总 B/C/D 三个实盘账户的持仓/事件/异常，唯一的写操作是"市价平仓"；
2026-08-17 新增 TV 信号日志聚合 + 一键重放/编辑后重放 + 手动发单——这三个
写操作不重新实现任何交易逻辑，而是本机 HTTP 回环去调用各账户自己
`binance-engine` 已有、已验证的 Console API（`/api/console/tv_signals` 等），
读各账户自己 `.env` 里的 `CONSOLE_PASSWORD` 登录换 session cookie 后转发一次
调用，不导入 `position_supervisor_binance`，不绕过原账户自己的鉴权/校验/
去重/风控逻辑。

## 部署

- VPS 路径：`/root/binance-dashboard/`（`server.py` + `index.html`），运行身份 root
- systemd：`binance-dashboard.service`，监听 `127.0.0.1:8877`
- nginx 反代到 `http://<VPS_IP>/dashboard/`
- 更新后需要 `systemctl restart binance-dashboard`

此前这份代码只存在于 VPS 本地和某次会话的临时 scratchpad 目录里，没有版本控制，
2026-08-17 起正式纳入本仓库，跟 `binance-gateway/` 走同样的"不在主 git 部署流程
里，但源码要有备份"约定——更新时手动 scp 到 VPS 对应路径 + 重启服务，不走
`git reset --hard` 那一套（三账户 B/C/D 才那样做）。

## 依赖

纯 stdlib（`http.cookiejar`/`urllib`），VPS 上的 venv 没装 `requests`，新代码
沿用这个约定没有引入新依赖。

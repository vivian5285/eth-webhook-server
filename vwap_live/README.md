# vwap_live

`vwap_mean_reversion`(擂台现排名第一)的真账户测试版。独立项目，部署在
`root@187.77.130.144`（真实账户所在的 VPS），不碰 B/C/D/E 现有的雷达引擎
代码——像 `polymarket_quant` 一样单独一个 systemd 服务。

## 两道闸门（缺一都不会下真实单）

1. `BINANCE_API_KEY` / `BINANCE_API_SECRET` 非空
2. `LIVE_TRADING=true`

两道都没打开时是**观察模式**：正常拉行情、判信号、写本地账本、发通知，
就是不碰交易所。武装之后每一步都完全一样，只是 `executor.py` 真的去
下单/挂止损/平仓。

## 强烈建议

- **单独开一个新的币安子账户/API key**，只给合约交易权限、**不给提现
  权限**，跟 B/C/D/E 现有账户完全隔离。把 key 直接写进 VPS 上的 `.env`，
  不要贴聊天里。
- 杠杆硬顶 3x（`config.py` 代码层面限制，测试阶段下调，不是靠记得别改）。
- 每笔仓位自动设成**逐仓(ISOLATED)**保证金——跟账户上可能同时存在的
  其它仓位(比如同账户自己的雷达引擎)完全隔离保证金池，不会互相传导。
- `DAILY_LOSS_LIMIT_USD` 单日实现亏损触及就自动熔断，当天不再开新仓。
- 只跑 `.env.example` 里预设的品种（擂台上持续跑赢的那一批），不是全品种。

## 部署

```
scp -r vwap_live root@187.77.130.144:/root/
ssh root@187.77.130.144
cd /root/vwap_live
cp .env.example .env   # 保持 LIVE_TRADING=false，先观察
python3 selfcheck.py   # 应打印 SELFCHECK OK
cp deploy/vwap-live.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now vwap-live.service
journalctl -u vwap-live -f
```

## 上线开关

观察几天、日志/Telegram 通知看着靠谱之后：
1. 去币安开新的子账户 + API key（只勾合约交易，不勾提现）
2. 把 key 写进 `/root/vwap_live/.env` 的 `BINANCE_API_KEY`/`BINANCE_API_SECRET`
3. 把 `LIVE_TRADING` 改成 `true`
4. `systemctl restart vwap-live`

想随时退回观察模式：把 `LIVE_TRADING` 改回 `false`（或干脆清空 key）
再重启，已开的真实仓位不会被自动追加，但也不会被自动平掉——退出真实
模式前先确认交易所上没有未平仓位。

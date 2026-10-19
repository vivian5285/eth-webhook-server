"""只读体检：客户 E 账户(MARIO)。只调用查询接口，不下单、不改设置、不打印密钥。"""
import os, json
env = {}
for line in open("/home/binanceE/binance-engine/.env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")
from binance.client import Client
c = Client(env.get("BINANCE_API_KEY"), env.get("BINANCE_API_SECRET") or env.get("BINANCE_SECRET_KEY") or env.get("BINANCE_API_SECRET_KEY"))
acc = c.futures_account()
print("合约钱包余额 totalWalletBalance:", acc.get("totalWalletBalance"), "| 保证金余额 totalMarginBalance:", acc.get("totalMarginBalance"),
      "| 可用 availableBalance:", acc.get("availableBalance"), "| 未实现盈亏:", acc.get("totalUnrealizedProfit"))
print("各资产：", [(a["asset"], a["walletBalance"]) for a in acc.get("assets", []) if float(a.get("walletBalance") or 0) != 0])
print("持仓模式(dualSidePosition=True 是双向/对冲模式):", c.futures_get_position_mode())
try:
    print("联合保证金模式:", c.futures_get_multi_assets_mode())
except Exception as e:
    print("联合保证金模式 查询失败:", type(e).__name__)
pos = [p for p in c.futures_position_information() if float(p.get("positionAmt") or 0) != 0]
print("持仓数:", len(pos), [(p["symbol"], p.get("positionSide"), p["positionAmt"], p.get("marginType")) for p in pos])
oo = c.futures_get_open_orders()
print("普通挂单数:", len(oo), [(o["symbol"], o["type"], o["side"]) for o in oo][:10])
try:
    algo = c._request_futures_api("get", "openAlgoOrders", True, data={})
    print("条件单(algo)数:", len(algo.get("orders", algo) if isinstance(algo, dict) else algo))
except Exception as e:
    print("条件单查询:", type(e).__name__)
lv = {}
for b in c.futures_leverage_bracket()[:0]:
    pass
print("账户可交易:", acc.get("canTrade"))

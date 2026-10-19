"""用币安"测试下单"接口(POST /fapi/v1/order/test，只校验不成交)验证执行器的下单代码：数量精度、价格精度、GTX、reduceOnly、市价单。
把执行器 Client 的 futures_create_order 换成 futures_create_test_order，撤单/查单换成空操作；不创建 ARMED 文件，不会有任何真实订单。"""
import sys, time, json
sys.path.insert(0, "/home/binanceE/planf_live")
import planf_executor as P
ex = P.Exec(); ex.load_filters()
calls = []
real_test = ex.c.futures_create_test_order
def fake_create(**kw):
    r = real_test(**kw); calls.append((kw, "OK" if r == {} or isinstance(r, dict) else r)); return {"avgPrice": "test", "status": "TEST"}
ex.c.futures_create_order = fake_create
ex.c.futures_cancel_order = lambda **kw: calls.append((kw, "cancel(跳过)"))
ex.st = {"working": {}, "eq_hist": [], "cb_until": 0}
cases = [("ETHUSDT", 0.021, 0.0, 0.021, "开仓"), ("SOLUSDT", -0.37, 0.0, -0.37, "开仓"), ("XRPUSDT", 12.3, 0.0, 12.3, "开仓"),
         ("BNBUSDT", -0.03, 0.06, 0.03, "减仓"), ("NVDAUSDT", 0.07, 0.0, 0.07, "开仓"), ("MUUSDT", -0.05, 0.05, 0.0, "平到0")]
for sym, delta, cur, tq, why in cases:
    f = ex.filters[sym]
    print(sym, "限价:", ex.execute(sym, delta, cur, tq, f, why, []))
    ex.st["working"][sym]["start"] -= 16 * 60          # 假装挂了16分钟没成交 → 走市价补
    print(sym, "市价:", ex.execute(sym, delta, cur, tq, f, why, []))
print()
for kw, r in calls:
    print({k: v for k, v in kw.items() if k != "newClientOrderId"}, "→", r)

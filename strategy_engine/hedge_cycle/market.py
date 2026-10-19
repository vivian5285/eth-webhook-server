"""Gate公开行情与实际结算。只有GET公开路径，无API key、账户、下单依赖。"""
import json,math,time,urllib.parse,urllib.request
from strategy_engine import klines
from .config import SYMBOLS

BASE='https://api.gateio.ws/api/v4/futures/usdt'
_funding_mark_cache={}
_meta_cache={}


def get_json(path,params=None):
    url=BASE+path+('?' + urllib.parse.urlencode(params) if params else '')
    req=urllib.request.Request(url,headers={'User-Agent':'hedge-cycle-public-paper-only'})
    with urllib.request.urlopen(req,timeout=12) as resp:return json.load(resp)


def contract(symbol):
    if symbol not in SYMBOLS:raise ValueError('Symbol outside hedge universe')
    if symbol in klines.BINANCE_SOURCE_SYMBOLS:raise ValueError('Gate-only portfolio')
    return klines._symbol_to_gate_contract(symbol)


def metadata(symbol):
    hit=_meta_cache.get(symbol)
    if hit and time.time()-hit[0]<3600:return hit[1]
    d=get_json('/contracts/'+contract(symbol))
    if float(d['leverage_max'])<10 or d.get('in_delisting'):raise ValueError('Contract unsuitable for 10x margin model')
    _meta_cache[symbol]=(time.time(),d)
    return d


def marks():
    raw=get_json('/tickers');wanted={contract(s):s for s in SYMBOLS}
    result={wanted[r['contract']]:float(r['mark_price']) for r in raw if r.get('contract') in wanted}
    if set(result)!=set(SYMBOLS) or any(not math.isfinite(v) or v<=0 for v in result.values()):
        raise ValueError('Missing/invalid Gate marks')
    return result


def funding_mark(symbol,t_sec):
    key=(symbol,t_sec)
    if key in _funding_mark_cache:return _funding_mark_cache[key]
    # 结算时刻之前刚闭合的mark_1m收盘价，公开历史近似，不拿现在价给过去计费。
    # 部分历史funding事件延迟几秒（例如整点+3秒），不能当作K线开盘时间。
    candle_open=t_sec//60*60-60
    rows=get_json('/candlesticks',{'contract':'mark_'+contract(symbol),'interval':'1m',
                                 'from':candle_open,'to':candle_open+59})
    good=[r for r in rows if int(r['t'])==candle_open]
    if not good:raise ValueError('Funding settlement mark unavailable: '+symbol)
    value=float(good[-1]['c'])
    if not math.isfinite(value) or value<=0:raise ValueError('Invalid settlement mark')
    _funding_mark_cache[key]=value
    return value


def funding_since(symbol,start_ms,now_ms):
    if start_ms>=now_ms:return []
    start=int(start_ms/1000);end=int(now_ms/1000);result=[]
    for _ in range(20):
        rows=get_json('/funding_rate',{'contract':contract(symbol),'from':start,'to':end,'limit':1000})
        if not isinstance(rows,list):raise ValueError('Invalid funding history')
        for r in rows:
            t=int(r['t']);rate=float(r['r'])
            if start_ms<t*1000<=now_ms:
                result.append({'time_ms':t*1000,'rate':rate,'mark':funding_mark(symbol,t)})
        if len(rows)<1000:break
        earliest=min(int(r['t']) for r in rows)
        if earliest<=start:break
        end=earliest-1
    else:raise ValueError('Funding pagination incomplete')
    return sorted({r['time_ms']:r for r in result}.values(),key=lambda r:r['time_ms'])

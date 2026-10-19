"""独立双腿纸面常驻进程，60秒检查mark止损，4h收盘才产生入场/循环。

两组账户一笔SQLite事务，flock防止重复进程。已完成bar/cycle/结算cursor全持久化。
任何Gate数据不完整都不提交本轮、不用币安/旧价格补齐，日志和网页stale提示故障。
"""
import logging,time
from strategy_engine import klines
from . import market,store
from .config import PARAMS,SYMBOLS,NAMES,HEDGE_NAME,CONTROL_NAME
from .engine import new_state,signal_context,tick_hedge,tick_control,within_caps

log=logging.getLogger(__name__)


def run_once(now_ms=None):
    now_ms=now_ms or int(time.time()*1000)
    accounts=store.read_accounts()
    for name in NAMES:
        if name not in accounts:accounts[name]=new_state(name,now_ms)
        if accounts[name]['config']!=PARAMS:raise RuntimeError('Config changed: migration/review required, never reset equity silently')
    prices=market.marks();contexts={};maintenance={};funding={}
    for symbol in SYMBOLS:
        meta=market.metadata(symbol);maintenance[symbol]=float(meta['maintenance_rate'])
        contexts[symbol]=signal_context(klines.get_bars(symbol,'4h',limit=150),
            klines.get_bars(symbol,'1d',limit=150),now_ms)
        earliest=min(accounts[n]['funding_cursor'][symbol] for n in NAMES)
        funding[symbol]=market.funding_since(symbol,earliest,now_ms)
    hedge,ev1=tick_hedge(accounts[HEDGE_NAME],prices,contexts,now_ms,funding,maintenance)
    control,ev2=tick_control(accounts[CONTROL_NAME],hedge,prices,now_ms,funding,maintenance)
    for state in (hedge,control):
        state['last_prices']=prices
        state['contexts']=contexts
        state['funding_intervals']={s:int(market.metadata(s)['funding_interval']) for s in SYMBOLS}
        if not state['paused'] and not within_caps(state,prices):raise RuntimeError('Post-plan risk invariant failed')
    store.commit({HEDGE_NAME:hedge,CONTROL_NAME:control},ev1+ev2)
    for e in ev1+ev2:
        log.info('[%s][%s][%s] %s qty=%s reason=%s',e['strategy'],e['symbol'],e['side'],
                 e['action'],e.get('qty',''),e.get('reason',''))
    log.info('tick_complete marks=Gate hedge_equity=%.4f gross=%.3fx margin=%.2f%% risk=%.2f%% events=%d',
        hedge['last_snapshot']['equity'],hedge['last_snapshot']['gross_mult'],
        hedge['last_snapshot']['margin_frac']*100,hedge['last_snapshot']['stop_risk_frac']*100,len(ev1+ev2))
    return hedge,control


def main():
    import fcntl
    store.DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    with open(str(store.DB_PATH)+'.runner.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        store.initialize()
        while True:
            started=time.time()
            try:run_once()
            except Exception:log.exception('Gate/state validation failed; no partial transaction, tick skipped')
            time.sleep(max(1,PARAMS['poll_seconds']-(time.time()-started)))


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s HedgeCycle: %(message)s')
    main()

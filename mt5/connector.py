"""One MT5 terminal per account. Windows Python (also inside a prepared Wine prefix).

No retries of broker submissions. A durable unknown result precedes every claim.
No passwords, tokens or SDK result/request objects are logged.
"""
import argparse
import datetime as dt
import json
import math
import os
import re
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

MAGIC = 26061001

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with os.fdopen(os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600), 'w', encoding='utf-8') as output:
        json.dump(value, output, allow_nan=False)
        output.flush(); os.fsync(output.fileno())
    os.replace(temporary,path)

class Panel:
    def __init__(self, url, key):
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise ValueError('A verified HTTPS panel origin is required')
        if not isinstance(key,str) or len(key)<30: raise ValueError('Bridge key missing')
        self.url,self.key=url.rstrip('/'),key
    def request(self,path,body=None):
        data=json.dumps(body,allow_nan=False).encode() if body is not None else None
        request=urllib.request.Request(self.url+'/api/bridge/'+path,data=data,headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json'})
        # Do not forward the bearer token through redirects.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args): return None
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=8) as response:
            return json.loads(response.read(1024*1024))

class Connector:
    def __init__(self, sdk, panel, terminal_path, state_dir, allow_live=False):
        self.mt5,self.panel,self.terminal_path=sdk,panel,terminal_path
        self.directory=Path(state_dir); self.directory.mkdir(parents=True,exist_ok=True)
        self.identity=self.directory/'identity.json'; self.journal=self.directory/'execution.json'
        if not self.identity.exists(): atomic_json(self.identity,{'instance_id':uuid.uuid4().hex})
        self.instance=json.loads(self.identity.read_text())['instance_id']
        self.revision=None; self.login=None; self.server=None; self.allow_live=allow_live
    def status(self,status):
        self.panel.request('status',{'instance_id':self.instance,'status':status})
    def connected(self, credentials):
        self.login,self.server=credentials['login'],credentials['server']
        current=self.mt5.account_info()
        terminal=self.mt5.terminal_info()
        if self.revision!=credentials['revision'] or current is None or str(current.login)!=self.login or current.server!=self.server or terminal is None or not terminal.connected:
            self.status('connecting')
            self.mt5.shutdown()
            args=[self.terminal_path] if self.terminal_path else []
            ok=self.mt5.initialize(*args,login=int(self.login),password=credentials['password'],server=self.server,timeout=10000)
            if not ok:
                self.status('login_failed'); return False
            self.revision=credentials['revision']
        account=self.mt5.account_info(); terminal=self.mt5.terminal_info()
        if account is None or terminal is None or not terminal.connected or str(account.login)!=self.login or account.server!=self.server:
            self.status('disconnected'); return False
        self.status('connected' if self.permissions(account,terminal) else 'permissions_blocked')
        return True
    @staticmethod
    def permissions(account,terminal):
        return bool(terminal.connected and terminal.trade_allowed and not getattr(terminal,'tradeapi_disabled',True) and account.trade_allowed and account.trade_expert)
    def open_risk(self,positions):
        total,unsafe=0.0,0
        for p in positions:
            quote=self.mt5.symbol_info_tick(p.symbol)
            buy=p.type==self.mt5.POSITION_TYPE_BUY
            if quote is None or p.sl<=0 or abs(time.time()-quote.time)>15 or quote.bid<=0 or quote.ask<quote.bid:
                unsafe+=1; continue
            current=quote.bid if buy else quote.ask
            if (buy and p.sl>=current) or (not buy and p.sl<=current): unsafe+=1; continue
            loss=self.mt5.order_calc_profit(self.mt5.ORDER_TYPE_BUY if buy else self.mt5.ORDER_TYPE_SELL,p.symbol,p.volume,current,p.sl)
            if loss is None or not math.isfinite(loss): unsafe+=1; continue
            total+=max(0,-loss)
        return total,unsafe
    def snapshot(self,symbol,timeframe):
        if not self.mt5.symbol_select(symbol,True): raise ValueError('Symbol unavailable')
        account=self.mt5.account_info(); terminal=self.mt5.terminal_info()
        info=self.mt5.symbol_info(symbol); quote=self.mt5.symbol_info_tick(symbol)
        positions=self.mt5.positions_get(); pending=self.mt5.orders_get()
        if account is None or terminal is None or info is None or quote is None or positions is None or pending is None:
            raise ValueError('MT5 data unavailable')
        if str(account.login)!=self.login or account.server!=self.server: raise ValueError('MT5 identity changed')
        if abs(time.time()-quote.time)>15: raise ValueError('Quote time is stale or ahead of local clock')
        rates=self.mt5.copy_rates_from_pos(symbol,getattr(self.mt5,'TIMEFRAME_'+timeframe),1,200)
        if rates is None or len(rates)<60: raise ValueError('Not enough closed candles')
        bars=[{k:int(row[k]) if k=='time' else float(row[k]) for k in ('time','open','high','low','close')} for row in rates]
        interval={'M5':300,'M15':900,'H1':3600}[timeframe]
        if bars[-1]['time']>time.time()-interval or time.time()-bars[-1]['time']>3*interval+60:
            raise ValueError('Closed candles are stale or not closed')
        utc=dt.datetime.now(dt.timezone.utc)
        history=self.mt5.history_deals_get(utc-dt.timedelta(days=7),utc)
        if history is None: raise ValueError('MT5 history unavailable')
        entries={self.mt5.DEAL_ENTRY_IN:'in',self.mt5.DEAL_ENTRY_OUT:'out',self.mt5.DEAL_ENTRY_INOUT:'inout',self.mt5.DEAL_ENTRY_OUT_BY:'out_by'}
        deals=[]
        for d in reversed(history):
            if d.type not in (self.mt5.DEAL_TYPE_BUY,self.mt5.DEAL_TYPE_SELL): continue
            deals.append({'ticket':str(d.ticket),'time':float(d.time),'symbol':d.symbol,'entry':entries[d.entry],'side':'BUY' if d.type==self.mt5.DEAL_TYPE_BUY else 'SELL','volume':float(d.volume),'price':float(d.price),'profit':float(d.profit),'commission':float(d.commission),'swap':float(d.swap)})
            if len(deals)>=200: break
        risk,unsafe=self.open_risk(positions)
        return {'instance_id':self.instance,'login':str(account.login),'server':account.server,'symbol':symbol,'timeframe':timeframe,'observed_at':time.time(),'quote_age':max(0,time.time()-quote.time),'balance':float(account.balance),'equity':float(account.equity),'currency':account.currency,'is_demo':account.trade_mode==self.mt5.ACCOUNT_TRADE_MODE_DEMO,'trade_allowed':self.permissions(account,terminal),'bid':float(quote.bid),'ask':float(quote.ask),'point':float(info.point),'digits':int(info.digits),'stops_level':int(info.trade_stops_level),'open_risk':risk,'positions_count':len(positions),'unprotected_positions':unsafe,'pending_orders':len(pending),'bars':bars,'deals':deals,'positions':[{'symbol':p.symbol,'side':'BUY' if p.type==self.mt5.POSITION_TYPE_BUY else 'SELL'} for p in positions]}
    def record(self,oid,status,reason,ticket='',volume=0,price=0):
        atomic_json(self.journal,{'id':oid,'status':status,'reason':reason,'ticket':str(ticket),'volume':float(volume),'price':float(price)})
    def flush(self):
        if not self.journal.exists(): return True
        result=json.loads(self.journal.read_text())
        self.panel.request('result',result)
        if result['status']=='unknown':
            # Keep the local marker even after server ACK. Explicit reconciliation required.
            return False
        self.journal.unlink(); return True
    def prepare_request(self,order):
        sdk=self.mt5
        a=sdk.account_info(); t=sdk.terminal_info()
        if a is None or t is None or not self.permissions(a,t): raise ValueError('Trading permissions blocked')
        if str(a.login)!=self.login or a.server!=self.server: raise ValueError('Account identity changed')
        demo=a.trade_mode==sdk.ACCOUNT_TRADE_MODE_DEMO
        if (demo and order['mode']!='demo') or (not demo and (order['mode']!='live' or not self.allow_live)):
            raise ValueError('Account/trading mode blocked')
        if order['side'] not in ('BUY','SELL'): raise ValueError('Invalid direction')
        symbol=order['symbol']; info=sdk.symbol_info(symbol); quote=sdk.symbol_info_tick(symbol)
        if info is None or quote is None or info.point<=0 or info.trade_tick_size<=0 or quote.bid<=0 or quote.ask<quote.bid or abs(time.time()-quote.time)>15 or order['expires_at']<time.time(): raise ValueError('Invalid/expired quote')
        buy=order['side']=='BUY'; sign=1 if buy else -1; entry=quote.ask if buy else quote.bid
        if (quote.ask-quote.bid)/info.point>order['max_spread_points'] or abs(entry-order['entry'])/info.point>order['max_deviation_points']: raise ValueError('Spread or price drift blocked')
        sl=round(round(order['sl']/info.trade_tick_size)*info.trade_tick_size,info.digits)
        tp=round(round(order['tp']/info.trade_tick_size)*info.trade_tick_size,info.digits)
        current=quote.bid if buy else quote.ask
        if min(sl,tp)<=0 or sign*(current-sl)<=info.trade_stops_level*info.point or sign*(tp-current)<=info.trade_stops_level*info.point: raise ValueError('Broker stops invalid')
        positions=sdk.positions_get(); pending=sdk.orders_get()
        if positions is None or pending is None: raise ValueError('Position state unavailable')
        risk,unsafe=self.open_risk(positions)
        if unsafe or pending or len(positions)>=order['max_positions']: raise ValueError('Position risk blocked')
        def exposure(symbol, direction):
            match=re.match(r'^(AUD|CAD|CHF|EUR|GBP|JPY|NZD|USD|XAU|XAG|BTC|ETH)(AUD|CAD|CHF|EUR|GBP|JPY|NZD|USD)',symbol.upper())
            if not match: return set()
            sign=1 if direction=='BUY' else -1
            return {(match[1],sign),(match[2],-sign)}
        for p in positions:
            if order.get('one_position_per_symbol') and p.symbol==symbol: raise ValueError('Symbol position already open')
            if order.get('block_shared_currency') and exposure(p.symbol,'BUY' if p.type==sdk.POSITION_TYPE_BUY else 'SELL') & exposure(symbol,order['side']): raise ValueError('Shared currency direction blocked')

        if info.volume_min<=0 or info.volume_step<=0 or info.volume_max<info.volume_min: raise ValueError('Invalid broker lot constraints')
        typ=sdk.ORDER_TYPE_BUY if buy else sdk.ORDER_TYPE_SELL
        worst=entry+sign*order['max_deviation_points']*info.point
        unit=sdk.order_calc_profit(typ,symbol,info.volume_min,worst,sl)
        if unit is None or not math.isfinite(unit) or unit>=0: raise ValueError('Broker risk unavailable')
        budget=min(order['risk_money'],a.equity*order['risk_pct']/100)
        volume=round(math.floor(min(info.volume_max,budget/(-unit/info.volume_min))/info.volume_step)*info.volume_step,8)
        if volume<info.volume_min: raise ValueError('Minimum lot exceeds budget')
        loss=sdk.order_calc_profit(typ,symbol,volume,worst,sl); margin=sdk.order_calc_margin(typ,symbol,volume,entry)
        if loss is None or margin is None or not math.isfinite(loss) or not math.isfinite(margin) or loss>=0 or margin>a.margin_free: raise ValueError('Margin or risk unavailable')
        reserved=-loss*(1+order['cost_buffer_pct']/100)
        if -loss>budget+1e-5 or risk+reserved>a.equity*order['max_open_risk_pct']/100 or a.equity-risk-reserved<=max(order['daily_floor'],order['total_floor']): raise ValueError('Final risk cap blocked')
        # FOK/IOC are symbol capability bits, distinct from ORDER_FILLING constants.
        if info.filling_mode & 1: filling=sdk.ORDER_FILLING_FOK
        elif info.filling_mode & 2: filling=sdk.ORDER_FILLING_IOC
        elif info.trade_exemode!=sdk.SYMBOL_TRADE_EXECUTION_MARKET: filling=sdk.ORDER_FILLING_RETURN
        else: raise ValueError('No supported market filling policy')
        return {'action':sdk.TRADE_ACTION_DEAL,'symbol':symbol,'volume':volume,'type':typ,'price':entry,'sl':sl,'tp':tp,'deviation':order['max_deviation_points'],'magic':MAGIC,'comment':'PA:'+order['id'][:24],'type_time':sdk.ORDER_TIME_GTC,'type_filling':filling}
    def execute(self,order):
        oid=order['id']
        # Caller already persisted unknown BEFORE claim; never submit without a journal.
        if not self.journal.exists(): raise ValueError('Execution journal missing')
        try:
            request=self.prepare_request(order)
            check=self.mt5.order_check(request)
            if check is None or check.retcode!=0: raise ValueError('Broker preflight rejected')
            # Recheck all account, quote, margin and risk values after broker preflight.
            request=self.prepare_request(order)
        except ValueError as error:
            self.record(oid,'rejected',str(error)); return
        try:
            result=self.mt5.order_send(request)  # Exactly one submission; NEVER retry.
        except Exception:
            self.record(oid,'unknown','Broker submission interrupted; reconcile before resuming'); return
        if result is None:
            self.record(oid,'unknown','Broker response unavailable; reconcile before resuming'); return
        if result.retcode in (self.mt5.TRADE_RETCODE_DONE,self.mt5.TRADE_RETCODE_DONE_PARTIAL):
            self.record(oid,'executed','Broker confirmed fill',result.deal,result.volume,result.price)
        else:
            rejected={10004,10006,10013,10014,10015,10016,10017,10018,10019,10020,10021,10030,10026,10027}
            self.record(oid,'rejected' if result.retcode in rejected else 'unknown','Broker retcode '+str(result.retcode),getattr(result,'order',0))
    def cycle(self):
        credentials=self.panel.request('connection',{'instance_id':self.instance})
        if not self.connected(credentials): return
        config=credentials['config']; credentials['password']=''
        if not self.flush():
            print('Execution uncertain. Stop the agent and reconcile in the panel. No order will be retried.'); return
        for symbol in config['symbols']:
            try: snapshot=self.snapshot(symbol,config['timeframe'])
            except ValueError:
                self.status('data_unavailable'); continue
            poll=self.panel.request('poll',snapshot)
            oid=poll.get('order_id')
            if not oid: continue
            fresh=self.snapshot(symbol,config['timeframe'])
            self.record(oid,'unknown','Claim/execution journal requires broker reconciliation')
            try: order=self.panel.request('claim/'+oid,fresh)
            except urllib.error.HTTPError as error:
                if 400<=error.code<500: self.journal.unlink()
                raise
            self.execute(order); self.flush(); return

def main():
    parser=argparse.ArgumentParser(description='Pars Agent MT5 connector; HTTPS required')
    parser.add_argument('--config',default='connector.json'); parser.add_argument('--allow-live',action='store_true')
    parser.add_argument('--ack-reconciled',action='store_true',help='Clear a local unknown marker ONLY after panel and broker reconciliation')
    args=parser.parse_args()
    settings=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    directory=Path(args.config).resolve().parent/('state-'+str(settings.get('account_id','default')))
    directory.mkdir(exist_ok=True)
    lock=open(directory/'worker.lock','a+b'); lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if args.ack_reconciled:
        journal=directory/'execution.json'
        if not journal.exists(): return
        result=json.loads(journal.read_text())
        if result['status']!='unknown': raise ValueError('Only an unknown journal can be acknowledged')
        confirmation=input('After checking broker History AND reconciling the panel order, type its full order ID: ').strip()
        if confirmation!=result['id']: raise ValueError('Order ID mismatch')
        journal.unlink(); print('Local journal cleared. Agent remains under panel control.'); return
    try:
        import MetaTrader5 as mt5
    except ImportError:
        raise SystemExit('Install requirements using Windows Python 3.12 x64; native Linux Python cannot load the MT5 SDK.')
    worker=Connector(mt5,Panel(settings['panel_url'],settings['bridge_key']),settings.get('terminal_path',''),directory,args.allow_live)
    print('Connector started. Passwords/keys are never printed. Stop with Ctrl+C.')
    try:
        while True:
            try: worker.cycle()
            except urllib.error.HTTPError as error:
                print('Panel HTTP',error.code,'; check connection/key in panel. No broker retry.')
            except Exception:
                print('Connection/data unavailable. No broker retry; next connection check in 5 seconds.')
            time.sleep(5)
    except KeyboardInterrupt: pass
    finally: mt5.shutdown(); lock.close()

if __name__=='__main__': main()

import copy
import io
import json
import time
import zipfile
from types import SimpleNamespace as NS

import pytest
from test_agent import client, signup, new_account, ready, prepared, snapshot
from test_deploy import module
from app.db import transaction, initialize
from app.main import Config, account_config
from app.strategies import indicators, select_candidate
from app.evaluation import evaluate, simulate
from app.risk import position_gate
from mt5.connector import Connector, Panel

def test_portfolio_guards_count_manual_positions_and_missing_metadata():
    c=Config(one_position_per_symbol=True,block_shared_currency=True).model_dump()
    s={'symbol':'GBPUSDm','positions_count':1,'positions':[{'symbol':'EURUSDm','side':'BUY'}]}
    assert position_gate(c,s,'BUY')  # Both are short USD, including manual trades.
    assert position_gate(c,s,'SELL') is None
    s['symbol']='EURUSDm'
    assert position_gate(c,s,'SELL')  # Same symbol, even the opposite direction.
    s.pop('positions')
    assert position_gate(c,s,'BUY')  # Older bridges cannot bypass enabled guards.
    s['positions_count']=0
    assert position_gate(c,s,'BUY') is None

def test_evaluation_respects_consensus_votes(monkeypatch):
    import app.evaluation as module
    bars=[{'time':i,'open':100,'high':101,'low':99,'close':100} for i in range(130)]
    monkeypatch.setattr(module,'indicators',lambda _: {'atr':1,'regime':'trend','shock':False})
    monkeypatch.setattr(module,'analyze',lambda *_: {'side':'BUY','atr':1,'regime':'trend','shock':False})
    settings={'strategies':['ema_cross'],'policy':'consensus','rr':2,'point':.01,'spread':0,'slippage':0,'commission_bps':0,'risk_pct':.25}
    assert simulate(bars,**settings,min_votes=1)['trades']==1
    assert simulate(bars,**settings,min_votes=2)['trades']==0

def test_detect_ip_consensus_and_invalid_responses(monkeypatch):
    detect=module('detect-ip')
    monkeypatch.setattr(detect,'local_addresses',lambda:set())
    values=iter(['8.8.8.8','1.1.1.1','8.8.8.8'])
    monkeypatch.setattr(detect,'query',lambda _:next(values))
    assert detect.detect()=='8.8.8.8'
    for value in ['127.0.0.1','10.0.0.1','224.0.0.1','<html>bad</html>','8.8.8.8;echo bad','::1']:
        assert detect.public_ipv4(value) is None
    monkeypatch.setattr(detect,'query',lambda _:None)
    with pytest.raises(ValueError): detect.detect()
    monkeypatch.setattr(detect,'local_addresses',lambda:{'8.8.8.8'})
    assert detect.detect()=='8.8.8.8'
    monkeypatch.setattr(detect,'query',lambda _:'1.1.1.1')
    assert detect.detect()=='1.1.1.1'  # NAT egress uses corroborated public address.

def test_new_profile_credentials_private_and_bound_to_worker(client):
    h=signup(client)
    r=client.post('/api/accounts',headers=h,json={'name':'Demo','login':'12345','server':'Broker-Demo','initial_equity':10000,'password':'private-MT5-password'})
    assert r.status_code==200
    aid=r.json()['id']; bh={'Authorization':'Bearer '+r.json()['bridge_key']}
    a=client.get('/api/accounts').json()[0]
    assert a['config']['risk_pct']==.25 and a['config']['timeframe']=='M15'
    assert a['config']['decision_policy']=='adaptive'
    with transaction() as db:
        stored=db.execute('SELECT password FROM connections').fetchone()[0]
        assert 'private-MT5-password' not in stored
    for path in ['/api/accounts',f'/api/accounts/{aid}/dashboard','/api/me']:
        assert 'private-MT5-password' not in client.get(path).text
    identity={'instance_id':'worker-instance-1'}
    r=client.post('/api/bridge/connection',headers=bh,json=identity)
    assert r.status_code==200 and r.json()['password']=='private-MT5-password'
    assert r.headers['Cache-Control']=='no-store'
    assert client.post('/api/bridge/connection',headers=bh,json={'instance_id':'other-worker'}).status_code==409
    assert client.post('/api/bridge/status',headers=bh,json=identity|{'status':'connected'}).status_code==200
    assert client.get(f'/api/accounts/{aid}/dashboard').json()['connection']['status']=='connected'
    r=client.put(f'/api/accounts/{aid}/connection',headers=h,json={'password':'replacement-secret'})
    assert r.status_code==200
    assert client.post('/api/bridge/connection',headers=bh,json=identity).status_code==401
    bh={'Authorization':'Bearer '+r.json()['bridge_key']}
    assert client.post('/api/bridge/connection',headers=bh,json=identity).json()['password']=='replacement-secret'
    signup(client,'other@example.test')
    assert client.put(f'/api/accounts/{aid}/connection',headers={'X-CSRF-Token':client.get('/api/me').json()['csrf']},json={'password':'foreign'}).status_code==404

def test_validation_does_not_echo_password_and_csrf_is_required(client):
    h=signup(client); aid,bh=new_account(client,h)
    secret='not-for-response'
    r=client.put(f'/api/accounts/{aid}/connection',headers=h,json={'password':secret,'bad_extra':secret})
    assert r.status_code==422 and secret not in r.text
    assert client.put(f'/api/accounts/{aid}/connection',json={'password':secret}).status_code==403
    s=snapshot()
    client.post('/api/bridge/poll',headers=bh,json=s)
    c=client.get('/api/accounts').json()[0]['config']|{'running':True,'mode':'demo'}
    client.put(f'/api/accounts/{aid}/config',headers=h,json=c)
    assert client.put(f'/api/accounts/{aid}/connection',headers=h,json={'password':secret}).status_code==409

def test_daily_stop_latches_after_equity_recovers(client):
    h,aid,bh,s=ready(client)
    s['equity']=9590
    assert client.post('/api/bridge/poll',headers=bh,json=s).status_code==200
    d=client.get(f'/api/accounts/{aid}/dashboard').json()
    assert d['safety_halt'] and not d['account']['config']['running']
    s['equity']=10000
    client.post('/api/bridge/poll',headers=bh,json=s)
    c=d['account']['config']|{'running':True}
    assert client.put(f'/api/accounts/{aid}/config',headers=h,json=c).status_code==409
    c['risk_day_offset_minutes']=840
    assert client.put(f'/api/accounts/{aid}/config',headers=h,json=c).status_code==409
    assert client.post(f'/api/accounts/{aid}/reset-safety',headers=h).status_code==409

def test_cooldown_entry_cap_and_legacy_settings(client):
    h,aid,bh,s,oid=prepared(client)
    assert client.post('/api/bridge/claim/'+oid,headers=bh,json=s).status_code==200
    assert client.post('/api/bridge/result',headers=bh,json={'id':oid,'status':'executed','ticket':'777','volume':.01,'price':1.2}).status_code==200
    c=client.get('/api/accounts').json()[0]['config']|{'cooldown_seconds':300}
    assert client.put(f'/api/accounts/{aid}/config',headers=h,json=c).status_code==200
    s['bars'][-1]['time']+=1
    r=client.post('/api/bridge/poll',headers=bh,json=s)
    assert r.json()['order_id']=='' and 'وقفه' in r.json()['reason']
    c.update({'cooldown_seconds':0,'max_daily_entries':1})
    client.put(f'/api/accounts/{aid}/config',headers=h,json=c)
    assert 'سقف ورود' in client.post('/api/bridge/poll',headers=bh,json=s).json()['reason']
    old=Config().model_dump()
    for key in ['decision_policy','cooldown_seconds','max_daily_entries','max_losing_exits','max_spread_atr','volatility_filter','risk_day_offset_minutes','min_votes']: old.pop(key)
    old['risk_pct']=.75
    normalized=account_config(json.dumps(old))
    assert normalized['risk_pct']==.75 and normalized['decision_policy']=='priority'
    initialize(); initialize()  # Idempotent migrations preserve existing accounts/data.
    assert len(client.get('/api/accounts').json())==1

def test_adaptive_and_consensus_reject_conflicts_shocks_and_transition():
    buy={'side':'BUY','strategy':'donchian','regime':'trend','volatility_shock':False}
    assert select_candidate([buy],'adaptive')[0]==buy
    assert select_candidate([buy|{'regime':'transition'}],'adaptive')[0] is None
    assert select_candidate([buy|{'volatility_shock':True}],'adaptive')[0] is None
    assert select_candidate([buy],'consensus',2)[0] is None
    assert select_candidate([buy,buy|{'strategy':'ema_cross'}],'consensus',2)[0]
    assert select_candidate([buy,buy|{'side':'SELL'}],'priority')[0] is None
    assert indicators(snapshot()['bars'])['regime']=='range'

def test_evaluation_no_future_signals_cost_stress_and_tenant_checks(client):
    h=signup(client); aid,bh=new_account(client,h)
    bars=[]
    for i in range(600):
        value=1+i*.0001
        bars.append({'time':1700000000+i*300,'open':value,'close':value+.00005,'high':value+.00008,'low':value-.00003})
    settings={'strategies':['donchian'],'policy':'priority','rr':2,'point':.00001,'spread':0,'slippage':0,'commission_bps':0,'risk_pct':.25}
    r=evaluate(bars,**settings)
    assert r['parameters_tuned'] is False and r['holdout']['trades']>0
    assert all(t['entry_time']>=r['split_time'] for t in r['holdout']['sample'])
    no_cost=simulate(bars,**settings); with_cost=simulate(bars,**(settings|{'spread':1,'slippage':.1}))
    assert with_cost['return_pct']<no_cost['return_pct']
    response=client.post(f'/api/accounts/{aid}/evaluate',headers=h,json=settings|{'bars':bars})
    assert response.status_code==200 and response.json()['split_time']==r['split_time']
    broken=copy.deepcopy(bars);broken[-1]['time']=broken[0]['time']
    assert client.post(f'/api/accounts/{aid}/evaluate',headers=h,json=settings|{'bars':broken}).status_code==422
    signup(client,'foreign@example.test'); fh={'X-CSRF-Token':client.get('/api/me').json()['csrf']}
    assert client.post(f'/api/accounts/{aid}/evaluate',headers=fh,json=settings|{'bars':bars}).status_code==404

def test_download_contains_connector_without_any_credentials(client):
    assert client.get('/api/connector/download').status_code==401
    signup(client)
    response=client.get('/api/connector/download')
    assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert 'connector.py' in z.namelist() and 'setup.ps1' in z.namelist()
        assert 'connector.json' not in z.namelist()

class SDK:
    POSITION_TYPE_BUY=ORDER_TYPE_BUY=DEAL_TYPE_BUY=0
    ORDER_TYPE_SELL=DEAL_TYPE_SELL=1
    ACCOUNT_TRADE_MODE_DEMO=0
    TRADE_RETCODE_DONE=10009; TRADE_RETCODE_DONE_PARTIAL=10010
    ORDER_FILLING_FOK=0; ORDER_FILLING_IOC=1; ORDER_FILLING_RETURN=2
    SYMBOL_TRADE_EXECUTION_MARKET=2; TRADE_ACTION_DEAL=1; ORDER_TIME_GTC=0
    DEAL_ENTRY_IN=0; DEAL_ENTRY_OUT=1; DEAL_ENTRY_INOUT=2; DEAL_ENTRY_OUT_BY=3
    TIMEFRAME_M5=5
    def __init__(self,bars):
        self.bars=bars;self.sent=[];self.positions=[];self.pending=[];self.answer=NS(retcode=10009,deal=777,volume=.02,price=1.2)
        self.account=NS(login=12345,server='Broker-Demo',trade_mode=0,trade_allowed=True,trade_expert=True,equity=10000,balance=10000,margin_free=10000,currency='USD')
        self.terminal=NS(connected=True,trade_allowed=True,tradeapi_disabled=False)
        self.info=NS(point=.00001,trade_tick_size=.00001,digits=5,trade_stops_level=10,volume_min=.01,volume_step=.01,volume_max=100,filling_mode=3,trade_exemode=2)
    def initialize(self,*args,**kwargs): return True
    def shutdown(self): pass
    def account_info(self): return self.account
    def terminal_info(self): return self.terminal
    def symbol_select(self,*args): return True
    def symbol_info(self,*args): return self.info
    def symbol_info_tick(self,*args): return NS(bid=1.2,ask=1.2001,time=time.time())
    def copy_rates_from_pos(self,*args): return self.bars
    def history_deals_get(self,*args): return []
    def positions_get(self): return self.positions
    def orders_get(self): return self.pending
    def order_calc_profit(self,typ,symbol,volume,opened,closed): return (closed-opened)*volume*100000*(1 if typ==0 else -1)
    def order_calc_margin(self,*args): return 10
    def order_check(self,*args): return NS(retcode=0)
    def order_send(self,request): self.sent.append(request);return self.answer

def test_connector_full_protocol_and_broker_timeout_never_retries(client,tmp_path):
    h,aid,bh,s=ready(client)
    client.post(f'/api/accounts/{aid}/stop',headers=h)
    r=client.put(f'/api/accounts/{aid}/connection',headers=h,json={'password':'private-sdk-password'})
    bh={'Authorization':'Bearer '+r.json()['bridge_key']}
    class LocalPanel:
        def request(self,path,body=None):
            result=client.post('/api/bridge/'+path,headers=bh,json=body) if body is not None else client.get('/api/bridge/'+path,headers=bh)
            assert result.status_code==200,result.text
            return result.json()
    sdk=SDK(s['bars']); w=Connector(sdk,LocalPanel(),'',tmp_path)
    credentials=w.panel.request('connection',{'instance_id':w.instance})
    assert w.connected(credentials)
    initial=w.snapshot('EURUSD','M5')
    client.post('/api/bridge/poll',headers=bh,json=initial)
    c=client.get('/api/accounts').json()[0]['config']|{'running':True}
    client.put(f'/api/accounts/{aid}/config',headers=h,json=c)
    sdk.bars[-1]['time']+=1
    w.cycle()
    assert len(sdk.sent)==1 and not w.journal.exists()
    orders=client.get(f'/api/accounts/{aid}/dashboard').json()['orders']
    assert orders[0]['status']=='executed'
    w.cycle(); assert len(sdk.sent)==1
    sdk.bars[-1]['time']+=1
    sdk.answer=None
    w.cycle(); assert len(sdk.sent)==2 and w.journal.exists()
    assert json.loads(w.journal.read_text())['status']=='unknown'
    w.cycle(); assert len(sdk.sent)==2
    assert client.get(f'/api/accounts/{aid}/dashboard').json()['orders'][0]['status']=='unknown'

@pytest.mark.parametrize('block',['real','margin','unsafe','stale','future_quote','old_quote','minlot','permissions','unknown_filling'])
def test_connector_local_risk_blocks_before_broker_send(tmp_path,block):
    sdk=SDK(snapshot()['bars']);w=Connector(sdk,None,'',tmp_path)
    w.login='12345';w.server='Broker-Demo'
    order={'id':'a'*32,'side':'BUY','symbol':'EURUSD','mode':'demo','expires_at':time.time()+12,'entry':1.2001,'sl':1.198,'tp':1.2043,'risk_money':25,'risk_pct':.25,'max_spread_points':30,'max_deviation_points':10,'max_positions':3,'cost_buffer_pct':20,'max_open_risk_pct':2,'daily_floor':9600,'total_floor':9200}
    if block=='real': sdk.account.trade_mode=2;order['mode']='live'
    elif block=='margin': sdk.account.margin_free=0
    elif block=='unsafe': sdk.positions=[NS(symbol='EURUSD',sl=0,type=0,volume=.01)]
    elif block=='stale': order['expires_at']=time.time()-1
    elif block in ('future_quote','old_quote'): sdk.symbol_info_tick=lambda _: NS(bid=1.2,ask=1.2001,time=time.time()+(60 if block=='future_quote' else -60))
    elif block=='minlot': sdk.info.volume_min=10
    elif block=='permissions': sdk.terminal.tradeapi_disabled=True
    elif block=='unknown_filling': sdk.info.filling_mode=0
    w.record(order['id'],'unknown','journal before broker execution')
    w.execute(order)
    assert sdk.sent==[] and json.loads(w.journal.read_text())['status']=='rejected'

def test_panel_rejects_non_https_and_embedded_credentials():
    for url in ['http://8.8.8.8','https://user:secret@example.com','https://example.com/?key=secret']:
        with pytest.raises(ValueError): Panel(url,'a'*40)

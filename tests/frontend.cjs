// DOM integration checks; no broker, Telegram or browser connection is made.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root=path.join(__dirname,'../app/static');
const dom=new JSDOM(fs.readFileSync(path.join(root,'index.html'),'utf8'),{url:'http://testserver',runScripts:'outside-only'});
const w=dom.window;
const config={running:false,mode:'signals',strategies:['ema_cross','rsi_range','donchian'],symbols:['EURUSD'],timeframe:'M5',risk_pct:.5,max_open_risk_pct:2,daily_loss_pct:4,total_loss_pct:8,max_positions:3,max_spread_points:30,max_deviation_points:10,cost_buffer_pct:20,rr:2};
const account={id:'account1',name:'<img src=x onerror=alert(1)>',login:'12345',config,last_seen:Date.now()/1000,connected:true,snapshot:{equity:10000,balance:10000,open_risk:0,positions_count:0,currency:'USD',digits:5}};
const requests=[];
const bars=Array.from({length:60},(_,i)=>({time:1700000000+i*300,open:1.1,high:1.11,low:1.09,close:1.101}));
const dashboard={account,markets:[{symbol:'EURUSD',timeframe:'M5',bid:1.101,ask:1.102,bars,received_at:Date.now()/1000}],ideas:[{id:'i1',symbol:'EURUSD',timeframe:'M5',side:'WAIT',strategy:'ema_cross',reason:'<svg onload=alert(1)>',created:Date.now()/1000,regime:'range',rsi:50}],orders:[],audit:[]};
w.setInterval=()=>0;
w.fetch=async(url,opt={})=>{
  requests.push({url,...opt});let value={};
  if(url==='/api/me')value={email:'ali@example.test',csrf:'csrf-test',live_allowed:false,telegram:null,strategies:{ema_cross:{name:'EMA',description:'Trend'},rsi_range:{name:'RSI',description:'Range'},donchian:{name:'Channel',description:'Breakout'}}};
  else if(url==='/api/accounts')value=[account];
  else if(url==='/api/accounts/account1/dashboard')value=dashboard;
  else if(url.endsWith('/config')){account.config=JSON.parse(opt.body);value={ok:true};}
  else if(url.endsWith('/stop')){account.config.running=false;value={ok:true};}
  else if(url==='/api/telegram/queue')value=[];
  else if(url.endsWith('/report'))value={net_realized:0,note:'No trades',deals:[]};
  return {ok:true,status:200,json:async()=>value};
};
w.eval(fs.readFileSync(path.join(root,'app.js'),'utf8'));
const settle=()=>new Promise(r=>setTimeout(r,30));
(async()=>{
  await settle();
  assert.equal(w.document.querySelector('#workspace').hidden,false);
  assert.equal(w.document.querySelector('#auth').hidden,true);
  assert.equal(w.document.querySelector('#metric-equity').textContent,'10,000');
  assert.equal(w.document.querySelector('#chart').querySelectorAll('rect').length,60);
  assert.equal(w.document.querySelector('#chart-empty').hidden,true);
  assert.equal(w.document.querySelector('#account-select img'),null);
  assert.equal(w.document.querySelector('#ideas svg'),null);
  w.document.querySelector('[data-tab="risk"]').click();
  assert.equal(w.document.querySelector('[data-page="risk"]').hidden,false);
  assert.equal(w.document.querySelector('[data-page="overview"]').hidden,true);
  w.document.querySelector('#risk-form [name=risk_pct]').value='0.75';
  w.document.querySelector('#risk-form').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
  await settle();
  const saved=requests.find(x=>x.url.endsWith('/config')&&x.method==='PUT');
  assert.equal(saved.headers['X-CSRF-Token'],'csrf-test');
  assert.equal(JSON.parse(saved.body).risk_pct,.75);
  w.document.querySelector('#toggle-agent').click();await settle();
  assert.equal(account.config.running,true);
  w.document.querySelector('#stop-agent').click();await settle();
  assert.equal(account.config.running,false);
  assert.ok(requests.some(x=>x.url.endsWith('/stop')&&x.method==='POST'));
  dom.window.close();
  process.stdout.write('Frontend integration: boot, chart, escaped content, navigation, config, start/stop passed.\n');
})().catch(e=>{console.error(e);dom.window.close();process.exitCode=1;});

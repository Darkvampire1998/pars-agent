'use strict';
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
let csrf='', me=null, accounts=[], aid='', data=null, symbol='', activeTab='overview', registerMode=false, refreshing=false;
let toastTimer;
const titles={overview:['MARKET OVERVIEW','بازار، زیر نگاه تو.'],strategies:['STRATEGY ENGINE','قواعد معامله در دست تو.'],risk:['RISK CONTROL','هر ورود، با مرز مشخص.'],connection:['ACCOUNT CONNECTION','یک پل تا متاتریدر.'],telegram:['TELEGRAM REPORTS','گزارش‌ها، همیشه همراهت.'],reports:['EXECUTION REPORTS','هر معامله، قابل پیگیری.'],logs:['ACTIVITY LOG','رویدادهای اتاق معاملات.']};
titles.evaluation=['STRATEGY EVALUATION','هر راهبرد، با شواهد واقعی.'];
const controlDefaults={decision_policy:'priority',min_votes:2,cooldown_seconds:0,max_daily_entries:0,max_losing_exits:0,max_spread_atr:1,risk_day_offset_minutes:0,volatility_filter:false,one_position_per_symbol:false,block_shared_currency:false};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(v,d=2)=>Number.isFinite(Number(v))?Number(v).toLocaleString('en-US',{maximumFractionDigits:d}):'—';
const date=v=>v?new Date(v*1000).toLocaleString('fa-IR',{dateStyle:'short',timeStyle:'short'}):'—';
const action=f=>async e=>{try{await f(e);}catch(err){toast(err.message,true);}};
function toast(message,error=false){clearTimeout(toastTimer);$('#toast').textContent=message;$('#toast').className=error?'error':'';$('#toast').hidden=false;toastTimer=setTimeout(()=>$('#toast').hidden=true,6500);}
async function api(path,method='GET',body){
  const res=await fetch('/api'+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});
  const value=await res.json().catch(()=>({detail:'پاسخ سرور قابل خواندن نیست'}));
  if(!res.ok){
    if(res.status===401&&me){me=null;$('#workspace').hidden=true;$('#auth').hidden=false;}
    const detail=Array.isArray(value.detail)?value.detail.map(x=>x.msg).join('؛ '):value.detail;
    throw new Error(detail||'درخواست انجام نشد');
  }
  return value;
}
function requireAccount(){if(!aid)throw new Error('ابتدا یک حساب در بخش حساب و اتصال بساز.');if(data?.account?.id!==aid)throw new Error('در حال دریافت اطلاعات این حساب؛ چند لحظه صبر کن.');}
function account(){return data?.account?.id===aid?data.account:accounts.find(x=>x.id===aid);}
function showKey(key,id=aid){$('#key-value').value=key;$('#key-account-id').textContent='Account ID: '+id;$('#key-dialog').showModal();}
async function boot(){
  me=await api('/me');csrf=me.csrf;$('#user-email').textContent=me.email;
  $('#auth').hidden=true;$('#workspace').hidden=false;
  $('#live-note').textContent=me.live_allowed?'معامله واقعی به فعال‌سازی حالت live و AllowRealTrading داخل اکسپرت نیاز دارد.':'معامله واقعی فعلاً در تنظیمات سرور بسته است. حالت تحلیل و دمو قابل استفاده است.';
  $('#mode option[value=live]').disabled=!me.live_allowed;
  $('#strategy-cards').innerHTML=Object.entries(me.strategies).map(([key,s])=>`<article class="strategy-card"><label><input type="checkbox" name="strategies" value="${esc(key)}">${esc(s.name)}</label><p>${esc(s.description)}</p></article>`).join('');
  if(me.telegram){$('#telegram-form [name=chat_id]').value=me.telegram.chat_id;$('#telegram-form [name=enabled]').checked=!!me.telegram.enabled;}
  await refreshAccounts();if(!aid)switchTab('connection');
}
async function refreshAccounts(){
  accounts=await api('/accounts');
  if(!accounts.some(x=>x.id===aid))aid=accounts[0]?.id||'';
  $('#account-select').innerHTML=accounts.length?accounts.map(a=>`<option value="${esc(a.id)}">${esc(a.name)} · ${esc(a.login)}</option>`).join(''):'<option value="">حسابی وجود ندارد</option>';
  $('#account-select').value=aid;
  if(aid){const selected=aid,result=await api(`/accounts/${selected}/dashboard`);if(aid!==selected)return;data=result;fillForms();render();}else{data=null;render();}
}
function switchTab(name){
  if(!titles[name])return;activeTab=name;
  $$('[data-page]').forEach(x=>x.hidden=x.dataset.page!==name);
  $$('#nav [data-tab]').forEach(x=>x.classList.toggle('active',x.dataset.tab===name));
  $('#section-label').textContent=titles[name][0];$('#section-title').textContent=titles[name][1];
  fillForms();if(me)loadTab().catch(e=>toast(e.message,true));
}
function fillForms(){
  const c=account()?.config;if(!c)return;
  for(const field of ['risk_pct','max_open_risk_pct','daily_loss_pct','total_loss_pct','max_positions','max_spread_points','max_deviation_points','cost_buffer_pct'])$('#risk-form [name='+field+']').value=c[field];
  for(const field of Object.keys(controlDefaults)){
    const input=$('#risk-form [name='+field+']'),value=c[field]??controlDefaults[field];
    if(input.type==='checkbox')input.checked=!!value;else input.value=value;
  }
  $('#symbols').value=c.symbols.join(',');$('#timeframe').value=c.timeframe;$('#rr').value=c.rr;$('#mode').value=c.mode;
  $$('#strategy-cards input').forEach(x=>x.checked=c.strategies.includes(x.value));
}
function render(){
  const a=account(),s=a?.snapshot,c=a?.config;
  $('#decision-reason').textContent=data?.safety_halt||data?.decision?.reason||'منتظر دریافت کندل از ترمینال';
  $('#decision-context').textContent=data?.decision?`${data.decision.regime} · ADX ${num(data.decision.adx,1)} · ${date(data.decision.updated)}`:'';
  $('#mt5-account-id').textContent=a?`Account ID: ${a.id} | ${a.login} | ${a.server||''}`:'ابتدا حساب بساز یا انتخاب کن.';
  const states={waiting:'رمز ذخیره شده؛ منتظر اجرای کانکتور',connecting:'در حال ورود به متاتریدر',connected:'ورود کانکتور موفق؛ داده ترمینال را بررسی کن',login_failed:'ورود ناموفق؛ رمز و نام دقیق سرور را بررسی کن',disconnected:'کانکتور قطع است',permissions_blocked:'ورود انجام شده؛ معامله خودکار در ترمینال/حساب مجاز نیست',data_unavailable:'کندل یا داده حساب هنوز آماده نیست',mql5:'اتصال با اکسپرت؛ رمز در ترمینال نگهداری می‌شود'};
  $('#mt5-connection-state').textContent=states[data?.connection?.status]||'منتظر کانکتور';
  $('#connection-status').innerHTML=`<i class="dot ${a?.connected?'':'gray'}"></i>${a?.connected?'ترمینال متصل':'ترمینال متصل نیست'}`;
  $('#agent-status').textContent=c?.running?'ایجنت فعال':'ایجنت متوقف';
  $('#mode-badge').textContent=({signals:'فقط تحلیل',demo:'معامله دمو',live:'معامله واقعی'})[c?.mode]||'فقط تحلیل';
  $('#toggle-agent').textContent=c?.running?'توقف ایجنت':'شروع ایجنت';
  $('#metric-equity').textContent=s?num(s.equity):'—';$('#metric-balance').textContent=s?num(s.balance):'—';
  $('#metric-risk').textContent=s?num(s.open_risk):'—';$('#metric-positions').textContent=s?s.positions_count:'—';
  $('#currency').textContent=s?.currency||'داده ترمینال لازم است';$('#last-sync').textContent=a?.last_seen?'آخرین دریافت: '+date(a.last_seen):'منتظر اولین اتصال';
  if(!c?.symbols.includes(symbol))symbol=c?.symbols[0]||'';
  const markets=data?.markets||[];
  $('#watchlist').innerHTML=(c?.symbols||[]).map(x=>{const m=markets.find(y=>y.symbol===x);return `<button data-symbol="${esc(x)}" class="${x===symbol?'active':''}"><b>${esc(x)}</b><small>${m?num(m.bid,5):'—'}</small></button>`;}).join('')||'<p class="muted">حسابی انتخاب نشده است.</p>';
  const market=markets.find(x=>x.symbol===symbol&&x.timeframe===c?.timeframe);
  $('#market-symbol').textContent=symbol||'نماد انتخاب نشده';$('#market-price').textContent=market?num(market.bid,s?.digits||5):'—';
  $('#chart-timeframe').textContent=c?.timeframe||'M5';$('#chart-empty').hidden=!!market;
  $('#chart-count').textContent=(market?.bars.length||0)+' کندل';drawChart(market?.bars||[]);
  const ideas=(data?.ideas||[]).filter(x=>x.symbol===symbol&&x.timeframe===c?.timeframe);
  $('#market-state').textContent=market&&Date.now()/1000-market.received_at>20?'داده قدیمی':({range:'بازار رنج',trend:'بازار رونددار',transition:'بازار در حال تغییر',unknown:'نامشخص'})[ideas[0]?.regime]||'داده موجود نیست';
  $('#ideas').innerHTML=ideas.slice(0,9).map(x=>`<article class="idea"><div class="idea-head"><span>${esc(x.symbol)} · ${esc(x.timeframe)}</span><b class="side ${esc(x.side)}">${esc(x.side)}</b></div><h3>${esc(me?.strategies[x.strategy]?.name||x.strategy)}</h3><p>${esc(x.reason)}</p>${x.side!=='WAIT'?`<div class="levels"><div><span>ENTRY</span>${num(x.entry,5)}</div><div><span>STOP</span>${num(x.sl,5)}</div><div><span>TARGET</span>${num(x.tp,5)}</div></div>`:''}<div class="idea-meta">${date(x.created)} · RSI ${num(x.rsi,1)}</div></article>`).join('')||'<div class="empty">پس از اتصال ترمینال، تحلیل همین‌جا ظاهر می‌شود.</div>';
  $('#orders-list').innerHTML=(data?.orders||[]).map(o=>`<div class="order-row"><b>${esc(o.data.symbol)} · ${esc(o.data.side)}</b><span class="badge">${esc(o.status)}</span><span>${date(o.created)}</span><code dir="ltr">${esc(o.id)}</code><small>${esc(o.result?.reason||'')}</small></div>`).join('')||'<p class="muted">هنوز سفارشی ثبت نشده است.</p>';
  $('#audit-list').innerHTML=(data?.audit||[]).map(x=>`<div class="audit-row"><span dir="ltr">${esc(x.event)}</span><span>${date(x.created)}</span></div>`).join('')||'<p class="muted">رویدادی ثبت نشده است.</p>';
}
function drawChart(bars){
  const svg=$('#chart');svg.replaceChildren();if(!bars.length)return;
  const ns='http://www.w3.org/2000/svg', W=1000,H=300,pad=15,display=bars.slice(-100);
  const low=Math.min(...display.map(x=>x.low)),high=Math.max(...display.map(x=>x.high)),range=high-low||1;
  const y=value=>pad+(high-value)/range*(H-2*pad),spacing=900/display.length;
  function add(tag,attrs,text){const e=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,String(v)));if(text!==undefined)e.textContent=text;svg.append(e);return e;}
  for(let i=0;i<5;i++){const yy=pad+i*(H-2*pad)/4;add('line',{x1:0,y1:yy,x2:900,y2:yy,stroke:'#1e2b3c','stroke-width':1});add('text',{x:913,y:yy+4,fill:'#60718b','font-size':12,'font-family':'Arial'},num(high-i*range/4,5));}
  display.forEach((b,i)=>{const x=spacing*(i+.5),color=b.close>=b.open?'#42e0a1':'#f57887';add('line',{x1:x,y1:y(b.high),x2:x,y2:y(b.low),stroke:color,'stroke-width':1});add('rect',{x:x-spacing*.29,y:Math.min(y(b.open),y(b.close)),width:Math.max(1,spacing*.58),height:Math.max(1,Math.abs(y(b.open)-y(b.close))),fill:color});});
  add('line',{x1:0,x2:900,y1:y(display.at(-1).close),y2:y(display.at(-1).close),stroke:'#42e0a1','stroke-dasharray':'3 5',opacity:.5});
  add('text',{x:10,y:324,fill:'#60718b','font-size':11,'font-family':'Arial'},'100 CLOSED CANDLES · BROKER SERVER TIME');
}
async function loadTab(){
  if(activeTab==='telegram'){
    const rows=await api('/telegram/queue');
    $('#telegram-queue').innerHTML=rows.map(r=>`<div class="row"><span class="${esc(r.status)}">${esc(r.status)}</span><span>تلاش ${r.attempts}</span><span>${esc(r.last_error||'')}</span></div>`).join('')||'<p class="muted">هنوز گزارشی ارسال نشده است.</p>';
  }
  if(activeTab==='reports'&&aid){
    const r=await api(`/accounts/${aid}/report`);$('#report-net').textContent=num(r.net_realized)+' '+(account()?.snapshot?.currency||'');$('#report-note').textContent=r.note;
    $('#deals-table').innerHTML=r.deals.sort((a,b)=>b.time-a.time).map(d=>`<tr><td>${date(d.time)}</td><td>${esc(d.symbol)}</td><td>${esc(d.side)} / ${esc(d.entry)}</td><td class="num">${num(d.volume,4)}</td><td class="num">${num(d.price,5)}</td><td class="num">${num(d.profit+d.commission+d.swap)}</td></tr>`).join('')||'<tr><td colspan="6">دیل واقعی هنوز از ترمینال دریافت نشده است.</td></tr>';
  }
}
async function saveConfig(changes){requireAccount();await api(`/accounts/${aid}/config`,'PUT',{...account().config,...changes});await refreshAccounts();toast('تنظیمات ذخیره شد؛ سفارش‌های آماده قبلی لغو شدند.');}
$('#auth-form').addEventListener('submit',action(async e=>{e.preventDefault();const button=$('#auth-submit');button.disabled=true;try{const v=await api(registerMode?'/register':'/login','POST',Object.fromEntries(new FormData(e.target)));csrf=v.csrf;await boot();}finally{button.disabled=false;}}));
$('#auth-toggle').addEventListener('click',()=>{registerMode=!registerMode;$('#auth-title').textContent=registerMode?'ساخت حساب کاربری':'ورود به پنل';$('#auth-submit').textContent=registerMode?'ساخت حساب و ورود ←':'ورود به اتاق معاملات ←';$('#auth-toggle').textContent=registerMode?'حساب دارم؛ ورود':'حساب ندارم؛ ثبت‌نام';$('#auth-form [name=password]').autocomplete=registerMode?'new-password':'current-password';});
$$('[data-tab]').forEach(x=>x.addEventListener('click',()=>switchTab(x.dataset.tab)));
$('#account-select').addEventListener('change',action(async e=>{aid=e.target.value;data=null;symbol='';await refreshAccounts();await loadTab();}));
$('#watchlist').addEventListener('click',e=>{const b=e.target.closest('[data-symbol]');if(b){symbol=b.dataset.symbol;render();}});
$('#logout').addEventListener('click',action(async()=>{await api('/logout','POST',{});me=null;csrf='';accounts=[];data=null;aid='';$('#workspace').hidden=true;$('#auth').hidden=false;$('#auth-form').reset();}));
$('#toggle-agent').addEventListener('click',action(async()=>{requireAccount();if(account().config.running){await api(`/accounts/${aid}/stop`,'POST',{});await refreshAccounts();toast('ورود جدید متوقف شد.');}else{await saveConfig({running:true});}}));
$('#stop-agent').addEventListener('click',action(async()=>{requireAccount();await api(`/accounts/${aid}/stop`,'POST',{});await refreshAccounts();toast('ورود جدید متوقف شد. حد ضرر و هدف پوزیشن‌های باز باقی می‌مانند.');}));
$('#strategy-form').addEventListener('submit',action(async e=>{e.preventDefault();const fd=new FormData(e.target);await saveConfig({strategies:fd.getAll('strategies'),symbols:fd.get('symbols').split(',').map(x=>x.trim()).filter(Boolean),timeframe:fd.get('timeframe'),mode:fd.get('mode'),rr:Number(fd.get('rr'))});}));
$('#risk-form').addEventListener('submit',action(async e=>{e.preventDefault();const fd=new FormData(e.target),v=Object.fromEntries([...fd].filter(([k])=>!['decision_policy','volatility_filter','one_position_per_symbol','block_shared_currency'].includes(k)).map(([k,v])=>[k,Number(v)]));v.decision_policy=fd.get('decision_policy');for(const k of ['volatility_filter','one_position_per_symbol','block_shared_currency'])v[k]=fd.has(k);await saveConfig(v);}));
$('#account-form').addEventListener('submit',action(async e=>{e.preventDefault();const v=Object.fromEntries(new FormData(e.target));v.initial_equity=Number(v.initial_equity);const result=await api('/accounts','POST',v);aid=result.id;await refreshAccounts();e.target.reset();showKey(result.bridge_key);}));
$('#rotate-key').addEventListener('click',action(async()=>{requireAccount();const r=await api(`/accounts/${aid}/rotate-key`,'POST',{});await refreshAccounts();showKey(r.bridge_key);}));
$('#connection-form').addEventListener('submit',action(async e=>{e.preventDefault();requireAccount();const selected=aid,password=e.target.elements.password.value;const result=await api(`/accounts/${selected}/connection`,'PUT',{password});e.target.reset();await refreshAccounts();showKey(result.bridge_key,selected);}));
for(const profile of ['conservative','balanced'])$('#profile-'+profile).addEventListener('click',action(async()=>{if(!me?.profiles?.[profile])throw new Error('پروفایل از سرور دریافت نشده است');await saveConfig({...me.profiles[profile],running:false});}));
$('#reset-safety').addEventListener('click',action(async()=>{requireAccount();await api(`/accounts/${aid}/reset-safety`,'POST',{});await refreshAccounts();toast('توقف حفاظتی رفع شد؛ شروع ایجنت باید جدا انجام شود.');}));
$('#evaluation-form').addEventListener('submit',action(async e=>{
  e.preventDefault();requireAccount();const file=$('#evaluation-file').files[0];if(!file)throw new Error('فایل CSV لازم است');if(file.size>1024*1024)throw new Error('حداکثر حجم فایل ۱ مگابایت است');
  const lines=(await file.text()).trim().replace(/^\uFEFF/,'').split(/\r?\n/),headers=lines.shift().split(',').map(x=>x.trim().toLowerCase());
  if(!['time','open','high','low','close'].every(k=>headers.includes(k)))throw new Error('ستون‌های time,open,high,low,close لازم است');
  const bars=lines.filter(x=>x.trim()).map(line=>{const cells=line.split(',');return Object.fromEntries(['time','open','high','low','close'].map(k=>[k,Number(cells[headers.indexOf(k)])]));});
  const values=Object.fromEntries([...new FormData(e.target)].map(([k,v])=>[k,Number(v)])),c=account().config;
  const button=$('#evaluate-submit');button.disabled=true;
  try{const result=await api(`/accounts/${aid}/evaluate`,'POST',{...values,bars,strategies:c.strategies,policy:c.decision_policy||'priority',min_votes:c.min_votes||2,rr:c.rr,risk_pct:c.risk_pct});
    const names={development:'بخش توسعه',holdout:'۳۰٪ آخر مستقل',cost_stress:'هزینه دوبرابر در بخش آخر'};
    $('#evaluation-result').innerHTML=`<p class="notice">${esc(result.note)}</p><table><thead><tr><th>بخش</th><th>معامله</th><th>برد %</th><th>امید R</th><th>ضریب سود</th><th>بازده %</th><th>افت %</th></tr></thead><tbody>${Object.entries(names).map(([key,title])=>{const r=result[key];return `<tr><td>${title}</td><td>${r.trades}</td><td>${r.win_rate_pct===null?'—':num(r.win_rate_pct)}</td><td>${r.expectancy_r===null?'—':num(r.expectancy_r,4)}</td><td>${r.profit_factor===null?'—':num(r.profit_factor,3)}</td><td>${num(r.return_pct,3)}</td><td>${num(r.max_drawdown_pct,3)}</td></tr>`;}).join('')}</tbody></table><p>${esc(({insufficient:'تعداد معاملات برای نتیجه‌گیری کافی نیست.',unfavorable:'نتیجه بخش آخر یا آزمون هزینه مناسب نیست.',needs_demo_validation:'این نتیجه هنوز به آزمون دمو و داده مستقل تازه نیاز دارد.'})[result.assessment])}</p>`;
  }finally{button.disabled=false;}
}));
$('#close-key').addEventListener('click',()=>{$('#key-dialog').close();$('#key-value').value='';});
$('#key-dialog').addEventListener('cancel',()=>{$('#key-value').value='';});
$('#copy-key').addEventListener('click',action(async()=>{await navigator.clipboard.writeText($('#key-value').value);toast('کلید کپی شد.');}));
$('#telegram-form').addEventListener('submit',action(async e=>{e.preventDefault();const fd=new FormData(e.target);await api('/telegram','PUT',{token:fd.get('token'),chat_id:fd.get('chat_id'),enabled:fd.has('enabled')});e.target.elements.token.value='';toast('تنظیمات تلگرام ذخیره شد.');await loadTab();}));
$('#telegram-test').addEventListener('click',action(async()=>{await api('/telegram/test','POST',{});toast('پیام تست در صف قرار گرفت.');await loadTab();}));
$('#telegram-report').addEventListener('click',action(async()=>{requireAccount();await api(`/accounts/${aid}/telegram-report`,'POST',{});toast('گزارش ۲۴ ساعت در صف قرار گرفت.');await loadTab();}));
$('#reconcile-form').addEventListener('submit',action(async e=>{e.preventDefault();requireAccount();const fd=new FormData(e.target);await api(`/accounts/${aid}/reconcile`,'POST',{id:fd.get('id'),status:fd.get('status'),ticket:fd.get('ticket'),reason:fd.get('reason'),broker_checked:fd.has('broker_checked')});e.target.reset();await refreshAccounts();toast('نتیجه بررسی بروکر ثبت شد.');}));
setInterval(async()=>{if(!me||refreshing)return;refreshing=true;try{if(aid){const selected=aid,result=await api(`/accounts/${selected}/dashboard`);if(aid===selected){data=result;render();}}await loadTab();}catch(e){if(me)toast(e.message,true);}finally{refreshing=false;}},5000);
boot().catch(()=>{$('#auth').hidden=false;$('#workspace').hidden=true;});

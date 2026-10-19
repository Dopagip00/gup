const test=require('node:test'),assert=require('node:assert/strict');
const p=require('../pages.js');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../pages.js'),'utf8');
const cal={'2026':{verified:true,holidays:['2026-10-01','2026-10-02','2026-10-05','2026-10-06','2026-10-07']}};
test('Monday premarket uses Friday; Monday after close expires Friday',()=>{
  assert.equal(p.context(cal,new Date('2026-10-19T09:00:00+08:00')).target,'2026-10-16');
  assert.equal(p.context(cal,new Date('2026-10-19T16:00:00+08:00')).target,'2026-10-19');
});
test('unknown calendar refuses all new recommendations',()=>{
  const c=p.context({},new Date('2027-01-04T09:00:00+08:00'));
  assert.equal(c.target,null);assert.equal(p.gatePlan({date:c.date,buy:{symbol:'sh600000'}},c).buy,null);
});
test('old daily recommendation never becomes today buy or sell when Actions fail',()=>{
  const c=p.context(cal,new Date('2026-10-19T09:10:00+08:00'));
  const plan=p.gatePlan({date:'2026-10-16',status:'published',buy:{symbol:'sh600000'},sell:[{}]},c);
  assert.equal(plan.buy,null);assert.equal(plan.sell.length,0);
});
test('stale static quotes suppress actionable and nested research levels',()=>{
  const stock={date:'2026-10-16',entry_low:10,research_levels:{entry_high:11},eligible:true,prediction:{eligible:true}};
  const c=p.context(cal,new Date('2026-10-19T17:00:00+08:00'));
  const data=p.gateDashboard({meta:{data_date:'2026-10-16'},index:null,stocks:[stock]},c,{status:'published',buy:stock});
  assert.equal(stock.eligible,false);assert.equal(stock.prediction.eligible,false);
  assert.equal(stock.research_levels,undefined);assert.equal(data.meta.daily_buy,null);
});

const NOW=Date.parse('2026-10-19T09:10:00+08:00');
const manifest={generated_at:'2026-10-19T09:05:00+08:00',data_date:'2026-10-16',repository:'Dopagip00/gup',privacy:'public-market-research-only',snapshot_schema_version:1,app_version:'2.3.0',data_schema_version:1};
function storage(limit=Infinity){const values={};let failures=0;Object.defineProperties(values,{getItem:{value:key=>values[key]??null},setItem:{value:(key,value)=>{
  const bytes=Object.keys(values).filter(x=>x!==key).reduce((sum,x)=>sum+2*(x.length+values[x].length),0)+2*(key.length+value.length);
  if(bytes>limit){failures++;throw Object.assign(Error('quota'),{name:'QuotaExceededError',code:22});}values[key]=value;
}},removeItem:{value:key=>delete values[key]},quotaFailures:{get:()=>failures}});return values;}
function browser(options={}){
  const calls=[],localStorage=options.storage||storage();
  const stock={symbol:'sh601811',name:'研究样本',date:'2026-10-16',eligible:true,entry_low:10,entry_high:11,stop_loss:9,take_profit:12,prediction:{available:true,eligible:true,rank_value:1,expected_return_pct:2}};
  const fixtures={
    '/gup/data/manifest.json':manifest,'/gup/data/calendar.json':cal,
    '/gup/data/dashboard.json':{meta:{data_date:'2026-10-16'},stocks:[stock]},
    '/gup/data/reports.json':[{date:'2026-10-16',count:1}],
    '/gup/data/reports/2026-10-16.json':{meta:{data_date:'2026-10-16',report_date:'2026-10-16'},stocks:[stock]},
    '/gup/data/daily-plan.json':{date:'2026-10-19',status:'published',buy:stock,sell:[]},
    '/gup/data/stocks/sh601811.json':{stock},'/gup/data/longterm.json':{as_of:'2026-10-16',stocks:[stock]}
  };
  let fetchImpl=options.fetch;
  class FixedDate extends Date{constructor(...args){super(...(args.length?args:[options.now??NOW]));}static now(){return options.now??NOW;}}
  const sandbox={URL,Intl,Date:FixedDate,AbortController,structuredClone,document:{baseURI:options.base||'https://dopagip00.github.io/gup/#overview'},localStorage,
    setTimeout:(fn,ms)=>setTimeout(fn,ms===9000?20:0),clearTimeout,
    fetch:async(url,init)=>{calls.push(url.href);if(fetchImpl)return fetchImpl(url,init,fixtures);const value=fixtures[url.pathname];return {ok:Boolean(value),status:value?200:404,json:async()=>structuredClone(value)};}};
  vm.runInNewContext(source,sandbox);
  return {api:sandbox.PagesResearch.api,calls,storage:localStorage,setFetch:fn=>{fetchImpl=fn;}};
}
const response=value=>({ok:true,status:200,json:async()=>structuredClone(value)});
test('an ungenerated selected day shows the actual latest day without requesting a missing file or retaining buy prices',async()=>{
  const b=browser(),data=await b.api('/api/dashboard?date=2026-10-19');
  assert.equal(data.meta.snapshot_transport.mode,'live');assert.equal(data.meta.data_date,'2026-10-16');
  assert.equal(data.meta.report_selection.status,'unavailable');assert.equal(data.meta.report_selection.requested_date,'2026-10-19');assert.equal(data.meta.report_selection.actual_date,'2026-10-16');
  assert.match(data.meta.warnings.join(''),/2026-10-19.*尚未生成/);assert.equal(data.meta.daily_buy,null);
  assert.equal(data.stocks[0].eligible,false);assert.equal(data.stocks[0].entry_low,undefined);
  assert(!b.calls.some(url=>url.includes('/data/reports/2026-10-19.json')));
  assert(Object.keys(b.storage).some(key=>key.endsWith('dashboard:latest')));assert(!Object.keys(b.storage).some(key=>key.endsWith('dashboard:2026-10-19')));
});
test('a missing-day selection can restore only the explicitly labelled real latest snapshot offline',async()=>{
  const live=browser();await live.api('/api/dashboard?date=2026-10-19');
  const b=browser({storage:live.storage,fetch:async()=>{throw Error('offline');}}),data=await b.api('/api/dashboard?date=2026-10-19');
  assert.equal(data.meta.snapshot_transport.mode,'offline');assert.equal(data.meta.data_date,'2026-10-16');assert.equal(data.meta.report_selection.requested_date,'2026-10-19');assert.equal(data.stocks[0].eligible,false);
});
test('a listed report is fetched only for its real date and rejects a mismatched payload',async()=>{
  const b=browser(),data=await b.api('/api/dashboard?date=2026-10-16');
  assert.equal(data.meta.report_date,'2026-10-16');assert.equal(data.meta.report_selection,undefined);
  const bad=browser({fetch:async(url,init,fixtures)=>response(url.pathname.includes('/reports/2026-10-16.json')?{meta:{data_date:'2026-10-16',report_date:'2026-10-19'},stocks:[]}:fixtures[url.pathname])});
  await assert.rejects(bad.api('/api/dashboard?date=2026-10-16'),/历史报告日期无效/);
});
test('a report-list network error stays a real loading failure instead of being labelled a missing day',async()=>{
  const b=browser({fetch:async(url,init,fixtures)=>url.pathname.endsWith('reports.json')?{ok:false,status:503}:response(fixtures[url.pathname])});
  await assert.rejects(b.api('/api/dashboard?date=2026-10-19'),/503/);
  assert(!b.calls.some(url=>url.includes('/data/dashboard.json')));
});
test('a newer deployment can open the selected day only after the same epoch publishes its report',async()=>{
  const current={...manifest,generated_at:'2026-10-19T20:00:00+08:00',data_date:'2026-10-19'};
  const b=browser({now:Date.parse('2026-10-19T21:00:00+08:00'),fetch:async(url,init,fixtures)=>{
    if(url.pathname.endsWith('manifest.json'))return response(current);
    if(url.pathname.endsWith('reports.json'))return response([{date:'2026-10-19',count:1},{date:'2026-10-16',count:1}]);
    if(url.pathname.endsWith('/reports/2026-10-19.json'))return response({meta:{data_date:'2026-10-19',report_date:'2026-10-19'},stocks:[]});
    return response(fixtures[url.pathname]);
  }});
  const data=await b.api('/api/dashboard?date=2026-10-19');assert.equal(data.meta.data_date,'2026-10-19');assert.equal(data.meta.report_date,'2026-10-19');assert.equal(data.meta.report_selection,undefined);
  assert.equal(data.meta.snapshot_transport.generated_at,current.generated_at);
});
test('a lagging archive list cannot label an existing latest market day as ungenerated',async()=>{
  const current={...manifest,generated_at:'2026-10-19T20:00:00+08:00',data_date:'2026-10-19'};
  const b=browser({now:Date.parse('2026-10-19T21:00:00+08:00'),fetch:async(url,init,fixtures)=>{
    if(url.pathname.endsWith('manifest.json'))return response(current);
    if(url.pathname.endsWith('dashboard.json'))return response({meta:{data_date:'2026-10-19',report_date:'2026-10-19'},stocks:[]});
    return response(fixtures[url.pathname]);
  }});
  const data=await b.api('/api/dashboard?date=2026-10-19');assert.equal(data.meta.data_date,'2026-10-19');assert.equal(data.meta.report_selection.status,'available');
  assert.equal(data.meta.report_selection.source,'latest');assert.doesNotMatch(data.meta.warnings.join(''),/尚未生成/);assert.equal(data.meta.daily_buy,null);
  assert(!b.calls.some(url=>url.includes('/data/reports/2026-10-19.json')));
});
test('archive discovery and report content stay in one deployment when the selected day is published mid-request',async()=>{
  const current={...manifest,generated_at:'2026-10-19T20:00:00+08:00',data_date:'2026-10-19'};let reads=0;
  const b=browser({now:Date.parse('2026-10-19T21:00:00+08:00'),fetch:async(url,init,fixtures)=>{
    if(url.pathname.endsWith('manifest.json'))return response(++reads<=2?manifest:current);
    const latest=url.searchParams.get('v')===current.generated_at;
    if(url.pathname.endsWith('reports.json'))return response(latest?[{date:'2026-10-19',count:1}]:fixtures[url.pathname]);
    if(url.pathname.endsWith('dashboard.json'))return response(latest?{meta:{data_date:'2026-10-19',report_date:'2026-10-19'},stocks:[]}:fixtures[url.pathname]);
    if(url.pathname.endsWith('/reports/2026-10-19.json'))return response({meta:{data_date:'2026-10-19',report_date:'2026-10-19'},stocks:[]});
    return response(fixtures[url.pathname]);
  }});
  const data=await b.api('/api/dashboard?date=2026-10-19');assert.equal(data.meta.report_date,'2026-10-19');assert.equal(data.meta.report_selection,undefined);assert.equal(data.meta.snapshot_transport.generated_at,current.generated_at);
});
test('phone fetches only repository-relative public files and retries a transient failure',async()=>{
  let fails=1;
  const b=browser({fetch:async(url,init,fixtures)=>{if(url.pathname.endsWith('manifest.json')&&fails-- >0)return {ok:false,status:503};return response(fixtures[url.pathname]);}});
  const data=await b.api('/api/dashboard');
  assert.equal(data.meta.snapshot_transport.mode,'live');assert.equal(data.stocks[0].eligible,true);
  assert(b.calls.every(url=>url.startsWith('https://dopagip00.github.io/gup/data/')));
  assert(b.calls.filter(url=>url.includes('manifest.json')).length>=3);
});
test('a complete persisted snapshot reloads offline with no buy eligibility or prices',async()=>{
  const live=browser();await live.api('/api/dashboard');
  const offline=browser({storage:live.storage,fetch:async()=>{throw new TypeError('offline');}});
  const data=await offline.api('/api/dashboard');
  assert.equal(data.meta.snapshot_transport.mode,'offline');assert.equal(data.meta.daily_buy,null);
  assert.equal(data.stocks[0].eligible,false);assert.equal(data.stocks[0].prediction.eligible,false);
  assert.equal(data.stocks[0].entry_low,undefined);assert.equal(data.stocks[0].stale,true);
  assert.match(data.meta.warnings.join(''),/离线研究缓存/);
});
test('cached daily plan and stock details never keep a live buy',async()=>{
  const live=browser();await live.api('/api/daily-plan');await live.api('/api/stock?symbol=sh601811');
  const offline=browser({storage:live.storage,fetch:async()=>{throw Error('offline');}});
  const plan=await offline.api('/api/daily-plan'),detail=await offline.api('/api/stock?symbol=sh601811');
  assert.equal(plan.buy,null);assert.equal(plan.status,'offline');assert.equal(detail.stock.eligible,false);assert.equal(detail.stock.entry_low,undefined);
});
test('cached eligibility can recover only after a fresh successful network snapshot',async()=>{
  const b=browser();await b.api('/api/dashboard');
  b.setFetch(async()=>{throw Error('offline');});await b.api('/api/refresh',{method:'POST'});
  const offline=await b.api('/api/dashboard');assert.equal(offline.stocks[0].eligible,false);
  b.setFetch(null);await b.api('/api/refresh',{method:'POST'});
  const recovered=await b.api('/api/dashboard');assert.equal(recovered.meta.snapshot_transport.mode,'live');assert.equal(recovered.stocks[0].eligible,true);
});
test('invalid version, dates and cross-site cache are never accepted offline',async()=>{
  for(const change of [entry=>entry.schema=2,entry=>entry.manifest.snapshot_schema_version=2,entry=>entry.manifest.app_version='unknown',entry=>entry.manifest.data_schema_version=2,entry=>entry.manifest.generated_at='2026-10-01T09:00:00+08:00',entry=>entry.manifest.generated_at='2026-10-20T09:00:00+08:00',entry=>entry.manifest.data_date='2026-10-22',entry=>entry.base='https://other.invalid/gup/']){
    const live=browser();await live.api('/api/dashboard');const key=Object.keys(live.storage)[0],entry=JSON.parse(live.storage[key]);change(entry);live.storage[key]=JSON.stringify(entry);
    const offline=browser({storage:live.storage,fetch:async()=>{throw Error('offline');}});
    await assert.rejects(offline.api('/api/dashboard'),/offline/);
  }
});
test('an incomplete bundle is never stored for offline use',async()=>{
  const live=browser({fetch:async(url,init,fixtures)=>url.pathname.endsWith('calendar.json')?{ok:false,status:404}:response(fixtures[url.pathname])});
  await assert.rejects(live.api('/api/dashboard'),/404/);assert.equal(Object.keys(live.storage).length,0);
});
test('a malformed or mismatched successful response is never saved as a complete snapshot',async()=>{
  for(const [file,value,path] of [
    ['calendar.json',null,'/api/dashboard'],
    ['dashboard.json',{meta:{data_date:'2026-10-15'},stocks:[]},'/api/dashboard'],
    ['sh601811.json',{stock:{symbol:'sh688305',date:'2026-10-16'}},'/api/stock?symbol=sh601811'],
    ['daily-plan.json',{date:'2026-10-20',buy:{}},'/api/daily-plan']
  ]){
    const b=browser({fetch:async(url,init,fixtures)=>response(url.pathname.endsWith(file)?value:fixtures[url.pathname])});
    await assert.rejects(b.api(path),/无效|不一致/);assert.equal(Object.keys(b.storage).length,0);
  }
});
test('a deployment changing during fetch discards all files from the previous epoch',async()=>{
  let manifests=0;
  const next={...manifest,generated_at:'2026-10-19T09:06:00+08:00'};
  const b=browser({fetch:async(url,init,fixtures)=>{
    if(url.pathname.endsWith('manifest.json'))return response(++manifests===1?manifest:next);
    const value=structuredClone(fixtures[url.pathname]);
    if(url.pathname.endsWith('dashboard.json'))value.stocks[0].name=url.searchParams.get('v')===next.generated_at?'new':'old';
    return response(value);
  }});
  const data=await b.api('/api/dashboard');assert.equal(data.stocks[0].name,'new');assert.equal(data.meta.snapshot_transport.generated_at,next.generated_at);
});
test('a network timeout is bounded and does not create fake cached data',async()=>{
  const b=browser({fetch:(url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(Object.assign(Error('abort'),{name:'AbortError'}))))});
  await assert.rejects(b.api('/api/dashboard'),/超时/);assert.equal(b.calls.length,2);assert.equal(Object.keys(b.storage).length,0);
});
test('a snapshot older than the current close stays non-actionable in offline cache',async()=>{
  const live=browser();await live.api('/api/dashboard');
  const offline=browser({now:Date.parse('2026-10-19T17:00:00+08:00'),storage:live.storage,fetch:async()=>{throw Error('offline');}});
  const data=await offline.api('/api/dashboard');assert.equal(data.meta.daily_buy,null);assert.equal(data.stocks[0].eligible,false);
});
test('byte budget preserves latest dashboard and daily plan through several large detail snapshots',async()=>{
  const store=storage(3*1024*1024),b=browser({storage:store,fetch:async(url,init,fixtures)=>{
    const value=structuredClone(fixtures[url.pathname]||fixtures['/gup/data/stocks/sh601811.json']);
    if(url.pathname.endsWith('dashboard.json'))value.meta.padding='x'.repeat(650000);
    if(url.pathname.startsWith('/gup/data/stocks/')){value.stock.symbol=url.pathname.slice(-13,-5);value.padding='x'.repeat(1000000);}
    return response(value);
  }});
  await b.api('/api/dashboard');await b.api('/api/daily-plan');
  for(const symbol of ['sh600241','sh601811','sh688305'])await b.api('/api/stock?symbol='+symbol);
  const bytes=Object.keys(store).reduce((sum,key)=>sum+2*(key.length+store[key].length),0);assert(bytes<=3*1024*1024);
  const offline=browser({storage:store,fetch:async()=>{throw Error('offline');}});
  assert.equal((await offline.api('/api/dashboard')).meta.snapshot_transport.mode,'offline');
  assert.equal((await offline.api('/api/daily-plan')).status,'offline');
});
test('real quota errors evict only owned older entries and retry before abandoning a new dashboard',async()=>{
  const store=storage(1800000);store['unrelated-user-setting']='keep';
  const b=browser({storage:store,fetch:async(url,init,fixtures)=>{
    const value=structuredClone(fixtures[url.pathname]);
    if(url.pathname.endsWith('longterm.json'))value.padding='x'.repeat(480000);
    if(url.pathname.endsWith('dashboard.json'))value.meta.padding='x'.repeat(550000);
    return response(value);
  }});
  await b.api('/api/longterm');await b.api('/api/daily-plan');await b.api('/api/dashboard');
  assert(store.quotaFailures>0);assert.equal(store['unrelated-user-setting'],'keep');
  const offline=browser({storage:store,fetch:async()=>{throw Error('offline');}});
  assert.equal((await offline.api('/api/dashboard')).meta.snapshot_transport.mode,'offline');
  assert.equal((await offline.api('/api/daily-plan')).status,'offline');
});
test('an individually oversized cache entry is skipped without breaking live results or protected records',async()=>{
  const store=storage(3*1024*1024),b=browser({storage:store});await b.api('/api/dashboard');await b.api('/api/daily-plan');
  b.setFetch(async(url,init,fixtures)=>{const value=structuredClone(fixtures[url.pathname]);if(url.pathname.endsWith('sh601811.json'))value.padding='x'.repeat(1700000);return response(value);});
  assert.equal((await b.api('/api/stock?symbol=sh601811')).snapshot_transport.mode,'live');
  const offline=browser({storage:store,fetch:async()=>{throw Error('offline');}});
  assert.equal((await offline.api('/api/dashboard')).meta.snapshot_transport.mode,'offline');assert.equal((await offline.api('/api/daily-plan')).status,'offline');
  await assert.rejects(offline.api('/api/stock?symbol=sh601811'),/offline/);
});

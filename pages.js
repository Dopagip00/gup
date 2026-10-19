/* Public snapshots work independently of a local computer. Cache is research-only. */
(function(root){
  'use strict';
  const CACHE_SCHEMA=3, CACHE_MAX_AGE=7*86400000, MAX_CACHE_ENTRIES=6;
  const REQUEST_TIMEOUT=9000, ATTEMPTS=2;
  function clock(now=new Date()){
    const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(now);
    const p=Object.fromEntries(parts.map(x=>[x.type,x.value]));
    return {date:`${p.year}-${p.month}-${p.day}`,time:`${p.hour}:${p.minute}`};
  }
  function trading(date,calendars){
    const cal=calendars[date.slice(0,4)];
    if(!cal || cal.verified!==true || !Array.isArray(cal.holidays)) return null;
    const weekday=new Date(date+'T12:00:00+08:00').getUTCDay();
    return weekday!==0 && weekday!==6 && !cal.holidays.includes(date);
  }
  function context(calendars,now=new Date()){
    const c=clock(now),open=trading(c.date,calendars);
    let date=c.date;
    if(open===null)return {...c,open,target:null};
    if(open===true && c.time>='16:00')return {...c,open,target:date};
    for(let i=0;i<370;i++){
      const d=new Date(date+'T12:00:00+08:00');d.setUTCDate(d.getUTCDate()-1);date=d.toISOString().slice(0,10);
      const valid=trading(date,calendars);
      if(valid===null)return {...c,open,target:null};
      if(valid)return {...c,open,target:date};
    }
    return {...c,open,target:null};
  }
  function expire(stock,reason='快照已落后于当前完整交易日或日历不可验证，暂停价格建议。'){
    if(!stock)return stock;
    stock.stale=true;stock.eligible=false;
    if(stock.prediction)stock.prediction.eligible=false;
    for(const k of ['entry_low','entry_high','stop_loss','take_profit','research_levels','reference_levels','levels'])delete stock[k];
    stock.warnings=[...(stock.warnings||[]),reason];
    return stock;
  }
  function gatePlan(plan,c){
    if(c.open===false)return {date:c.date,status:'closed',buy:null,sell:[],news:[],timezone:'Asia/Shanghai',message:'上交所非交易日，无当日买卖计划。'};
    if(c.open!==true || plan.date!==c.date)return {date:c.date,status:'pending',buy:null,sell:[],news:[],timezone:'Asia/Shanghai',message:c.open===null?'当前年度交易日历尚未核验，暂停新建议。':'今日09:00日报尚未发布，或云端任务延迟/失败；请查看运行记录。'};
    return plan;
  }
  function gateDashboard(data,c,plan){
    const m=data.meta||{};
    m.now=new Date().toISOString();m.is_trading=false;
    m.daily_buy=plan.buy||null;m.daily_plan_status=plan.status;m.daily_plan_message=plan.message||'';
    if(!c.target || m.data_date!==c.target){
      m.stale=true;m.warnings=[...(m.warnings||[]),'云端快照未覆盖当前完整交易日，请等待更新。'];
      expire(data.index);(data.stocks||[]).forEach(x=>expire(x));
      m.daily_buy=null;m.daily_plan_status='pending';
    }
    return data;
  }
  function baseUrl(){return new URL('./',document.baseURI);}
  function validDate(value){return typeof value==='string' && /^\d{4}-\d\d-\d\d$/.test(value) && Number.isFinite(Date.parse(value+'T12:00:00Z')) && new Date(value+'T12:00:00Z').toISOString().slice(0,10)===value;}
  function validManifest(m,now=Date.now(),cached=false){
    if(!m || typeof m!=='object' || m.repository!=='Dopagip00/gup' || m.privacy!=='public-market-research-only')return false;
    if(typeof m.generated_at!=='string' || !/^\d{4}-\d\d-\d\dT.+(?:Z|[+-]\d\d:\d\d)$/.test(m.generated_at))return false;
    const generated=Date.parse(m.generated_at);
    if(!Number.isFinite(generated) || generated>now+300000 || !validDate(m.data_date) || m.data_date>clock(new Date(generated)).date)return false;
    if(cached && (m.snapshot_schema_version!==1 || !/^\d+\.\d+\.\d+(?:[-+][\w.-]+)?$/.test(m.app_version||'') || m.data_schema_version!==1 || now-generated>CACHE_MAX_AGE))return false;
    return true;
  }
  async function fetchJson(path){
    let last;
    for(let attempt=0;attempt<ATTEMPTS;attempt++){
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),REQUEST_TIMEOUT);
      try{
        const response=await fetch(new URL(path,baseUrl()),{cache:'no-store',signal:controller.signal});
        if(!response.ok){const error=new Error(`研究文件暂不可用（${response.status}）`);error.retryable=response.status>=500||response.status===408||response.status===429;throw error;}
        return await response.json();
      }catch(error){last=error;if(error.retryable===false)break;}
      finally{clearTimeout(timer);}
      if(attempt+1<ATTEMPTS)await new Promise(resolve=>setTimeout(resolve,200));
    }
    throw new Error(last?.name==='AbortError'?'公开快照读取超时，请重试。':last?.message||'公开快照读取失败');
  }
  let manifest=null,checked=0,checking=null;
  const files=new Map(),memory=new Map();
  async function currentManifest(force=false){
    if(force || !manifest || Date.now()-checked>15000){
      if(!checking)checking=fetchJson('data/manifest.json').then(m=>{
        if(!validManifest(m))throw new Error('公开快照清单或日期无效');
        if(manifest?.generated_at!==m.generated_at)files.clear();
        manifest=m;checked=Date.now();return m;
      }).finally(()=>{checking=null;});
      return checking;
    }
    return manifest;
  }
  function storageKey(key){return `gup:research-only:${CACHE_SCHEMA}:${baseUrl().href}:${key}`;}
  function validateBundle(paths,data,m){
    for(const path of paths){
      const value=data[path];
      if(!value || typeof value!=='object')throw new Error('公开研究文件结构无效');
      if(path==='data/calendar.json' && Array.isArray(value))throw new Error('交易日历结构无效');
      if(path==='data/dashboard.json' && (!Array.isArray(value.stocks) || value.meta?.data_date!==m.data_date))throw new Error('行情日期与公开快照清单不一致');
      if(path.startsWith('data/reports/') && (!Array.isArray(value.stocks) || !validDate(value.meta?.data_date) || value.meta.data_date>m.data_date))throw new Error('历史报告日期无效');
      if(path==='data/daily-plan.json' && (!validDate(value.date) || value.date>clock(new Date(m.generated_at)).date || (value.sell!==undefined&&!Array.isArray(value.sell))))throw new Error('日报日期或结构无效');
      if(path.startsWith('data/stocks/') && (value.stock?.symbol!==path.slice(-13,-5) || !validDate(value.stock?.date) || value.stock.date>m.data_date))throw new Error('个股代码或日期与快照不一致');
      if(path==='data/longterm.json' && (!Array.isArray(value.stocks) || !validDate(value.as_of) || value.as_of>m.data_date))throw new Error('长线研究日期或结构无效');
    }
  }
  function readCache(key,paths){
    let entry=memory.get(key);
    if(!entry)try{entry=JSON.parse(root.localStorage?.getItem(storageKey(key))||'null');}catch{}
    const now=Date.now();
    if(!entry || entry.schema!==CACHE_SCHEMA || entry.base!==baseUrl().href || !validManifest(entry.manifest,now,true) || !Number.isFinite(entry.saved_at) || entry.saved_at>now+300000 || now-entry.saved_at>CACHE_MAX_AGE || !entry.files || paths.some(path=>!Object.hasOwn(entry.files,path)))return null;
    try{validateBundle(paths,entry.files,entry.manifest);}catch{return null;}
    return structuredClone(entry);
  }
  function saveCache(key,m,data){
    if(!validManifest(m,Date.now(),true))return;
    const entry={schema:CACHE_SCHEMA,base:baseUrl().href,saved_at:Date.now(),manifest:structuredClone(m),files:structuredClone(data)};
    memory.delete(key);memory.set(key,entry);
    while(memory.size>MAX_CACHE_ENTRIES)memory.delete([...memory.keys()].find(x=>x!=='dashboard:latest'&&x!=='daily-plan')||memory.keys().next().value);
    try{
      const prefix=`gup:research-only:${CACHE_SCHEMA}:${baseUrl().href}:`;
      const keys=Object.keys(root.localStorage||{}).filter(x=>x.startsWith(prefix) && x!==storageKey(key)).sort((a,b)=>Number(a===storageKey('dashboard:latest')||a===storageKey('daily-plan'))-Number(b===storageKey('dashboard:latest')||b===storageKey('daily-plan')));
      while(keys.length>=MAX_CACHE_ENTRIES)root.localStorage.removeItem(keys.shift());
      root.localStorage?.setItem(storageKey(key),JSON.stringify(entry));
    }catch{} // Private browsing and full phone storage must not break live access.
  }
  async function bundle(paths,key){
    try{
      for(let attempt=0;attempt<2;attempt++){
        const m=await currentManifest(attempt>0),epoch=m.generated_at;
        const values=await Promise.all(paths.map(path=>{
          const cacheKey=`${epoch}:${path}`;
          if(!files.has(cacheKey))files.set(cacheKey,fetchJson(`${path}?v=${encodeURIComponent(epoch)}`).catch(error=>{files.delete(cacheKey);throw error;}));
          return files.get(cacheKey);
        }));
        // A Pages deployment can change while these files are in flight.
        const after=await currentManifest(true);
        if(after.generated_at!==epoch)continue;
        const data=Object.fromEntries(paths.map((path,i)=>[path,values[i]]));
        validateBundle(paths,data,m);
        saveCache(key,m,data);
        return {files:structuredClone(data),transport:{mode:'live',generated_at:epoch,data_date:m.data_date}};
      }
      throw new Error('公开快照正在更新，请重新读取。');
    }catch(error){
      const cached=readCache(key,paths);
      if(!cached)throw error;
      return {files:cached.files,transport:{mode:'offline',generated_at:cached.manifest.generated_at,data_date:cached.manifest.data_date,message:'公开网站快照暂时加载失败，当前为离线研究缓存；所有新买入资格已取消。',error:error.message}};
    }
  }
  function attach(data,transport){if(data && typeof data==='object')data.snapshot_transport=transport;return data;}
  function offlineDashboard(data,transport){
    data.meta=data.meta||{};data.meta.snapshot_transport=transport;
    if(transport.mode==='offline'){
      data.meta.daily_buy=null;data.meta.daily_plan_status='offline';data.meta.stale=true;
      data.meta.warnings=[...(data.meta.warnings||[]),transport.message];
      expire(data.index,transport.message);(data.stocks||[]).forEach(x=>expire(x,transport.message));
    }
    return data;
  }
  async function api(path,options={}){
    if(options.method && options.method!=='GET'){
      if(path==='/api/refresh'){checked=0;files.clear();return {status:'idle',message:'正在重新读取公开快照。采集与发布由云端计划执行。'};}
      throw new Error('静态网站的策略设置请在 GitHub 项目中修改并发布。');
    }
    const u=new URL(path,'https://research.invalid'),name=u.pathname.slice(5);
    if(name==='dashboard'){
      const date=u.searchParams.get('date');
      if(date&&!/^\d{4}-\d{2}-\d{2}$/.test(date))throw new Error('无效日期');
      const file=date?`data/reports/${date}.json`:'data/dashboard.json',paths=[file,'data/calendar.json','data/daily-plan.json'];
      const b=await bundle(paths,`dashboard:${date||'latest'}`),c=context(b.files[paths[1]]);
      return offlineDashboard(gateDashboard(b.files[file],c,gatePlan(b.files[paths[2]],c)),b.transport);
    }
    if(name==='stock'){
      const symbol=u.searchParams.get('symbol');if(!/^sh\d{6}$/.test(symbol||''))throw new Error('无效代码');
      const file=`data/stocks/${symbol}.json`,b=await bundle([file,'data/calendar.json'],`stock:${symbol}`),data=b.files[file];
      if(data.stock.date!==context(b.files['data/calendar.json']).target || b.transport.mode==='offline')expire(data.stock,b.transport.message);
      return attach(data,b.transport);
    }
    if(name==='daily-plan'){
      const b=await bundle(['data/daily-plan.json','data/calendar.json'],'daily-plan');
      const plan=gatePlan(b.files['data/daily-plan.json'],context(b.files['data/calendar.json']));
      if(b.transport.mode==='offline'){plan.buy=null;plan.status='offline';plan.stale=true;plan.message=b.transport.message;(plan.sell||[]).forEach(x=>{x.stale=true;});}
      return attach(plan,b.transport);
    }
    if(name==='longterm'){
      const b=await bundle(['data/longterm.json','data/calendar.json'],'longterm'),data=b.files['data/longterm.json'],c=context(b.files['data/calendar.json']);
      if(!c.target||data.as_of!==c.target||b.transport.mode==='offline'){data.warnings=[...(data.warnings||[]),b.transport.message||'长线快照未覆盖最近完整交易日。'];(data.stocks||[]).forEach(x=>{x.eligible=false;x.stale=true;});}
      return attach(data,b.transport);
    }
    if(!['config','system','reports','quotes','forecast-audit'].includes(name))throw new Error('不支持的静态接口');
    const file=`data/${name}.json`,b=await bundle([file],name),data=b.files[file];
    if(name==='quotes'&&b.transport.mode==='offline'){data.quotes=[];data.status=b.transport.message;}
    return attach(data,b.transport);
  }
  const apiObject={api,download:date=>/^\d{4}-\d{2}-\d{2}$/.test(date||'')?`./data/csv/${date}.csv`:'#',context,gatePlan,gateDashboard,validManifest};
  root.PagesResearch=apiObject;
  if(typeof module!=='undefined')module.exports=apiObject;
})(globalThis);


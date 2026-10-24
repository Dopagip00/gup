/* Public snapshots work independently of a local computer. Cache is research-only. */
(function(root){
  'use strict';
  const CACHE_SCHEMA=3, CACHE_MAX_AGE=7*86400000, MAX_CACHE_ENTRIES=6, CACHE_BYTE_BUDGET=3*1024*1024;
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
        if(!response.ok){const error=new Error(`研究文件暂不可用（${response.status}）`);error.httpStatus=response.status;error.retryable=response.status>=500||response.status===408||response.status===429;throw error;}
        return await response.json();
      }catch(error){last=error;if(error.retryable===false)break;}
      finally{clearTimeout(timer);}
      if(attempt+1<ATTEMPTS)await new Promise(resolve=>setTimeout(resolve,200));
    }
    throw Object.assign(new Error(last?.name==='AbortError'?'公开快照读取超时，请重试。':last?.message||'公开快照读取失败'),{httpStatus:last?.httpStatus});
  }
  let manifest=null,checked=0,checking=null;
  const files=new Map(),memory=new Map(),memoryBytes=new Map();
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
  function protectedCache(key){return key===storageKey('dashboard:latest')||key===storageKey('daily-plan')||key===storageKey('daily-watchlist');}
  function reportDates(value,m){
    if(!Array.isArray(value))throw new Error('历史报告清单结构无效');
    const dates=value.map(report=>typeof report==='string'?report:report?.date||report?.report_date);
    if(dates.some(date=>!validDate(date)||date>m.data_date))throw new Error('历史报告清单日期无效');
    return dates;
  }
  function cacheBytes(key,value){return (key.length+value.length)*2;}
  function cacheVictim(keys,incoming){
    return keys.find(key=>!protectedCache(key)) || (protectedCache(incoming)?keys.find(key=>key!==incoming):null);
  }
  function emptyBrief(date,status='unavailable'){
    return {protocol:'market-brief-v1',date,generated_at:null,status,items:[],coverage:[],warnings:['该日没有可核验的真实早间消息快照，未使用其他日期回填。']};
  }
  function validateBrief(value,m,path){
    const hosts=['www.sse.com.cn','static.sse.com.cn','finance.cctv.com','www.pbc.gov.cn','www.csrc.gov.cn','www.cls.cn','wallstreetcn.com'];
    const urlOk=value=>{try{const u=new URL(value);return u.protocol==='https:'&&hosts.includes(u.hostname)&&!u.username&&!u.password&&!u.search&&!u.hash&&(!u.port||u.port==='443');}catch{return false;}};
    const stamp=value=>typeof value==='string'&&/T.+(?:Z|[+-]\d\d:\d\d)$/.test(value)&&Number.isFinite(Date.parse(value));
    if(value.protocol!=='market-brief-v1'||!validDate(value.date)||!['complete','partial','unavailable','historical_unavailable','closed'].includes(value.status)
        ||!Array.isArray(value.items)||value.items.length>24||!Array.isArray(value.coverage)||value.coverage.length>8||!Array.isArray(value.warnings))throw Error('早间消息合同无效');
    if(path.startsWith('data/market-briefs/')&&value.date!==path.slice(-15,-5))throw Error('消息档案日期不一致');
    const generated=Date.parse(value.generated_at),limit=Date.parse(m.generated_at);
    if(value.generated_at!==null&&(!stamp(value.generated_at)||generated>limit||clock(new Date(generated)).date!==value.date))throw Error('消息采集时间无效');
    if(value.items.length&&!stamp(value.generated_at)||value.status==='complete'&&!value.items.length)throw Error('消息条目缺少真实收集时间');
    for(const item of value.items){
      if(!item||typeof item.title!=='string'||item.title.length>250||!urlOk(item.url)||!stamp(item.collected_at)||Date.parse(item.collected_at)>generated
          ||clock(new Date(item.collected_at)).date!==value.date||!['day','minute','second'].includes(item.published_precision))throw Error('消息条目来源或收集时间无效');
      if(item.published_precision==='day'){
        if(!validDate(item.published_at)||item.published_at>clock(new Date(item.collected_at)).date)throw Error('消息发布时间来自未来');
      }else if(!stamp(item.published_at)||Date.parse(item.published_at)>Date.parse(item.collected_at))throw Error('消息发布时间无效');
    }
    for(const source of value.coverage){
      if(!source||!urlOk(source.url)||!['complete','partial','error'].includes(source.status)||!Number.isInteger(source.count)||source.count<0||source.count>10000
          ||source.collected_at!==null&&(!stamp(source.collected_at)||Date.parse(source.collected_at)>generated||clock(new Date(source.collected_at)).date!==value.date))throw Error('消息来源覆盖无效');
    }
  }
  function validateBundle(paths,data,m){
    for(const path of paths){
      const value=data[path];
      if(!value || typeof value!=='object')throw new Error('公开研究文件结构无效');
      if(path==='data/market-brief.json'||path.startsWith('data/market-briefs/'))validateBrief(value,m,path);
      if(path==='data/calendar.json' && Array.isArray(value))throw new Error('交易日历结构无效');
      if(path==='data/dashboard.json' && (!Array.isArray(value.stocks) || value.meta?.data_date!==m.data_date))throw new Error('行情日期与公开快照清单不一致');
      if(path==='data/reports.json')reportDates(value,m);
      if(path.startsWith('data/reports/') && (!Array.isArray(value.stocks) || !validDate(value.meta?.data_date) || value.meta.data_date>m.data_date || value.meta?.report_date!==path.slice(-15,-5)))throw new Error('历史报告日期无效');
      if(path==='data/daily-plan.json' && (!validDate(value.date) || value.date>clock(new Date(m.generated_at)).date || (value.sell!==undefined&&!Array.isArray(value.sell))))throw new Error('日报日期或结构无效');
      if(path==='data/daily-watchlist.json'||path.startsWith('data/watchlists/')){
        if(value.protocol!=='daily-observation-v1'||!validDate(value.date)||!Array.isArray(value.items)||value.items.length>5||value.model_validated!==false||value.actionable!==false)throw new Error('独立观察榜协议或结构无效');
        if(value.data_date!==null&&(!validDate(value.data_date)||value.data_date>m.data_date))throw new Error('观察榜引用了快照以后的行情');
        if(path.startsWith('data/watchlists/')&&value.date!==path.slice(-15,-5))throw new Error('观察榜历史日期不一致');
      }
      if(path.startsWith('data/stocks/') && (value.stock?.symbol!==path.slice(-13,-5) || !validDate(value.stock?.date) || value.stock.date>m.data_date))throw new Error('个股代码或日期与快照不一致');
      if(path==='data/longterm.json' && (!Array.isArray(value.stocks) || !validDate(value.as_of) || value.as_of>m.data_date))throw new Error('长线研究日期或结构无效');
    }
  }
  function readCache(key,paths){
    let entry=memory.get(key);
    if(!entry)try{
      const raw=root.localStorage?.getItem(storageKey(key));
      if(raw && cacheBytes(storageKey(key),raw)>CACHE_BYTE_BUDGET){root.localStorage.removeItem(storageKey(key));return null;}
      entry=JSON.parse(raw||'null');
    }catch{}
    const now=Date.now();
    if(!entry || entry.schema!==CACHE_SCHEMA || entry.base!==baseUrl().href || !validManifest(entry.manifest,now,true) || !Number.isFinite(entry.saved_at) || entry.saved_at>now+300000 || now-entry.saved_at>CACHE_MAX_AGE || !entry.files || paths.some(path=>!Object.hasOwn(entry.files,path)))return null;
    try{validateBundle(paths,entry.files,entry.manifest);}catch{return null;}
    return structuredClone(entry);
  }
  function saveCache(key,m,data){
    if(!validManifest(m,Date.now(),true))return;
    const entry={schema:CACHE_SCHEMA,base:baseUrl().href,saved_at:Date.now(),manifest:structuredClone(m),files:structuredClone(data)};
    const incoming=storageKey(key),serialized=JSON.stringify(entry),size=cacheBytes(incoming,serialized);
    memory.delete(key);memoryBytes.delete(key);
    if(size>CACHE_BYTE_BUDGET){try{root.localStorage?.removeItem(incoming);}catch{}return;}
    memory.set(key,entry);memoryBytes.set(key,size);
    while(memory.size>MAX_CACHE_ENTRIES || [...memoryBytes.values()].reduce((a,b)=>a+b,0)>CACHE_BYTE_BUDGET){
      const victim=cacheVictim([...memory.keys()].map(storageKey),incoming);
      if(!victim)break;
      const name=[...memory.keys()].find(item=>storageKey(item)===victim);memory.delete(name);memoryBytes.delete(name);
    }
    try{
      const prefix=`gup:research-only:${CACHE_SCHEMA}:${baseUrl().href}:`;
      const records=new Map();
      for(const existing of Object.keys(root.localStorage||{}).filter(x=>x.startsWith(prefix)&&x!==incoming)){
        const raw=root.localStorage.getItem(existing)||'',bytes=cacheBytes(existing,raw);
        if(bytes>CACHE_BYTE_BUDGET){root.localStorage.removeItem(existing);continue;}
        let saved=0;try{saved=JSON.parse(raw).saved_at||0;}catch{}
        records.set(existing,{bytes,saved});
      }
      const ordered=[...records.keys()].sort((a,b)=>records.get(a).saved-records.get(b).saved);
      records.set(incoming,{bytes:size,saved:Date.now()});ordered.push(incoming);
      while(records.size>MAX_CACHE_ENTRIES || [...records.values()].reduce((sum,x)=>sum+x.bytes,0)>CACHE_BYTE_BUDGET){
        const victim=cacheVictim(ordered.filter(x=>records.has(x)),incoming);
        if(!victim)break;
        records.delete(victim);root.localStorage.removeItem(victim);
      }
      if(!records.has(incoming))return;
      // Browser/site quota can be lower than our budget or shared with other data.
      // Each failed attempt removes at most one owned entry; never touch other keys.
      for(let attempt=0,limit=records.size;attempt<limit;attempt++){
        try{root.localStorage?.setItem(incoming,serialized);return;}
        catch(error){
          if(!['QuotaExceededError','NS_ERROR_DOM_QUOTA_REACHED'].includes(error?.name)&&![22,1014].includes(error?.code))return;
          const victim=cacheVictim(ordered.filter(x=>records.has(x)&&x!==incoming),incoming);
          if(!victim)return;records.delete(victim);root.localStorage.removeItem(victim);
        }
      }
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
        return {files:structuredClone(data),transport:{mode:'live',generated_at:epoch,data_date:m.data_date},manifest:m};
      }
      throw new Error('公开快照正在更新，请重新读取。');
    }catch(error){
      const cached=readCache(key,paths);
      if(!cached)throw error;
      return {files:cached.files,transport:{mode:'offline',generated_at:cached.manifest.generated_at,data_date:cached.manifest.data_date,message:'公开网站快照暂时加载失败，当前为离线研究缓存；所有新买入资格已取消。',error:error.message},manifest:cached.manifest};
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
  function gateWatchlist(value,calendars,requested=null,now=new Date()){
    const data=structuredClone(value),today=clock(now),release=Date.parse(data.release_at),generated=Date.parse(data.generated_at);
    const hide=(status,message)=>({...data,status,items:[],top_pick:null,eligible:false,actionable:false,model_validated:false,message});
    if(trading(data.date,calendars)!==true)return hide('closed','交易日历尚未核准或该日休市，不揭示观察榜。');
    if(!Number.isFinite(release)||clock(new Date(release)).date!==data.date||clock(new Date(release)).time!=='09:00'||!Number.isFinite(generated)||generated>now.getTime()+300000)return hide('unavailable','观察榜准备或揭榜时间无效。');
    if(requested&&requested!==data.date)return hide('unavailable','所选观察档案日期无效。');
    if(!requested&&data.date<today.date)return hide('pending','今日观察榜尚未准备；旧榜保留在历史档案，不改标今日。');
    if(now.getTime()<release)return hide('prepared',`${data.date} 的观察榜已预备，09:00按实际客户端时钟揭榜；服务器尚未在09:00发布。`);
    const target=context(calendars,new Date(data.date+'T09:00:00+08:00')).target;
    if(['watch_only','cash'].includes(data.status)&&data.data_date!==target)return hide('insufficient_data','观察榜行情未达到该日盘前完整交易日，不改标数据。');
    data.eligible=false;data.actionable=false;data.model_validated=false;
    for(const item of data.items){item.eligible=false;item.actionable=false;item.model_validated=false;for(const key of ['entry_low','entry_high','stop_loss','take_profit','max_position_pct'])item[key]=null;}
    data.top_pick=data.items[0]||null;
    if(!data.published_at&&['watch_only','cash'].includes(data.status)){
      data.release_mode='prepublished_client_reveal';data.revealed_at=now.toISOString();
      data.message='此观察榜已在盘前预先准备，现按客户端北京时间09:00揭榜；published_at仍为空，不冒充服务器准点发布。';
    }else if(data.published_at&&(!Number.isFinite(Date.parse(data.published_at))||Date.parse(data.published_at)<release||Date.parse(data.published_at)>now.getTime()+300000))return hide('unavailable','服务器实际发布时间无效，拒绝冒充已发布。');
    if(requested&&requested<today.date)data.historical=true;
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
      if(date&&!validDate(date))throw new Error('无效日期');
      let available=true;
      if(date){const index=await bundle(['data/reports.json'],'reports');available=reportDates(index.files['data/reports.json'],index.manifest).includes(date);}
      for(let attempt=0;attempt<2;attempt++){
        const file=date&&available?`data/reports/${date}.json`:'data/dashboard.json';
        const paths=[file,'data/calendar.json','data/daily-plan.json',...(date?['data/reports.json']:[])];
        // Missing dates reuse the latest real snapshot and its real cache key.
        const b=await bundle(paths,`dashboard:${date&&available?date:'latest'}`),c=context(b.files['data/calendar.json']);
        if(date){
          const listed=reportDates(b.files['data/reports.json'],b.manifest).includes(date);
          if(listed!==available){available=listed;continue;}
        }
        const data=gateDashboard(b.files[file],c,gatePlan(b.files['data/daily-plan.json'],c));
        if(date&&!available && data.meta.report_date===date && data.meta.data_date===date){
          data.meta.report_selection={status:'available',requested_date:date,actual_date:date,source:'latest'};
          data.meta.warnings=['所选行情已存在于最新真实快照；历史档案清单仍在更新，当前仅作历史复盘。',...(data.meta.warnings||[])];
          data.meta.daily_buy=null;data.meta.daily_plan_status='history';
          expire(data.index,'当前选择为历史复盘，不作为新买入建议。');(data.stocks||[]).forEach(stock=>expire(stock,'当前选择为历史复盘，不作为新买入建议。'));
        }else if(date&&!available){
          const actual=data.meta.report_date||data.meta.data_date;
          const reason=`${date} 的行情研究尚未生成；当前展示最近已生成的 ${actual} 行情，日期未替换。请点击“最新”重新读取。`;
          data.meta.report_selection={status:'unavailable',requested_date:date,actual_date:actual};
          data.meta.warnings=[reason,...(data.meta.warnings||[])];data.meta.daily_buy=null;data.meta.daily_plan_status='pending';
          expire(data.index,reason);(data.stocks||[]).forEach(stock=>expire(stock,reason));
        }
        return offlineDashboard(data,b.transport);
      }
      throw new Error('公开档案清单正在更新，请重新读取。');
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
    if(name==='daily-watchlist'){
      const date=u.searchParams.get('date');if(date&&!validDate(date))throw new Error('无效日期');
      const day=date||clock().date;let file=`data/watchlists/${day}.json`,b;
      try{b=await bundle([file,'data/calendar.json'],`daily-watchlist:${day}`);}
      catch(error){
        if(date||error.httpStatus!==404)throw error;
        // A missing current archive can use only the validated primary. Network
        // failures, malformed snapshots and explicit history never masquerade as 404.
        file='data/daily-watchlist.json';b=await bundle([file,'data/calendar.json'],'daily-watchlist');
      }
      const value=gateWatchlist(b.files[file],b.files['data/calendar.json'],date);
      if(b.transport.mode==='offline')value.warnings=[...(value.warnings||[]),b.transport.message];
      return attach(value,b.transport);
    }
    if(name==='market-brief'){
      const date=u.searchParams.get('date');if(date&&!validDate(date))throw Error('无效日期');
      const day=date||clock().date,file=date?`data/market-briefs/${day}.json`:'data/market-brief.json';
      let b;
      try{b=await bundle([file],`market-brief:${date||'latest'}`);}
      catch(error){if(error.httpStatus===404)return emptyBrief(day,date&&day<clock().date?'historical_unavailable':'unavailable');throw error;}
      const value=b.files[file];
      if(value.date!==day)return attach(emptyBrief(day,date&&day<clock().date?'historical_unavailable':'unavailable'),b.transport);
      if(b.transport.mode==='offline')value.warnings=[...(value.warnings||[]),b.transport.message];
      return attach(value,b.transport);
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
  const apiObject={api,download:date=>/^\d{4}-\d{2}-\d{2}$/.test(date||'')?`./data/csv/${date}.csv`:'#',context,gatePlan,gateDashboard,gateWatchlist,validManifest};
  root.PagesResearch=apiObject;
  if(typeof module!=='undefined')module.exports=apiObject;
})(globalThis);


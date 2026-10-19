/* Static Pages adapter. Re-check dates on the phone even when a workflow fails. */
(function(root){
  'use strict';
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
  function expire(stock){
    if(!stock)return stock;
    stock.stale=true;stock.eligible=false;
    if(stock.prediction)stock.prediction.eligible=false;
    for(const k of ['entry_low','entry_high','stop_loss','take_profit','research_levels','reference_levels','levels'])delete stock[k];
    stock.warnings=[...(stock.warnings||[]),'快照已落后于当前完整交易日或日历不可验证，暂停价格建议。'];
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
      expire(data.index);(data.stocks||[]).forEach(expire);
      m.daily_buy=null;m.daily_plan_status='pending';
    }
    return data;
  }
  async function fetchJson(path){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);
    try{const response=await fetch(new URL(path,document.baseURI),{cache:'no-store',signal:controller.signal});
      if(!response.ok)throw new Error(`研究文件暂不可用（${response.status}）`);return await response.json();
    }finally{clearTimeout(timer);}
  }
  let epoch='',checked=0,checking=null;
  const cached=new Map();
  async function read(path){
    if(Date.now()-checked>15000){
      if(!checking)checking=fetchJson('data/manifest.json').then(m=>{
        if(!m.generated_at)throw new Error('缺少快照生成时间');
        if(epoch!==m.generated_at){epoch=m.generated_at;cached.clear();}checked=Date.now();
      }).finally(()=>{checking=null;});
      await checking;
    }
    if(!cached.has(path))cached.set(path,fetchJson(`${path}?v=${encodeURIComponent(epoch)}`).catch(error=>{cached.delete(path);throw error;}));
    return structuredClone(await cached.get(path));
  }
  async function api(path,options={}){
    if(options.method && options.method!=='GET'){
      if(path==='/api/refresh')return {status:'idle',message:'已重新读取网站结果。数据采集由云端计划运行，此按钮不会触发采集。'};
      throw new Error('静态网站的策略设置请在 GitHub 项目中修改并发布。');
    }
    const u=new URL(path,'https://research.invalid'),name=u.pathname.slice(5);
    if(name==='dashboard'){
      const date=u.searchParams.get('date');
      if(date&&!/^\d{4}-\d{2}-\d{2}$/.test(date))throw new Error('无效日期');
      const [data,cal,rawPlan]=await Promise.all([read(date?`data/reports/${date}.json`:'data/dashboard.json'),read('data/calendar.json'),read('data/daily-plan.json')]);
      return gateDashboard(data,context(cal),gatePlan(rawPlan,context(cal)));
    }
    if(name==='stock'){
      const symbol=u.searchParams.get('symbol');if(!/^sh\d{6}$/.test(symbol||''))throw new Error('无效代码');
      const [data,cal]=await Promise.all([read(`data/stocks/${symbol}.json`),read('data/calendar.json')]);
      if(data.stock.date!==context(cal).target)expire(data.stock);return data;
    }
    if(name==='daily-plan'){
      const [plan,cal]=await Promise.all([read('data/daily-plan.json'),read('data/calendar.json')]);return gatePlan(plan,context(cal));
    }
    if(name==='longterm'){
      const [data,cal]=await Promise.all([read('data/longterm.json'),read('data/calendar.json')]);
      if(!context(cal).target||data.as_of!==context(cal).target){data.warnings=[...(data.warnings||[]),'长线快照未覆盖最近完整交易日。'];(data.stocks||[]).forEach(x=>{x.eligible=false;x.stale=true;});}return data;
    }
    if(!['config','system','reports','quotes','forecast-audit'].includes(name))throw new Error('不支持的静态接口');
    return read(`data/${name}.json`);
  }
  const apiObject={api,download:date=>/^\d{4}-\d{2}-\d{2}$/.test(date||'')?`./data/csv/${date}.csv`:'#',context,gatePlan,gateDashboard};
  root.PagesResearch=apiObject;
  if(typeof module!=='undefined')module.exports=apiObject;
})(globalThis);

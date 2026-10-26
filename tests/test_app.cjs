const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const path=require('node:path'),canonical=path.join(__dirname,'../../ashare-platform/static/app.js');
const source=fs.readFileSync(fs.existsSync(canonical)?canonical:path.join(__dirname,'../engine/static/app.js'),'utf8');
class Element{
  constructor(tag='div'){this.tag=tag;this.children=[];this.hidden=false;this.value='';this.checked=false;this.style={};this._text='';this.classList={toggle(){},add(){},remove(){}};}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(x=>x.textContent??String(x)).join('');}
  get childNodes(){return this.children;}
  append(...nodes){for(const node of nodes)node?.tag==='#fragment'?this.append(...node.children):this.children.push(node);}
  replaceChildren(...nodes){this._text='';this.children=[];this.append(...nodes);}
  setAttribute(){}addEventListener(){}after(){}prepend(...nodes){this.children.unshift(...nodes);}getBoundingClientRect(){return {width:0,height:0};}
  showModal(){this.open=true;}querySelectorAll(){return [];}querySelector(){return new Element();}
}
function app(options={}){
  const nodes=new Map(),document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);},createElement:tag=>new Element(tag),createTextNode:value=>Object.assign(new Element('#text'),{textContent:value}),createDocumentFragment:()=>new Element('#fragment')};
  document.getElementById('min-score').value='0';document.getElementById('stock-sort').value='prediction';
  class FixedDate extends Date{constructor(...args){super(...(args.length?args:[options.now??Date.now()]));}static now(){return options.now??Date.now();}}
  const sandbox={document,window:{PagesResearch:options.pages||{}},location:{hostname:'dopagip00.github.io'},structuredClone,URL,requestAnimationFrame:()=>{},Intl,Date:FixedDate,setTimeout,clearTimeout,fetch:()=>{throw Error('unexpected fetch');}};
  vm.runInNewContext(source.slice(0,source.lastIndexOf("document.querySelectorAll('[data-view]')"))+"\nglobalThis.ui={state,shortEligible,renderStocks,renderDashboard,loadDashboard,renderPrediction,renderAIReview,renderTopPick,renderDailyWatchlist,loadDailyWatchlist,renderObservationDay,renderMarketBrief,loadMarketBrief,disableDisconnectedResearch,openStock,setApi:fn=>api=fn,setRenderDetail:fn=>renderDetail=fn};",sandbox);
  return {ui:sandbox.ui,nodes,get:id=>document.getElementById(id)};
}
function stock(overrides={}){return {symbol:'sh601811',name:'技术分73的样本',score:73,date:'2026-10-16',eligible:false,prediction:{available:true,eligible:false,rank_value:null,expected_return_pct:5},entry_low:10,entry_high:11,stop_loss:9,take_profit:12,...overrides};}
function qualified(){return stock({eligible:true,chase_risk:{version:'anti-chase-v1',blocked:false,gate_passed:true},prediction:{model_version:'ridge-cycle-v3',validation_protocol:'purged-expanding-3x60-decision-v1',available:true,eligible:true,validation_decision:{protocol:'fixed-nonoverlap-positive-net-v1',eligible:true},rank_value:1,expected_return_pct:2}});}
test('technical diagnosis is shown as dated facts and never imported into a historical view',async()=>{
  const a=app(),s=stock({reasons:['已有规则依据']});
  a.ui.setApi(async()=>({stock:s,bars:[],technical_diagnosis:{status:'complete',sections:[{title:'技术指标',text:'MACD(12,26,9)',facts:[{label:'RSI14',value:'62.50'}]}]}}));
  await a.ui.openStock(s);
  assert.equal(a.get('detail-content').hidden,false);assert.equal(a.get('technical-diagnosis').hidden,false);
  assert.match(a.get('technical-diagnosis').textContent,/MACD\(12,26,9\).*RSI14.*62.50/);
  assert.equal(s.score,73);assert.equal(s.eligible,false);
  a.ui.state.historyDate='2026-10-16';await a.ui.openStock(s);
  assert.equal(a.get('technical-diagnosis').hidden,true);assert.equal(a.get('technical-diagnosis').textContent,'');
});
const WATCH_NOW=Date.parse('2026-10-20T09:15:00+08:00');
test('a weekend cash preparation reveals at nine with its decision reason and no invented server time',()=>{
  const pages=require('../pages.js'),cal={'2026':{verified:true,holidays:[]}};
  const cash=watchlist({date:'2026-10-26',data_date:'2026-10-23',signal_date:'2026-10-23',prepared_data_date:'2026-10-23',
    generated_at:'2026-10-23T21:25:00+08:00',prepared_at:'2026-10-23T21:25:00+08:00',release_at:'2026-10-26T09:00:00+08:00',
    published_at:null,publication_timing:'prepared',status:'cash',items:[],top_pick:null,eligible:false,actionable:false,message:'弱势市场 · 指数低于MA20与MA60'});
  const original=structuredClone(cash);
  for(const [clock,label] of [['2026-10-26T08:59:59+08:00','09:00 揭榜'],['2026-10-26T09:00:00+08:00','预备记录已揭榜']]){
    const now=Date.parse(clock),value=pages.gateWatchlist(cash,cal,null,new Date(now));
    value.snapshot_transport={mode:'live',generated_at:'2026-10-23T21:26:00+08:00',data_date:'2026-10-23'};
    const a=app({now,pages});a.ui.renderDailyWatchlist(value);
    assert.equal(a.get('recommendation-status').textContent,label);assert.equal(value.published_at,null);
    assert.equal(a.get('watchlist-body').children.length,0);assert.equal(a.get('watch-priority-metrics').hidden,true);
    if(clock.includes('09:00')){
      assert.equal(a.get('watch-priority-name').textContent,'空仓等待');
      assert.equal(a.get('watch-priority-reason').textContent,cash.message);
      assert.equal(value.message,cash.message);
    }
  }
  assert.deepEqual(cash,original);
  const next=pages.gateWatchlist(cash,cal,null,new Date('2026-10-27T09:00:00+08:00'));
  assert.equal(next.status,'pending');assert.equal(next.top_pick,null);
});
test('publication badge shows the actual minute even when the trading-window flag says on time',()=>{
  const a=app({now:WATCH_NOW}),value=watchlist({published_at:'2026-10-20T09:09:38+08:00'});
  a.ui.renderDailyWatchlist(value);assert.equal(a.get('recommendation-status').textContent,'09:09 发布');
  assert.equal(value.publication_timing,'on_time');
  a.ui.renderDailyWatchlist(watchlist({published_at:'2026-10-20T09:20:00+08:00'}));
  assert.equal(a.get('recommendation-status').textContent,'推荐待更新');
});
test('cash decision keeps the original previous-close basis after today closing data arrives',()=>{
  const a=app({now:Date.parse('2026-10-20T17:00:00+08:00')});
  a.ui.state.dashboard={meta:{data_date:'2026-10-20'}};
  a.ui.renderDailyWatchlist(watchlist({status:'cash',items:[],top_pick:null}));
  assert.match(a.get('research-dates').textContent,/本日榜单依据：2026-10-19收盘.*最新行情：2026-10-20/);
});
test('factor checks show failures and missing data without changing the score or leaking into history',async()=>{
  const a=app(),s=stock({reasons:['已有依据']});
  const factor_screen={symbol:s.symbol,as_of:s.date,groups:[{name:'财务与估值',status:'fail',items:[{label:'年度ROE',status:'fail',value:2,unit:'%',rule:'≥8%'},{label:'PE',status:'unknown',value:null,rule:'0 < PE ≤ 40'}]}],unavailable_dimensions:['实时资金流']};
  a.ui.setApi(async()=>({stock:s,bars:[],factor_screen}));await a.ui.openStock(s);
  assert.equal(a.get('factor-screen').hidden,false);
  assert.match(a.get('factor-screen').textContent,/年度ROE不符.*2\.00%.*PE缺数据/);
  assert.match(a.get('factor-screen').textContent,/未接入：实时资金流/);assert.equal(s.score,73);
  a.ui.state.historyDate=s.date;await a.ui.openStock(s);assert.equal(a.get('factor-screen').hidden,true);assert.equal(a.get('factor-screen').textContent,'');
  a.ui.state.historyDate='';factor_screen.as_of='2026-10-15';await a.ui.openStock(s);assert.equal(a.get('factor-screen').hidden,true);
});
test('weak-market overlay pauses current recommendations while preserving the original cohort and history',()=>{
  const a=app({now:WATCH_NOW}),data=watchlist();a.ui.state.dailyWatchlist=data;
  a.ui.state.dashboard={meta:{data_date:'2026-10-19',expected_date:'2026-10-19',main_wave_market:{protocol:'main-wave-daily-v1',as_of:'2026-10-19',regime:'weak'}}};
  const original=JSON.stringify(data);a.ui.renderDailyWatchlist(data);
  assert.equal(a.get('watch-priority-name').textContent,'空仓等待');assert.equal(a.get('watchlist-body').children.length,1);
  assert.match(a.get('watchlist-summary').textContent,/原始榜单/);assert.equal(JSON.stringify(data),original);
  a.ui.state.historyDate='2026-10-20';a.ui.renderDailyWatchlist(data);
  assert.match(a.get('watch-priority-name').textContent,/观察样本/);
});
test('a published cash decision is explicit and never fabricates a first pick',()=>{
  const a=app({now:WATCH_NOW});a.ui.renderDailyWatchlist(watchlist({status:'cash',items:[],top_pick:null,message:'空仓等待：弱势市场'}));
  assert.equal(a.get('watch-priority-name').textContent,'空仓等待');assert.equal(a.get('watchlist-body').children.length,0);
  assert.match(a.get('watch-priority-reason').textContent,/弱势市场/);
});
function watchlist(overrides={}){
  const first={symbol:'sh600000',name:'观察样本',asof:'2026-10-19',score:73.5,components:{technical:80,financial:63.75},reasons:['量价规则相对靠前','财务字段已取得'],risk_notes:['模型未通过正式买入验证','公告与AI未完整核验'],verification:{announcements_status:'unverified',session_status:'unverified',ai_status:'unverified'},eligible:false,actionable:false,entry_low:9876.54,entry_high:9999.99,stop_loss:8765.43,take_profit:12345.67};
  return {protocol:'daily-observation-v1',date:'2026-10-20',data_date:'2026-10-19',signal_date:'2026-10-19',generated_at:'2026-10-20T08:55:00+08:00',release_at:'2026-10-20T09:00:00+08:00',published_at:'2026-10-20T09:02:00+08:00',publication_timing:'on_time',late:false,status:'watch_only',model_validated:false,items:[first],top_pick:first,verification:first.verification,warnings:[],...overrides};
}
test('an unqualified observation can be visible without becoming a formal buy or showing any supplied trade price',()=>{
  const a=app({now:WATCH_NOW}),data=watchlist();a.ui.state.connected=true;a.ui.state.dashboard={meta:{mode:'manual_research'},stocks:[]};a.ui.renderTopPick([]);a.ui.renderDailyWatchlist(data);
  assert.equal(a.get('watchlist-body').children.length,1);assert.equal(a.get('watch-priority-name').textContent,'观察样本 · 600000.SH');assert.equal(a.get('watch-priority-score').textContent,'73.5 / 100');
  assert.equal(a.ui.state.topPick,null);assert.equal(a.ui.shortEligible(data.items[0]),false);assert.equal(a.get('pick-metrics').hidden,true);
  const displayed=a.get('watchlist-body').textContent+a.get('watch-priority-reason').textContent;for(const price of ['9876.54','9999.99','8765.43','12345.67'])assert(!displayed.includes(price));
  assert.doesNotMatch(a.get('watchlist-body').textContent,/未通过正式买入验证|非买入信号/);assert.equal(a.get('watchlist-summary').textContent,'1 只 · 综合排序');assert.equal(a.get('top-pick').hidden,true);
  assert.match(a.get('watch-priority-verification').textContent,/公告待补充.*停复牌待补充.*AI待补充/);
});
test('observation dates distinguish the assigned day, previous close, actual generation and a late release',()=>{
  const a=app({now:WATCH_NOW});a.ui.renderDailyWatchlist(watchlist({publication_timing:'late',late:true,published_at:'2026-10-20T09:12:00+08:00'}));
  const dates=a.get('watchlist-dates').textContent;assert.match(dates,/榜单所属：2026-10-20/);assert.match(dates,/截至前收盘：2026-10-19/);assert.match(dates,/实际生成：/);assert.match(dates,/实际发布：/);assert.match(dates,/迟到补发/);
});
test('prepared or future observations cannot expose their ranking before a real release',()=>{
  for(const [now,overrides] of [
    [Date.parse('2026-10-20T08:59:00+08:00'),{}],
    [WATCH_NOW,{published_at:null,publication_timing:'prepared'}],
    [WATCH_NOW,{date:'2026-10-21',release_at:'2026-10-21T09:00:00+08:00',published_at:null,publication_timing:'prepared'}],
    [WATCH_NOW,{published_at:'2026-10-20T09:20:00+08:00'}]
  ]){
    const a=app({now});a.ui.renderDailyWatchlist(watchlist(overrides));assert.equal(a.get('watchlist-body').children.length,0);assert.equal(a.get('watch-priority-metrics').hidden,true);assert.doesNotMatch(a.get('watch-priority-name').textContent,/观察样本/);assert.match(a.get('watchlist-empty').textContent,/尚未正式发布/);
  }
});
test('a genuine static gateway preparation reaches the app at nine without inventing server publication',()=>{
  const pages=require('../pages.js'),cal={'2026':{verified:true,holidays:[]}};
  const prepared=watchlist({generated_at:'2026-10-19T18:00:00+08:00',prepared_at:'2026-10-19T18:00:00+08:00',prepared_data_date:'2026-10-19',published_at:null,publication_timing:'prepared',late:false,eligible:false,actionable:false});
  const original=structuredClone(prepared);
  for(const [clock,count] of [['2026-10-20T08:59:59+08:00',0],['2026-10-20T09:00:00+08:00',1]]){
    const now=Date.parse(clock),gated=pages.gateWatchlist(prepared,cal,null,new Date(now));
    gated.snapshot_transport={mode:'live',generated_at:'2026-10-19T18:01:00+08:00',data_date:'2026-10-19'};
    const a=app({now,pages});a.ui.renderDailyWatchlist(gated);
    assert.equal(a.get('watchlist-body').children.length,count);
    assert.equal(gated.published_at,null);assert.equal(gated.generated_at,prepared.generated_at);assert.equal(gated.prepared_at,prepared.prepared_at);
    if(count){
      assert.equal(a.get('watch-priority-name').textContent,'观察样本 · 600000.SH');
      assert.match(a.get('watchlist-dates').textContent,/实际发布：尚未发布.*客户端揭榜/);
      assert.match(a.get('watchlist-alert').textContent,/服务器实际发布时间仍为空/);
      assert.equal(a.get('watchlist-summary').textContent,'1 只 · 综合排序');
      assert.equal(gated.items[0].entry_low,null);assert.equal(a.ui.shortEligible(gated.items[0]),false);
      gated.snapshot_transport.mode='offline';a.ui.renderDailyWatchlist(gated);assert.equal(a.get('watchlist-body').children.length,1);
      assert.match(a.get('watchlist-alert').textContent,/离线观察记录/);
    }
  }
  assert.deepEqual(prepared,original);
  const future=pages.gateWatchlist({...prepared,date:'2026-10-21',release_at:'2026-10-21T09:00:00+08:00'},cal,null,new Date(WATCH_NOW));
  const a=app({now:WATCH_NOW,pages});a.ui.renderDailyWatchlist(future);assert.equal(a.get('watchlist-body').children.length,0);
  const unknown=pages.gateWatchlist(prepared,{},null,new Date(WATCH_NOW));a.ui.renderDailyWatchlist(unknown);assert.equal(a.get('watchlist-body').children.length,0);
});
test('a forged client reveal marker cannot bypass genuine preparation and snapshot evidence',()=>{
  const pages=require('../pages.js'),cal={'2026':{verified:true,holidays:[]}};
  const prepared=watchlist({generated_at:'2026-10-19T18:00:00+08:00',prepared_at:'2026-10-19T18:00:00+08:00',prepared_data_date:'2026-10-19',published_at:null,publication_timing:'prepared',late:false,eligible:false,actionable:false});
  const shown=pages.gateWatchlist(prepared,cal,null,new Date(WATCH_NOW));
  shown.snapshot_transport={mode:'live',generated_at:'2026-10-19T18:01:00+08:00',data_date:'2026-10-19'};
  for(const mutate of [
    row=>delete row.prepared_at,
    row=>row.prepared_at='2026-10-19T18:00:00',
    row=>row.generated_at='2026-10-20T09:01:00+08:00',
    row=>row.release_at='2026-10-20T08:00:00+08:00',
    row=>row.revealed_at='2026-10-20T08:59:59+08:00',
    row=>row.prepared_data_date='2026-10-16',
    row=>row.snapshot_transport.generated_at='2026-10-20T10:00:00+08:00',
    row=>delete row.snapshot_transport,
    row=>row.actionable=true,
    row=>row.late=true
  ]){
    const bad=structuredClone(shown);mutate(bad);const a=app({now:WATCH_NOW,pages});a.ui.renderDailyWatchlist(bad);
    assert.equal(a.get('watchlist-body').children.length,0);assert.equal(bad.published_at,null);
  }
  const unsupported=app({now:WATCH_NOW});unsupported.ui.renderDailyWatchlist(shown);assert.equal(unsupported.get('watchlist-body').children.length,0);
});
test('old and selected historical observations never acquire today labels or substitute another day',()=>{
  const data=watchlist({date:'2026-10-19',data_date:'2026-10-16',signal_date:'2026-10-16',generated_at:'2026-10-19T08:55:00+08:00',release_at:'2026-10-19T09:00:00+08:00',published_at:'2026-10-19T09:02:00+08:00'});
  const latest=app({now:WATCH_NOW});latest.ui.renderDailyWatchlist(data);assert.match(latest.get('watchlist-title').textContent,/2026-10-19.*历史记录/);assert.match(latest.get('watchlist-alert').textContent,/尚未取得 2026-10-20/);assert.doesNotMatch(latest.get('watch-priority-label').textContent,/今日/);
  const selected=app({now:WATCH_NOW});selected.ui.state.historyDate='2026-10-19';selected.ui.renderDailyWatchlist(data);assert.equal(selected.get('watchlist-body').children.length,1);assert.match(selected.get('watchlist-title').textContent,/历史记录/);assert.match(selected.get('watchlist-dates').textContent,/截至前收盘：2026-10-16/);
  selected.ui.renderDailyWatchlist(watchlist());assert.equal(selected.get('watchlist-body').children.length,0);assert.match(selected.get('watchlist-empty').textContent,/请求 2026-10-19，取得记录属于 2026-10-20/);assert.equal(selected.get('watch-priority-metrics').hidden,true);
});
test('withdrawing the immutable original first observation never promotes a runner-up',()=>{
  const a=app({now:WATCH_NOW}),data=watchlist(),second={...data.items[0],symbol:'sh600001',name:'第二观察'};data.items.push(second);data.withdrawn_symbols=['sh600000'];data.alerts=[{symbol:'sh600000',reason:'新增风险公告',withdrawn:true}];a.ui.renderDailyWatchlist(data);
  assert.match(a.get('watch-priority-name').textContent,/今日首选已撤销/);assert.doesNotMatch(a.get('watch-priority-name').textContent,/第二观察/);assert.equal(a.get('watch-priority-metrics').hidden,true);assert.equal(a.get('watchlist-body').children.length,2);assert.match(a.get('watchlist-body').children[0].textContent,/已撤销/);assert.match(a.get('watchlist-alert').textContent,/新增风险公告/);
});
test('observation names, reasons and risks render only as text and absent finance stays unverified',()=>{
  const a=app({now:WATCH_NOW}),data=watchlist(),attack='<img src=x onerror=alert(1)>';data.items[0].name=attack;data.items[0].reasons=[attack];data.items[0].risk_notes=[attack];data.items[0].components.financial=null;a.ui.renderDailyWatchlist(data);
  const row=a.get('watchlist-body').children[0];assert.equal(row.children[1].children[0].textContent,attack);assert.equal(row.children[3].textContent,'数据见详情');assert.match(row.textContent,/财务 未核验/);assert.equal(a.get('watch-priority-reason').textContent,'数据见详情');assert(row.children[4].textContent.includes(attack));
  function tags(element){return [element.tag,...element.children.flatMap(child=>child?.children?tags(child):[])];}assert(!tags(row).includes('img'));
});
test('the recommendation card and table show brief source-backed facts while complete reasons stay in details',()=>{
  const a=app({now:WATCH_NOW}),data=watchlist(),item=data.items[0];
  item.components.return_5d_pct=-1.39;
  item.verification.financial_complete=true;item.verification.financial_data_date=item.asof;
  item.reasons=['原技术分94.2，固定风险扣分15.99后78.21；近5日-1.39%，偏离MA20+2.72%。','60日下行波动1.33%，最大回撤11.49%；市场状态防御。','已披露财务分80.0；PE(TTM)13.16，ROE17.88%。'];
  const original=structuredClone(data);a.ui.renderDailyWatchlist(data);
  const row=a.get('watchlist-body').children[0],summary='近5日-1.39% · PE13.16 · ROE17.88%';
  assert.equal(a.get('watch-priority-reason').textContent,summary);assert.equal(row.children[3].textContent,summary);
  assert.doesNotMatch(a.get('watch-priority-reason').textContent,/原技术分|固定风险扣分|60日/);
  const details=row.children[4].children[0];assert.equal(details.tag,'details');for(const reason of item.reasons)assert(details.textContent.includes(reason));
  assert.deepEqual(data,original);
  item.verification.financial_complete=false;item.verification.financial_quality_available=true;a.ui.renderDailyWatchlist(data);
  assert.equal(a.get('watch-priority-reason').textContent,'近5日-1.39% · ROE17.88%');
  item.verification.financial_quality_available=false;a.ui.renderDailyWatchlist(data);assert.equal(a.get('watch-priority-reason').textContent,'近5日-1.39%');
  item.components.return_5d_pct=Infinity;item.reasons=['PE(TTM)NaN，ROEInfinity%'];a.ui.renderDailyWatchlist(data);
  assert.equal(a.get('watch-priority-reason').textContent,'数据见详情');
});
test('hard risk or insufficient data keeps a genuine empty observation list without a fabricated first',()=>{
  const a=app({now:WATCH_NOW});a.ui.renderDailyWatchlist(watchlist({status:'insufficient_data',items:[],top_pick:null,warnings:['所有候选未通过追高风险或完整收盘检查']}));
  assert.equal(a.get('watchlist-body').children.length,0);assert.match(a.get('watchlist-empty').textContent,/追高风险或完整收盘检查/);assert.equal(a.get('watch-priority-metrics').hidden,true);assert.doesNotMatch(a.get('watch-priority-name').textContent,/观察样本/);
});
test('an absent old-version watchlist endpoint cannot disconnect a working dashboard',async()=>{
  const a=app({now:WATCH_NOW}),dashboard={meta:{data_date:'2026-10-19'},stocks:[]};a.ui.state.connected=true;a.ui.state.dashboard=dashboard;a.ui.setApi(async()=>{throw Error('404');});await a.ui.loadDailyWatchlist();
  assert.equal(a.ui.state.connected,true);assert.equal(a.ui.state.dashboard,dashboard);assert.equal(a.ui.state.dailyWatchlist,null);assert.match(a.get('watchlist-empty').textContent,/旧版快照可能尚未提供此功能/);assert.equal(a.get('watchlist-refresh').disabled,false);
});
test('late latest-watchlist responses cannot overwrite a newer selected historical request',async()=>{
  const a=app({now:WATCH_NOW}),pending=[],paths=[];a.ui.setApi(path=>{paths.push(path);return new Promise(resolve=>pending.push(resolve));});
  const first=a.ui.loadDailyWatchlist();a.ui.state.historyDate='2026-10-19';const second=a.ui.loadDailyWatchlist();const historical=watchlist({date:'2026-10-19',data_date:'2026-10-16',release_at:'2026-10-19T09:00:00+08:00',published_at:'2026-10-19T09:02:00+08:00'});
  pending[1](historical);await second;pending[0](watchlist());await first;
  assert.deepEqual(paths,['/api/daily-watchlist','/api/daily-watchlist?date=2026-10-19']);assert.equal(a.ui.state.dailyWatchlist.date,'2026-10-19');assert.match(a.get('watchlist-title').textContent,/2026-10-19.*历史记录/);
});
test('today recommendation retains its small assigned date while source dates remain truthful',()=>{
  const now=Date.parse('2026-10-21T14:30:00+08:00'),a=app({now});
  const data={meta:{data_date:'2026-10-20',report_date:'2026-10-20',snapshot_transport:{mode:'live',generated_at:'2026-10-21T14:00:00+08:00'}},index:{date:'2026-10-20'},stocks:[]};
  a.ui.state.dashboard=data;a.ui.renderDashboard(data);
  assert.equal(a.get('recommendation-date').textContent,'2026-10-21');assert.equal(a.get('recommendation-date-label').textContent,'北京时间');
  assert.match(a.get('research-dates').textContent,/行情日期：2026-10-20/);assert.equal(a.get('history-date').value,'');
  a.ui.state.historyDate='2026-10-20';a.ui.renderDashboard(data);
  assert.equal(a.get('recommendation-date').textContent,'2026-10-20');assert.match(a.get('recommendation-date-label').textContent,/历史推荐/);
  assert.equal(data.meta.data_date,'2026-10-20');assert.equal(data.index.date,'2026-10-20');
});
test('a current completed snapshot labels its two missing stocks without changing stale or eligibility',()=>{
  const a=app({now:Date.parse('2026-10-21T17:30:00+08:00')});
  const data={meta:{report_date:'2026-10-21',data_date:'2026-10-21',expected_date:'2026-10-21',stale:true,coverage:{total:2318,ok:2316,failed:2},warnings:['2只行情未取得']},index:{date:'2026-10-21',stale:false},stocks:[]};
  const before=structuredClone(data);a.ui.state.dashboard=data;a.ui.renderDashboard(data);
  assert.equal(a.get('global-alert-summary').textContent,'2只行情待补齐 · 查看记录');assert.deepEqual(data,before);
});
test('a genuinely lagging or unknown market date keeps the overdue wording and offline takes priority',()=>{
  const now=Date.parse('2026-10-21T17:30:00+08:00');
  const base={meta:{report_date:'2026-10-21',data_date:'2026-10-21',expected_date:'2026-10-21',stale:true,coverage:{total:2318,ok:2316,failed:2},warnings:['待补齐']},index:{date:'2026-10-21',stale:false},stocks:[]};
  for(const mutate of [
    row=>{row.meta.report_date='2026-10-20';row.meta.data_date='2026-10-20';row.index.date='2026-10-20';},
    row=>delete row.meta.expected_date,
    row=>row.meta.report_date='2026-10-20',
    row=>row.index.stale=true,
    row=>{row.meta.report_date=row.meta.data_date=row.meta.expected_date=row.index.date='2026-02-30';},
    row=>{row.meta.report_date=row.meta.data_date=row.meta.expected_date=row.index.date='2026-10-22';}
  ]){
    const data=structuredClone(base);mutate(data);const before=structuredClone(data),a=app({now});a.ui.state.dashboard=data;a.ui.renderDashboard(data);
    assert.equal(a.get('global-alert-summary').textContent,'行情尚未达到目标交易日');assert.deepEqual(data,before);
  }
  const offline=structuredClone(base);offline.meta.snapshot_transport={mode:'offline',generated_at:'2026-10-21T17:00:00+08:00'};
  const before=structuredClone(offline),a=app({now});a.ui.state.dashboard=offline;a.ui.renderDashboard(offline);
  assert.equal(a.get('global-alert-summary').textContent,'离线缓存 · 新买入资格已取消');assert.deepEqual(offline,before);
});
test('a new close never relabels the frozen daily cohort when dashboard and watchlist arrive in either order',async()=>{
  const now=Date.parse('2026-10-21T17:30:00+08:00');
  for(const first of ['dashboard','watchlist']){
    const a=app({now}),pending={};a.ui.setApi(url=>new Promise(resolve=>{pending[url.startsWith('/api/dashboard')?'dashboard':'watchlist']=resolve;}));
    const dashboard={meta:{data_date:'2026-10-21',report_date:'2026-10-21',snapshot_transport:{mode:'live',generated_at:'2026-10-21T17:00:00+08:00'}},index:{date:'2026-10-21'},stocks:[]};
    const frozen=watchlist({date:'2026-10-21',data_date:'2026-10-20',signal_date:'2026-10-20',generated_at:'2026-10-21T14:00:00+08:00',release_at:'2026-10-21T09:00:00+08:00',published_at:'2026-10-21T14:00:00+08:00',late:true,publication_timing:'late'});
    frozen.items[0].asof='2026-10-20';const original=structuredClone(frozen);
    const requests={dashboard:a.ui.loadDashboard(),watchlist:a.ui.loadDailyWatchlist()};
    const values={dashboard,watchlist:frozen},second=first==='dashboard'?'watchlist':'dashboard';
    pending[first](values[first]);await requests[first];pending[second](values[second]);await requests[second];
    assert.equal(a.get('recommendation-date').textContent,'2026-10-21');
    assert.match(a.get('research-dates').textContent,/本日榜单依据：2026-10-20收盘.*最新行情：2026-10-21.*公开快照更新/);
    assert.match(a.get('watchlist-dates').textContent,/截至前收盘：2026-10-20/);
    assert.deepEqual(a.ui.state.dailyWatchlist,original);assert.equal(a.ui.state.dashboard.meta.data_date,'2026-10-21');
  }
  const pages=require('../pages.js'),cal={'2026':{verified:true,holidays:[]}},next=watchlist({date:'2026-10-22',data_date:'2026-10-21',signal_date:'2026-10-21',generated_at:'2026-10-21T17:00:00+08:00',prepared_at:'2026-10-21T17:00:00+08:00',prepared_data_date:'2026-10-21',release_at:'2026-10-22T09:00:00+08:00',published_at:null,publication_timing:'prepared',late:false});
  const hidden=pages.gateWatchlist(next,cal,null,new Date(now)),a=app({now,pages});a.ui.renderDailyWatchlist(hidden);
  assert.equal(a.get('recommendation-date').textContent,'2026-10-21');assert.equal(a.get('watchlist-body').children.length,0);assert.doesNotMatch(a.get('watch-priority-name').textContent,/观察样本/);
});
test('compact observation presentation keeps key gaps and withdrawal visible with full risk details retained',()=>{
  const a=app({now:WATCH_NOW}),data=watchlist();data.items[0].verification.financial_complete=false;
  a.ui.renderDailyWatchlist(data);
  assert.equal(a.get('watchlist-gap').hidden,false);assert.equal(a.get('watchlist-gap').textContent,'1只财务待补充');assert.match(a.get('watchlist-verification').textContent,/公告待补充.*停复牌待补充.*AI待补充/);
  const row=a.get('watchlist-body').children[0];assert.equal(row.children[4].children[0].tag,'details');assert.match(row.textContent,/公告与AI未完整核验/);
  data.items[0].verification.financial_quality_available=true;a.ui.renderDailyWatchlist(data);
  assert.match(a.get('watchlist-body').textContent,/财务质量.*估值待核验/);assert.match(a.get('watchlist-gap').textContent,/估值待补充/);assert.doesNotMatch(a.get('watchlist-gap').textContent,/财务待补充/);
  data.withdrawn_symbols=[data.items[0].symbol];a.ui.renderDailyWatchlist(data);assert.match(a.get('watchlist-gap').textContent,/已撤销/);
  const html=fs.readFileSync(path.join(__dirname,'../../ashare-platform/static/index.html'),'utf8');
  assert.match(html,/<details[^>]*id="watchlist-details"/);assert.match(html,/<details[^>]*id="market-research-details"/);assert.equal((html.match(/class="important-note"/g)||[]).length,0);
});
test('recommendations lead the page, dates stay small at the right and the empty certified card is absent',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../../ashare-platform/static/index.html'),'utf8');
  const overview=html.slice(html.indexOf('<section id="view-overview"'),html.indexOf('<section id="view-longterm"'));
  assert.doesNotMatch(overview,/market-brief-panel|daily-news-list/);
  const news=html.slice(html.indexOf('<section id="view-news"'),html.indexOf('<section id="view-reports"'));
  assert.match(news,/market-brief-panel/);assert.match(news,/daily-news-list/);
  assert.doesNotMatch(html,/每日交易计划|view-plan|plan-buy|daily-sell/);
  assert(overview.indexOf('id="watch-priority-name"')<overview.indexOf('id="history-date"'));
  assert.match(overview,/<h1 id="watchlist-title">今日推荐<\/h1>.*class="recommendation-meta"[^]*?<time id="recommendation-date">/);
  assert.doesNotMatch(overview,/recommendation-day|观察名单未通过|仅观察 · 非买入信号|暂无经过验证的首选/);
  assert.match(overview,/<article class="top-pick" id="top-pick" hidden>/);
  const a=app({now:WATCH_NOW}),data=watchlist(),original=structuredClone(data);
  a.ui.state.connected=true;a.ui.state.dashboard={meta:{mode:'manual_research'},stocks:[]};a.ui.renderTopPick([]);a.ui.renderDailyWatchlist(data);
  assert.equal(a.get('watch-priority-label').textContent,'今日首选');assert.equal(a.get('watchlist-title').textContent,'今日推荐');
  assert.equal(a.get('watch-priority-name').textContent,'观察样本 · 600000.SH');assert.equal(a.get('top-pick').hidden,true);
  assert.equal(a.get('recommendation-status').textContent,'09:02 发布');assert.deepEqual(data,original);assert.equal(a.ui.shortEligible(data.items[0]),false);
});
test('a concise recommendation keeps concrete risk and offline records while removing repeated certification text',()=>{
  const a=app({now:WATCH_NOW}),data=watchlist();
  data.items[0].risk_notes.push('近3日涨幅偏快，避免追价');data.items[0].verification.financial_complete=true;
  data.snapshot_transport={mode:'offline'};data.warnings=['短线模型验证未作为本观察榜的合格买入依据','实报有两页暂不可读'];
  const original=structuredClone(data);a.ui.renderDailyWatchlist(data);
  assert.match(a.get('watchlist-body').textContent,/近3日涨幅偏快，避免追价/);assert.match(a.get('watchlist-alert').textContent,/实报有两页暂不可读|离线观察记录/);
  assert.equal(a.get('watchlist-gap').textContent,'离线记录');assert.doesNotMatch(a.get('watchlist-body').textContent+a.get('watchlist-alert').textContent,/未通过正式买入验证|短线模型验证未作为/);
  assert.deepEqual(data,original);
});
function marketBrief(overrides={}){
  const item={id:'one',title:'央行公布公开政策信息',url:'https://www.pbc.gov.cn/example.html',source:'中国人民银行',published_at:'2026-10-20T08:10:00+08:00',published_precision:'minute',collected_at:'2026-10-20T08:12:00+08:00',importance:'high',importance_reason:'公开政策信息',topics:['政策'],impact:'policy',scope:'官方标题核验'};
  return {protocol:'market-brief-v1',date:'2026-10-20',generated_at:'2026-10-20T08:15:00+08:00',status:'complete',items:[item],coverage:[],warnings:[],...overrides};
}
test('news page shows all collected items and preserves source date precision',()=>{
  const a=app({now:WATCH_NOW}),data=marketBrief();data.items=Array.from({length:4},(_,i)=>({...data.items[0],id:String(i),title:`重要消息${i}`}));
  data.items[0].published_at='2026-10-20';data.items[0].published_precision='day';
  data.items[1].published_at='2026-10-20T08:10:23+08:00';data.items[1].published_precision='second';
  data.status='partial';data.coverage=[{source:'证监会',status:'error',collected_at:'2026-10-20T08:14:00+08:00',count:0,error:'公开来源暂不可用'}];
  a.ui.renderMarketBrief(data);
  assert.equal(a.get('market-brief-items').children.length,4);assert.equal(a.get('market-brief-more').hidden,false);
  assert.match(a.get('market-brief-items').children[3].textContent,/2026-10-20 · 仅提供日期/);assert.doesNotMatch(a.get('market-brief-items').children[3].textContent,/00:00/);
  assert.match(a.get('market-brief-date').textContent,/2026-10-20.*实际整理/);assert.match(a.get('market-brief-status').textContent,/部分来源缺失/);assert.match(a.get('market-brief-gap').textContent,/证监会/);
});
test('recent important news leads while older background follows current headlines',()=>{
  const a=app({now:WATCH_NOW}),data=marketBrief(),base=data.items[0];
  data.items=[
    {...base,id:'recent',title:'普通最新标题',importance:'normal',importance_reason:'官方近期财经标题，供背景阅读。'},
    {...base,id:'future',title:'无效未来重要消息',importance:'high',collected_at:'2026-10-20T10:00:00+08:00'},
    {...base,id:'normal-old',title:'普通稍早标题',published_at:'2026-10-20T08:00:00+08:00',importance:'normal',importance_reason:'官方近期财经标题，供背景阅读。'},
    {...base,id:'policy',title:'此前货币政策公告',published_at:'2026-10-19T09:00:00+08:00',importance:'high',topics:['货币政策'],importance_reason:'标题涉及货币政策，值得核对原文。'},
    {...base,id:'regulation',title:'此前资本市场公告',published_at:'2026-10-19T14:00:00+08:00',importance:'high',topics:['资本市场'],importance_reason:'标题涉及资本市场，值得核对原文。'}
  ];
  const original=structuredClone(data);a.ui.renderMarketBrief(data);
  assert.deepEqual(a.get('market-brief-items').children.map(row=>row.children[0].textContent),['此前资本市场公告','普通最新标题','普通稍早标题','此前货币政策公告']);
  assert.equal(a.get('market-brief-items').children[2].children[0].textContent,'普通稍早标题');
  assert.match(a.get('market-brief-items').children[0].textContent,/10.?19/);assert.doesNotMatch(a.get('market-brief-items').textContent,/标题涉及资本市场/);
  assert.doesNotMatch(a.get('market-brief-items').textContent,/供背景阅读|无效未来重要消息/);
  assert.match(a.get('market-brief-gap').textContent,/1条消息缺少有效日期或来源时间/);assert.deepEqual(data,original);
  const generic=marketBrief();generic.items[0].importance_reason='官方近期财经标题，供背景阅读。';a.ui.renderMarketBrief(generic);
  assert.equal(a.get('market-brief-items').children[0].children.filter(child=>child.tag==='p').length,0);
});
test('news keeps actual source failures visible without repeating generic method notes in the main column',()=>{
  const a=app({now:WATCH_NOW}),data=marketBrief();data.late=true;data.warnings=['仅按官方标题和固定主题规则选取，重要性不是利好/利空评分，不推断受益股。','优先隔夜及当日已发布消息；不足时保留窗口内较早背景，日期精度不会伪造为具体时分。','本次为实际晚到消息更新，不是08点采集或09点准时发布证明，不授权新买入。'];
  a.ui.renderMarketBrief(data);assert.equal(a.get('market-brief-gap').hidden,true);assert.match(a.get('market-brief-warnings').textContent,/仅按官方标题/);assert.match(a.get('market-brief-warnings').textContent,/优先隔夜及当日已发布消息/);
  assert.match(a.get('market-brief-warnings').textContent,/本次为实际晚到消息更新/);assert.match(a.get('market-brief-date').textContent,/迟到整理/);
  data.coverage=[{source:'交易所',status:'error',count:0}];a.ui.renderMarketBrief(data);
  assert.equal(a.get('market-brief-gap').hidden,false);assert.equal(a.get('market-brief-gap').textContent,'来源缺口：交易所');
});
test('important news is text-only and invalid links or future observations never become trusted items',()=>{
  const a=app({now:WATCH_NOW}),data=marketBrief(),attack='<img src=x onerror=alert(1)>';
  data.items[0].title=attack;data.items[0].url='https://www.pbc.gov.cn/?token=secret';a.ui.renderMarketBrief(data);
  const heading=a.get('market-brief-items').children[0].children[0];assert.equal(heading.textContent,attack);assert.equal(heading.children.length,0);
  data.items[0].url='https://user:secret@www.pbc.gov.cn/example';a.ui.renderMarketBrief(data);assert.equal(a.get('market-brief-items').children[0].children[0].children.length,0);
  data.items[0].collected_at='2026-10-20T10:00:00+08:00';a.ui.renderMarketBrief(data);assert.equal(a.get('market-brief-items').children.length,0);assert.match(a.get('market-brief-gap').textContent,/缺少有效日期或来源时间/);
});
test('late news responses cannot replace a newer today or historical selection',async()=>{
  const a=app({now:WATCH_NOW}),pending=[],paths=[];a.ui.state.connected=true;a.ui.setApi(url=>{paths.push(url);return new Promise(resolve=>pending.push(resolve));});
  const first=a.ui.loadMarketBrief();a.ui.state.newsDate='2026-10-19';const historical=a.ui.loadMarketBrief();a.ui.state.newsDate='';const latest=a.ui.loadMarketBrief();
  const old=marketBrief();old.items[0].title='过时的首次响应';pending[0](old);await first;assert.equal(a.get('market-brief-items').children.length,0);
  const current=marketBrief();current.items[0].title='最新当日消息';pending[2](current);await latest;
  pending[1](marketBrief({date:'2026-10-19',status:'historical_unavailable',items:[]}));await historical;
  assert.deepEqual(paths,['/api/market-brief?date=2026-10-20','/api/market-brief?date=2026-10-19','/api/market-brief?date=2026-10-20']);
  assert.match(a.get('market-brief-items').textContent,/最新当日消息/);assert.doesNotMatch(a.get('market-brief-items').textContent,/过时的首次响应/);assert.equal(a.ui.state.connected,true);
});
test('missing historical news never borrows today and a news endpoint failure keeps the dashboard connected',async()=>{
  const a=app({now:WATCH_NOW}),dashboard={meta:{data_date:'2026-10-19'},stocks:[]};a.ui.state.connected=true;a.ui.state.dashboard=dashboard;a.ui.state.newsDate='2026-10-19';
  a.ui.setApi(async()=>marketBrief());await a.ui.loadMarketBrief();assert.equal(a.get('market-brief-items').children.length,0);assert.match(a.get('market-brief-empty').textContent,/未用其他日期替代/);
  a.ui.setApi(async()=>{throw Error('404');});await a.ui.loadMarketBrief();assert.equal(a.ui.state.connected,true);assert.equal(a.ui.state.dashboard,dashboard);assert.match(a.get('market-brief-empty').textContent,/行情与观察榜仍可查看/);
});
test('a missing selected date keeps the connection and selection while clearly showing the actual market day',async()=>{
  const a=app();a.ui.state.historyDate='2026-10-19';a.get('history-date').value='2026-10-19';
  a.ui.setApi(async()=>({meta:{data_date:'2026-10-16',report_date:'2026-10-16',snapshot_transport:{mode:'live',generated_at:'2026-10-19T20:00:00+08:00'},report_selection:{status:'unavailable',requested_date:'2026-10-19',actual_date:'2026-10-16'}},stocks:[stock()]}));
  await a.ui.loadDashboard();assert.equal(a.ui.state.connected,true);assert.equal(a.ui.state.historyDate,'2026-10-19');assert.equal(a.get('history-date').value,'2026-10-19');
  assert.match(a.get('market-status').textContent,/公开快照已连接/);assert.match(a.get('global-alert').textContent,/所选行情日期 2026-10-19 尚未生成/);assert.match(a.get('global-alert').textContent,/展示 2026-10-16/);
  assert.doesNotMatch(a.get('global-alert').textContent,/历史行情研究：2026-10-19|公开网站快照加载失败/);assert.match(a.get('research-dates').textContent,/行情日期：2026-10-16/);assert.match(a.get('research-dates').textContent,/公开快照更新：/);assert.equal(a.ui.state.topPick,null);assert.equal(a.get('export-btn').hidden,true);
});
test('a real dashboard network failure still cancels eligibility and displays loading failure',async()=>{
  const a=app();a.ui.state.historyDate='2026-10-19';a.ui.state.dashboard={meta:{data_date:'2026-10-16',report_date:'2026-10-16'},stocks:[qualified()]};a.ui.state.connected=true;
  a.ui.setApi(async()=>{throw Error('研究文件暂不可用（503）');});await a.ui.loadDashboard();
  assert.equal(a.ui.state.connected,false);assert.equal(a.ui.state.dashboard.stocks[0].eligible,false);assert.match(a.get('market-status').textContent,/公开快照加载失败/);assert.match(a.get('global-alert').textContent,/503/);
});
test('high technical scores cannot enter default qualified candidates',()=>{
  const a=app();a.ui.state.dashboard={meta:{report_date:'2026-10-16'},stocks:[stock()]};a.ui.state.connected=true;a.ui.renderStocks();
  assert.equal(a.get('stocks-body').children.length,0);assert.match(a.get('stocks-empty').textContent,/暂无验证合格候选/);
  assert.match(a.get('stocks-empty').textContent,/技术分再高也不代表可以买入/);
});
test('explicitly expanding failed research preserves details without buy prices or recommendation score',()=>{
  const a=app();a.ui.state.dashboard={meta:{},stocks:[stock()]};a.ui.state.connected=true;a.get('show-unqualified').checked=true;a.ui.renderStocks();
  const row=a.get('stocks-body').children[0];assert.match(row.textContent,/技术分 \/ 非概率/);assert.match(row.textContent,/未通过 · 非推荐/);assert.match(row.textContent,/非推荐详情/);
  assert.deepEqual(row.children.slice(6,9).map(x=>x.textContent),['—','—','—']);
});
test('only a qualified current anti-chase policy permits a candidate',()=>{
  const a=app(),s=qualified();assert.equal(a.ui.shortEligible(s),true);
  for(const risk of [undefined,{version:'old',gate_passed:true,blocked:false},{version:'anti-chase-v1',gate_passed:false,blocked:false},{version:'anti-chase-v1',gate_passed:true,blocked:true}])assert.equal(a.ui.shortEligible({...s,chase_risk:risk}),false);
});
test('old models or failed cost-stressed decision validation cannot become candidates',()=>{
  const a=app(),s=qualified();
  for(const prediction of [
    {...s.prediction,model_version:'ridge-cycle-v2'},
    {...s.prediction,validation_protocol:'purged-expanding-3x60-v1'},
    {...s.prediction,validation_decision:undefined},
    {...s.prediction,validation_decision:{protocol:'old',eligible:true}},
    {...s.prediction,validation_decision:{protocol:'fixed-nonoverlap-positive-net-v1',eligible:false}}
  ])assert.equal(a.ui.shortEligible({...s,prediction}),false);
});
test('compact rejected rows with omitted default flags stay fail-closed and retain readable reasons',()=>{
  const a=app(),row={symbol:'sh600241',name:'稀疏研究样本',score:90,
    prediction:{expected_return_pct:8,validation_protocol_complete:true,validation_aggregate:{samples:180,mae_pct:3},reason:'决策验证未胜过现金；更多见详情'},
    chase_risk:{blocked:true,reasons:[{detail:'急涨后停止新买入'}]}};
  assert.equal(a.ui.shortEligible(row),false);a.ui.state.connected=true;a.ui.state.dashboard={meta:{},stocks:[row]};a.ui.renderStocks();
  assert.equal(a.get('stocks-body').children.length,0);a.get('show-unqualified').checked=true;a.ui.renderStocks();
  const displayed=a.get('stocks-body').children[0];assert.match(displayed.textContent,/急涨后停止新买入/);assert.match(displayed.textContent,/非推荐/);
  assert.deepEqual(displayed.children.slice(6,9).map(x=>x.textContent),['—','—','—']);
});
test('failed positive estimates stay neutral and expose the actual failure reason',()=>{
  const a=app(),s=stock({prediction:{available:true,eligible:false,expected_return_pct:12,reason:'最近段成本压力收益未胜过现金与基准'}});
  a.ui.state.dashboard={meta:{},stocks:[s]};a.ui.state.connected=true;a.get('show-unqualified').checked=true;a.ui.renderStocks();
  const row=a.get('stocks-body').children[0];assert.equal(row.children[2].children[0].className.includes('positive-text'),false);assert.match(row.textContent,/最近段成本压力收益未胜过现金与基准/);
  a.ui.renderPrediction(s.prediction,false,false,s.prediction.reason);
  const detail=a.get('detail-prediction');assert.match(detail.textContent,/未通过的模型点估计/);assert.match(detail.textContent,/正的点估计也不能当作买入信号/);
});
test('decision diagnostic keeps negative losses and distinguishes historical stress from real profit',()=>{
  const a=app(),metrics={samples:90,trades:40,net_mean_per_opportunity_pct:-.42,net_mean_selected_pct:-.945,passive_net_mean_pct:.1,average_loss_pct:-3.2,worst_trade_pct:-12.4,expected_shortfall_10_pct:-8.7,max_drawdown_pct:26};
  a.ui.renderPrediction({available:true,eligible:false,expected_return_pct:5,validation_decision:{protocol:'fixed-nonoverlap-positive-net-v1',eligible:false,failures:['没有胜过现金'],cost_stress_pct:.3,aggregate:metrics,latest_fold:{...metrics,samples:30,trades:10}}},false);
  const detail=a.get('detail-prediction');assert.match(detail.textContent,/全部分段合并/);assert.match(detail.textContent,/最近验证段/);assert.match(detail.textContent,/-12\.40%/);assert.match(detail.textContent,/-8\.70%/);
  assert.match(detail.textContent,/不能当作实盘净收益、未来收益概率/);assert.match(detail.textContent,/并非连续持仓/);
});
test('no selected historical cycles show missing tail returns instead of fabricated zero',()=>{
  const a=app(),metrics={samples:90,trades:0,net_mean_per_opportunity_pct:0,net_mean_selected_pct:null,average_loss_pct:null,worst_trade_pct:null,expected_shortfall_10_pct:null,max_drawdown_pct:0};
  a.ui.renderPrediction({available:true,eligible:false,validation_decision:{protocol:'fixed-nonoverlap-positive-net-v1',eligible:false,aggregate:metrics,latest_fold:metrics}},false);
  const detail=a.get('detail-prediction');assert.match(detail.textContent,/所选周期成本压力后均值—/);assert.match(detail.textContent,/所选最差单周期—/);assert.match(detail.textContent,/所选最差10%周期均值—/);
});
test('disconnection removes page buy qualification without mutating the prior snapshot',()=>{
  const a=app(),s=qualified(),old={meta:{daily_buy:s},stocks:[s]},safe=a.ui.disableDisconnectedResearch(old);
  assert.equal(safe.meta.daily_buy,null);assert.equal(a.ui.shortEligible(safe.stocks[0]),false);assert.equal(old.stocks[0].eligible,true);assert.equal(old.meta.daily_buy,s);
});
test('an immutable old plan cannot bypass current model and anti-chase gates in the highlighted pick',()=>{
  const a=app(),old=stock();a.ui.state.connected=true;a.ui.state.dashboard={meta:{mode:'manual_research',daily_plan_status:'published',daily_buy:old,coverage:{total:100,ok:100}}};a.ui.renderTopPick([]);
  assert.equal(a.ui.state.topPick,null);assert.equal(a.get('pick-metrics').hidden,true);
});
test('A to B to A detail navigation rejects the oldest same-symbol response',async()=>{
  const a=app(),pending=[];a.ui.setApi(()=>new Promise(resolve=>pending.push(resolve)));a.ui.setRenderDetail(()=>{});
  const first=a.ui.openStock(stock()),second=a.ui.openStock(stock({symbol:'sh688305'})),third=a.ui.openStock(stock());
  pending[0]({stock:stock({score:99}),bars:[]});await first;assert.equal(a.ui.state.detailData,null);
  pending[1]({stock:stock({symbol:'sh688305',score:4}),bars:[]});await second;assert.equal(a.ui.state.detailData,null);
  pending[2]({stock:stock({score:73}),bars:[]});await third;assert.equal(a.ui.state.detailData.stock.score,73);
});
test('a wrong-symbol detail response never displays another stock score',async()=>{
  const a=app();a.ui.setApi(async()=>({stock:stock({symbol:'sh688305',score:4}),bars:[]}));a.ui.setRenderDetail(()=>{});
  await a.ui.openStock(stock());assert.equal(a.ui.state.detailData,null);assert.equal(a.get('detail-content').hidden,true);assert.match(a.get('detail-loading').textContent,/代码不匹配/);
});
test('cloud review states reflect actual validation and do not claim configuration or success',()=>{
  const a=app();a.ui.renderAIReview(undefined);assert.match(a.get('ai-review-status').textContent,/尚未读取云端复核记录/);assert.doesNotMatch(a.get('ai-review-status').textContent,/未配置|已通过/);
  for(const [status,label] of [['missing','待记录'],['invalid','校验失败'],['partial','未完成'],['late','迟到，仅复盘'],['unavailable','不可用']]){
    a.ui.renderAIReview({status,valid:true,morning_qualified:true});assert.match(a.get('ai-review-status').textContent,new RegExp(label));assert.doesNotMatch(a.get('ai-review-status').textContent,/盘前复核记录已通过校验/);
  }
  for(const [valid,morning] of [[false,true],[true,false],[true,undefined]]){
    a.ui.renderAIReview({status:'complete',valid,morning_qualified:morning});assert.doesNotMatch(a.get('ai-review-status').textContent,/盘前复核记录已通过校验/);
  }
  a.ui.renderAIReview({status:'complete',valid:true,morning_qualified:true});assert.match(a.get('ai-review-status').textContent,/盘前复核记录已通过校验/);
});
test('cloud review shows record scope and errors as text, with a safe task conversation link',()=>{
  const a=app();a.ui.renderAIReview({status:'late',summary:'仅复盘，不造买入',scope:'官方公告目录标题，不含PDF正文',errors:['部分内容待核验'],checked_at:'2026-10-19T10:12:00+08:00',run_url:'https://chatgpt.com/c/review-example',run_url_scope:'task_chat'});
  const panel=a.get('ai-review-status'),link=panel.children.find(child=>child.tag==='a');assert.match(panel.textContent,/仅复盘，不造买入/);assert.match(panel.textContent,/不含PDF正文/);assert.match(panel.textContent,/部分内容待核验/);assert.match(panel.textContent,/记录校验时间/);
  assert.equal(link.href,'https://chatgpt.com/c/review-example');assert.equal(link.rel,'noopener noreferrer');assert.equal(link.textContent,'查看复核任务对话 ↗');assert.doesNotMatch(link.textContent,/review-example/);
});
test('cloud review never renders unsafe, credential-bearing or foreign links',()=>{
  const a=app();
  for(const run_url of ['javascript:alert(1)','http://chatgpt.com/c/x','https://chatgpt.com.evil.invalid/c/x','https://user:secret@chatgpt.com/c/x','https://chatgpt.com:8443/c/x','https://chatgpt.com/c/x?token=secret','https://chatgpt.com/c/x#secret','not a url']){
    a.ui.renderAIReview({status:'partial',run_url});assert.equal(a.get('ai-review-status').children.some(child=>child.tag==='a'),false);
  }
});

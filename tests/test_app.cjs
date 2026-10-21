const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const path=require('node:path'),canonical=path.join(__dirname,'../../ashare-platform/static/app.js');
const source=fs.readFileSync(fs.existsSync(canonical)?canonical:path.join(__dirname,'../engine/static/app.js'),'utf8');
class Element{
  constructor(tag='div'){this.tag=tag;this.children=[];this.hidden=false;this.value='';this.checked=false;this.style={};this._text='';this.classList={toggle(){},add(){},remove(){}};}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(x=>x.textContent??String(x)).join('');}
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
  vm.runInNewContext(source.slice(0,source.lastIndexOf("document.querySelectorAll('[data-view]')"))+"\nglobalThis.ui={state,shortEligible,renderStocks,renderDashboard,loadDashboard,renderPrediction,renderAIReview,renderTopPick,renderDailyWatchlist,loadDailyWatchlist,disableDisconnectedResearch,openStock,setApi:fn=>api=fn,setRenderDetail:fn=>renderDetail=fn};",sandbox);
  return {ui:sandbox.ui,nodes,get:id=>document.getElementById(id)};
}
function stock(overrides={}){return {symbol:'sh601811',name:'技术分73的样本',score:73,date:'2026-10-16',eligible:false,prediction:{available:true,eligible:false,rank_value:null,expected_return_pct:5},entry_low:10,entry_high:11,stop_loss:9,take_profit:12,...overrides};}
function qualified(){return stock({eligible:true,chase_risk:{version:'anti-chase-v1',blocked:false,gate_passed:true},prediction:{model_version:'ridge-cycle-v3',validation_protocol:'purged-expanding-3x60-decision-v1',available:true,eligible:true,validation_decision:{protocol:'fixed-nonoverlap-positive-net-v1',eligible:true},rank_value:1,expected_return_pct:2}});}
const WATCH_NOW=Date.parse('2026-10-20T09:15:00+08:00');
function watchlist(overrides={}){
  const first={symbol:'sh600000',name:'观察样本',asof:'2026-10-19',score:73.5,components:{technical:80,financial:63.75},reasons:['量价规则相对靠前','财务字段已取得'],risk_notes:['模型未通过正式买入验证','公告与AI未完整核验'],verification:{announcements_status:'unverified',session_status:'unverified',ai_status:'unverified'},eligible:false,actionable:false,entry_low:9876.54,entry_high:9999.99,stop_loss:8765.43,take_profit:12345.67};
  return {protocol:'daily-observation-v1',date:'2026-10-20',data_date:'2026-10-19',signal_date:'2026-10-19',generated_at:'2026-10-20T08:55:00+08:00',release_at:'2026-10-20T09:00:00+08:00',published_at:'2026-10-20T09:02:00+08:00',publication_timing:'on_time',late:false,status:'watch_only',model_validated:false,items:[first],top_pick:first,verification:first.verification,warnings:[],...overrides};
}
test('an unqualified observation can be visible without becoming a formal buy or showing any supplied trade price',()=>{
  const a=app({now:WATCH_NOW}),data=watchlist();a.ui.state.connected=true;a.ui.state.dashboard={meta:{mode:'manual_research'},stocks:[]};a.ui.renderTopPick([]);a.ui.renderDailyWatchlist(data);
  assert.equal(a.get('watchlist-body').children.length,1);assert.equal(a.get('watch-priority-name').textContent,'观察样本 · 600000.SH');assert.equal(a.get('watch-priority-score').textContent,'73.5 / 100');
  assert.equal(a.ui.state.topPick,null);assert.equal(a.ui.shortEligible(data.items[0]),false);assert.equal(a.get('pick-metrics').hidden,true);
  const displayed=a.get('watchlist-body').textContent+a.get('watch-priority-reason').textContent;for(const price of ['9876.54','9999.99','8765.43','12345.67'])assert(!displayed.includes(price));
  assert.match(a.get('watchlist-body').textContent,/仅观察 · 未通过正式买入验证/);assert.match(a.get('watchlist-summary').textContent,/不提供未经验证的买卖价位/);
  assert.match(a.get('watch-priority-verification').textContent,/公告未完成完整核验/);assert.match(a.get('watch-priority-verification').textContent,/AI未完成完整核验/);assert.match(a.get('watch-priority-verification').textContent,/未核验不代表安全/);
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
      assert.match(a.get('watchlist-summary').textContent,/服务器尚未发布/);
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
  assert.match(a.get('watch-priority-name').textContent,/原最优先观察股已撤销/);assert.doesNotMatch(a.get('watch-priority-name').textContent,/第二观察/);assert.equal(a.get('watch-priority-metrics').hidden,true);assert.equal(a.get('watchlist-body').children.length,2);assert.match(a.get('watchlist-body').children[0].textContent,/已撤销观察/);assert.match(a.get('watchlist-alert').textContent,/新增风险公告/);
});
test('observation names, reasons and risks render only as text and absent finance stays unverified',()=>{
  const a=app({now:WATCH_NOW}),data=watchlist(),attack='<img src=x onerror=alert(1)>';data.items[0].name=attack;data.items[0].reasons=[attack];data.items[0].risk_notes=[attack];data.items[0].components.financial=null;a.ui.renderDailyWatchlist(data);
  const row=a.get('watchlist-body').children[0];assert.equal(row.children[1].children[0].textContent,attack);assert.equal(row.children[3].textContent,attack);assert.match(row.textContent,/财务 未核验/);assert.equal(a.get('watch-priority-reason').textContent,attack);
  function tags(element){return [element.tag,...element.children.flatMap(child=>child?.children?tags(child):[])];}assert(!tags(row).includes('img'));
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

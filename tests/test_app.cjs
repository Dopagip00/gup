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
function app(){
  const nodes=new Map(),document={getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);},createElement:tag=>new Element(tag),createTextNode:value=>Object.assign(new Element('#text'),{textContent:value}),createDocumentFragment:()=>new Element('#fragment')};
  document.getElementById('min-score').value='0';document.getElementById('stock-sort').value='prediction';
  const sandbox={document,window:{PagesResearch:{}},location:{hostname:'dopagip00.github.io'},structuredClone,URL,requestAnimationFrame:()=>{},Intl,Date,setTimeout,clearTimeout,fetch:()=>{throw Error('unexpected fetch');}};
  vm.runInNewContext(source.slice(0,source.lastIndexOf("document.querySelectorAll('[data-view]')"))+"\nglobalThis.ui={state,shortEligible,renderStocks,renderDashboard,loadDashboard,renderPrediction,renderAIReview,renderTopPick,disableDisconnectedResearch,openStock,setApi:fn=>api=fn,setRenderDetail:fn=>renderDetail=fn};",sandbox);
  return {ui:sandbox.ui,nodes,get:id=>document.getElementById(id)};
}
function stock(overrides={}){return {symbol:'sh601811',name:'技术分73的样本',score:73,date:'2026-10-16',eligible:false,prediction:{available:true,eligible:false,rank_value:null,expected_return_pct:5},entry_low:10,entry_high:11,stop_loss:9,take_profit:12,...overrides};}
function qualified(){return stock({eligible:true,chase_risk:{version:'anti-chase-v1',blocked:false,gate_passed:true},prediction:{model_version:'ridge-cycle-v3',validation_protocol:'purged-expanding-3x60-decision-v1',available:true,eligible:true,validation_decision:{protocol:'fixed-nonoverlap-positive-net-v1',eligible:true},rank_value:1,expected_return_pct:2}});}
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

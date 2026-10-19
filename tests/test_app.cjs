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
  vm.runInNewContext(source.slice(0,source.lastIndexOf("document.querySelectorAll('[data-view]')"))+"\nglobalThis.ui={state,shortEligible,renderStocks,renderTopPick,disableDisconnectedResearch,openStock,setApi:fn=>api=fn,setRenderDetail:fn=>renderDetail=fn};",sandbox);
  return {ui:sandbox.ui,nodes,get:id=>document.getElementById(id)};
}
function stock(overrides={}){return {symbol:'sh601811',name:'技术分73的样本',score:73,date:'2026-10-16',eligible:false,prediction:{available:true,eligible:false,rank_value:null,expected_return_pct:5},entry_low:10,entry_high:11,stop_loss:9,take_profit:12,...overrides};}
function qualified(){return stock({eligible:true,chase_risk:{version:'anti-chase-v1',blocked:false,gate_passed:true},prediction:{available:true,eligible:true,rank_value:1,expected_return_pct:2}});}
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

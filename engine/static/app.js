'use strict';

const $ = id => document.getElementById(id);
const state = {dashboard: null, config: null, csrf: '', historyDate: '', range: 60, view: 'overview', limit: 100, selected: null, detailData: null, request: 0, connected: false, quotes: new Map(), topPick: null, longterm: null, dailyPlan: null, planStock: null};
const universeNames = {watchlist: '自选观察池', sse50: '上证 50 成分股', shanghai: '全部沪市 A 股', all: '沪深 A 股'};
const statusNames = {idle:'等待计划更新', running:'正在更新', success:'更新完成', partial:'部分数据异常', error:'更新失败', pending:'待成交', filled:'已成交', expired:'已失效', cancelled:'已撤销'};
const numeric = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
const number = (value, digits = 2) => numeric(value) ? Number(value).toLocaleString('zh-CN', {minimumFractionDigits:digits, maximumFractionDigits:digits}) : '—';
const shortEligible = stock => stock?.eligible === true && stock?.prediction?.available === true && stock?.prediction?.eligible === true && !stock.stale && !stock.excluded && numeric(stock.prediction.rank_value) && numeric(stock.prediction.expected_return_pct);
const predictionRank = stock => stock?.prediction?.available !== false && numeric(stock?.prediction?.rank_value) ? Number(stock.prediction.rank_value) : -Infinity;
const visiblePrediction = prediction => prediction?.available === false ? {available:false,reason:prediction.reason,model_version:prediction.model_version,horizon_days:prediction.horizon_days,target_kind:prediction.target_kind,next_day:{available:false}} : prediction || {};
const horizonLabel = prediction => Number(prediction?.horizon_days)===2?'两交易日周期':prediction?.horizon || (numeric(prediction?.horizon_days)?`${number(prediction.horizon_days,0)} 个交易日`:'两交易日周期');
const returnBasis = prediction => !numeric(prediction?.horizon_days)||Number(prediction.horizon_days)===2?'买入日开盘 → 下一交易日收盘（研究近似）':prediction?.return_basis||prediction?.target_definition||'收益口径按该历史报告的预测期限定义';
const percentage = (value, signed = false) => numeric(value) ? `${signed && Number(value) > 0 ? '+' : ''}${number(value)}%` : '—';
const text = (id, value) => {const node=$(id); if(node) node.textContent = value === null || value === undefined || value === '' ? '—' : String(value);};
const shortTime = value => {if(!value) return '—'; const d=new Date(value); return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false});};
const symbolLabel = value => String(value || '').replace(/^(sh|sz|bj)(\d{6})$/i, (_, ex, code) => `${code}.${ex.toUpperCase()}`);
function node(tag, className, content) {const n=document.createElement(tag); if(className)n.className=className; if(content !== undefined)n.textContent=String(content); return n;}
function setChange(id, value, prefix='') {const n=$(id); n.textContent=prefix+percentage(value,true); n.classList.toggle('positive-text',Number(value)>0); n.classList.toggle('negative-text',Number(value)<0);}
function list(value) {return Array.isArray(value) ? value : value ? [value] : [];}
function clear(n) {n.replaceChildren(); return n;}
function message(value, error=false) {const n=$('toast'); n.textContent=String(value); n.classList.toggle('error',error); n.hidden=false; clearTimeout(message.timer); message.timer=setTimeout(()=>n.hidden=true,error?6500:4200);}

async function api(path, options = {}) {
  const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),45000);
  try {
    const response=await fetch(path,{cache:'no-store',...options,signal:controller.signal,headers:{'Content-Type':'application/json',...(options.method && options.method!=='GET'?{'X-CSRF-Token':state.csrf}:{}),...options.headers}});
    let result; try {result=await response.json();} catch {throw new Error(`服务返回了无效响应（${response.status}）`);}
    if(!response.ok) throw new Error(result.error || result.message || `请求失败（${response.status}）`);
    return result;
  } catch(error) {if(error.name==='AbortError')throw new Error('请求超时，后台任务可能仍在继续，请稍后重试。'); throw error;} finally {clearTimeout(timeout);}
}

function switchView(view) {
  if(!['overview','longterm','plan','reports','settings'].includes(view)) view='overview';
  state.view=view;
  document.querySelectorAll('.view').forEach(v=>v.hidden=v.id!==`view-${view}`);
  document.querySelectorAll('.nav-item').forEach(v=>v.classList.toggle('active',v.dataset.view===view));
  text('view-name',{overview:'短线机会',longterm:'长线价值',plan:'每日交易计划',reports:'每日研究档案',settings:'数据与设置'}[view]);
  if(location.hash!==`#${view}`) history.replaceState(null,'',`#${view}`);
  if(view==='longterm') loadLongterm();
  if(view==='plan') loadDailyPlan();
  if(view==='reports') loadReports();
  if(view==='settings') {loadConfig();loadSystem();}
  if(view==='overview') requestAnimationFrame(drawIndex);
}

async function loadDashboard() {
  const id=++state.request, date=state.historyDate;
  try {
    const data=await api(`/api/dashboard${date?`?date=${encodeURIComponent(date)}`:''}`);
    if(id!==state.request) return;
    state.connected=true; state.dashboard=data; state.csrf=data.meta?.csrf_token || state.csrf;
    renderDashboard(data);
  } catch(error) {
    if(id!==state.request)return;
    state.connected=false; state.topPick=null; renderTopPick([]);
    $('global-alert').hidden=false; $('global-alert').classList.add('error');
    text('global-alert',`无法连接研究服务：${error.message}。请检查网站服务与网络。页面保留的内容可能已经过期。`);
    text('market-status','服务未连接'); $('market-status').classList.remove('live');
    if(!state.dashboard){text('index-chart-empty','暂无可用数据');setEmpty('stocks-empty','研究服务暂不可用','服务恢复后，页面会自动重试。');}
  }
}

function setEmpty(id,title,description='') {const root=$(id); root.hidden=false; root.replaceChildren(node('span','empty-icon','◫'),node('strong','',title),node('p','',description));}

function renderDashboard(data) {
  const m=data.meta||{}, ix=data.index||{}, stocks=list(data.stocks), coverage=m.coverage||{};
  const status=typeof m.status==='object'?m.status:{status:m.status};
  const running=status.status==='running';
  text('market-status',running?'研究更新中':m.is_trading?'交易时段':'休市 / 非交易时段');
  $('market-status').classList.toggle('live',!!m.is_trading || running);
  $('refresh-btn').disabled=running;
  text('refresh-btn',running?`同步中 ${status.completed || 0}/${status.total || '—'}`:'↻ 更新研究');
  const warnings=list(m.warnings).map(String);
  if(state.historyDate) warnings.unshift(`历史研究快照：${state.historyDate}。历史价位仅用于复盘，不能作为当前委托依据。`);
  if(status.status==='error' && status.error) warnings.push(status.error);
  if(running) warnings.unshift(`${status.phase || '正在下载和分析行情'}${status.total?` · 已处理 ${status.completed||0} / ${status.total} 只`:''}。已有结果会逐步显示。`);
  $('global-alert').hidden=!warnings.length;
  $('global-alert').classList.toggle('error',status.status==='error');
  text('global-alert',[...new Set(warnings)].join('\n'));
  text('index-price',number(ix.raw_close ?? ix.close)); setChange('index-change',ix.change_pct,ix.date?`${ix.date}　`:'');
  const trend=ix.trend || ix.forecast || ix.signal || (ix.close?'等待分析':'等待数据');
  text('index-trend',typeof trend==='object'?trend.label || trend.direction || '规则趋势观察':trend);
  text('index-trend-note',ix.stale?'数据未达到目标交易日，暂停趋势建议':'规则趋势判断 · 非预测概率');
  const opportunities=stocks.filter(shortEligible);
  const opportunity=$('opportunity-count'); clear(opportunity).append(document.createTextNode(stocks.length?String(opportunities.length):'—'),node('small','','只'));
  text('opportunity-note',stocks.length?`当日 ${stocks.length} 只研究中的验证合格候选`:'等待真实行情生成候选');
  text('coverage',numeric(coverage.total)?`${number(coverage.ok,0)} / ${number(coverage.total,0)}`:'—');
  text('coverage-note',`有效 / ${universeNames[m.universe] || m.universe || '全部研究范围'}${numeric(coverage.excluded)?` · ${number(coverage.excluded,0)} 只指标股仅作参考`:''}`);
  renderCounts('coverage-breakdown',[[m.universe==='shanghai'?'全部沪市研究':'全部研究范围',coverage.total],['有效日线研究',coverage.ok],['研究待补齐',coverage.failed],['推荐排除指标股',coverage.excluded],['非指标研究范围',coverage.candidate_total],['非指标有效',coverage.candidate_ok],['非指标待补齐',coverage.candidate_failed]]);
  const currentPrediction=stocks.find(s=>!s.excluded && s.prediction)?.prediction;
  text('prediction-horizon-header',`${horizonLabel(currentPrediction)}预期 / 区间`);text('ranking-basis',`${returnBasis(currentPrediction)}。按预期收益、分段验证误差与成本压力综合排序；未通过者仅作研究。`);
  text('brief-status',!ix.close?'等待沪指历史数据':ix.stale?'数据过期 · 暂停参考':opportunities.length?`${opportunities.length} 只股票满足观察条件`:'耐心等待符合条件的机会');
  const brief=clear($('brief-reasons'));
  let reasons=list(ix.reasons).slice(0,3);
  if(numeric(ix.forecast?.up_rate_pct))reasons.push(`技术规则历史样本（${ix.forecast.horizon||'规则观察期'}）上涨占比 ${percentage(ix.forecast.up_rate_pct)}，${ix.forecast.sample_count} 个重叠样本；不是当前周期预测概率。`);
  const quality=m.model_quality;
  if(quality){reasons.push(`模型分段验证：${number(quality.protocol_complete,0)} 只完成，${number(quality.model_qualified,0)} 只通过；未通过者不作为新买入推荐。`);if(numeric(quality.mean_stock_oos_mae_pct)&&numeric(quality.mean_stock_zero_baseline_mae_pct))reasons.push(`逐股时间验证平均误差 ${number(quality.mean_stock_oos_mae_pct)} 个百分点，零收益基准 ${number(quality.mean_stock_zero_baseline_mae_pct)} 个百分点。${quality.mean_stock_oos_mae_pct>=quality.mean_stock_zero_baseline_mae_pct?'当前汇总未显示误差优势。':''}这不是荐股实盘胜率。`);}
  if(!reasons.length)reasons=[running?'正在同步行情，完成后显示趋势依据。':'趋势评估基于完整日线，盘中波动不直接替换收盘研究。'];
  reasons.forEach(r=>brief.append(node('li','',typeof r==='object'?r.description||r.label||JSON.stringify(r):r)));
  text('data-date',m.data_date || m.report_date); text('next-run',shortTime(m.next_run));
  text('index-chart-date',ix.date || '收盘数据');
  text('index-chart-caption',ix.source?`数据：${ix.source}`:'历史走势不代表未来表现');
  text('stock-count',`${stocks.length} 只`);
  text('settings-current-universe',universeNames[m.universe] || m.universe);
  const lastSuccess=m.last_success;
  text('settings-last-success',lastSuccess && typeof lastSuccess==='object'?`${shortTime(lastSuccess.finished_at)}${lastSuccess.target_date?` · 交易日 ${lastSuccess.target_date}`:''}`:shortTime(lastSuccess));
  text('settings-schedule',m.schedule); text('settings-next-run',shortTime(m.next_run));
  $('export-btn').href=`/api/export.csv${state.historyDate?`?date=${encodeURIComponent(state.historyDate)}`:''}`;
  if(!state.historyDate)$('history-date').value=m.report_date || m.data_date || '';
  renderTopPick(stocks); renderStocks(); drawIndex(); renderRuns(data.runs || []);
  text('hosting-mode-badge',m.hosted?'云端调度':'本机服务');
  text('hosting-description',m.hosted?'云端托管：数据获取与交易日研究由云端任务运行，不依赖你的电脑开机。计划生成结果以每日交易计划的报告日期与状态为准。':'本机运行：研究更新依赖本地服务与网络可用。若已部署云站，请在云站查看服务端计划状态。');
}

function renderStocks() {
  if(!state.dashboard)return;
  const query=$('stock-search').value.trim().toLowerCase(), min=Number($('min-score').value), actionable=$('actionable-only').checked;
  const all=list(state.dashboard.stocks), sort=$('stock-sort').value, showExcluded=$('show-excluded').checked;
  const filtered=all.filter(s=>(showExcluded || !s.excluded) && (!query || `${s.symbol} ${s.name}`.toLowerCase().includes(query)) && (numeric(s.score)?Number(s.score)>=min:min===0) && (!actionable || shortEligible(s)));
  text('stock-count',showExcluded?`${all.length} 只含指标参考`:`${all.filter(s=>!s.excluded).length} 只非指标`);
  filtered.sort((a,b)=>sort==='prediction'?predictionRank(b)-predictionRank(a):sort==='expected'?(numeric(b.prediction?.expected_return_pct)?Number(b.prediction.expected_return_pct):-Infinity)-(numeric(a.prediction?.expected_return_pct)?Number(a.prediction.expected_return_pct):-Infinity):sort==='symbol'?String(a.symbol).localeCompare(String(b.symbol)):sort==='change'?(Number(b.change_pct)||0)-(Number(a.change_pct)||0):(numeric(b.score)?Number(b.score):-1)-(numeric(a.score)?Number(a.score):-1));
  const body=clear($('stocks-body')), fragment=document.createDocumentFragment();
  filtered.slice(0,state.limit).forEach(s=>{
    const tr=node('tr'); tr.tabIndex=0; tr.setAttribute('aria-label',`查看 ${s.name||s.symbol} 研究详情`);
    tr.addEventListener('click',()=>openStock(s)); tr.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openStock(s);}});
    const name=node('td'); name.append(node('span','stock-name',s.name || s.symbol),node('span','stock-code',symbolLabel(s.symbol))); tr.append(name);
    const price=node('td'); price.append(node('span','price-primary',number(s.raw_close ?? s.close)),node('span',`price-sub ${Number(s.change_pct)>0?'positive-text':Number(s.change_pct)<0?'negative-text':''}`,percentage(s.change_pct,true))); tr.append(price);
    const p=visiblePrediction(s.prediction), expected=node('td');expected.append(node('span',`price-primary ${Number(p.expected_return_pct)>0?'positive-text':Number(p.expected_return_pct)<0?'negative-text':''}`,percentage(p.expected_return_pct,true)),node('span','price-sub',numeric(p.interval_low_pct)&&numeric(p.interval_high_pct)?`${percentage(p.interval_low_pct,true)} 至 ${percentage(p.interval_high_pct,true)}`:'区间尚不可用'));tr.append(expected);
    const validation=node('td');validation.append(node('span','price-primary',numeric(p.validation_aggregate?.mae_pct)?`${number(p.validation_aggregate.mae_pct)} 个百分点` : numeric(p.validation_mae_pct)?`${number(p.validation_mae_pct)} 个百分点`:'待验证'),node('span','price-sub',p.validation_protocol_complete?`${number(p.validation_aggregate.samples,0)} 个分段验证样本`:numeric(p.validation_samples)?`${number(p.validation_samples,0)} 样本 · 分段验证不足`:'验证样本不足'));tr.append(validation);
    const score=node('td'), wrap=node('div','score-wrap'), bar=node('span',`score-track${Number(s.score)>=70?' high':''}`), fill=node('i'); fill.style.width=`${Math.max(0,Math.min(100,Number(s.score)||0))}%`; bar.append(fill); wrap.append(node('span','score-number',number(s.score,0)),bar); score.append(wrap); tr.append(score);
    const status=node('td'); status.append(node('span',`pill ${s.stale?'warn':shortEligible(s)?'positive':'neutral'}`,s.excluded?'指标参考':s.stale?'等待更新':shortEligible(s)?'重点关注':!s.prediction?'等待验证':Number(s.score)<45?'风险观察':'观察 · 仅研究')); tr.append(status);
    const levelsEnabled=!s.stale && !state.historyDate && !s.excluded;
    tr.append(node('td','',levelsEnabled?priceRange(s):'—'));
    tr.append(node('td','',levelsEnabled?number(researchValue(s,'stop_loss')):'—'),node('td','',levelsEnabled?number(researchValue(s,'take_profit')):'—'),node('td','table-arrow','↗'));
    fragment.append(tr);
  }); body.append(fragment);
  $('stocks-empty').hidden=filtered.length>0;
  if(!filtered.length)setEmpty('stocks-empty',all.length?'没有匹配的研究结果':'尚无研究结果',all.length?'调整关键词、最低评分或观察条件。':'服务将在同步真实数据后生成研究排名；可点击“更新研究”开始。');
  text('table-summary',`显示 ${Math.min(state.limit,filtered.length)} / ${filtered.length} 只${query||min||actionable?` · 总计 ${all.length} 只`:''} · 研究日期 ${state.dashboard.meta?.report_date || '—'}`);
  $('show-more').hidden=filtered.length<=state.limit;
}

function drawChart(canvas, rawBars, options={}) {
  const rect=canvas.getBoundingClientRect(); if(!rect.width || !rect.height)return;
  const scale=Math.min(devicePixelRatio||1,2), w=rect.width,h=rect.height;
  canvas.width=Math.round(w*scale);canvas.height=Math.round(h*scale);
  const ctx=canvas.getContext('2d');ctx.scale(scale,scale);ctx.clearRect(0,0,w,h);
  const bars=list(rawBars).filter(b=>numeric(b.close)); if(!bars.length)return;
  const pad={left:13,right:59,top:22,bottom:28}, cw=w-pad.left-pad.right,ch=h-pad.top-pad.bottom;
  const vals=bars.map(b=>Number(b.close)), max=Math.max(...vals), min=Math.min(...vals), spread=Math.max(max-min,max*.01,1), lo=min-spread*.13,hi=max+spread*.13;
  const x=i=>pad.left+i*cw/Math.max(1,bars.length-1), y=v=>pad.top+(hi-v)/(hi-lo)*ch;
  ctx.font='9px "Segoe UI", "Microsoft YaHei", sans-serif';ctx.textBaseline='middle';
  for(let i=0;i<5;i++){const val=lo+(hi-lo)*i/4,yy=y(val);ctx.strokeStyle='#edf0f4';ctx.lineWidth=1;ctx.setLineDash([3,4]);ctx.beginPath();ctx.moveTo(pad.left,yy);ctx.lineTo(w-pad.right,yy);ctx.stroke();ctx.fillStyle='#9aa8b9';ctx.textAlign='left';ctx.fillText(number(val),w-pad.right+9,yy);}
  ctx.setLineDash([]);
  if(numeric(options.entryLow) && numeric(options.entryHigh)){
    const top=Math.max(pad.top,y(Number(options.entryHigh))),bottom=Math.min(h-pad.bottom,y(Number(options.entryLow)));
    if(bottom>top){ctx.fillStyle='#5da88720';ctx.fillRect(pad.left,top,cw,bottom-top);ctx.strokeStyle='#70a99366';ctx.setLineDash([4,4]);[top,bottom].forEach(yy=>{ctx.beginPath();ctx.moveTo(pad.left,yy);ctx.lineTo(w-pad.right,yy);ctx.stroke();});ctx.setLineDash([]);}
  }
  const gradient=ctx.createLinearGradient(0,pad.top,0,h-pad.bottom);gradient.addColorStop(0,'#6b90bd22');gradient.addColorStop(1,'#6b90bd02');
  ctx.beginPath(); vals.forEach((v,i)=>i?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v)));ctx.lineTo(x(bars.length-1),h-pad.bottom);ctx.lineTo(x(0),h-pad.bottom);ctx.closePath();ctx.fillStyle=gradient;ctx.fill();
  if(options.ma){ctx.beginPath();ctx.strokeStyle='#bc9e67';ctx.lineWidth=1.25;let started=false;bars.forEach((b,i)=>{const v=numeric(b.ma20)?Number(b.ma20):i>=19?vals.slice(i-19,i+1).reduce((a,b)=>a+b,0)/20:null;if(v!==null){started?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v));started=true;}});ctx.stroke();}
  ctx.beginPath();ctx.strokeStyle='#426893';ctx.lineWidth=1.8;vals.forEach((v,i)=>i?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v)));ctx.stroke();
  const last=vals.length-1;ctx.beginPath();ctx.fillStyle='#426893';ctx.arc(x(last),y(vals[last]),3,0,Math.PI*2);ctx.fill();
  ctx.fillStyle='#99a7b8';ctx.font='9px "Segoe UI", sans-serif';const ticks=Math.min(4,bars.length);for(let i=0;i<ticks;i++){const idx=Math.round(i*(bars.length-1)/Math.max(1,ticks-1));ctx.textAlign=i===0?'left':i===ticks-1?'right':'center';ctx.fillText(String(bars[idx].date||'').slice(5),x(idx),h-10);}
}

function drawIndex(){const bars=list(state.dashboard?.index?.bars).slice(-state.range);$('index-chart-empty').hidden=bars.length>0;if(!bars.length && state.dashboard)text('index-chart-empty','沪指历史数据暂不可用，等待同步');drawChart($('index-chart'),bars,{ma:true});}

async function openStock(stock) {
  state.selected=stock; state.detailData=null;
  const symbol=stock.symbol, historical=state.historyDate;
  text('detail-code',`${symbolLabel(symbol)} · ${historical?'历史快照':'最新研究'}`);text('detail-name',stock.name || symbol);
  $('detail-loading').hidden=false; $('detail-content').hidden=true;
  setEmpty('detail-loading','正在加载个股详情…');
  if(!$('stock-dialog').open)$('stock-dialog').showModal();
  try{
    const result=await api(`/api/stock?symbol=${encodeURIComponent(symbol)}`);
    if(state.selected?.symbol!==symbol || !$('stock-dialog').open)return;
    const detail=historical?stock:result.stock || stock;
    const bars=list(result.bars).filter(b=>!historical || b.date<=historical);
    state.detailData={...result,stock:detail,bars,historical};
    renderDetail();
  }catch(error){if(state.selected?.symbol===symbol)setEmpty('detail-loading','个股数据暂不可用',error.message);}
}

function renderDetail() {
  const d=state.detailData;if(!d)return;const s=d.stock, historical=d.historical, valid=!s.stale && !s.excluded && !historical;
  $('detail-loading').hidden=true;$('detail-content').hidden=false;
  text('detail-price',number(s.raw_close ?? s.close));setChange('detail-change',s.change_pct);text('detail-score',`${number(s.score,0)} / 100`);text('detail-signal',s.stale?'等待更新':s.excluded?'指标参考':shortEligible(s)?'重点关注':'观察 · 仅研究');
  $('detail-signal').className=`pill ${shortEligible(s)?'positive':s.stale?'warn':'neutral'}`;
  let warnings=list(s.warnings).map(String);if(s.excluded)warnings.unshift(s.exclusion_reason || '该股属于排除的权重指标股，仅作参考，不进入短线首选。');if(s.prediction && !s.prediction.eligible)warnings.unshift('预测验证未通过；如下方有价位，仅为研究参考，不构成买入建议，请勿据此强行买入。');if(historical)warnings.unshift('当前为历史研究快照。买入参考与模拟委托不可用于当前交易，历史回测未在此重算。');else if(s.stale)warnings.unshift('行情尚未达到目标交易日，暂停价位建议和模拟买入。');
  $('detail-alert').hidden=!warnings.length;text('detail-alert',warnings.join('\n'));
  requestAnimationFrame(()=>drawChart($('detail-chart'),d.bars.slice(-120),{entryLow:valid?Number(s.entry_low)*(Number(s.close)/Number(s.raw_close || s.close)):null,entryHigh:valid?Number(s.entry_high)*(Number(s.close)/Number(s.raw_close || s.close)):null}));
  text('detail-chart-date',d.bars.length?`${d.bars[Math.max(0,d.bars.length-120)].date} — ${d.bars.at(-1).date}`:'无历史数据');
  const levels=clear($('detail-levels'));
  [['观察买入区间',valid?priceRange(s):'—'],['止损参考',valid?number(researchValue(s,'stop_loss')):'—'],['止盈参考',valid?number(researchValue(s,'take_profit')):'—']].forEach(([label,value])=>{const box=node('div');box.append(node('span','level-label',label),node('strong','level-value',value));levels.append(box);});
  renderPrediction(s.prediction, historical);
  const reasons=clear($('detail-reasons'));list(s.reasons).forEach(r=>reasons.append(node('li','',typeof r==='object'?r.description||r.label||JSON.stringify(r):r)));if(s.forecast?.label)reasons.prepend(node('li','',`${s.forecast.horizon || '趋势观察'}：${s.forecast.label}。${numeric(s.forecast.up_rate_pct)?`同类历史上涨占比 ${percentage(s.forecast.up_rate_pct)}（${s.forecast.sample_count} 个重叠样本，非预测概率）。`:s.forecast.note || ''}`));if(!reasons.childNodes.length)reasons.append(node('li','','暂无足够数据生成评分依据。'));
  const factors=clear($('detail-factors'));
  const factorList=Array.isArray(s.factors)?s.factors:Object.entries(s.factors||{}).map(([name,value])=>({name,value}));
  factorList.forEach(f=>{const row=node('div','factor-row');if(typeof f==='string'){row.textContent=f;}else{row.append(node('span','',f.name||f.label||f.factor||'因子'),node('strong','',f.description||f.reason||(numeric(f.score)?`${number(f.score,0)}${numeric(f.max)?` / ${f.max}`:' 分'}`:f.value??'—')));}factors.append(row);});
  const bt=historical?null:d.backtest, back=clear($('backtest-values'));
  if(bt){[['累计收益',bt.total_return_pct,'%'],['同期个股持有',bt.benchmark_return_pct,'%'],['最大回撤',bt.max_drawdown_pct,'%'],['已平仓交易',bt.closed_trade_count,'次'],['历史胜率',bt.win_rate_pct,'%'],['交易费用',bt.fees,'元']].forEach(([label,value,unit])=>{const n=node('div','backtest-item');n.append(node('span','',label),node('strong','',numeric(value)?`${number(value,unit==='次'?0:2)}${unit}`:'—'));back.append(n);});}
  text('backtest-note',historical?'历史快照不展示使用未来数据计算的回测。':bt?[...list(bt.warnings),...list(bt.notes),bt.note,bt.costs?`成本：${typeof bt.costs==='string'?bt.costs:JSON.stringify(bt.costs)}`:null,bt.start_date&&bt.end_date?`验证区间：${bt.start_date} 至 ${bt.end_date}`:null,'历史理论验证已按策略费用模型计入交易成本；未模拟所有市场冲击，不能保证未来收益。'].filter(Boolean).join(' '):d.backtest_error || '历史样本不足或尚无回测结果。');
  text('detail-asof',`研究日期 ${s.date||'—'} · 曲线为前复权研究价格，委托参考为未复权价`);
  let sources=$('detail-sources');if(!sources){sources=node('div','source-links');sources.id='detail-sources';$('detail-asof').after(sources);}clear(sources);
  if(/^sh\d{6}$/.test(s.symbol))for(const [label,url] of [['腾讯行情',`https://gu.qq.com/${s.symbol}/gp`],['东方财富行情',`https://quote.eastmoney.com/${s.symbol}.html`],['上交所公司公告',`https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId=${s.symbol.slice(2)}`]]){const a=node('a','text-button',label+' ↗');a.href=url;a.target='_blank';a.rel='noopener noreferrer';sources.append(a);}
}

async function loadConfig(){try{const cfg=await api('/api/config');state.config=cfg;state.csrf=cfg.csrf_token||state.csrf;$('setting-universe').value=cfg.universe||'shanghai';$('setting-watchlist').value=Array.isArray(cfg.watchlist)?cfg.watchlist.join(', '):cfg.watchlist||'';$('setting-refresh').value=cfg.refresh_minutes||5;if(cfg.hosted){$('settings-form').querySelectorAll('input,select,button').forEach(n=>n.disabled=true);text('settings-status','云端固定研究全部沪市A股；交易日08:00收集、09:00发布。策略修改请在同一项目中发布更新。');}}catch(e){text('settings-status',e.message);$('settings-status').classList.add('error');}}

async function loadReports(){try{const result=await api('/api/reports'),reports=Array.isArray(result)?result:list(result.reports);const container=clear($('reports-list'));reports.forEach(r=>{const date=typeof r==='string'?r:r.date||r.report_date;const row=node('div','report-row');row.append(node('span','report-date',date),node('span','report-description',numeric(r.count)?`${r.count} 只股票的研究快照`:'每日研究快照'));const button=node('button','button light small','查看研究 ↗');button.addEventListener('click',()=>{state.historyDate=date;$('history-date').value=date;state.limit=100;switchView('overview');loadDashboard();});row.append(button);const download=node('a','text-button','CSV ↓');download.href=`/api/export.csv?date=${encodeURIComponent(date)}`;row.append(download);container.append(row);});$('reports-empty').hidden=reports.length>0;}catch(e){setEmpty('reports-empty','无法加载历史报告',e.message);}loadForecastAudit();}
async function loadForecastAudit(){
  let panel=$('forecast-audit-panel');if(!panel){panel=node('article','panel run-panel');panel.id='forecast-audit-panel';$('view-reports').append(panel);}
  try{const audit=await api('/api/forecast-audit');clear(panel).append(node('h2','','事前预测跟踪'),node('p','fine-print',audit.selection||'只跟踪实际事前记录；不回填历史成绩。'));
    panel.append(node('p','',`已完成 ${number(audit.matured,0)} 条 · 等待D2完成 ${number(audit.waiting,0)} 条 · 数据待核验 ${number(audit.data_unavailable,0)} 条`));
    const groups=audit.by_model_version||{};Object.entries(groups).forEach(([version,summary])=>{const row=node('div','run-row');row.append(node('strong','',version),node('span','',`${number(summary.samples,0)} 条`),node('span','',`误差 ${number(summary.mae_pct)} 个百分点 · 历史方向 ${percentage(summary.direction_accuracy_pct)}`));panel.append(row);});
    if(!audit.matured)panel.append(node('p','fine-print','尚无已完成的事前记录，暂不展示准确率或收益成绩。'));
    const auditLink=node('a','text-button','查看模型历史审计与局限 ↗');auditLink.href='https://github.com/Dopagip00/gup/blob/main/ACCURACY_AUDIT.md';auditLink.target='_blank';auditLink.rel='noopener noreferrer';panel.append(auditLink);
    panel.append(node('p','fine-print',`${audit.plan_window?.scope||''} ${audit.return_basis||''}`));
    list(audit.warnings).forEach(w=>panel.append(node('p','fine-print',String(w))));
  }catch(error){clear(panel).append(node('h2','','事前预测跟踪'),node('p','fine-print',`跟踪记录暂不可用：${error.message}；不以旧回测冒充事前成绩。`));}
}
function renderRuns(runs){const root=clear($('runs-list'));list(runs).slice(0,12).forEach(r=>{const row=node('div','run-row');row.append(node('strong','',shortTime(r.started_at||r.date)),node('span',`pill ${r.status==='success'?'positive':r.status==='error'?'negative':'neutral'}`,statusNames[r.status]||r.status||'—'),node('span','',`${r.target_date||''} · 成功 ${r.ok||0} / ${r.total||0}${r.failed?` · 失败 ${r.failed}`:''}${r.error?` · ${r.error}`:''}`));root.append(row);});if(!runs.length)root.append(node('div','fine-print','暂无运行记录。'));}

async function loadQuotes(){try{const data=await api('/api/quotes');state.quotes=new Map(list(data.quotes).map(q=>[q.symbol,q]));const ix=state.quotes.get('sh000001');const current=ix?`沪指 ${number(ix.price)} (${percentage(ix.change_pct,true)}) · 报价时间 ${shortTime(ix.datetime||ix.timestamp)}`:'暂无可用盘中报价';text('quote-status',`延时报价　${current}　｜　${data.status || '研究评分基于完整日线'}${data.fetched_at?` · 最近获取 ${shortTime(data.fetched_at)}`:''}`);}catch{ text('quote-status','延时报价暂不可用。研究评分基于完整日线；请勿把缓存行情当作实时成交价格。');}}

function renderTopPick(stocks) {
  const meta=state.dashboard?.meta||{},coverage=meta.coverage || {};
  const hasCandidateCoverage=numeric(coverage.candidate_total) && numeric(coverage.candidate_ok);
  const total=hasCandidateCoverage?coverage.candidate_total:coverage.total,ok=hasCandidateCoverage?coverage.candidate_ok:coverage.ok;
  const coverageReady=numeric(total) && Number(total)>0 && numeric(ok) && Number(ok)/Number(total)>=0.99;
  const candidates=state.connected && !state.historyDate && coverageReady ? list(stocks).filter(shortEligible).sort((a,b)=>predictionRank(b)-predictionRank(a)) : [];
  const published=meta.mode==='manual_research',official=meta.daily_buy?.stock||meta.daily_buy;
  const selected=published?(state.connected&&!state.historyDate&&meta.daily_plan_status==='published'&&official?official:null):candidates[0]||null; state.topPick=selected;
  $('pick-metrics').hidden=!selected; $('pick-details').hidden=!selected; $('pick-empty-label').hidden=!!selected;
  $('top-pick').classList.toggle('has-pick',!!selected);
  text('pick-date',selected&&published?`${meta.daily_buy.recommended_date||''} · 盘前日报`:state.dashboard?.meta?.data_date || '');
  if(!selected){text('pick-horizon','两交易日周期');text('pick-name','暂无经过验证的首选');text('pick-thesis',state.historyDate?'当前为历史复盘。返回最新研究后，按有效数据与验证结果重新评估。':!state.connected?'等待研究服务连接，暂不产生新的首选。':published?(meta.daily_plan_message||'09:00日报尚未发布。'):!coverageReady?`${hasCandidateCoverage?'非指标候选':'全沪研究'}有效覆盖未达99%，暂不生成唯一首选。指标股排除数与研究待补齐数分别统计。`:'股票、数据有效性与预测验证必须同时通过。当前等待满足全部条件的机会。');return;}
  const p=selected.prediction;
  text('pick-horizon',horizonLabel(p));
  text('pick-name',selected.name || selected.symbol);
  text('pick-thesis',`${symbolLabel(selected.symbol)} · ${returnBasis(p)}。当日买入研究与前日建议卖出参考分别更新，由你自行操作。`);
  text('pick-return',percentage(p.expected_return_pct,true));
  text('pick-interval',`估计区间 ${percentage(p.interval_low_pct,true)} 至 ${percentage(p.interval_high_pct,true)}`);
  text('pick-entry',numeric(selected.entry_low)&&numeric(selected.entry_high)?`${number(selected.entry_low)} – ${number(selected.entry_high)}`:'等待有效买入区间');
  text('pick-validation',`分段验证误差 ${number(p.validation_aggregate?.mae_pct??p.validation_mae_pct)} 个百分点 · ${number(p.validation_aggregate?.samples??p.validation_samples,0)} 个样本`);
}

function renderPrediction(prediction, historical) {
  const root=clear($('detail-prediction')), p=visiblePrediction(prediction);
  root.append(node('h3','',`${horizonLabel(p)} · 预期与验证`),node('p','prediction-basis',returnBasis(p)));
  const grid=node('div','prediction-grid');
  [['预计涨幅',percentage(p.expected_return_pct,true)],['估计区间',numeric(p.interval_low_pct)&&numeric(p.interval_high_pct)?`${percentage(p.interval_low_pct,true)} 至 ${percentage(p.interval_high_pct,true)}`:'—'],['验证平均绝对误差',numeric(p.validation_mae_pct)?`${number(p.validation_mae_pct)} 个百分点`:'—'],['验证方向准确率',percentage(p.validation_direction_accuracy_pct)],['验证样本',number(p.validation_samples,0)],['训练 / 历史样本',number(p.sample_count,0)]].forEach(([label,value])=>{const box=node('div');box.append(node('span','level-label',label),node('strong','level-value',value));grid.append(box);});
  root.append(grid);
  const evidence=node('section','nextday-summary');evidence.append(node('h3','',p.validation_protocol_complete?'分段验证与成本压力':'尚未完成当前分段验证'));
  const evidenceGrid=node('div','nextday-grid'), aggregate=p.validation_aggregate||{}, nonoverlap=p.validation_nonoverlapping||{};
  [['分段通过',Array.isArray(p.validation_folds)?`${p.validation_folds.filter(f=>f.beats_both_baselines).length} / ${p.validation_folds.length}`:'—'],['分段平均误差',numeric(aggregate.mae_pct)?`${number(aggregate.mae_pct)} 个百分点`:'—'],['零收益基准误差',numeric(aggregate.zero_baseline_mae_pct)?`${number(aggregate.zero_baseline_mae_pct)} 个百分点`:'—'],['非重叠验证样本',number(nonoverlap.samples,0)],['非重叠方向准确率',percentage(nonoverlap.direction_accuracy_pct)],['成本压力后预期',percentage(p.cost_stress_return_pct,true)]].forEach(([label,value])=>{const box=node('div');box.append(node('span','level-label',label),node('strong','level-value',value));evidenceGrid.append(box);});
  evidence.append(evidenceGrid,node('p','fine-print',p.reason||'等待完整验证证据。'),node('p','fine-print','成本压力使用每回合0.30个百分点的研究假设，并非个人实际费用。非重叠处理不能消除股票、市场与时期之间的相关性；以上均不是本次上涨概率。'));root.append(evidence);
  const next=p.next_day || {},nextSummary=node('section','nextday-summary'),nextHeading=node('h3');
  const nextAvailable=next.available!==false && numeric(next.expected_return_pct);
  nextHeading.append(document.createTextNode('下一交易日 · 预测摘要'),node('span',`pill ${next.eligible===true?'positive':'warn'}`,next.eligible===true?'通过研究筛选':nextAvailable?'未通过预测筛选':'样本不足 / 暂不可用'));nextSummary.append(nextHeading);
  const nextGrid=node('div','nextday-grid');
  [['次日预期涨幅',nextAvailable?percentage(next.expected_return_pct,true):'—'],['经验区间',nextAvailable&&numeric(next.interval_low_pct)&&numeric(next.interval_high_pct)?`${percentage(next.interval_low_pct,true)} 至 ${percentage(next.interval_high_pct,true)}`:'—'],['验证 MAE',numeric(next.validation_mae_pct)?`${number(next.validation_mae_pct)} 个百分点`:'—'],['验证样本数',number(next.validation_samples,0)]].forEach(([label,value])=>{const box=node('div');box.append(node('span','level-label',label),node('strong','level-value',value));nextGrid.append(box);});
  nextSummary.append(nextGrid,node('p','fine-print',next.reason || (next.eligible===true?`次日预测独立通过研究筛选；首页按${horizonLabel(p)}的综合值排名。`:'次日预测尚未通过验证或样本不足，暂不据此生成买入建议。')));root.append(nextSummary);
  root.append(node('p','fine-print',`${p.method || '预测尚未完成验证。'} ${historical?'当前展示历史快照中的预测信息。':''} 预计涨幅有估计误差；验证方向准确率仅描述历史验证样本，不代表本次上涨概率。经验区间未校准覆盖概率，不保证覆盖实际收益。`));
}

async function loadLongterm() {
  const button=$('longterm-refresh');button.disabled=true;
  try {
    const result=await api('/api/longterm');state.longterm=result;const stocks=list(result.stocks).sort((a,b)=>(Number(b.score)||0)-(Number(a.score)||0));
    const eligible=stocks.filter(s=>s.eligible===true && !s.stale), reference=stocks.filter(s=>s.eligible!==true || s.stale);
    const coverage=result.coverage||{};renderCounts('longterm-coverage',[['估值筛查范围',coverage.universe??coverage.total],['估值合格待核验总数',coverage.valuation_qualified??coverage.candidate_total],['已核验财报',coverage.financial_requested??coverage.processed??stocks.length],['待处理',coverage.pending??coverage.not_researched],['财务信息完整',coverage.financial_complete],['合格候选',coverage.eligible??eligible.length]]);
    const warnings=list(result.warnings).map(String);$('longterm-alert').hidden=!warnings.length;$('longterm-alert').classList.remove('error');text('longterm-alert',warnings.join('\n'));
    text('longterm-asof',`数据时点：${result.as_of || '尚未提供'} · 已研究 ${stocks.length} 只，合格 ${eligible.length} 只`);text('longterm-count',`${eligible.length} 只`);
    const body=clear($('longterm-body')),theses=clear($('longterm-theses'));
    eligible.forEach(s=>{const tr=node('tr'),name=node('td');name.append(node('span','stock-name',s.name||s.symbol),node('span','stock-code',symbolLabel(s.symbol)));tr.append(name);[number(s.score,0),number(s.pe_ttm),number(s.pb)].forEach(value=>tr.append(node('td','',value)));
      const roe=node('td');roe.append(node('span','price-primary',percentage(s.roe)),node('span','price-sub',s.roe_report_date?`完整年度 ${s.roe_report_date}`:'完整年度未提供'));roe.title=s.roe_basis || '最近已公告完整年度的加权 ROE';tr.append(roe);
      [percentage(s.revenue_growth),percentage(s.profit_growth),s.report_date||'—',s.publication_date||'—'].forEach(value=>tr.append(node('td','',value)));body.append(tr);
      const card=node('article','panel thesis-card'),head=node('div','panel-header');head.append(node('h2','',s.name||s.symbol),node('span','pill neutral',symbolLabel(s.symbol)));card.append(head);
      const content=node('div','thesis-content');content.append(node('h3','','长期观察逻辑'));list(s.thesis).forEach(t=>content.append(node('p','',typeof t==='object'?t.description||t.reason||JSON.stringify(t):t)));if(!s.thesis || Array.isArray(s.thesis)&&!s.thesis.length)content.append(node('p','','尚无足够基本面数据形成结论。'));
      content.append(node('h3','','需要关注的风险'));list(s.risks).forEach(r=>content.append(node('p','',typeof r==='object'?r.description||JSON.stringify(r):r)));if(!s.risks || Array.isArray(s.risks)&&!s.risks.length)content.append(node('p','','请进一步核对财报质量、行业周期与估值变化。'));
      content.append(node('div','fine-print',`最新财报期：${s.report_date || '未提供'} · 公告日：${s.publication_date || '未提供'} · 完整年度加权 ROE 对应报告期：${s.roe_report_date || '未提供'}`),node('div','fine-print',`来源：${s.source || '未提供'} · 数据日期：${s.date || '未提供'}`));card.append(content);theses.append(card);
    });$('longterm-empty').hidden=eligible.length>0;
    if(!eligible.length)setEmpty('longterm-empty','暂无合格长线候选','仅通过基本面筛选且数据未过期的股票进入候选。其他已取得数据保留在下方参考区。');
    const referenceBody=clear($('longterm-reference-body'));text('longterm-reference-count',`${reference.length} 只`);$('longterm-reference-panel').hidden=!reference.length;
    reference.forEach(s=>{const tr=node('tr'),name=node('td');name.append(node('span','stock-name',s.name||s.symbol),node('span','stock-code',symbolLabel(s.symbol)));tr.append(name);const status=node('td');status.append(node('span','pill warn',s.stale?'数据过期':'未通过筛选'));tr.append(status,node('td','',s.report_date||'—'),node('td','',s.publication_date||'—'));const risks=list(s.risks).map(r=>typeof r==='object'?r.description||JSON.stringify(r):String(r));tr.append(node('td','reference-reason',[s.stale?'快照已过期，需重新取得有效数据。':null,...risks].filter(Boolean).join('；') || '财务信息或筛选条件尚不完整。'));referenceBody.append(tr);});
  }catch(error){$('longterm-alert').hidden=false;$('longterm-alert').classList.add('error');text('longterm-alert',`长线研究暂不可用：${error.message}。已有内容如仍显示，仅为上次获取的记录。`);if(!state.longterm)setEmpty('longterm-empty','等待基本面数据接口','数据取得后再显示长线分析，不使用模拟数值。');}finally{button.disabled=false;}
}

function renderCounts(id, pairs) {
  const root=clear($(id));pairs.forEach(([label,value])=>{const item=node('div','coverage-item');item.append(node('span','',label),node('strong','',numeric(value)?number(value,0):value??'—'));root.append(item);});
}

function researchValue(stock, key) {
  for(const source of [stock,stock?.research_levels,stock?.reference_levels,stock?.levels])if(numeric(source?.[key]))return source[key];
  return numeric(stock?.[`research_${key}`])?stock[`research_${key}`]:null;
}
function priceRange(stock, low='entry_low', high='entry_high') {const a=researchValue(stock,low),b=researchValue(stock,high);return numeric(a)&&numeric(b)?`${number(a)} – ${number(b)}`:'—';}

async function loadDailyPlan() {
  const button=$('plan-refresh');button.disabled=true;
  try {
    const result=await api('/api/daily-plan');state.dailyPlan=result;
    const statuses={published:'当日日报已发布',closed:'非交易日',ready:'今日研究已更新',completed:'今日研究已更新',partial:'部分信息待补齐',pending:'等待今日研究',waiting:'等待今日研究',error:'研究更新异常',research_only:'仅研究参考',no_candidate:'暂无合格买入建议',non_trading_day:'非交易日'};
    text('daily-plan-date',result.date||result.as_of||'尚无报告日期');text('daily-plan-status',statuses[result.status]||result.status||'研究状态未提供');
    const warnings=list(result.warnings).map(String);if(result.message)warnings.unshift(String(result.message));$('daily-plan-alert').hidden=!warnings.length;text('daily-plan-alert',warnings.join('\n'));
    const buy=result.buy;let stock=null;
    if(buy){const nested=buy.stock&&typeof buy.stock==='object'?buy.stock:{};stock={...nested,...buy,symbol:buy.symbol||nested.symbol||(typeof buy.stock==='string'?buy.stock:null)};}
    state.planStock=stock;
    const qualified=stock&&shortEligible(stock)&&stock.status!=='research_only'&&!result.stale;
    text('plan-buy-name',stock?.name||stock?.symbol||'暂无今日买入研究');text('plan-buy-code',stock?`${symbolLabel(stock.symbol)} · ${result.date||stock.date||'报告日期未提供'}`:'没有合格信号时保留空白，不强行推荐');
    text('plan-buy-status',!stock?'等待研究':qualified?'重点关注':'仅研究价位');$('plan-buy-status').className=`pill ${qualified?'positive':'warn'}`;
    text('plan-buy-entry',stock?priceRange(stock):'—');text('plan-buy-target',number(stock&&researchValue(stock,'take_profit')));text('plan-buy-stop',number(stock&&researchValue(stock,'stop_loss')));text('plan-buy-expected',percentage(stock?.prediction?.expected_return_pct??stock?.expected_return_pct,true));
    text('plan-buy-note',!stock?'暂无可用买入研究。请等待完整交易日数据、信息整理与模型校验。':qualified?'价位为模型研究参考，不保证触价、成交或盈利。请自行核对实时报价、公告与交易成本。':'仅研究价位：该候选尚未通过完整验证，不构成买入建议，请勿据此强行买入。缺失价格保留为空。');
    const reasons=clear($('plan-buy-reasons'));list(stock?.reasons||buy?.reasons).forEach(r=>reasons.append(node('li','',typeof r==='object'?r.description||r.reason||JSON.stringify(r):r)));$('plan-buy-detail').hidden=!stock?.symbol;
    const sells=list(result.sell),body=clear($('daily-sell-body'));text('daily-sell-count',`${sells.length} 只`);$('daily-sell-empty').hidden=sells.length>0;
    sells.forEach(s=>{const tr=node('tr'),name=node('td');name.append(node('span','stock-name',s.name||s.symbol),node('span','stock-code',symbolLabel(s.symbol)));tr.append(name,node('td','',s.recommended_date||s.entry_date||'—'),node('td','',priceRange(s)),node('td','',priceRange(s,'sell_low','sell_high')),node('td','',number(researchValue(s,'take_profit'))),node('td','',number(researchValue(s,'stop_loss'))));const status=node('td');status.append(node('span',`pill ${s.stale?'warn':'neutral'}`,s.stale?'等待更新':({reference:'卖出参考',data_unavailable:'等待有效行情',ready:'卖出参考',waiting:'等待价位',risk:'风险观察',research_only:'仅研究参考',expired:'历史建议',hold:'继续观察'}[s.status]||s.status||'研究参考')));tr.append(status,node('td','reference-reason',list(s.reasons).map(r=>typeof r==='object'?r.description||JSON.stringify(r):String(r)).join('；')||'根据你的实际持仓及可卖数量自行决定。'));body.append(tr);});
    const news=list(result.news),newsRoot=clear($('daily-news-list'));$('daily-news-empty').hidden=news.length>0;
    news.forEach(item=>{const article=node('article','daily-news-item');const title=typeof item==='string'?item:item.title||item.name||'信息记录';const heading=node('h3','',title);if(typeof item==='object'&&item.url){try{const link=new URL(item.url);if(['https:','http:'].includes(link.protocol)){const anchor=node('a','',title);anchor.href=link.href;anchor.target='_blank';anchor.rel='noopener noreferrer';heading.replaceChildren(anchor);}}catch{}}article.append(heading);if(typeof item==='object'){if(item.summary||item.description)article.append(node('p','',item.summary||item.description));article.append(node('span','fine-print',`${item.source||'来源未提供'} · ${shortTime(item.published_at||item.date||item.datetime)}`));}newsRoot.append(article);});
    text('daily-refreshed',`报告更新：${shortTime(result.published_at||result.updated_at||result.generated_at||result.fetched_at)} · 北京时间`);
  }catch(error){$('daily-plan-alert').hidden=false;text('daily-plan-alert',`每日研究计划暂不可用：${error.message}。已有显示如有，仅为上次获取结果，请核对报告日期。`);}finally{button.disabled=false;}
}

async function loadSystem() {
  const button=$('system-refresh');button.disabled=true;
  try {
    const result=await api('/api/system'),v=typeof result.version==='object'?result.version:result;$('system-alert').hidden=true;text('system-version',v.version);text('system-schema',v.data_schema_version);text('system-project-id',v.project_id);text('system-project-path',result.project_path|| (result.hosted?'云端托管':'未提供'));
    const migration=result.migration||{};text('migration-status',migration.message||migration.description||migration.status||'请按迁移说明备份数据，再在目标环境恢复并核对版本。');
    const backup=migration.backup_command||migration.commands?.backup,restore=migration.restore_command||migration.commands?.restore;
    text('migration-backup-command',backup||'备份命令由部署环境的迁移说明提供');text('migration-restore-command',restore);$('migration-restore-wrap').hidden=!restore;
  }catch(error){$('system-alert').hidden=false;text('system-alert',`暂未取得版本与迁移信息：${error.message}。请以部署环境中的迁移说明为准。`);}finally{button.disabled=false;}
}

document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>switchView(b.dataset.view)));
window.addEventListener('hashchange',()=>switchView(location.hash.slice(1)));
$('method-btn').addEventListener('click',()=>switchView('settings'));
$('refresh-btn').addEventListener('click',async()=>{$('refresh-btn').disabled=true;try{const result=await api('/api/refresh',{method:'POST',body:'{}'});message(result.message||(result.status==='running'?'研究任务已在运行':'已开始更新真实行情和研究排名'));await loadDashboard();}catch(e){message(e.message,true);$('refresh-btn').disabled=false;}});
$('history-date').addEventListener('change',()=>{state.historyDate=$('history-date').value;state.limit=100;loadDashboard();});
$('today-btn').addEventListener('click',()=>{state.historyDate='';state.limit=100;loadDashboard();});
['stock-search','min-score','actionable-only','show-excluded','stock-sort'].forEach(id=>$(id).addEventListener(id==='stock-search'?'input':'change',()=>{state.limit=100;renderStocks();}));
$('show-more').addEventListener('click',()=>{state.limit+=100;renderStocks();});
document.querySelectorAll('[data-range]').forEach(button=>button.addEventListener('click',()=>{state.range=Number(button.dataset.range);document.querySelectorAll('[data-range]').forEach(b=>b.classList.toggle('active',b===button));drawIndex();}));
$('close-dialog').addEventListener('click',()=>$('stock-dialog').close());
$('stock-dialog').addEventListener('click',e=>{if(e.target===$('stock-dialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
$('settings-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.currentTarget.querySelector('button[type=submit]');button.disabled=true;$('settings-status').classList.remove('error');text('settings-status','正在保存…');try{await api('/api/config',{method:'POST',body:JSON.stringify({universe:$('setting-universe').value,watchlist:$('setting-watchlist').value,refresh_minutes:Number($('setting-refresh').value)})});text('settings-status','设置已保存。新的股票范围将在下次研究更新时生效；可返回市场页面点击“更新研究”。');message('研究设置已保存');await loadDashboard();}catch(e){text('settings-status',e.message);$('settings-status').classList.add('error');}finally{button.disabled=false;}});
$('reports-refresh').addEventListener('click',loadReports);
$('longterm-refresh').addEventListener('click',loadLongterm);
$('plan-refresh').addEventListener('click',loadDailyPlan);
$('plan-buy-detail').addEventListener('click',()=>{if(state.planStock?.symbol)openStock(state.planStock);});
$('system-refresh').addEventListener('click',loadSystem);
$('pick-details').addEventListener('click',()=>{if(state.topPick)openStock(state.topPick);});
let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{drawIndex();if($('stock-dialog').open&&state.detailData)renderDetail();},140);});
function clock(){text('clock',new Date().toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}));}
clock();setInterval(clock,30000);
switchView(location.hash.slice(1)||'overview');
Promise.allSettled([loadDashboard(),loadConfig(),loadQuotes()]);
setInterval(()=>{if(!document.hidden){loadDashboard();loadQuotes();if(state.view==='reports')loadReports();if(state.view==='longterm')loadLongterm();if(state.view==='plan')loadDailyPlan();}},30000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden){loadDashboard();loadQuotes();if(state.view==='plan')loadDailyPlan();}});

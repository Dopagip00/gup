const test=require('node:test'),assert=require('node:assert/strict');
const p=require('../pages.js');
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

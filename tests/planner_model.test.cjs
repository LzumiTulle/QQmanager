const test=require('node:test'),assert=require('node:assert/strict');
const {taskDate,totals,shift,lanes}=require('../web/planner-model');
test('calendar totals use Beijing date and only open tasks in displayed range',()=>{
 assert.equal(taskDate('2026-11-16T17:00:00Z'),'2026-11-17');
 assert.deepEqual(totals([{date:'2026-11-17',courses:[{},{}]}],[{status:'open',due:'2026-11-16T17:00:00Z'},{status:'done',due:'2026-11-17T09:00:00+08:00'},{status:'open',due:null}]),{courses:2,tasks:1});
 assert.equal(shift('2026-01-31',1,'month'),'2026-02-01');assert.equal(shift('2026-12-31',1,'month'),'2027-01-01');assert.equal(shift('2026-11-17',-1,'week'),'2026-11-10');
});
test('overlapping classes occupy separate lanes while empty periods stay empty',()=>{
 const rows=lanes([{start_period:5,end_period:6},{start_period:1,end_period:4},{start_period:3,end_period:4}]);
 assert.deepEqual(rows.map(r=>[r.course.start_period,r.lane,r.lanes]),[[1,0,2],[3,1,2],[5,0,1]]);
});

(function(root){
 'use strict';
 function taskDate(value){if(!value)return '';const d=new Date(value);if(!Number.isFinite(d.getTime()))return '';return new Date(d.getTime()+8*3600000).toISOString().slice(0,10);}
 function totals(days,tasks){const dates=new Set(days.map(d=>d.date));return {courses:days.reduce((n,d)=>n+d.courses.length,0),tasks:tasks.filter(t=>t.status==='open'&&dates.has(taskDate(t.due))).length};}
 function shift(date,n,scope){const d=new Date(date+'T12:00:00Z');if(scope==='month'){d.setUTCDate(1);d.setUTCMonth(d.getUTCMonth()+n);}else d.setUTCDate(d.getUTCDate()+n*(scope==='week'?7:1));return d.toISOString().slice(0,10);}
 function lanes(courses){const rows=[...courses].sort((a,b)=>a.start_period-b.start_period),out=[];let cluster=[],end=0;function flush(){const ends=[];for(const course of cluster){let lane=ends.findIndex(e=>e<course.start_period);if(lane<0)lane=ends.length;ends[lane]=course.end_period;out.push({course,lane,cluster:cluster[0]});}const count=ends.length;for(const r of out)if(r.cluster===cluster[0])r.lanes=count;cluster=[];}for(const c of rows){if(cluster.length&&c.start_period>end)flush();if(!cluster.length)end=0;cluster.push(c);end=Math.max(end,c.end_period);}if(cluster.length)flush();return out;}
 // Notice and planner fields are Beijing wall-clock values, regardless of the device timezone.
 function expiryFor(fact) {
   if (!fact?.value || !/^\d{4}-\d{2}-\d{2}(?:T(?:[01]\d|2[0-3]):[0-5]\d)?$/.test(fact.value)) return null;
   const day = fact.value.slice(0,10);
   const deadline = fact.kind === 'deadline';
   const clock = deadline ? fact.value.slice(11) : fact.end;
   const midnight = Date.parse(day+'T00:00:00+08:00');
   // An event with only a start time has no known finish. Keep it current for that day.
   const at = clock ? Date.parse(day+'T'+clock+':00+08:00') : midnight+86400000;
   if (!Number.isFinite(at)) return null;
   const label = deadline ? '已逾期' : fact.kind === 'plan' ? '计划时间已过' : '已过期';
   return {at,label};
 }
 const api={taskDate,totals,shift,lanes,expiryFor};if(typeof module!=='undefined')module.exports=api;root.QQPlanner=api;
})(typeof window!=='undefined'?window:globalThis);

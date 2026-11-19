'use strict';
// Display-only grouping: originals, evidence and saved/read state remain intact.
(function(root){
 const topics=['考试','课程','学业','活动','通知','待办','资料','其他'];
 function topic(item){
  if(['考试','课程','学业','活动'].includes(item.topic))return item.topic;
  const title=String(item.title||'');
  if(/考试|补考|缓考|重修|考场|考务|准考证|四六级|四级|六级/.test(title))return '考试';
  if(/调课|停课|补课|上课地点|课程调整|课堂签到/.test(title))return '课程';
  if(/学业|学位|学分|培养方案|毕业|选课|奖学金|助学金|读书笔记|作业/.test(title))return '学业';
  if(item.kind==='活动'||/活动|今晚签到|讲座报名/.test(title))return '活动';
  if(topics.includes(item.topic))return item.topic;
  return ['通知','待办','资料'].includes(item.kind)?item.kind:'其他';
 }
 function group(items){return topics.map(name=>({name,items:items.filter(i=>topic(i)===name)})).filter(g=>g.items.length);}
 const api={topics,topic,group};
 if(typeof module==='object'&&module.exports)module.exports=api;else root.QQFilters=api;
})(typeof globalThis==='object'?globalThis:this);

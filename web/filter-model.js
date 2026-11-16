'use strict';
// Display-only grouping: originals, evidence and saved/read state remain intact.
(function(root){
 const topics=['考试','学业','通知','待办','资料','其他'];
 function topic(item){
  const title=String(item.title||'');
  if(/考试|补考|缓考|重修|考场|考务|准考证|四六级|四级|六级/.test(title))return '考试';
  if(/学业|学位|学分|培养方案|毕业|选课|奖学金|助学金/.test(title))return '学业';
  return ['通知','待办','资料'].includes(item.kind)?item.kind:'其他';
 }
 function group(items){return topics.map(name=>({name,items:items.filter(i=>topic(i)===name)})).filter(g=>g.items.length);}
 const api={topics,topic,group};
 if(typeof module==='object'&&module.exports)module.exports=api;else root.QQFilters=api;
})(typeof globalThis==='object'?globalThis:this);

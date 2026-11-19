'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {topic,group}=require('../web/filter-model.js');
test('topic keywords group exam and academic notices without borrowing unrelated source text',()=>{
 assert.equal(topic({title:'第13周考试安排',kind:'通知'}),'考试');
 assert.equal(topic({title:'补考报名截止',kind:'待办'}),'考试');
 assert.equal(topic({title:'学位自查材料收集',kind:'待办'}),'学业');
 assert.equal(topic({title:'项目测试报告提交',kind:'待办',content:'上文有考试安排'}),'待办');
 assert.equal(topic({title:'Python 教程',kind:'资料'}),'资料');
 assert.equal(topic({title:'今晚活动签到',kind:'通知'}),'活动');
 assert.equal(topic({title:'第一次读书笔记审核与上交',topic:'通知'}),'学业');
 assert.equal(topic({title:'早八课程上课地点通知',topic:'通知'}),'课程');
 assert.equal(topic({title:'今晚签到：请先报名',topic:'通知'}),'活动');
});
test('display grouping retains every item exactly once in its original order and state',()=>{
 const items=[{id:9,title:'考试安排',kind:'通知',status:'saved'},{id:7,title:'学分核查',kind:'通知'},{id:3,title:'补考安排',kind:'待办'},{id:1,title:'交流活动',kind:'活动'}];
 const result=group(items);
 assert.deepEqual(result.map(g=>[g.name,g.items.map(i=>i.id)]),[['考试',[9,3]],['学业',[7]],['活动',[1]]]);
 assert.equal(result[0].items[0],items[0]);assert.equal(items[0].status,'saved');
 assert.equal(result.flatMap(g=>g.items).length,items.length);
});

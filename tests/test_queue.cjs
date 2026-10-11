const test=require('node:test');
const assert=require('node:assert/strict');
const queue=require('../web/queue.js');
test('sequence stops at end and loop wraps in both directions',()=>{
  const q=queue.make(['a','b','c'],null);
  assert.equal(queue.move(q),'a');assert.equal(queue.move(q),'b');assert.equal(queue.move(q),'c');assert.equal(queue.move(q),null);
  q.mode='loop';assert.equal(queue.move(q),'a');assert.equal(queue.move(q,-1),'c');
  assert.equal(queue.move(queue.make(['a'],'a','loop')),'a');assert.equal(queue.move(queue.make([])),null);
});
test('shuffle visits each track, avoids immediate repeat, and retraces history',()=>{
  const q=queue.make(['a','b','c'],'a','shuffle');
  assert.equal(queue.move(q,1,()=>0),'b');assert.equal(queue.move(q,1,()=>0),'c');
  assert.equal(queue.move(q,-1),'b');assert.equal(queue.move(q,1),'c');assert.equal(queue.move(q,1,()=>0),'a');
  assert.equal(queue.move(queue.make(['a'],'a','shuffle')),'a');
});
test('deleted files and new playlist additions are reconciled',()=>{
  const q=queue.make(['a','b','c'],'a','shuffle');queue.move(q,1,()=>0);queue.move(q,1,()=>0);
  queue.reconcile(q,['a','c','d']);assert.equal(queue.move(q,-1),'a');assert.equal(queue.move(q,1),'c');
  assert.equal(queue.move(q,1,()=>0),'d');
  const seq=queue.make(['a','b','c'],'b');queue.reconcile(seq,['a','c']);assert.equal(queue.move(seq),'a');
  queue.reconcile(seq,[]);assert.equal(queue.move(seq),null);
});

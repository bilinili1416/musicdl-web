const test=require('node:test');
const assert=require('node:assert/strict');
const memory=require('../web/memory.js');
const a='a'.repeat(32),b='b'.repeat(32);

test('recent searches survive serialization, deduplicate, move to front and stay bounded',()=>{
  let history=[];
  for(let i=0;i<25;i++)history=memory.rememberSearch(history,`song ${i}`,i);
  history=memory.rememberSearch(history,'ＳＯＮＧ 20',30);
  const restored=memory.history(JSON.parse(JSON.stringify(history)));
  assert.equal(restored.length,20);
  assert.equal(restored[0].keyword,'ＳＯＮＧ 20');
  assert.equal(restored.filter(item=>item.keyword.toLowerCase()==='song 20').length,0);
  assert.deepEqual(memory.history([]),[]);
});
test('malformed browser history cannot turn into search requests',()=>{
  assert.deepEqual(memory.history({keyword:'wrong shape'}),[]);
  assert.deepEqual(memory.history([null,{}, {keyword:'   ',updated:1},{keyword:'x'.repeat(101),updated:2},{keyword:5,updated:1}]),[]);
});
test('each song retains its position and playback end restarts only that song',()=>{
  let saved=memory.rememberPlayback({},a,45.25,false,1);
  saved=memory.rememberPlayback(saved,b,88,false,2);
  saved=memory.playback(JSON.parse(JSON.stringify(saved)));
  assert.equal(saved.lastFile,b);
  assert.equal(saved.positions[a].time,45.25);
  assert.equal(memory.resumeTime(saved.positions[b].time,240),88);
  saved=memory.rememberPlayback(saved,b,240,true,3);
  assert.equal(saved.positions[b].time,0);
  assert.equal(saved.positions[a].time,45.25);
});
test('shorter replacements, completed tracks and invalid times start from zero',()=>{
  assert.equal(memory.resumeTime(120,90),0);
  assert.equal(memory.resumeTime(89.9,90),0);
  assert.equal(memory.resumeTime(-1,90),0);
  assert.equal(memory.resumeTime(30,NaN),0);
  assert.equal(memory.resumeTime(30,90),30);
  assert.deepEqual(memory.playback({lastFile:'../secret',positions:{[a]:{time:Infinity,updated:1}}}),{version:1,lastFile:null,positions:{}});
});

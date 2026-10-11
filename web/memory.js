'use strict';

// Shared by the browser and Node checks; only small metadata is stored here.
(function (root) {
  const fileId = value => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
  const key = value => value.normalize('NFKC').toLocaleLowerCase();
  function history(value) {
    const seen = new Set();
    return (Array.isArray(value) ? value : []).filter(item => item && typeof item.keyword === 'string' && item.keyword.trim().length > 0 && item.keyword.trim().length <= 100 && Number.isFinite(item.updated))
      .sort((a,b) => b.updated-a.updated).filter(item => {const id=key(item.keyword.trim());if(seen.has(id))return false;seen.add(id);return true;})
      .slice(0,20).map(item => ({keyword:item.keyword.trim(),updated:item.updated}));
  }
  function rememberSearch(value, keyword, now=Date.now()) {
    return history([{keyword,updated:now},...history(value).filter(item=>key(item.keyword)!==key(keyword.trim()))]);
  }
  function playback(value) {
    const positions = {};
    if(value && value.positions && typeof value.positions==='object' && !Array.isArray(value.positions)) {
      Object.entries(value.positions).filter(([id,item])=>fileId(id) && item && Number.isFinite(item.time) && item.time>=0 && item.time<604800 && Number.isFinite(item.updated))
        .sort((a,b)=>b[1].updated-a[1].updated).slice(0,200).forEach(([id,item])=>{positions[id]={time:item.time,updated:item.updated};});
    }
    return {version:1,lastFile:fileId(value?.lastFile)?value.lastFile:null,positions};
  }
  function rememberPlayback(value, id, time, ended=false, now=Date.now()) {
    const saved=playback(value);
    if(!fileId(id) || !Number.isFinite(time) || time<0 || time>=604800)return saved;
    saved.lastFile=id;
    saved.positions[id]={time:ended?0:time,updated:now};
    return playback(saved);
  }
  function resumeTime(saved, duration) {
    return Number.isFinite(saved) && saved>0 && Number.isFinite(duration) && saved<duration-.5 ? saved : 0;
  }
  const memory={history,rememberSearch,playback,rememberPlayback,resumeTime};
  if(typeof module==='object' && module.exports)module.exports=memory;
  else root.MusicdlMemory=memory;
})(globalThis);

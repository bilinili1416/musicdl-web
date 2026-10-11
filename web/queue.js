'use strict';
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.MusicdlQueue=factory();})(typeof globalThis!=='undefined'?globalThis:this,()=>{
  const unique=ids=>[...new Set(ids.filter(id=>typeof id==='string'))];
  function make(ids,current=null,mode='sequence'){
    ids=unique(ids);return {ids,current,mode,history:current?[current]:[],cursor:current?0:-1,remaining:ids.filter(id=>id!==current)};
  }
  function reconcile(q,ids){
    ids=unique(ids);const added=ids.filter(id=>!q.ids.includes(id)&&id!==q.current);
    q.remaining=unique([...q.remaining.filter(id=>ids.includes(id)),...added]);q.ids=ids;
    // Retain the cursor even when a track disappears; movement skips missing entries.
    return q;
  }
  function move(q,direction=1,random=Math.random){
    if(!q.ids.length)return null;
    let next;
    if(q.mode==='shuffle'){
      let cursor=q.cursor+direction;
      while(cursor>=0&&cursor<q.history.length&&!q.ids.includes(q.history[cursor]))cursor+=direction;
      if(cursor>=0&&cursor<q.history.length){q.cursor=cursor;next=q.history[cursor];}
      else if(direction<0)return null;
      else{
        if(!q.remaining.length)q.remaining=q.ids.filter(id=>id!==q.current);
        if(!q.remaining.length)q.remaining=[...q.ids];
        const index=Math.min(q.remaining.length-1,Math.max(0,Math.floor(random()*q.remaining.length)));
        next=q.remaining.splice(index,1)[0];q.history=q.history.slice(0,q.cursor+1);q.history.push(next);
        if(q.history.length>200)q.history.shift();q.cursor=q.history.length-1;
      }
    }else{
      const at=q.ids.indexOf(q.current);let index=at<0?(direction>0?0:q.ids.length-1):at+direction;
      if(q.mode==='loop')index=(index+q.ids.length)%q.ids.length;
      if(index<0||index>=q.ids.length)return null;next=q.ids[index];
    }
    q.current=next;return next;
  }
  return {make,reconcile,move};
});

'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const memory=require('../web/memory.js');
const queue=require('../web/queue.js');

// Run the actual page handlers against a small DOM and in-memory API. There is
// no browser session or access to the user's files/storage in these tests.
function page(){
  const elements=new Map(),storage=new Map(),calls=[];
  class Element {
    constructor(id=''){this.id=id;this.dataset={};this.listeners={};this.style={setProperty(){}};this.classList={add(){},remove(){},toggle(){},contains(){return false;}};this.open=false;this.value='';this.hidden=false;this.innerHTML='';this.textContent='';this.readyState=0;this.attributes={};}
    addEventListener(name,fn){(this.listeners[name]??=[]).push(fn);}
    async trigger(name,event={}){for(const fn of this.listeners[name]||[])await fn(event);}
    setAttribute(name,value){this.attributes[name]=value;}
    removeAttribute(name){delete this.attributes[name];}
    hasAttribute(name){return name in this.attributes;}
    append(){} remove(){} focus(){} click(){} pause(){this.paused=true;} load(){}
    showModal(){this.open=true;} close(){this.open=false;for(const fn of this.listeners.close||[])fn();}
  }
  const element=selector=>{if(!elements.has(selector))elements.set(selector,new Element(selector.replace('#','')));return elements.get(selector);};
  const document=new Element();document.body=new Element();document.querySelector=element;
  document.querySelectorAll=selector=>selector==='#musicDeleteDialog [data-close-dialog]'?[element('#cancelDeleteMusic'),element('#deleteMusicClose')]:[];
  document.createElement=()=>new Element();
  const first='a'.repeat(32),second='b'.repeat(32),job1='1'.repeat(32),job2='2'.repeat(32),active='3'.repeat(32);
  const song=(fid,title)=>({fileId:fid,title,artist:'Singer',source:'LocalImport',format:'WAV',size:'1 MB'});
  const backend={songs:[song(first,'First'),song(second,'Second')],jobs:[{id:job1,fileId:first,status:'completed',song:song(first,'First')},{id:job2,fileId:second,status:'completed',song:song(second,'Second')},{id:active,status:'queued',song:song(null,'Active')}],imports:[{id:'import-one',fileId:first,status:'completed',song:song(first,'First')}],playlists:[{id:'all',name:'All',files:[first,second]}]};
  const window=new Element();window.matchMedia=()=>({matches:false});window.scrollTo=()=>{};
  const context={document,window,MusicdlMemory:memory,MusicdlQueue:queue,console,AbortController,URL,TextDecoder,
    location:{href:'http://localhost/'},matchMedia:window.matchMedia,
    localStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value)},
    setTimeout:()=>0,clearTimeout(){},setInterval(){},
    fetch:async(endpoint,options={})=>{
      const body=options.body?JSON.parse(options.body):null;calls.push({endpoint,body});let data;
      if(endpoint==='/api/downloads/delete'){backend.jobs=backend.jobs.filter(j=>!body.ids.includes(j.id));data={deleted:body.ids};}
      else if(endpoint==='/api/library/delete'){
        backend.songs=backend.songs.filter(s=>!body.ids.includes(s.fileId));backend.jobs=backend.jobs.filter(j=>!body.ids.includes(j.fileId));backend.imports=backend.imports.filter(j=>!body.ids.includes(j.fileId));backend.playlists.forEach(p=>p.files=p.files.filter(id=>!body.ids.includes(id)));
        data={deleted:body.ids,missing:[],errors:[],warnings:[]};
      }else if(endpoint==='/api/downloads')data={jobs:backend.jobs};
      else if(endpoint.startsWith('/api/lyrics/')){
        const fid=endpoint.split('/')[3];
        if(body){
          backend.lyricData={plain:body.text||'Online lyrics',lines:[],hasTranslation:false,source:'已保存歌词'};
          backend.imports.filter(j=>j.fileId===fid).forEach(j=>Object.assign(j,{status:'completed',lyricStatus:'existing',message:'已补全歌词'}));
        }
        data=backend.lyricData||{plain:'',lines:[],hasTranslation:false};
      }
      else if(endpoint==='/api/library')data={songs:backend.songs};
      else if(endpoint==='/api/playlists')data={playlists:backend.playlists};
      else if(endpoint==='/api/imports')data={imports:backend.imports};
      else throw new Error('Unexpected endpoint '+endpoint);
      return {ok:true,json:async()=>JSON.parse(JSON.stringify(data))};
    }};
  vm.createContext(context);
  const code=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8').replace(/init\(\);\s*$/,'');
  vm.runInContext(code+'\nglobalThis.pageState=state;globalThis.pageRender={renderDownloads,renderLibrary,renderImports,loadLyrics};',context);
  const state=context.pageState;
  state.library=structuredClone(backend.songs);state.jobs=structuredClone(backend.jobs);state.imports=structuredClone(backend.imports);state.playlists=structuredClone(backend.playlists);
  return {state,element,document,calls,backend,storage,first,second,job1,job2,active,render:context.pageRender};
}

test('history deletion keeps local library, queue and playback intact',async()=>{
  const p=page();p.state.selectedHistory.add(p.job1);p.state.playingFile=p.first;p.state.queue=queue.make([p.first,p.second],p.first);
  await p.element('#deleteHistory').trigger('click');
  assert.deepEqual(p.calls.find(c=>c.endpoint==='/api/downloads/delete').body,{ids:[p.job1]});
  assert.equal(p.state.library.length,2);assert.equal(p.backend.songs.length,2);assert.equal(p.state.playingFile,p.first);
  assert.equal(p.state.selectedHistory.size,0);assert.equal(p.state.jobs.some(j=>j.id===p.job1),false);
});

test('music dialog cancellation sends no delete request; only final confirmation deletes',async()=>{
  const p=page();p.state.selectedMusic.add(p.first);p.state.selectedMusic.add(p.second);
  await p.element('#deleteMusic').trigger('click');
  assert.equal(p.element('#musicDeleteDialog').open,true);assert.match(p.element('#musicDeleteMessage').textContent,/2 首/);
  assert.equal(p.calls.length,0);assert.equal(p.state.library.length,2);
  p.element('#musicDeleteDialog').close();
  await p.element('#confirmDeleteMusic').trigger('click');
  assert.equal(p.calls.length,0);
  await p.element('#deleteMusic').trigger('click');
  await p.element('#confirmDeleteMusic').trigger('click');
  assert.deepEqual(p.calls.find(c=>c.endpoint==='/api/library/delete').body,{ids:[p.first,p.second],confirmed:true});
  assert.equal(p.element('#musicDeleteDialog').open,false);assert.equal(p.state.library.length,0);
});

test('confirmed music deletion clears playback, lyrics, queue and all corresponding rows',async()=>{
  const p=page();p.state.selectedMusic.add(p.first);p.state.playingFile=p.first;
  p.state.playback={lastFile:p.first,positions:{[p.first]:{time:20},[p.second]:{time:10}}};
  p.state.lyricOffsets[p.first]=5;p.state.lyricCache.set(p.first,{original:'Lyrics'});p.state.seenImports.add('import-one');
  p.state.queue=queue.make([p.first,p.second],p.first,'shuffle');
  await p.element('#deleteMusic').trigger('click');await p.element('#confirmDeleteMusic').trigger('click');
  assert.equal(p.state.playingFile,null);assert.equal(p.element('#audioPlayer').paused,true);assert.equal(p.element('#player').hidden,true);
  assert.equal(p.state.playback.positions[p.first],undefined);assert.equal(p.state.playback.positions[p.second].time,10);
  assert.equal(p.state.lyricOffsets[p.first],undefined);assert.equal(p.state.lyricCache.has(p.first),false);
  assert.equal(p.state.queue.ids.includes(p.first),false);assert.equal(p.state.queue.history.includes(p.first),false);
  assert.equal(p.state.imports.length,0);assert.equal(p.element('#importReport').hidden,true);
  assert.equal(p.state.jobs.some(j=>j.fileId===p.first),false);assert.equal(p.state.library[0].fileId,p.second);
  assert.equal(JSON.parse(p.storage.get('musicdl.playback')).positions[p.first],undefined);
});

test('all-history selection excludes active jobs and tab changes clear selection',async()=>{
  const p=page();p.element('#selectAllHistory').checked=true;
  await p.document.trigger('change',{target:p.element('#selectAllHistory')});
  assert.equal(p.state.selectedHistory.size,2);assert.equal(p.state.selectedHistory.has(p.active),false);
  assert.match(p.element('#downloadList').innerHTML,/data-select-history="33333333333333333333333333333333"[^>]*disabled/);
  const tab={dataset:{status:'failed'}};
  await p.element('#downloadTabs').trigger('click',{target:{closest:()=>tab}});
  assert.equal(p.state.selectedHistory.size,0);assert.equal(p.element('#deleteHistory').disabled,true);
});

for(const action of ['import','selection','automatic'])test(`lyric ${action} immediately clears the local-library import warning`,async()=>{
  const p=page();
  Object.assign(p.backend.imports[0],{lyricStatus:'failed',message:'需手动补全'});
  p.state.imports=structuredClone(p.backend.imports);p.state.seenImports.add('import-one');p.state.playingFile=p.first;
  p.render.renderImports();assert.match(p.element('#importReportSummary').textContent,/1 首需手动补全/);
  if(action==='import'){
    await p.element('#importOriginal').trigger('click');
    await p.element('#lyricsFile').trigger('change',{target:{files:[{size:10,arrayBuffer:async()=>new TextEncoder().encode('Manual lyrics').buffer}]}});
  }else if(action==='selection'){
    await p.element('#lyricsCandidates').trigger('click',{target:{closest:()=>({dataset:{lyricSong:'123'}})}});
  }else await p.render.loadLyrics(p.first);
  assert.match(p.element('#importReportSummary').textContent,/0 首需手动补全/);
  assert.doesNotMatch(p.element('#importReportItems').innerHTML,/import-failed|手动找歌词/);
  assert.equal(p.state.imports[0].lyricStatus,'existing');
});

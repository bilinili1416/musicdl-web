'use strict';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const lossless = (format) => ['FLAC','WAV','ALAC','APE','WV','TTA','DSF','DFF'].includes(format);
function readStorage(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
const sourceDefaults = [
  {id:'NeteaseMusicClient', name:'网易云音乐', mark:'云', color:'#e45259'},
  {id:'QQMusicClient', name:'QQ 音乐', mark:'Q', color:'#26ad80'},
  {id:'KuwoMusicClient', name:'酷我音乐', mark:'酷', color:'#dfa725'},
  {id:'KugouMusicClient', name:'酷狗音乐', mark:'K', color:'#428bea'},
  {id:'MiguMusicClient', name:'咪咕音乐', mark:'咪', color:'#dd609a'},
  {id:'QianqianMusicClient', name:'千千音乐', mark:'千', color:'#8b67cb'},
];
const savedPrefs = readStorage('musicdl.preferences', {});
const storedFavorites = readStorage('musicdl.favorites', []);
const state = {
  view:'discover', sources:sourceDefaults, selectedSources:new Set(Array.isArray(savedPrefs.sources) ? savedPrefs.sources.filter(s => sourceDefaults.some(p => p.id === s)) : sourceDefaults.slice(0,3).map(s=>s.id)),
  limit:[5,12,20,30].includes(savedPrefs.limit) ? savedPrefs.limit : 12,
  songs:[], selected:new Set(), favorites:Array.isArray(storedFavorites) ? storedFavorites.filter(s=>s && typeof s.title==='string').slice(0,500) : [],
  jobs:[], library:[], keyword:'', searching:false, hasSearched:false, downloadTab:'all', ready:false,
  jobsSignature:'', seenJobs:new Map(), downloadsLoaded:false, queuing:false,
  playingFile:null, lyrics:[], lyricsIndex:-1, lyricRequest:0, importKind:'original', lyricScrollUntil:0,
  lyricOffsets:readStorage('musicdl.lyricOffsets', {}), lyricCache:new Map(),
};
if (!state.selectedSources.size) state.selectedSources.add(sourceDefaults[0].id);

function toast(message, error=false) {
  const node=document.createElement('div');
  node.className=`toast${error?' error':''}`;
  node.textContent=message;
  $('#toasts').append(node);
  setTimeout(()=>node.remove(), 4800);
}
function savePreferences() {
  try { localStorage.setItem('musicdl.preferences', JSON.stringify({limit:state.limit,sources:[...state.selectedSources]})); }
  catch { toast('浏览器无法保存偏好，本次设置仍然有效',true); }
}
function favoriteKey(song) { return [song.title,song.artist,song.source,song.format].join('\u0000'); }
function isFavorite(song) { return state.favorites.some(f=>favoriteKey(f)===favoriteKey(song)); }
function toggleFavorite(song) {
  if (!song) return;
  const existed=isFavorite(song);
  if (!existed && state.favorites.length >= 500) return toast('收藏已达 500 首，请先移除部分歌曲',true);
  state.favorites=existed ? state.favorites.filter(f=>favoriteKey(f)!==favoriteKey(song)) : [{...song},...state.favorites];
  try { localStorage.setItem('musicdl.favorites',JSON.stringify(state.favorites)); }
  catch { toast('浏览器无法持久保存收藏，本次收藏仍然有效',true); }
  $('#favoriteCount').textContent=state.favorites.length;
  renderResults(); renderFavorites();
  toast(existed?'已从收藏中移除':'已加入你的音乐收藏');
}
async function api(path, body) {
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),15000);
  try {
    const response=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,signal:controller.signal});
    const data=await response.json();
    if (!response.ok) throw new Error(data.error || '请求失败，请稍后重试');
    return data;
  } catch(error) {
    if(error.name==='AbortError') throw new Error('音乐服务响应超时，请稍后重试');
    if(error instanceof TypeError) throw new Error('无法连接音乐服务，请检查网络或服务状态');
    throw error;
  } finally { clearTimeout(timer); }
}
function sourceInfo(id) { return state.sources.find(s=>s.id===id) || {name:id?.replace('MusicClient','') || '本地音乐',color:'#957ab7'}; }
function setSidebar(open) {
  const narrow=window.matchMedia('(max-width:680px)').matches;
  $('#sidebar').classList.toggle('open',open);
  $('#sidebar').inert=narrow&&!open;
  $('#menuButton').setAttribute('aria-expanded',String(open));
  if(narrow&&!open&&$('#sidebar').contains(document.activeElement)) $('#menuButton').focus();
}
function sourceBadge(id) { const s=sourceInfo(id); return `<span class="source-badge" style="--source-color:${esc(s.color)}"><i></i>${esc(s.name)}</span>`; }
function formatBadge(format) { return `<span class="format-badge${lossless(format)?' lossless':''}">${esc(format)}</span>`; }
function trackArt(index=0) {
  const colors=[['#eee7f9','#ab8cce'],['#f1e5d9','#c4a484'],['#dde9ed','#91aab8'],['#e2eddf','#93af8e']];
  const [bg,color]=colors[index%colors.length];
  return `<span class="track-art" style="--art-bg:${bg};--art-color:${color}">${icon('music')}</span>`;
}
function empty(iconName,title,text,action='') { return `<div class="empty-state"><div class="empty-icon">${icon(iconName)}</div><h3>${esc(title)}</h3><p>${esc(text)}</p>${action}</div>`; }
function renderSources() {
  $('#sourceList').innerHTML=state.sources.map(s=>`<button class="source-pill${state.selectedSources.has(s.id)?' selected':''}" data-source="${esc(s.id)}" aria-pressed="${state.selectedSources.has(s.id)}"><span class="source-mark" style="--source-color:${esc(s.color)}">${esc(s.mark)}</span>${esc(s.name)}<svg class="check-mark" aria-hidden="true"><use href="#i-check"/></svg></button>`).join('');
  $('#toggleSources').textContent=state.selectedSources.size===state.sources.length?'重置':'全选';
}
function setView(view) {
  if(!['discover','downloads','favorites','library'].includes(view)) return;
  state.view=view;
  $$('.view').forEach(n=>n.hidden=n.id!==`${view}View`);
  $$('.nav-item[data-view]').forEach(n=>{n.classList.toggle('active',n.dataset.view===view);n.setAttribute('aria-current',n.dataset.view===view?'page':'false');});
  $('#breadcrumbTitle').textContent={discover:'发现音乐',downloads:'下载管理',favorites:'我的收藏',library:'本地音乐'}[view];
  setSidebar(false);
  if(view==='library') refreshLibrary();
  if(view==='downloads') { renderDownloads(); refreshDownloads(true); }
  if(view==='favorites') renderFavorites();
  window.scrollTo({top:0,behavior:'instant'});
}
function visibleSongs() {
  let songs=state.songs.filter(s=>($('#sourceFilter').value==='all'||s.source===$('#sourceFilter').value) && ($('#formatFilter').value==='all'||lossless(s.format)===($('#formatFilter').value==='lossless')));
  if($('#sortSelect').value==='title') songs.sort((a,b)=>a.title.localeCompare(b.title,'zh-CN'));
  if($('#sortSelect').value==='size') songs.sort((a,b)=>b.sizeBytes-a.sizeBytes);
  return songs;
}
function renderResults() {
  if(!state.hasSearched) return;
  const songs=visibleSongs();
  $('#resultsTitle').textContent=`“${state.keyword}” 的搜索结果`;
  $('#resultsSubtitle').textContent=`找到 ${state.songs.length} 首歌曲 · 当前显示 ${songs.length} 首 · 音质以平台返回为准`;
  if(!songs.length) {
    $('#resultsTable').innerHTML=empty('search',state.searching?'正在寻找喜欢的声音…':state.songs.length?'没有符合筛选条件的歌曲':'暂时没有找到歌曲',state.searching?'各平台的结果会陆续显示，请稍候。':state.songs.length?'尝试选择其他平台或音质。':'试试更简短的关键词，或切换其他音乐平台。');
  } else {
    $('#resultsTable').innerHTML=`<div class="table-wrap"><table><thead><tr><th class="check-cell"><input type="checkbox" id="selectAll" aria-label="选择当前筛选下的全部歌曲" ${songs.every(s=>state.selected.has(s.id))?'checked':''}></th><th>歌曲 / 歌手</th><th>专辑</th><th>平台</th><th>音质</th><th>时长</th><th>大小</th><th style="text-align:right">操作</th></tr></thead><tbody>${songs.map((s,i)=>`<tr><td class="check-cell"><input type="checkbox" data-select="${esc(s.id)}" aria-label="选择 ${esc(s.title)}" ${state.selected.has(s.id)?'checked':''}></td><td><div class="track-cell">${trackArt(i)}<span class="track-text"><strong title="${esc(s.title)}">${esc(s.title)}</strong><small>${esc(s.artist)}</small></span></div></td><td class="album-cell" title="${esc(s.album)}">${esc(s.album)}</td><td>${sourceBadge(s.source)}</td><td>${formatBadge(s.format)}</td><td>${esc(s.duration)}</td><td>${esc(s.size)}</td><td><div class="row-actions"><button class="icon-button${isFavorite(s)?' favorited':''}" data-favorite="${esc(s.id)}" aria-label="${isFavorite(s)?'取消收藏':'收藏'} ${esc(s.title)}" title="${isFavorite(s)?'取消收藏':'收藏'}">${icon('heart')}</button><button class="icon-button" data-download="${esc(s.id)}" aria-label="下载 ${esc(s.title)}" title="下载歌曲">${icon('download')}</button></div></td></tr>`).join('')}</tbody></table></div>`;
    const checkbox=$('#selectAll');
    checkbox.indeterminate=songs.some(s=>state.selected.has(s.id))&&!songs.every(s=>state.selected.has(s.id));
  }
  updateBatchButton();
}
function updateBatchButton() {
  $('#batchDownload').disabled=!state.selected.size||state.queuing;
  $('#batchDownload span').textContent=state.selected.size?`下载选中 (${state.selected.size})`:'下载选中';
}
function updateSourceFilter() {
  const previous=$('#sourceFilter').value;
  const ids=[...new Set(state.songs.map(s=>s.source))];
  $('#sourceFilter').innerHTML='<option value="all">所有平台</option>'+ids.map(id=>`<option value="${esc(id)}">${esc(sourceInfo(id).name)}</option>`).join('');
  $('#sourceFilter').value=ids.includes(previous)?previous:'all';
}
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function search(keyword) {
  if(state.searching) return toast('正在搜索中，各平台结果会陆续显示');
  keyword=String(keyword||'').trim();
  if(!keyword) { $('#searchInput').focus(); return; }
  if(!state.ready) return toast('音乐服务未就绪，请稍后重试',true);
  if(!state.selectedSources.size) return toast('请至少选择一个音乐平台',true);
  setView('discover');
  $('#searchInput').value=keyword;
  state.keyword=keyword; state.searching=true; state.hasSearched=true;
  state.songs=[]; state.selected.clear();
  $('#formatFilter').value='all'; $('#sortSelect').value='default';
  $('#sourceFilter').innerHTML='<option value="all">所有平台</option>';
  $('#discoveryContent').hidden=true; $('#resultsSection').hidden=false; $('#searchStatus').hidden=false;
  $('#searchButton').disabled=true;
  $('#searchButton').innerHTML='<span class="spinner"></span>搜索中';
  $('#searchStatus').innerHTML='<span class="spinner"></span>正在连接音乐平台，结果会陆续显示…';
  renderResults();
  try {
    const {id}=await api('/api/search',{keyword,sources:[...state.selectedSources],limit:state.limit});
    const started=Date.now();
    while(true) {
      const result=await api(`/api/search/${id}`);
      const changed=result.songs.length!==state.songs.length;
      state.songs=result.songs;
      if(result.status==='failed') throw new Error(result.message||'搜索失败');
      if(result.status==='completed') {
        state.searching=false;
        $('#searchStatus').hidden=!result.errors.length;
        if(result.errors.length) $('#searchStatus').innerHTML=`${icon('help')}<div class="warning-list">${result.errors.map(e=>`<span>${esc(sourceInfo(e.source).name)}：${esc(e.message)}</span>`).join('')}</div>`;
        updateSourceFilter(); renderResults(); break;
      }
      $('#searchStatus').innerHTML=`<span class="spinner"></span>正在搜索 · ${result.completed} / ${result.total} 个平台已完成 · 已找到 ${result.songs.length} 首歌曲`;
      if(changed) { updateSourceFilter(); renderResults(); }
      if(Date.now()-started>180000) throw new Error('搜索耗时较长，请减少平台数量后重试');
      await delay(1300);
    }
  } catch(error) {
    state.searching=false;
    $('#searchStatus').textContent=error.message;
    $('#searchStatus').hidden=false;
    renderResults(); toast(error.message,true);
  } finally {
    state.searching=false;
    $('#searchButton').disabled=false;
    $('#searchButton').innerHTML=`搜索音乐${icon('arrow')}`;
  }
}
async function queueDownloads(ids) {
  if(state.queuing) return;
  state.queuing=true; updateBatchButton();
  try {
    const result=await api('/api/downloads',{ids});
    toast(`${result.ids.length} 首歌曲已加入下载队列`);
    state.selected.clear(); renderResults();
    await refreshDownloads(true);
  } catch(error) { toast(error.message,true); }
  finally { state.queuing=false; updateBatchButton(); }
}
function jobStatus(job) {
  const labels={queued:'等待下载',downloading:'正在下载',completed:'下载完成',failed:'下载失败'};
  return `<div class="job-status ${esc(job.status)}">${job.status==='downloading'?'<span class="spinner"></span>':job.status==='completed'?icon('check'):''}<span>${labels[job.status]||esc(job.status)}</span>${job.error?`<span class="job-error" title="${esc(job.error)}">${esc(job.error)}</span>`:''}</div>`;
}
function fileActions(song) {
  return `<div class="row-actions"><button class="icon-button" data-play="${esc(song.fileId)}" title="播放" aria-label="播放 ${esc(song.title)}">${icon('play')}</button><a class="icon-button" href="/api/files/${encodeURIComponent(song.fileId)}?download=1" title="保存文件" aria-label="保存 ${esc(song.title)}">${icon('download')}</a></div>`;
}
function downloadRow(job,index=0) {
  const s=job.song;
  return `<div class="download-row">${trackArt(index)}<span class="track-text"><strong title="${esc(s.title)}">${esc(s.title)}</strong><small>${esc(s.artist)} · ${esc(sourceInfo(s.source).name)} · ${esc(s.format)}</small></span>${jobStatus(job)}${job.status==='completed'&&job.fileId?fileActions({...s,fileId:job.fileId}):job.status==='failed'?`<button class="secondary" data-research="${esc(s.title+' '+s.artist)}">重新搜索</button>`:''}</div>`;
}
function renderDownloads() {
  const active=state.jobs.filter(j=>['queued','downloading'].includes(j.status)).length;
  $('#downloadCount').textContent=active;
  $('#downloadStats').innerHTML=[['进行中',active,'download'],['已完成',state.jobs.filter(j=>j.status==='completed').length,'check'],['未完成',state.jobs.filter(j=>j.status==='failed').length,'help']].map(([label,num,i])=>`<div class="stat-card"><span><small>${label}</small><strong>${num}</strong></span>${icon(i)}</div>`).join('');
  const filtered=state.jobs.filter(j=>state.downloadTab==='all'||(state.downloadTab==='active'?['queued','downloading'].includes(j.status):j.status===state.downloadTab));
  $('#downloadList').innerHTML=filtered.length?filtered.map(downloadRow).join(''):empty('download','还没有下载任务',state.downloadTab==='all'?'去发现音乐，搜索并收藏你喜欢的声音。':'当前分类中没有下载任务。',state.downloadTab==='all'?`<button class="primary" data-view="discover">去发现音乐${icon('arrow')}</button>`:'');
  const recent=state.jobs.slice(0,3);
  $('#recentDownloads').innerHTML=recent.length?recent.map(downloadRow).join(''):`<div class="recent-empty"><span class="empty-icon">${icon('download')}</span><div><strong>你的下一首心动，还在路上</strong><p>搜索一首喜欢的歌，让音乐收藏从这里开始。</p></div><button class="text-button" id="focusSearch">开始探索${icon('arrow')}</button></div>`;
}
async function refreshDownloads(report=false) {
  try {
    const {jobs}=await api('/api/downloads');
    const signature=JSON.stringify(jobs);
    if(signature===state.jobsSignature) return;
    let completed=false;
    for(const job of jobs) {
      const previous=state.seenJobs.get(job.id);
      if(state.downloadsLoaded&&previous&&previous!==job.status&&['completed','failed'].includes(job.status)) {
        toast(job.status==='completed'?`“${job.song.title}” 下载完成`:`“${job.song.title}” 下载失败`,job.status==='failed');
        completed ||= job.status==='completed';
      }
      state.seenJobs.set(job.id,job.status);
    }
    state.downloadsLoaded=true; state.jobs=jobs; state.jobsSignature=signature;
    renderDownloads();
    if(completed&&state.view==='library') refreshLibrary();
  } catch(error) { if(report) toast(error.message,true); }
}
function renderFavorites() {
  $('#favoriteCount').textContent=state.favorites.length;
  $('#favoritesList').innerHTML=state.favorites.length?`<div class="track-grid">${state.favorites.map((s,i)=>`<div class="download-row">${trackArt(i)}<span class="track-text"><strong title="${esc(s.title)}">${esc(s.title)}</strong><small>${esc(s.artist)} · ${esc(sourceInfo(s.source).name)}</small></span><div class="row-actions"><button class="icon-button favorited" data-remove-favorite="${i}" aria-label="取消收藏 ${esc(s.title)}" title="取消收藏">${icon('heart')}</button><button class="icon-button" data-research="${esc(s.title+' '+s.artist)}" aria-label="搜索 ${esc(s.title)}" title="重新搜索获取下载链接">${icon('search')}</button></div></div>`).join('')}</div>`:empty('heart','把喜欢的旋律，放在这里','点击搜索结果中的爱心，即可加入你的音乐收藏。',`<button class="primary" data-view="discover">发现好音乐${icon('arrow')}</button>`);
}
async function refreshLibrary() {
  $('#refreshLibrary').disabled=true;
  try {
    const {songs}=await api('/api/library');
    state.library=songs;
    $('#libraryList').innerHTML=songs.length?songs.map((s,i)=>`<div class="download-row">${trackArt(i)}<span class="track-text"><strong title="${esc(s.title)}">${esc(s.title)}</strong><small>${esc(s.artist)} · ${esc(s.format)} · ${esc(s.size)}</small></span>${fileActions(s)}</div>`).join(''):empty('library','音乐收藏，从第一首开始','完成一次下载后，音频文件会出现在这里。',`<button class="primary" data-view="discover">去搜索音乐${icon('arrow')}</button>`);
  } catch(error) {
    $('#libraryList').innerHTML=empty('help','暂时无法读取音乐库',error.message);
    toast(error.message,true);
  } finally { $('#refreshLibrary').disabled=false; }
}
function play(fid) {
  const song=state.library.find(s=>s.fileId===fid)||state.jobs.filter(j=>j.fileId===fid).map(j=>({...j.song,fileId:j.fileId}))[0];
  if(!song) return;
  $('#playerTitle').textContent=song.title; $('#playerArtist').textContent=song.artist;
  $('#player').hidden=false; document.body.classList.add('playing');
  state.playingFile=fid;
  $('#lyricsTitle').textContent=song.title; $('#lyricsArtist').textContent=song.artist;
  const offset=Number(state.lyricOffsets?.[fid]) || 0;
  $('#lyricsOffset').value=Math.max(-30,Math.min(30,offset));
  state.lyricScrollUntil=0;
  setLyricsVisible(true);
  loadLyrics(fid);
  const audio=$('#audioPlayer');
  audio.src=`/api/files/${encodeURIComponent(fid)}`;
  audio.play().catch(()=>toast('该格式可能不受浏览器支持，可保存文件后使用本地播放器',true));
}

function setLyricsVisible(visible) {
  $('#lyricsPanel').hidden=!visible;
  $('#toggleLyrics').setAttribute('aria-expanded',String(visible));
  $('#toggleLyrics').setAttribute('aria-label',visible?'收起歌词':'显示歌词');
  if(visible) updateLyrics(true);
}
function renderLyrics(data) {
  state.lyrics=data.lines || []; state.lyricsIndex=-1;
  $('#lyricsStatus').textContent=data.lines?.length ? `${data.source || '歌词'} · ${data.hasTranslation?'原文与译文':'暂无译文'}` : data.plain?'无时间轴 · 静态歌词':'暂无歌词';
  let content;
  if(state.lyrics.length) {
    content=state.lyrics.map((line,index)=>`<button class="lyric-line${!line.text?' lyric-gap':''}" data-lyric-index="${index}" aria-label="跳转到 ${esc(line.time.toFixed(1))} 秒"><span class="lyric-original">${esc(line.text || '♪ · ♪ · ♪')}</span>${line.translation?`<span class="lyric-translation">${esc(line.translation)}</span>`:''}</button>`).join('');
  } else if(data.plain || data.translationPlain) {
    content=`<div class="lyric-plain">${esc(data.plain || '')}</div>${data.translationPlain?`<div class="lyric-plain translated">${esc(data.translationPlain)}</div>`:''}`;
  } else {
    content=empty('music','让歌词跟上音乐','可以在线查找歌词，或导入原文／翻译 LRC。纯音乐也可以没有歌词。');
  }
  if(data.translationPlain && state.lyrics.length) content+=`<details class="plain-translation"><summary>查看无时间轴的译文</summary><p>${esc(data.translationPlain)}</p></details>`;
  if(data.unmatchedTranslations) content+=`<p class="lyric-note">${Number(data.unmatchedTranslations)} 句译文未匹配时间轴，请检查是否为同一版本。</p>`;
  $('#lyricsLines').innerHTML=content;
  $('#lyricsScroll').scrollTop=0;
  updateLyrics(true);
}
function updateLyrics(force=false) {
  if(!state.lyrics.length || $('#lyricsPanel').hidden) return;
  const time=$('#audioPlayer').currentTime+(Number($('#lyricsOffset').value)||0);
  let low=0,high=state.lyrics.length;
  while(low<high) { const mid=(low+high)>>1; if(state.lyrics[mid].time<=time)low=mid+1;else high=mid; }
  const index=low-1;
  if(index===state.lyricsIndex&&!force)return;
  const lines=$$('#lyricsLines .lyric-line');
  if(lines[state.lyricsIndex]) { lines[state.lyricsIndex].classList.remove('active');lines[state.lyricsIndex].removeAttribute('aria-current'); }
  state.lyricsIndex=index;
  const active=lines[index];
  if(!active)return;
  active.classList.add('active');active.setAttribute('aria-current','true');
  if(force||Date.now()>state.lyricScrollUntil) {
    const scroll=$('#lyricsScroll');
    const top=active.getBoundingClientRect().top-scroll.getBoundingClientRect().top+scroll.scrollTop-scroll.clientHeight/2+active.clientHeight/2;
    scroll.scrollTo({top,behavior:force||matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth'});
  }
}
async function loadLyrics(fid) {
  const request=++state.lyricRequest;
  state.lyrics=[];state.lyricsIndex=-1;
  $('#lyricsLines').innerHTML='<div class="lyrics-loading"><span class="spinner"></span>正在读取歌词…</div>';
  $('#lyricsStatus').textContent='正在读取歌词…';
  try {
    const data=state.lyricCache.get(fid)||await api(`/api/lyrics/${encodeURIComponent(fid)}`);
    if(request!==state.lyricRequest)return;
    renderLyrics(data);
    state.lyricCache.set(fid,data);
    if(!data.hasTranslation&&!data.lookupAttempted) {
      $('#lyricsStatus').textContent='正在查找原文与译文…';
      try {
        const found=await api(`/api/lyrics/${encodeURIComponent(fid)}/lookup`,{});
        if(request!==state.lyricRequest)return;
        if(found.candidates) {
          data.lookupAttempted=true;state.lyricCache.set(fid,data);renderLyrics(data);
        } else {
          found.lookupAttempted=true;state.lyricCache.set(fid,found);renderLyrics(found);
        }
      } catch {
        if(request!==state.lyricRequest)return;
        data.lookupAttempted=true;state.lyricCache.set(fid,data);renderLyrics(data);
        $('#lyricsStatus').textContent+=' · 在线暂不可用';
      }
    }
  } catch(error) {
    if(request!==state.lyricRequest)return;
    renderLyrics({});$('#lyricsStatus').textContent=error.message;
  }
}
function renderLyricCandidates(candidates) {
  $('#lyricsCandidates').innerHTML=candidates.length ? candidates.map(song=>`<button class="lyric-candidate" data-lyric-song="${esc(song.id)}"><span><strong>${esc(song.title)}</strong><small>${esc(song.artist)} · ${esc(song.album)}</small></span><span>${Math.floor(song.duration/60)}:${String(Math.floor(song.duration%60)).padStart(2,'0')}${icon('arrow')}</span></button>`).join('') : '<p class="field-hint">没有匹配歌词，可以更换关键词或导入 LRC。</p>';
}
async function searchLyrics() {
  const fid=state.playingFile,request=state.lyricRequest;
  if(!fid)return;
  $('#lyricsCandidates').innerHTML='<div class="lyrics-loading"><span class="spinner"></span>正在查找歌词版本…</div>';
  const submit=$('#lyricsSearchForm button');submit.disabled=true;
  try {
    const result=await api(`/api/lyrics/${encodeURIComponent(fid)}/lookup`,{keyword:$('#lyricsQuery').value.trim()});
    if(request!==state.lyricRequest)return;
    renderLyricCandidates(result.candidates || []);
  } catch(error) { if(request===state.lyricRequest)$('#lyricsCandidates').textContent=error.message; }
  finally { submit.disabled=false; }
}
$('#lookupLyrics').addEventListener('click',()=>{
  const song=state.library.find(s=>s.fileId===state.playingFile)||state.jobs.find(j=>j.fileId===state.playingFile)?.song;
  $('#lyricsQuery').value=song?`${song.title.split(/[（(]/)[0]} ${song.artist.split(/[,，/&]/)[0]}`:'';
  $('#lyricsSearchDialog').showModal();searchLyrics();
});
$('#lyricsSearchForm').addEventListener('submit',event=>{event.preventDefault();searchLyrics();});
$('#lyricsCandidates').addEventListener('click',async event=>{
  const button=event.target.closest('[data-lyric-song]');if(!button)return;
  const fid=state.playingFile,request=++state.lyricRequest;
  $('#lyricsCandidates').inert=true;
  try {
    const data=await api(`/api/lyrics/${encodeURIComponent(fid)}/lookup`,{songId:button.dataset.lyricSong});
    if(request!==state.lyricRequest)return;
    state.lyricCache.set(fid,{...data,lookupAttempted:true});renderLyrics(data);$('#lyricsSearchDialog').close();
    toast(data.hasTranslation?'原文与译文已保存':'歌词已保存；该版本暂无译文，可导入翻译 LRC');
  } catch(error) { if(request===state.lyricRequest)toast(error.message,true); }
  finally { $('#lyricsCandidates').inert=false; }
});
for(const [id,kind] of [['importOriginal','original'],['importTranslation','translation']]) {
  $(`#${id}`).addEventListener('click',()=>{state.importKind=kind;$('#lyricsFile').value='';$('#lyricsFile').click();});
}
$('#lyricsFile').addEventListener('change',async event=>{
  const file=event.target.files[0],fid=state.playingFile,request=++state.lyricRequest,kind=state.importKind;
  if(!file||!fid)return;
  if(file.size>262144)return toast('请选择不超过 256 KB 的歌词文件',true);
  try {
    const raw=await file.arrayBuffer();
    let text=new TextDecoder('utf-8').decode(raw);
    if(new Uint8Array(raw)[0]===255&&new Uint8Array(raw)[1]===254)text=new TextDecoder('utf-16le').decode(raw);
    else if(new Uint8Array(raw)[0]===254&&new Uint8Array(raw)[1]===255)text=new TextDecoder('utf-16be').decode(raw);
    else if(text.includes('\ufffd'))text=new TextDecoder('gb18030').decode(raw);
    const data=await api(`/api/lyrics/${encodeURIComponent(fid)}/import`,{kind,text});
    if(request!==state.lyricRequest)return;
    state.lyricCache.set(fid,{...data,lookupAttempted:true});renderLyrics(data);
    toast(data.unmatchedTranslations?'已导入，部分译文时间未对齐，请检查歌词版本':'歌词已导入并保存');
  } catch(error) { if(request===state.lyricRequest)toast(error.message,true); }
});
$('#lyricsLines').addEventListener('click',event=>{
  const line=event.target.closest('[data-lyric-index]');if(!line)return;
  const audio=$('#audioPlayer'),time=state.lyrics[Number(line.dataset.lyricIndex)]?.time;
  if(time===undefined||!Number.isFinite(audio.duration))return;
  audio.currentTime=Math.max(0,Math.min(audio.duration,time-(Number($('#lyricsOffset').value)||0)));
  state.lyricScrollUntil=0;updateLyrics(true);
});
$('#lyricsOffset').addEventListener('change',()=>{
  const offset=Math.max(-30,Math.min(30,Number($('#lyricsOffset').value)||0));$('#lyricsOffset').value=offset;
  if(!state.lyricOffsets||typeof state.lyricOffsets!=='object'||Array.isArray(state.lyricOffsets))state.lyricOffsets={};
  state.lyricOffsets[state.playingFile]=offset;
  try{localStorage.setItem('musicdl.lyricOffsets',JSON.stringify(state.lyricOffsets));}catch{toast('浏览器无法保存歌词偏移，本次设置仍有效',true);}
  updateLyrics(true);
});
['wheel','touchstart','pointerdown'].forEach(name=>$('#lyricsScroll').addEventListener(name,()=>{state.lyricScrollUntil=Date.now()+5000;},{passive:true}));
$('#followLyrics').addEventListener('click',()=>{state.lyricScrollUntil=0;updateLyrics(true);});
$('#hideLyrics').addEventListener('click',()=>{setLyricsVisible(false);$('#toggleLyrics').focus();});
$('#toggleLyrics').addEventListener('click',()=>setLyricsVisible($('#lyricsPanel').hidden));
['timeupdate','seeked','loadedmetadata','ended'].forEach(name=>$('#audioPlayer').addEventListener(name,()=>updateLyrics()));

document.addEventListener('click',event=>{
  const button=event.target.closest('button,[data-view]');
  if(!button) return;
  if(button.dataset.view) setView(button.dataset.view);
  if(button.dataset.keyword||button.dataset.research) search(button.dataset.keyword||button.dataset.research);
  if(button.dataset.source) {
    const id=button.dataset.source;
    if(state.selectedSources.has(id)) state.selectedSources.delete(id); else state.selectedSources.add(id);
    savePreferences(); renderSources();
  }
  if(button.dataset.download) queueDownloads([button.dataset.download]);
  if(button.dataset.favorite) toggleFavorite(state.songs.find(s=>s.id===button.dataset.favorite));
  if(button.dataset.removeFavorite!==undefined) toggleFavorite(state.favorites[Number(button.dataset.removeFavorite)]);
  if(button.dataset.play) play(button.dataset.play);
  if(button.hasAttribute('data-close-dialog')) button.closest('dialog').close();
  if(button.id==='focusSearch') { $('#searchInput').focus(); $('#searchInput').scrollIntoView({behavior:'smooth',block:'center'}); }
});
$('#searchForm').addEventListener('submit',e=>{e.preventDefault();search($('#searchInput').value);});
$('#toggleSources').addEventListener('click',()=>{
  state.selectedSources=new Set((state.selectedSources.size===state.sources.length?state.sources.slice(0,3):state.sources).map(s=>s.id));
  savePreferences(); renderSources();
});
$('#newSearchButton').addEventListener('click',()=>{
  if(state.searching) return toast('正在搜索，请等待搜索完成');
  state.hasSearched=false; state.selected.clear();
  $('#resultsSection').hidden=true; $('#searchStatus').hidden=true; $('#discoveryContent').hidden=false;
});
['sourceFilter','formatFilter','sortSelect'].forEach(id=>$(`#${id}`).addEventListener('change',renderResults));
$('#resultsTable').addEventListener('change',event=>{
  if(event.target.dataset.select) {
    const id=event.target.dataset.select;
    if(event.target.checked) state.selected.add(id); else state.selected.delete(id);
  }
  if(event.target.id==='selectAll') visibleSongs().forEach(s=>event.target.checked?state.selected.add(s.id):state.selected.delete(s.id));
  renderResults();
});
$('#batchDownload').addEventListener('click',()=>queueDownloads([...state.selected]));
$('#refreshDownloads').addEventListener('click',async()=>{ $('#refreshDownloads').disabled=true; await refreshDownloads(true); $('#refreshDownloads').disabled=false; });
$('#refreshLibrary').addEventListener('click',refreshLibrary);
$('#downloadTabs').addEventListener('click',e=>{
  const button=e.target.closest('[data-status]'); if(!button) return;
  state.downloadTab=button.dataset.status;
  $$('#downloadTabs button').forEach(n=>n.classList.toggle('active',n===button)); renderDownloads();
});
$('#settingsButton').addEventListener('click',()=>{ $('#resultLimit').value=state.limit; $('#settingsDialog').showModal(); });
$('#helpButton').addEventListener('click',()=>$('#helpDialog').showModal());
$('#settingsForm').addEventListener('submit',e=>{ e.preventDefault();state.limit=Number($('#resultLimit').value);savePreferences();$('#settingsDialog').close();toast('设置已保存，下次搜索时生效'); });
$$('dialog').forEach(dialog=>dialog.addEventListener('click',e=>{ if(e.target===dialog){const rect=dialog.getBoundingClientRect(); if(e.clientX<rect.left||e.clientX>rect.right||e.clientY<rect.top||e.clientY>rect.bottom) dialog.close();} }));
$('#menuButton').setAttribute('aria-controls','sidebar');
$('#menuButton').addEventListener('click',()=>setSidebar(!$('#sidebar').classList.contains('open')));
document.addEventListener('click',e=>{if($('#sidebar').classList.contains('open')&&!e.target.closest('#sidebar,#menuButton'))setSidebar(false);});
window.addEventListener('resize',()=>setSidebar($('#sidebar').classList.contains('open')));
$('#closePlayer').addEventListener('click',()=>{ ++state.lyricRequest;state.playingFile=null;$('#lyricsSearchDialog').close();setLyricsVisible(false);$('#audioPlayer').pause(); $('#audioPlayer').removeAttribute('src');$('#audioPlayer').load();$('#player').hidden=true;document.body.classList.remove('playing'); });
$('#audioPlayer').addEventListener('error',()=>{if($('#audioPlayer').hasAttribute('src'))toast('无法播放此文件，请保存后使用本地播放器试听',true);});
document.addEventListener('keydown',e=>{ if(e.key==='/'&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&!e.target.closest('input,textarea,select,dialog')){e.preventDefault();setView('discover');$('#searchInput').focus();} if(e.key==='Escape')setSidebar(false); });

async function init() {
  setSidebar(false); renderSources(); renderDownloads(); renderFavorites();
  try {
    const status=await api('/api/status');
    state.ready=status.ready; state.sources=status.sources;
    $('.local-tag').innerHTML=`<span class="status-dot"></span>${status.deployment==='cloud'?'云端运行':'本地运行'}`;
    $('#engineText').textContent=status.ready?'音乐引擎已连接':'请先安装 musicdl';
    $('#engineDot').classList.toggle('offline',!status.ready);
    $('#version').textContent=status.version?`v${status.version}`:'';
    $('#outputPath').textContent=status.output;
    renderSources(); await refreshDownloads();
  } catch(error) {
    $('#engineText').textContent='音乐服务未连接'; $('#engineDot').classList.add('offline');
    $('#outputPath').textContent='连接服务后显示下载目录'; toast(error.message,true);
  }
  setInterval(()=>{if(!document.hidden)refreshDownloads();},2500);
}
init();

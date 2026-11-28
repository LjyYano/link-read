// Article interactions from LjyYano/html-library, pages/charlie-munger-etf-trap/index.html.
(()=>{'use strict';
const $=(q,r=document)=>r.querySelector(q), $$=(q,r=document)=>Array.from(r.querySelectorAll(q));
const labels=JSON.parse($('#linkread-reader-labels')?.textContent||'{}');
const t=(label,values={})=>(Object.prototype.hasOwnProperty.call(labels,label)?labels[label]:label).replace(/\{(\w+)\}/g,(_,key)=>values[key]??`{${key}}`);
const isAllowedHostOrigin=origin=>origin==='null'||origin==='tauri://localhost'||origin==='https://tauri.localhost'||/^http:\/\/(?:localhost|127\.0\.0\.1)(?::\d+)?$/.test(origin);
const isDesktopEmbedded=()=>window.parent!==window&&(window.__linkreadDesktop===true||location.protocol==='linkread-reader:');
const tabNames=$$('.tab').map(b=>b.dataset.tab);
const tabList=$('.tabs-scroll'),tabStrip=$('.reader-tab-strip');
const tabsLeft=$('#reader-tabs-left'),tabsRight=$('#reader-tabs-right');
let activeTab='transcript', readingPosition=null,navigationVersion=0;
function revealSelectedTab(){
  const selected=$('.tab.active',tabList);
  if(!selected)return;
  const tab=selected.getBoundingClientRect(),list=tabList.getBoundingClientRect();
  if(tab.left<list.left)tabList.scrollLeft+=tab.left-list.left;
  else if(tab.right>list.right)tabList.scrollLeft+=tab.right-list.right;
}
function updateTabArrows(){
  const overflow=tabList.scrollWidth>tabStrip.clientWidth+1;
  tabsLeft.hidden=tabsRight.hidden=!overflow;
  tabsLeft.disabled=tabList.scrollLeft<=1;
  tabsRight.disabled=tabList.scrollLeft+tabList.clientWidth>=tabList.scrollWidth-1;
}
function fitTabs(){updateTabArrows();revealSelectedTab();updateTabArrows();}
if(tabStrip&&tabList&&tabsLeft&&tabsRight){
  tabList.addEventListener('scroll',updateTabArrows,{passive:true});
  tabsLeft.addEventListener('click',()=>tabList.scrollBy({left:-tabList.clientWidth*.75,behavior:smooth()}));
  tabsRight.addEventListener('click',()=>tabList.scrollBy({left:tabList.clientWidth*.75,behavior:smooth()}));
  window.addEventListener('resize',fitTabs);
  document.fonts?.ready.then(fitTabs);
  fitTabs();
}
function showTab(name,changeHash=true,restorePosition=true){navigationVersion++;if(!tabNames.includes(name))name='transcript';readingPosition?.beforeTab();activeTab=name;$$('.tab').forEach(b=>{const v=b.dataset.tab===name;b.classList.toggle('active',v);b.setAttribute('aria-selected',String(v));b.tabIndex=v?0:-1});$$('.panel').forEach(p=>{const v=p.dataset.panel===name;p.classList.toggle('active',v);p.hidden=!v});if(tabList)revealSelectedTab();if(changeHash){if(readingPosition)readingPosition.updateHash(name);else history.replaceState(null,'','#'+name)}readingPosition?.afterTab(name,restorePosition);if(name==='keywords')requestAnimationFrame(layoutKeywords);}
function applyHash(initial=false){
  const hash=location.hash.slice(1);
  if(initial&&(!hash||readingPosition?.ownHash(hash)))return false;
  const cue=/^cue-([0-9]+)$/.exec(hash);
  const target=/^(?:chapter|para)-[0-9]+(?:-[0-9]+)?$/.test(hash)?document.getElementById(hash):
    cue?$$('.reading-paragraph').find(p=>Number(p.dataset.cueStart)<=Number(cue[1])&&Number(p.dataset.cueEnd)>=Number(cue[1])):null;
  readingPosition?.navigation();
  if(target){
    showTab('transcript',false,false);
    const version=navigationVersion;
    requestAnimationFrame(()=>{if(version===navigationVersion)target.scrollIntoView({behavior:'instant',block:'start'})});
  }else{
    showTab(hash==='anki'?'highlights':hash,false,false);
    window.scrollTo({top:0,behavior:'instant'});
  }
  return true;
}
$$('.tab').forEach(button=>{
  button.addEventListener('click',()=>showTab(button.dataset.tab));
  button.addEventListener('keydown',event=>{
    const index=tabNames.indexOf(button.dataset.tab);
    const next=event.key==='ArrowRight'?(index+1)%tabNames.length:event.key==='ArrowLeft'?(index+tabNames.length-1)%tabNames.length:event.key==='Home'?0:event.key==='End'?tabNames.length-1:-1;
    if(next<0)return;
    event.preventDefault();showTab(tabNames[next]);
    const target=$(`[data-tab="${tabNames[next]}"]`);target.focus({preventScroll:true});
  });
});
window.addEventListener('hashchange',()=>applyHash());
function toast(t){const e=$('#toast');e.textContent=t;e.style.display='block';clearTimeout(toast.timer);toast.timer=setTimeout(()=>e.style.display='none',2200)}
const smooth=()=>matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth';
function sourceTarget(target){
  if(!target||!$('#panel-transcript').contains(target))return false;
  readingPosition?.navigation();showTab('transcript',true,false);
  const id=target.closest('.transcript-section')?.id;
  $$('[data-go-section]').forEach(b=>b.classList.toggle('current',b.dataset.goSection===id));
  const toc=$('.mobile-toc');if(toc)toc.open=false;
  target.tabIndex=-1;target.classList.add('reader-source-target');
  const version=navigationVersion;
  requestAnimationFrame(()=>{
    if(version!==navigationVersion)return;
    target.focus({preventScroll:true});
    const inset=($('.tabs')?.getBoundingClientRect().height||0)+12;
    window.scrollTo({top:Math.max(0,window.scrollY+target.getBoundingClientRect().top-inset),behavior:smooth()});
  });
  return true;
}
function goSection(id){return typeof id==='string'&&sourceTarget(document.getElementById(id))}
function goCue(cue){return Number.isSafeInteger(cue)&&cue>=0&&sourceTarget($$('.reading-paragraph').find(p=>Number(p.dataset.cueStart)<=cue&&Number(p.dataset.cueEnd)>=cue))}
document.addEventListener('click',event=>{
  if(event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||!(event.target instanceof Element))return;
  const action=event.target.closest('[data-go-section],[data-go-cue],[data-source-cue],a[href^="#chapter-"],a[href^="#cue-"]');
  if(!action)return;
  const hash=action.getAttribute('href')||'',cue=action.dataset.sourceCue??action.dataset.goCue??/^#cue-([0-9]+)$/.exec(hash)?.[1];
  const changed=cue!==undefined?goCue(Number(cue)):goSection(action.dataset.goSection||hash.slice(1));
  if(changed)event.preventDefault();
});
window.addEventListener('linkread-source-jump',event=>{if(event.detail&&Number.isSafeInteger(event.detail.cue))goCue(event.detail.cue)});
const validTheme=value=>value==='light'||value==='sepia'||value==='dark';
function currentTheme(){const current=document.documentElement.dataset.theme;return validTheme(current)?current:'light'}
function syncThemeButtons(){const active=currentTheme();$$('button[data-theme-choice]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.themeChoice===active)))}
function setReaderTheme(next){
  if(!validTheme(next))return;
  document.documentElement.dataset.theme=next;
  syncThemeButtons();
  try{localStorage.setItem(isDesktopEmbedded()?'linkread-desktop-theme':'yano-library-theme',next)}catch(e){}
  if(isDesktopEmbedded())window.parent.postMessage({linkReadTheme:next},'*');
}
window.linkreadSyncThemeButtons=syncThemeButtons;
try{const t=localStorage.getItem('yano-library-theme');if(validTheme(t))document.documentElement.dataset.theme=t}catch(e){}
syncThemeButtons();
$$('.theme-btn').forEach(b=>b.addEventListener('click',()=>setReaderTheme(currentTheme()==='dark'?'light':'dark')));
$$('button[data-theme-choice]').forEach(b=>b.addEventListener('click',()=>setReaderTheme(b.dataset.themeChoice)));
const inp=$('#text-search'),searchBar=$('#reader-search'),searchToggle=$('#reader-search-toggle');
let hits=[],hit=-1,hitsCapped=false,lastQuery='',searchTimer=0,searchVersion=0,composing=false;
const MAX_SEARCH_HITS=1500;
const popovers=[['#reader-appearance-toggle','#reader-appearance'],['#reader-export-toggle','#reader-export']];
const copyMenus=$$('[data-copy-options]').map(button=>[button,document.getElementById(button.getAttribute('aria-controls'))]).filter(([,panel])=>panel);
function fitPopover(panel){
  panel.removeAttribute('data-side');panel.style.maxHeight='';
  const anchor=panel.parentElement.getBoundingClientRect(),below=window.innerHeight-anchor.bottom-20,above=anchor.top-20;
  const upward=panel.scrollHeight>below&&above>below;
  if(upward)panel.dataset.side='above';
  panel.style.maxHeight=`${Math.max(80,upward?above:below)}px`;
}
function closePopovers(focus=false){
  for(const [button,panel]of popovers){
    if($(panel).hidden)continue;
    $(panel).hidden=true;$(button).setAttribute('aria-expanded','false');
    if(focus)$(button).focus({preventScroll:true});
  }
  for(const [button,panel]of copyMenus){
    if(panel.hidden)continue;
    panel.hidden=true;button.setAttribute('aria-expanded','false');
    if(focus)button.focus({preventScroll:true});
  }
}
for(const [button,panel]of popovers){
  $(button).addEventListener('click',()=>{
    const opening=$(panel).hidden;closePopovers();$(panel).hidden=!opening;$(button).setAttribute('aria-expanded',String(opening));
    if(opening){fitPopover($(panel));$(panel).querySelector('button:not(:disabled)')?.focus({preventScroll:true})}
  });
}
for(const [button,panel]of copyMenus){
  button.addEventListener('click',()=>{
    const opening=panel.hidden;closePopovers();panel.hidden=!opening;button.setAttribute('aria-expanded',String(opening));
    if(opening){fitPopover(panel);panel.querySelector('button')?.focus({preventScroll:true})}
  });
  button.closest('.anki-card').addEventListener('toggle',event=>{if(!event.target.open){panel.hidden=true;button.setAttribute('aria-expanded','false')}});
}
window.addEventListener('resize',()=>{
  for(const [,selector]of popovers){const panel=$(selector);if(!panel.hidden)fitPopover(panel)}
  for(const [,panel]of copyMenus)if(!panel.hidden)fitPopover(panel);
});
document.addEventListener('pointerdown',event=>{if(event.target instanceof Element&&!event.target.closest('.reader-tool,.quote-copy-control'))closePopovers()});
document.addEventListener('focusin',event=>{if(event.target instanceof Element&&!event.target.closest('.reader-tool,.quote-copy-control'))closePopovers()});
function clearHighlights(){
  const parents=new Set();
  $$('.reader-search-hit').forEach(mark=>{parents.add(mark.parentNode);mark.replaceWith(document.createTextNode(mark.textContent))});
  parents.forEach(parent=>parent?.normalize());
  hits=[];hit=-1;hitsCapped=false;
}
// Group real text nodes by their block boundary. Inline emphasis and links may
// split a match; nested publisher blocks must never produce duplicate results.
function searchBlocks(){
  const blocks=[];
  for(const element of $$('.reading-paragraph,.transcript-section h2,.transcript-section h3')){
    const nodes=[],walker=document.createTreeWalker(element,NodeFilter.SHOW_TEXT);
    while(walker.nextNode())nodes.push(walker.currentNode);
    blocks.push({nodes,panel:'transcript'});
  }
  for(const root of $$('.shownotes-copy')){
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
    let previous=null,group=null;
    while(walker.nextNode()){
      const node=walker.currentNode;
      const block=node.parentElement.closest('p,div,li,h2,h3,h4,h5,h6,pre,blockquote')||root;
      if(block!==previous){group={nodes:[],panel:'shownotes'};blocks.push(group);previous=block}
      group.nodes.push(node);
    }
  }
  return blocks;
}
function highlight(query){
  const pattern=new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'giu'),groups=[];
  let remaining=MAX_SEARCH_HITS;
  for(const block of searchBlocks()){
    if(remaining<=0){hitsCapped=true;break}
    const text=block.nodes.map(node=>node.nodeValue).join('');
    let matches=Array.from(text.matchAll(pattern),item=>({start:item.index,end:item.index+item[0].length,marks:[],panel:block.panel}));
    if(!matches.length)continue;
    if(matches.length>remaining){matches=matches.slice(0,remaining);hitsCapped=true;}
    remaining-=matches.length;
    let offset=0,index=0;
    for(const node of block.nodes){
      const text=node.nodeValue,next=offset+text.length,fragment=document.createDocumentFragment();
      let cursor=0;
      while(index<matches.length&&matches[index].start<next){
        const match=matches[index],start=Math.max(0,match.start-offset),end=Math.min(text.length,match.end-offset);
        if(start<end){
          fragment.append(text.slice(cursor,start));
          const mark=document.createElement('mark');mark.className='reader-search-hit';mark.textContent=text.slice(start,end);
          fragment.append(mark);match.marks.push(mark);cursor=end;
        }
        if(match.end>next)break;
        index++;
      }
      if(cursor){fragment.append(text.slice(cursor));node.replaceWith(fragment)}
      offset=next;
    }
    groups.push(...matches.filter(match=>match.marks.length));
  }
  return groups;
}
function paintSearch(){
  const current=hits[hit];
  $('#match-count').textContent=!lastQuery?'':!hits.length?t('No matches'):t(hitsCapped?'{current} of {total}+ · {section}':'{current} of {total} · {section}',{
    current:hit+1,total:hits.length,section:t(current?.panel==='shownotes'?'Shownotes':'Transcript')
  });
  $('#search-prev').disabled=$('#search-next').disabled=hits.length===0;
}
function selectHit(index){
  if(!hits.length)return;
  hits[hit]?.marks.forEach(mark=>mark.classList.remove('current-hit'));
  hit=(index+hits.length)%hits.length;
  const current=hits[hit],version=++searchVersion;
  current.marks.forEach(mark=>mark.classList.add('current-hit'));paintSearch();
  readingPosition?.navigation();
  if(activeTab!==current.panel)showTab(current.panel,true,false);
  const navigation=navigationVersion;
  requestAnimationFrame(()=>{if(version===searchVersion&&navigation===navigationVersion)current.marks[0]?.scrollIntoView({behavior:smooth(),block:'center'})});
}
function search(jump=true){
  clearTimeout(searchTimer);searchVersion++;clearHighlights();lastQuery=inp.value.trim();
  hits=lastQuery?highlight(lastQuery):[];
  hit=hits.length?Math.max(0,hits.findIndex(match=>match.panel===activeTab)):-1;
  if(jump&&hits.length)selectHit(hit);else paintSearch();
}
function step(n){if(inp.value.trim()!==lastQuery){search();return}if(hits.length)selectHit(hit+n)}
function openSearch(query){
  closePopovers();
  const restore=readingPosition?.preserveLayout();searchBar.hidden=false;searchToggle.setAttribute('aria-expanded','true');restore?.();
  if(typeof query==='string'){inp.value=query;search()}
  else if(inp.value.trim()!==lastQuery)search();
  inp.focus({preventScroll:true});inp.select();
}
function closeSearch(){
  clearTimeout(searchTimer);searchVersion++;
  const restore=readingPosition?.preserveLayout();searchBar.hidden=true;searchToggle.setAttribute('aria-expanded','false');
  clearHighlights();inp.value='';lastQuery='';paintSearch();restore?.();searchToggle.focus({preventScroll:true});
}
searchToggle.addEventListener('click',()=>searchBar.hidden?openSearch():closeSearch());
function scheduleSearch(){clearTimeout(searchTimer);if(!composing)searchTimer=setTimeout(()=>search(),220)}
inp.addEventListener('compositionstart',()=>{composing=true;clearTimeout(searchTimer)});
inp.addEventListener('compositionend',()=>{composing=false;scheduleSearch()});
inp.addEventListener('input',event=>{if(!event.isComposing)scheduleSearch()});
inp.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.isComposing){event.preventDefault();step(event.shiftKey?-1:1)}});
$('#search-prev').addEventListener('click',()=>step(-1));$('#search-next').addEventListener('click',()=>step(1));$('#search-close').addEventListener('click',closeSearch);
$$('[data-keyword]').forEach(button=>button.addEventListener('click',()=>{
  openSearch(button.dataset.keyword);
  if(!hits.length)toast(t('No matches in Transcript or Shownotes'));
}));
// An online cover can be unavailable offline, or a saved one can be damaged: either way
// the header goes back to its text-only layout. Its reserved box stays invisible until
// the image has arrived, and a stalled request is given up rather than waited on.
$$('.article-cover').forEach(cover=>{
  const drop=()=>{cover.closest('.hero')?.classList.remove('has-cover');cover.remove()};
  const loaded=()=>cover.classList.add('is-loaded');
  cover.addEventListener('load',loaded);
  cover.addEventListener('error',drop);
  if(cover.complete)cover.naturalWidth?loaded():drop();
  else setTimeout(()=>{if(!cover.classList.contains('is-loaded'))drop()},8000);
});
// Measure actual text instead of guessing CJK widths. The static flex layout
// remains readable without JS and is also the fallback for unusually large lists.
const cloud=$('.keyword-cloud');
let cloudWidth=0, cloudFrame=0;
function layoutKeywords(){
  if(!cloud)return;
  const width=Math.floor(cloud.getBoundingClientRect().width);
  if(width<1||width===cloudWidth)return;
  cloudWidth=width;
  cloud.classList.remove('is-positioned');
  cloud.style.height='';
  cloud.style.removeProperty('--cloud-scale');
  const words=$$('.cloud-word',cloud);
  words.forEach(word=>{word.style.left='';word.style.top='';word.style.maxWidth=''});
  // Scale the text on hover without moving the button's pointer hit area.
  const fitHover=()=>{
    const widths=words.map(word=>word.offsetWidth);
    words.forEach((word,index)=>word.style.setProperty('--cloud-zoom',Math.min(1.18,1+24/Math.max(1,widths[index])).toFixed(3)));
  };
  const layout=window.LinkReadKeywordLayout;
  if(words.length>52||!layout){fitHover();return}
  const margin=4,gap=4;
  words.forEach(word=>word.style.maxWidth=Math.max(1,width-2*margin)+'px');
  cloud.classList.add('is-positioned');
  // Few keywords would leave the sides of the card empty: enlarge the text and place again.
  let scale=1;
  for(let round=0;round<3;round++){
    cloud.style.setProperty('--cloud-scale',scale.toFixed(3));
    const sizes=words.map(word=>({w:word.offsetWidth,h:word.offsetHeight}));
    const area=sizes.reduce((sum,{w,h})=>sum+(w+gap)*(h+gap),0);
    const tallest=Math.max(...sizes.map(({h})=>h));
    const initialHeight=Math.max(tallest+2*margin,Math.ceil(area/(width*.7)));
    for(let attempt=0;attempt<8;attempt++){
      const height=Math.min(680,initialHeight+attempt*40);
      const boxes=layout.placeKeywords(sizes,width,height,{margin,gap});
      if(!boxes)continue;
      let left=width,top=height,right=0,bottom=0;
      boxes.forEach(({x,y},index)=>{
        left=Math.min(left,x);top=Math.min(top,y);
        right=Math.max(right,x+sizes[index].w);bottom=Math.max(bottom,y+sizes[index].h);
      });
      if(right-left<width*.85&&scale<1.35&&round<2){
        scale=Math.min(1.35,scale*width*.92/(right-left));
        break;
      }
      const fittedHeight=Math.max(tallest+2*margin,bottom-top+2*margin);
      const offsetX=Math.round((width-right-left)/2),offsetY=Math.round((fittedHeight-bottom-top)/2);
      cloud.style.height=fittedHeight+'px';
      words.forEach((word,index)=>{
        word.style.left=(boxes[index].x+offsetX)+'px';
        word.style.top=(boxes[index].y+offsetY)+'px';
      });
      fitHover();
      return;
    }
  }
  cloud.classList.remove('is-positioned');
  fitHover();
}
if(cloud){
  const schedule=()=>{cancelAnimationFrame(cloudFrame);cloudFrame=requestAnimationFrame(layoutKeywords)};
  if(window.ResizeObserver)new ResizeObserver(schedule).observe(cloud);
  else window.addEventListener('resize',schedule);
  document.fonts?.ready.then(()=>{cloudWidth=0;schedule()});
}
function closeReaderLayer(){
  // Fullscreen is above the reader toolbar, even when search remains open behind it.
  if(window.linkreadCloseMindmapLayer?.())return true;
  if(popovers.some(([,panel])=>!$(panel).hidden)||copyMenus.some(([,panel])=>!panel.hidden)){closePopovers(true);return true}
  if(!searchBar.hidden){closeSearch();return true}
  const toc=$('.mobile-toc');
  if(toc?.open&&toc.getClientRects().length){toc.open=false;toc.querySelector('summary')?.focus({preventScroll:true});return true}
  return false;
}
// Native toolbar focus does not change which layer is visually on top.
window.linkreadCloseReaderLayer=closeReaderLayer;
document.addEventListener('keydown',e=>{
  if(e.defaultPrevented||e.isComposing||e.keyCode===229)return;
  if(e.key==='Escape'){
    if(e.repeat){e.preventDefault();return}
    if(closeReaderLayer()){e.preventDefault();e.stopImmediatePropagation()}
    return;
  }
  if(e.key.toLowerCase()==='f'&&(e.metaKey||e.ctrlKey)&&!e.altKey&&!document.fullscreenElement&&!document.body.classList.contains('mindmap-fullscreen-open')){e.preventDefault();openSearch()}
});
async function copyRich(str,plain){const rich='<div style="font-family:Arial,PingFang SC,sans-serif;font-size:15px;line-height:1.9">'+str+'</div>';if(navigator.clipboard&&window.ClipboardItem&&window.isSecureContext){try{await navigator.clipboard.write([new ClipboardItem({'text/html':new Blob([rich],{type:'text/html'}),'text/plain':new Blob([plain],{type:'text/plain'})})]);return 'rich text'}catch(e){}}
 const div=document.createElement('div');div.contentEditable='true';div.style.cssText='position:fixed;left:-10000px;top:0;opacity:0';div.innerHTML=rich;document.body.append(div);const r=document.createRange();r.selectNodeContents(div);const sel=window.getSelection();sel.removeAllRanges();sel.addRange(r);let ok=false;try{ok=document.execCommand('copy')}catch(e){}sel.removeAllRanges();div.remove();if(ok)return 'rich text';if(navigator.clipboard?.writeText){await navigator.clipboard.writeText(plain);return 'plain text'}throw new Error('clipboard unavailable')}
$$('[data-copy-side]').forEach(button=>button.addEventListener('click',async()=>{
  const card=button.closest('.anki-card'),withSource=button.dataset.copySide==='source',isFront=button.dataset.copySide!=='back';
  const content=$(isFront?'.anki-front':'.anki-back',card).cloneNode(true);
  const stamp=$('.quote-time',card).textContent;
  $$('.red',content).forEach(phrase=>{phrase.style.color='#d44b40';phrase.style.fontWeight='700'});
  let rich=content.innerHTML,plain=content.textContent;
  if(withSource){
    const source=document.createElement('p'),identity=[card.dataset.copyTitle,card.dataset.copyAuthor].filter(Boolean).join(' · ');
    source.append(identity,document.createElement('br'),`${t('Time')}: ${stamp}`);
    plain+=`\n\n${identity}\n${t('Time')}: ${stamp}`;
    // This URL is generated by the renderer from the validated source identity.
    // Local files deliberately have no URL; Apple episode URLs do not invent a seek parameter.
    if(card.dataset.copySource){
      const link=document.createElement('a');link.href=card.dataset.copySource;link.textContent=card.dataset.copySourceLabel;
      source.append(document.createElement('br'),link);plain+=`\n${card.dataset.copySource}`;
    }
    rich+=source.outerHTML;closePopovers(true);
  }else if(!isFront){
    const time=document.createElement('p');time.textContent=`${t('Time')}: ${stamp}`;rich+=time.outerHTML;plain+=`\n${time.textContent}`;
  }
  try{await copyRich(rich,plain);toast(t('Copied'))}
  catch(e){toast(t('Could not copy. Check clipboard permissions.'))}
}));
// Standalone HTML and the static shelf share browser-local reading flags.
// Embedded desktop readers hide these controls and use the app's durable toolbar.
const actions=$('[data-reading-actions]'),stateStore='linkread-reading-state-v1';
if(actions){
  const stateKey=actions.dataset.readingKey;
  const initial={isRead:actions.dataset.isRead==='true',isStarred:actions.dataset.isStarred==='true'};
  let state={...initial};
  function storedMap(){
    const value=JSON.parse(localStorage.getItem(stateStore)||'{}');
    return value&&typeof value==='object'&&!Array.isArray(value)?value:{};
  }
  function sync(){
    state={...initial};
    try{const saved=storedMap()[stateKey];if(saved&&typeof saved.isRead==='boolean'&&typeof saved.isStarred==='boolean')state={isRead:saved.isRead,isStarred:saved.isStarred}}catch(e){}
    paint();
  }
  function paint(){
    const complete=$('[data-reading-complete]',actions),label=t(state.isRead?'Mark Unread':'Mark Complete');
    complete.setAttribute('aria-pressed',String(state.isRead));complete.setAttribute('aria-label',label);complete.title=label;
    const star=$('[data-reading-star]',actions);star.setAttribute('aria-pressed',String(state.isStarred));$('span',star).textContent=t(state.isStarred?'Starred':'Star');
  }
  function toggle(key){
    const next={...state,[key]:!state[key]};
    try{const all=storedMap();localStorage.setItem(stateStore,JSON.stringify({...all,[stateKey]:next}));state=next;paint();window.dispatchEvent(new CustomEvent('linkread-reading-state-change',{detail:{key:stateKey,...next}}))}
    catch(e){toast(t('Could not save reading status in this browser.'))}
  }
  $('[data-reading-complete]',actions).addEventListener('click',()=>toggle('isRead'));
  $('[data-reading-star]',actions).addEventListener('click',()=>toggle('isStarred'));
  window.addEventListener('storage',event=>{if(event.key===stateStore)sync()});
  window.addEventListener('pageshow',sync);
  sync();
}
$('[data-refresh-shownotes]')?.addEventListener('click',()=>{
  if(isDesktopEmbedded())window.parent.postMessage({linkReadRefreshShownotes:true},'*');
});
const article=$('.article-body');
const preferencesKey='linkread-reading-preferences-v1',readingText=$$('.article-body,.shownotes-copy');
function parseAppearance(raw){
  const defaults={version:1,fontSize:null,lineWidth:'standard',lineSpacing:'relaxed',fontFamily:'sans'};
  try{
    const saved=JSON.parse(raw||'null');
    if(!saved||typeof saved!=='object'||Array.isArray(saved)||saved.version!==1)return defaults;
    if(Number.isInteger(saved.fontSize)&&saved.fontSize>=14&&saved.fontSize<=26)defaults.fontSize=saved.fontSize;
    if(saved.lineWidth==='wide')defaults.lineWidth='wide';
    if(saved.lineSpacing==='compact')defaults.lineSpacing='compact';
    if(saved.fontFamily==='serif')defaults.fontFamily='serif';
  }catch{}
  return defaults;
}
let appearance=parseAppearance(null);
function appearanceStatus(saved){
  const status=$('#appearance-status');
  status.textContent=t(saved?'Transcript & Shownotes · Saved across articles':'Applies here; preferences could not be saved.');
  if(saved)delete status.dataset.error;else status.dataset.error='true';
}
try{appearance=parseAppearance(localStorage.getItem(preferencesKey))}catch{appearanceStatus(false)}
function paintAppearance(){
  readingText.forEach(element=>element.style.fontSize=appearance.fontSize===null?'':`${appearance.fontSize}px`);
  document.documentElement.dataset.readingWidth=appearance.lineWidth;
  document.documentElement.dataset.readingSpacing=appearance.lineSpacing;
  document.documentElement.dataset.readingFont=appearance.fontFamily;
  const size=parseFloat(getComputedStyle(article).fontSize);
  $('#font-status').textContent=`${size} px`;
  $('#font-decrease').disabled=size<=14;$('#font-increase').disabled=size>=26;
  $('#font-reset').disabled=appearance.fontSize===null&&appearance.lineWidth==='standard'&&appearance.lineSpacing==='relaxed'&&appearance.fontFamily==='sans';
  $$('[data-reading-font]').filter(button=>button.tagName==='BUTTON').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.readingFont===appearance.fontFamily)));
  $$('[data-reading-width]').filter(button=>button.tagName==='BUTTON').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.readingWidth===appearance.lineWidth)));
  $$('[data-reading-spacing]').filter(button=>button.tagName==='BUTTON').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.readingSpacing===appearance.lineSpacing)));
}
function changeAppearance(patch){
  const restore=readingPosition?.preserveLayout();
  appearance=patch===null?parseAppearance(null):{...appearance,...patch};
  paintAppearance();
  try{
    localStorage.setItem(preferencesKey,JSON.stringify(appearance));
    appearanceStatus(true);
  }catch{
    appearanceStatus(false);
    toast(t('Reading appearance could not be saved in this browser.'));
  }
  restore?.();
}
function changeFont(step){changeAppearance({fontSize:Math.max(14,Math.min(26,Math.round(parseFloat(getComputedStyle(article).fontSize)+step)))})}
paintAppearance();
window.addEventListener('resize',()=>{if(appearance.fontSize===null)paintAppearance()});
window.addEventListener('storage',event=>{
  if(event.storageArea!==localStorage)return;
  if(event.key!==preferencesKey&&event.key!==null)return;
  const restore=readingPosition?.preserveLayout();appearance=parseAppearance(event.newValue);paintAppearance();appearanceStatus(true);restore?.();
});
$('#font-decrease').addEventListener('click',()=>changeFont(-1));
$('#font-increase').addEventListener('click',()=>changeFont(1));
$('#font-reset').addEventListener('click',()=>changeAppearance(null));
$$('button[data-reading-font]').forEach(button=>button.addEventListener('click',()=>changeAppearance({fontFamily:button.dataset.readingFont})));
$$('button[data-reading-width]').forEach(button=>button.addEventListener('click',()=>changeAppearance({lineWidth:button.dataset.readingWidth})));
$$('button[data-reading-spacing]').forEach(button=>button.addEventListener('click',()=>changeAppearance({lineSpacing:button.dataset.readingSpacing})));
const sidebarKey='linkread-sidebar-v1',hasToc=Boolean($('.sidebar .toc'));
if(!hasToc)$('.shell')?.classList.add('no-toc');
let sidebarCollapsed=false;
try{sidebarCollapsed=localStorage.getItem(sidebarKey)==='collapsed'}catch{}
function paintSidebar(){
  const wide=window.innerWidth>1000;
  const collapsed=hasToc&&wide&&sidebarCollapsed;
  if(collapsed)document.documentElement.dataset.sidebar='collapsed';
  else delete document.documentElement.dataset.sidebar;
  if(isDesktopEmbedded())window.parent.postMessage({linkReadSidebar:{available:hasToc&&wide,open:!sidebarCollapsed}},'*');
  if(activeTab==='keywords')requestAnimationFrame(()=>{cloudWidth=0;layoutKeywords()});
}
function toggleSidebar(){
  if(!hasToc||window.innerWidth<=1000)return;
  const restore=readingPosition?.preserveLayout();
  sidebarCollapsed=!sidebarCollapsed;
  try{localStorage.setItem(sidebarKey,sidebarCollapsed?'collapsed':'open')}catch{}
  paintSidebar();
  restore?.();
}
paintSidebar();
window.addEventListener('resize',paintSidebar);
function printArticle(){closePopovers();window.print()}
$('#print-page').addEventListener('click',printArticle);
window.addEventListener('message',event=>{
  if(!isDesktopEmbedded()||event.source!==window.parent||!isAllowedHostOrigin(event.origin))return;
  if(event.data?.linkReadCommand==='search')openSearch();
  if(event.data?.linkReadCommand==='print')printArticle();
  if(event.data?.linkReadCommand==='toggle-sidebar')toggleSidebar();
});
readingPosition=window.LinkReadReadingPosition?.create({
  key:$('[data-reading-key]')?.dataset.readingKey,tabNames,
  getActiveTab:()=>activeTab,activateTab:name=>showTab(name,false,false),notice:toast,
});
window.linkreadReadingPosition=readingPosition;
const explicitNavigation=applyHash(true);
readingPosition?.start(explicitNavigation);
})();

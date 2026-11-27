import {Y,document as ydoc,encode,decode,replaceText} from './crdt.js';

const $=selector=>document.querySelector(selector);
const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const main=$('#main'), dialog=$('#dialog');
const state={repos:[],repo:null,branch:'main',path:null,tab:'docs',files:[],history:[],ticket:null,room:null};
let toast;
const roles={owner:'소유자',write:'편집',read:'읽기'};
const kinds={issue:'이슈',proposal:'수정 제안'};
const statusLabels={open:'열림',closed:'닫힘',merged:'병합됨'};
const button=(label,act,value='',primary=false)=>'<button type="button" '+(primary?'class="primary" ':'')+'data-act="'+esc(act)+'" data-value="'+esc(value)+'">'+esc(label)+'</button>';
const time=stamp=>new Date(stamp).toLocaleString('ko-KR',{dateStyle:'medium',timeStyle:'short'});

function say(message){
  $('#status').textContent=message;
  clearTimeout(toast);
  if(message) toast=setTimeout(()=>$('#status').textContent='',6000);
}
async function api(action,args={}){
  const response=await fetch('/api/action',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action,args})});
  const data=await response.json();
  if(!response.ok)throw new Error(data.error??'요청이 실패했습니다.');
  return data.result;
}
function form(title,fields,submit){
  $('#dialog-title').textContent=title;
  $('#dialog-error').textContent='';
  $('#dialog-fields').innerHTML=fields.map(([name,label,type,value])=>{
    const control=type==='textarea'
      ? '<textarea rows="4" name="'+esc(name)+'">'+esc(value)+'</textarea>'
      : Array.isArray(type) ? '<select name="'+esc(name)+'">'+type.map(([key,caption])=>'<option value="'+esc(key)+'"'+(key===value?' selected':'')+'>'+esc(caption)+'</option>').join('')+'</select>'
      : '<input name="'+esc(name)+'" type="'+esc(type??'text')+'" value="'+esc(value)+'" required>';
    return '<label>'+esc(label)+control+'</label>';
  }).join('');
  $('#dialog-form').onsubmit=async event=>{
    event.preventDefault();
    const values=Object.fromEntries(new FormData(event.target));
    $('#submit').disabled=true;
    try{await submit(values);dialog.close();}
    catch(error){$('#dialog-error').textContent=error.message;}
    finally{$('#submit').disabled=false;}
  };
  dialog.showModal();
}
$('#cancel').onclick=()=>dialog.close();
const scope=()=>({repo:state.repo.id,branch:state.branch,path:state.path});
const editable=()=>state.repo?.role!=='read';

async function loadRepos(){
  state.repos=await api('list_repositories');
  $('#repos').innerHTML=state.repos.map(r=>'<button type="button" data-act="repo" data-value="'+esc(r.id)+'" aria-current="'+(state.repo?.id===r.id)+'">'+esc(r.name)+'</button>').join('');
}
function leaveRoom(){if(state.room?.timer)clearInterval(state.room.timer);state.room=null;}
async function openRepo(repo) {
  leaveRoom();
  state.repo=await api('repository',{repo});
  state.branch='main';state.path=null;state.tab='docs';state.ticket=null;
  await Promise.all([loadRepos(),loadFiles()]);
  render();
}
async function loadFiles(){ state.files=await api('list_documents',{repo:state.repo.id,branch:state.branch}); }
async function refreshRepo(){state.repo=await api('repository',{repo:state.repo.id});render();}
function render(){
  if(!state.repo){
    main.innerHTML='<section class="empty"><h1>공유 문서 저장소</h1><p>저장소를 만들거나 초대 링크로 참여하세요.</p>'+button('저장소 만들기','new-repo','',true)+'</section>';
    return;
  }
  const r=state.repo;
  const starred=state.repos.find(x=>x.id===r.id)?.starred;
  const tabs=[['docs','문서'],['issues','이슈'],['proposals','수정 제안'],['releases','릴리스'],['members','멤버'],['activity','활동']];
  main.innerHTML='<div class="toolbar"><div><h1>'+esc(r.name)+'</h1><p>'+esc(r.description)+'</p></div><div class="row">'+button(starred?'★ 즐겨찾기':'☆ 즐겨찾기','star',starred?'off':'on')+button('새로 고침','refresh')+'</div></div>'+
    '<nav class="tabs" aria-label="저장소 보기">'+tabs.map(([key,label])=>'<button type="button" data-act="tab" data-value="'+key+'" aria-current="'+(state.tab===key)+'">'+label+'</button>').join('')+'</nav>'+
    ({docs:docView,issues:()=>ticketView('issue'),proposals:()=>ticketView('proposal'),members:memberView,releases:releaseView,activity:activityView}[state.tab])();
  if(state.tab==='docs'&&state.room) bindEditor();
}
function docView(){
  const branch='<label>브랜치 <select id="branch">'+state.repo.branches.map(b=>'<option '+(b.name===state.branch?'selected ':'')+'value="'+esc(b.name)+'">'+esc(b.name)+'</option>').join('')+'</select></label>';
  const files=state.files.map(f=>'<button type="button" data-act="doc" data-value="'+esc(f.path)+'" aria-current="'+(state.path===f.path)+'">'+esc(f.path)+'</button>').join('');
  const list='<section class="panel"><div class="row">'+branch+(editable()?button('브랜치 만들기','new-branch'):'')+'</div><nav class="list" aria-label="문서">'+files+'</nav>'+(editable()?button('문서 만들기','new-doc','',true):'')+'</section>';
  if(!state.room) return '<div class="workspace">'+list+'<section class="panel"><h2>문서를 선택하세요</h2></section></div>';
  const history=state.history.map(v=>'<article><strong>'+esc(v.message)+'</strong> <span class="hint">'+esc(v.actor)+' · '+time(v.created)+'</span><div class="row">'+button('비교 기준으로 선택','base',v.id)+button('비교 대상','compare',v.id)+(editable()?button('복원','restore',v.id):'')+'</div></article>').join('');
  const editor='<section class="panel editor"><div class="toolbar"><h2 class="path">'+esc(state.path)+'</h2><div class="row"><span id="live" class="hint" aria-live="polite">동기화됨</span>'+(editable()?button('버전 저장','checkpoint','',true):'')+'</div></div><textarea id="editor" aria-label="문서 본문" spellcheck="false" maxlength="64000" '+(editable()?'':'readonly')+'></textarea>'+
    '<section class="stack"><h2>버전 이력</h2><span class="hint" id="compare-base"></span><div class="list">'+(history||'<p>저장한 버전이 없습니다.</p>')+'</div><div id="diff"></div></section></section>';
  return '<div class="workspace">'+list+editor+'</div>';
}
function ticketView(kind){
  const active=state.ticket;
  if(active){
    const comments=active.comments.map(c=>'<article><strong>'+esc(c.actor)+'</strong> <span class="hint">'+time(c.created)+'</span><pre>'+esc(c.body)+'</pre></article>').join('');
    return '<section class="panel">'+button('목록','ticket-list')+'<h2 class="stack">'+esc(active.title)+'</h2><p>'+esc(statusLabels[active.status])+(active.path?' · '+esc(active.source)+' → '+esc(active.target)+' · '+esc(active.path):'')+'</p><pre>'+esc(active.body)+'</pre><div class="row">'+(editable()?button('댓글','comment')+(active.status!=='merged'?button(active.status==='open'?'닫기':'다시 열기','ticket-status',active.status==='open'?'closed':'open'):''):'')+(state.repo.role==='owner'&&active.kind==='proposal'&&active.status==='open'?button('병합','merge','',true):'')+'</div><div class="list">'+comments+'</div></section>';
  }
  const items=state.repo.tickets.filter(t=>t.kind===kind);
  return '<section class="panel"><div class="toolbar"><h2>'+kinds[kind]+'</h2>'+(editable()?button(kind==='issue'?'이슈 열기':'수정 제안 열기','new-ticket',kind,true):'')+'</div><div class="list">'+(items.map(t=>'<article>'+button(t.title,'ticket',t.id)+' <span class="hint">'+esc(statusLabels[t.status])+'</span></article>').join('')||'<p>아직 등록된 항목이 없습니다.</p>')+'</div></section>';
}
function memberView(){
  const owner=state.repo.role==='owner';
  return '<section class="panel"><div class="toolbar"><h2>멤버</h2>'+(owner?button('초대 링크 만들기','invite','',true):'')+'</div><div class="list">'+state.repo.members.map(m=>'<article class="toolbar"><div>'+esc(m.email)+' <span class="hint">'+roles[m.role]+'</span></div>'+(owner&&m.role!=='owner'?button('권한 변경','member',m.email):'')+'</article>').join('')+'</div><div id="invite-link"></div></section>';
}
function releaseView(){
  return '<section class="panel"><div class="toolbar"><h2>릴리스</h2>'+(state.repo.role==='owner'?button('릴리스 만들기','release','',true):'')+'</div><div class="list">'+(state.repo.releases.map(r=>'<article><strong>'+esc(r.tag)+'</strong> <span class="hint">'+time(r.created)+'</span><pre>'+esc(r.notes)+'</pre><code>'+esc(r.commit_id)+'</code></article>').join('')||'<p>릴리스가 없습니다.</p>')+'</div></section>';
}
function activityView(){
  return '<section class="panel"><h2>활동</h2><div class="list">'+state.repo.activity.map(a=>'<article><strong>'+esc(a.event)+'</strong> '+esc(a.detail)+' <span class="hint">'+esc(a.actor)+' · '+time(a.created)+'</span></article>').join('')+'</div></section>';
}
async function openDoc(path){
  leaveRoom();
  state.path=path;
  const args=scope();
  const [record,history]=await Promise.all([api('read_document',args),api('list_history',args)]);
  const model=ydoc(record.state);
  const room={args,model,rev:record.rev,pending:[],sending:null,composing:false,reading:false,base:null};
  model.on('update',(bytes,origin)=>{
    if(origin!=='remote'){room.pending.push(bytes);live('동기화 중');}
  });
  state.room=room;state.history=history;
  render();
  room.timer=setInterval(()=>tick(room),900);
}
function live(text){const label=$('#live');if(label)label.textContent=text;}
function bindEditor(){
  const room=state.room, area=$('#editor');
  if(!room||!area)return;
  area.value=room.model.getText('body').toString();
  area.oncompositionstart=()=>{room.composing=true;};
  area.oncompositionend=()=>{room.composing=false;replaceText(room.model,area.value);};
  area.oninput=()=>{if(!room.composing)replaceText(room.model,area.value);};
}
function updateRoom(room,record){
  if(record.unchanged||!record.state)return;
  const area=state.room===room?$('#editor'):null;
  if(room.composing)return;
  const text=room.model.getText('body');
  const selection=area?[area.selectionStart,area.selectionEnd].map(i=>Y.createRelativePositionFromTypeIndex(text,i)):null;
  Y.applyUpdate(room.model,decode(record.state),'remote');
  room.rev=Math.max(room.rev,record.rev);
  if(area&&area.value!==text.toString()){
    area.value=text.toString();
    const [from,to]=selection.map(p=>Y.createAbsolutePositionFromRelativePosition(p,room.model)?.index??0);
    area.setSelectionRange(from,to);
  }
}
async function flush(room=state.room){
  if(!room)return;
  if(room.sending)await room.sending;
  if(!room.pending.length)return;
  const pending=room.pending.splice(0);
  room.sending=(async()=>{
    try{
      updateRoom(room,await api('patch_document',{...room.args,update:encode(Y.mergeUpdates(pending))}));
      live('동기화됨');
    }catch(error){room.pending.unshift(...pending);live('다시 연결하는 중');throw error;}
  })();
  try{await room.sending;}finally{room.sending=null;}
}
async function tick(room){
  if(state.room!==room||room.composing||room.reading)return;
  room.reading=true;
  try{
    await flush(room);
    updateRoom(room,await api('read_document',{...room.args,since:room.rev}));
    if(!room.pending.length)live('동기화됨');
  } catch {live('다시 연결하는 중');}
  finally{room.reading=false;}
}
window.addEventListener('beforeunload',event=>{if(state.room?.pending.length||state.room?.sending){event.preventDefault();event.returnValue='';}});

const handlers={
  'new-repo':()=>form('저장소 만들기',[['name','저장소 이름'],['description','설명','textarea']],async a=>openRepo((await api('create_repository',a)).id)),
  repo:async repo=>{await flush();await openRepo(repo);},
  tab:async key=>{await flush();if(key!==state.tab)leaveRoom();state.tab=key;state.ticket=null;await refreshRepo();if(key==='docs'&&state.path&&!state.room)await openDoc(state.path);},
  refresh:async()=>{await flush();await loadRepos();await refreshRepo();if(state.tab==='docs'&&state.path)await openDoc(state.path);},
  star:async mode=>{await api('star_repository',{repo:state.repo.id,star:mode==='on'});await loadRepos();render();},
  doc:async path=>{await flush();await openDoc(path);},
  'new-doc':()=>form('문서 만들기',[['path','문서 경로','text','notes.md'],['text','본문','textarea']],async a=>{
    await flush();await api('create_document',{...scope(),...a});await loadFiles();await openDoc(a.path);
  }),
  'new-branch':()=>form('브랜치 만들기',[['name','브랜치 이름']],async a=>{
    await flush();await api('create_branch',{repo:state.repo.id,parent:state.branch,...a});leaveRoom();state.path=null;state.branch=a.name;await loadFiles();await refreshRepo();
  }),
  checkpoint:()=>form('버전 저장',[['message','버전 메시지']],async a=>{
    await flush();await api('checkpoint',{...scope(),...a});state.history=await api('list_history',scope());render();say('버전을 저장했습니다.');
  }),
  base:id=>{state.room.base=id;$('#compare-base').textContent='비교 기준을 선택했습니다.';},
  compare:async target=>{
    const base=state.room.base??state.history[1]?.id;
    if(!base)throw Error('비교 기준을 선택하세요.');
    const r=await api('compare_commits',{repo:state.repo.id,base,target});
    $('#diff').innerHTML='<div class="diff"><div><h2>기준본</h2><pre>'+esc(r.base.text)+'</pre></div><div><h2>비교본</h2><pre>'+esc(r.target.text)+'</pre></div></div>';
  },
  restore:commit=>form('선택한 버전의 내용으로 복원',[],async()=>{await flush();await api('restore_commit',{...scope(),commit,expected_rev:state.room.rev});await openDoc(state.path);say('작업 문서에 이전 내용을 복원했습니다.');}),
  invite:()=>form('저장소 초대',[['email','멤버 이메일','email'],['role','권한',[['write','편집'],['read','읽기']],'write']],async a=>{
    const invite=await api('invite_member',{repo:state.repo.id,...a});
    const href=new URL(invite.path,location.origin).href;
    $('#invite-link').innerHTML='<div class="stack"><label for="invite-url">초대 링크</label> <input id="invite-url" readonly value="'+esc(href)+'"> '+button('복사','copy-invite')+'</div>';
  }),
  'copy-invite':async()=>{await navigator.clipboard.writeText($('#invite-url').value);say('링크를 복사했습니다.');},
  member:email=>form('멤버 권한 변경',[['role','권한',[['write','편집'],['read','읽기'],['remove','제거']],'read']],async a=>{await api('set_member_role',{repo:state.repo.id,email,...a});await refreshRepo();}),
  'new-ticket':kind=>{
    const fields=[['title','제목'],['body','내용','textarea']];
    if(kind==='proposal'){
      const branches=state.repo.branches.map(b=>[b.name,b.name]);
      fields.push(['source','수정 브랜치',branches,branches[1]?.[0]],['target','대상 브랜치',branches,'main'],['path','문서 경로']);
    }
    form(kinds[kind]+' 열기',fields,async a=>{await api('create_ticket',{repo:state.repo.id,kind,...a});await refreshRepo();});
  },
  ticket:async ticket=>{state.ticket=await api('read_ticket',{repo:state.repo.id,ticket});render();},
  'ticket-list':async()=>{state.ticket=null;await refreshRepo();},
  comment:()=>form('댓글',[['body','내용','textarea']],async a=>{await api('comment',{repo:state.repo.id,ticket:state.ticket.id,...a});await handlers.ticket(state.ticket.id);}),
  'ticket-status':async status=>{await api('set_ticket_status',{repo:state.repo.id,ticket:state.ticket.id,status});await handlers.ticket(state.ticket.id);},
  merge:()=>form('수정 제안 병합',[],async()=>{await api('merge_proposal',{repo:state.repo.id,ticket:state.ticket.id});await handlers.ticket(state.ticket.id);}),
  release:()=>form('릴리스 만들기',[['tag','태그','text','v1.0.0'],['commit','커밋 ID'],['notes','노트','textarea']],async a=>{await api('create_release',{repo:state.repo.id,...a});await refreshRepo();}),
};
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-act]');
  if(!b)return;
  try{await handlers[b.dataset.act]?.(b.dataset.value);}catch(error){say(error.message);}
});
$('#new-repo').onclick=handlers['new-repo'];
main.addEventListener('change',async e=>{
  if(e.target.id!=='branch')return;
  try{await flush();leaveRoom();state.path=null;state.branch=e.target.value;await loadFiles();render();}catch(error){say(error.message);}
});
async function start(){
  const session=await fetch('/api/session').then(r=>r.json());
  if(!session.email){
    $('#new-repo').disabled=true;
    const href='/signin-with-chatgpt?return_to='+encodeURIComponent(location.pathname+location.search);
    main.innerHTML='<section class="empty"><h1>공유 문서 저장소</h1><a target="_top" href="'+esc(href)+'">ChatGPT로 로그인</a></section>';
    return;
  }
  $('#identity').textContent=session.email;
  const invite=new URLSearchParams(location.search).get('invite');
  if(invite){
    try{const joined=await api('accept_invite',{invite});history.replaceState(null,'','/');await openRepo(joined.repo);say('저장소에 참여했습니다.');return;}
    catch(error){say(error.message);}
  }
  await loadRepos();
  if(state.repos[0])await openRepo(state.repos[0].id);
  else render();
}
start().catch(error=>{main.innerHTML='<section class="empty"><h1>저장소를 열 수 없습니다</h1><p>'+esc(error.message)+'</p></section>';});

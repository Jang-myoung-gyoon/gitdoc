import { Y, document, snapshot, decode, replaceText } from './crdt.js';

const now = () => Date.now();
const id = () => crypto.randomUUID();
const fail = (message, status=400) => { throw Object.assign(new Error(message), {status}); };
const need = (value, message='찾을 수 없습니다.') => value ?? fail(message,404);
const sql = (db, query, ...values) => db.prepare(query).bind(...values);
const first = (db, query, ...values) => sql(db, query, ...values).first();
const all = async (db, query, ...values) => (await sql(db, query, ...values).all()).results;
const run = (db, query, ...values) => sql(db, query, ...values).run();
const string = (value, max, name, optional=false) => {
  if (typeof value !== 'string' || value.length>max || (!optional && !value.trim())) fail(name+'을 확인하세요.');
  return value;
};
const named = (value, name) => {
  if (typeof value !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$/.test(value) || value.split('/').some(x=>!x || x==='..' || x==='.')) fail(name+'을 확인하세요.');
  return value;
};
const selectDoc = 'SELECT * FROM documents WHERE repo=? AND branch=? AND path=?';
const maxText = 64000;
const maxUpdate = 840000;

async function role(db,user,repo,level='read') {
  const m=await first(db,'SELECT role FROM members WHERE repo=? AND uid=?',repo,user.id);
  if (!m || (level==='owner' && m.role!=='owner') || (level==='write' && m.role==='read')) fail('저장소 접근 권한이 없습니다.',403);
  return m.role;
}
function audit(db,user,repo,event,detail) {
  return sql(db,'INSERT INTO activity VALUES(?,?,?,?,?,?)',id(),repo,user.email,event,detail,now());
}
function docState(state) { return document(state).getText('body').toString(); }
const permission = "EXISTS(SELECT 1 FROM members WHERE repo=? AND uid=? AND role IN('owner','write'))";

export const actions = {
  async list_repositories(db,user) {
    return all(db, 'SELECT r.id,r.name,r.description,m.role,(SELECT COUNT(*) FROM stars WHERE repo=r.id) AS stars, EXISTS(SELECT 1 FROM stars WHERE repo=r.id AND uid=m.uid) AS starred FROM repos r JOIN members m ON m.repo=r.id WHERE m.uid=? ORDER BY r.created DESC LIMIT 100',user.id);
  },
  async create_repository(db,user,a) {
    const repo=id(), name=string(a.name,80,'이름').trim();
    const description=string(a.description??'',400,'설명',true);
    await db.batch([
      sql(db,'INSERT INTO repos VALUES(?,?,?,?,?)',repo,name,description,user.id,now()),
      sql(db,'INSERT INTO members VALUES(?,?,?,?)',repo,user.id,user.email,'owner'),
      sql(db,'INSERT INTO branches VALUES(?,?,?,?)',repo,'main',null,now()),
      audit(db,user,repo,'repository.created',name),
    ]);
    return {id:repo,name,description,role:'owner'};
  },
  async repository(db,user,{repo}) {
    const access=await role(db,user,repo);
    const [record, branches, members, tickets, releases, activity]=await Promise.all([
      first(db,'SELECT id,name,description,created FROM repos WHERE id=?',repo),
      all(db,'SELECT name,parent FROM branches WHERE repo=? ORDER BY created',repo),
      all(db,'SELECT email,role FROM members WHERE repo=? ORDER BY role,email',repo),
      all(db,'SELECT id,kind,title,status,actor,source,target,path FROM tickets WHERE repo=? ORDER BY created DESC LIMIT 100',repo),
      all(db,'SELECT tag,commit_id,notes,created FROM releases WHERE repo=? ORDER BY created DESC LIMIT 100',repo),
      all(db,'SELECT actor,event,detail,created FROM activity WHERE repo=? ORDER BY created DESC LIMIT 100',repo)
    ]);
    return {...record,role:access,branches,members,tickets,releases,activity};
  },
  async invite_member(db,user,a) {
    await role(db,user,a.repo,'owner');
    const email=string(a.email,254,'이메일').trim().toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || !['read','write'].includes(a.role)) fail('이메일과 권한을 확인하세요.');
    const invite=id(), expires=now()+604800000;
    await db.batch([
      sql(db,'INSERT INTO invites VALUES(?,?,?,?,?,NULL)',invite,a.repo,email,a.role,expires),
      audit(db,user,a.repo,'member.invited',email)
    ]);
    return {invite,expires,path:'/?invite='+invite};
  },
  async accept_invite(db,user,{invite}) {
    const rec=need(await first(db,'SELECT * FROM invites WHERE id=? AND email=? AND expires>? AND accepted IS NULL',invite,user.email,now()),'사용 가능한 초대를 찾을 수 없습니다.');
    const stamp=now();
    const results=await db.batch([
      sql(db,'UPDATE invites SET accepted=? WHERE id=? AND accepted IS NULL AND expires>?',stamp,invite,stamp),
      sql(db,'INSERT INTO members SELECT repo,?,?,role FROM invites WHERE id=? AND changes()=1 ON CONFLICT(repo,uid) DO NOTHING',user.id,user.email,invite),
    ]);
    if (!results[0].meta.changes) fail('이미 사용된 초대입니다.',409);
    return {repo:rec.repo};
  },
  async set_member_role(db,user,a) {
    await role(db,user,a.repo,'owner');
    if (!['read','write','remove'].includes(a.role)) fail('권한을 확인하세요.');
    const email=string(a.email,254,'이메일').toLowerCase();
    const [result]=await db.batch([a.role==='remove'
      ? sql(db,"DELETE FROM members WHERE repo=? AND email=? AND role!='owner'",a.repo,email)
      : sql(db,"UPDATE members SET role=? WHERE repo=? AND email=? AND role!='owner'",a.role,a.repo,email),
      audit(db,user,a.repo,'member.role',email+' '+a.role)]);
    if (!result.meta.changes) fail('변경할 멤버를 찾을 수 없습니다.',404);
    return {email,role:a.role};
  },
  async list_documents(db,user,{repo,branch='main'}) {
    await role(db,user,repo);
    return all(db,'SELECT path,rev,head,head_rev FROM documents WHERE repo=? AND branch=? ORDER BY path LIMIT 100',repo,branch);
  },
  async create_document(db,user,a) {
    await role(db,user,a.repo,'write');
    const branch=a.branch??'main', path=named(a.path,'문서 경로');
    const doc=document(); replaceText(doc,string(a.text??'',maxText,'본문',true));
    const state=snapshot(doc);
    if ((await all(db,'SELECT path FROM documents WHERE repo=? AND branch=? LIMIT 100',a.repo,branch)).length>=100) fail('브랜치의 문서 한도에 도달했습니다.');
    const result=await run(db,'INSERT OR IGNORE INTO documents(repo,branch,path,state) SELECT ?,?,?,? WHERE EXISTS(SELECT 1 FROM branches WHERE repo=? AND name=?)',a.repo,branch,path,state,a.repo,branch);
    if (!result.meta.changes) fail('브랜치 또는 새 문서 경로를 확인하세요.',409);
    await audit(db,user,a.repo,'document.created',branch+'/'+path).run();
    return {path,branch,rev:0};
  },
  async read_document(db,user,{repo,branch='main',path,since}) {
    const access=await role(db,user,repo);
    const d=need(await first(db,selectDoc,repo,branch,path));
    if (since===d.rev) return {rev:d.rev,head:d.head,unchanged:true,role:access};
    return {path,branch,rev:d.rev,head:d.head,head_rev:d.head_rev,state:d.state,text:docState(d.state),role:access};
  },
  async patch_document(db,user,a) {
    await role(db,user,a.repo,'write');
    const patch=decode(string(a.update,maxUpdate,'편집 업데이트'));
    return writeDoc(db,user,a,doc=>Y.applyUpdate(doc,patch));
  },
  async edit_document(db,user,a) {
    await role(db,user,a.repo,'write');
    if (!Number.isSafeInteger(a.expected_rev)) fail('읽은 문서의 rev를 지정하세요.');
    const text=string(a.text,maxText,'본문',true);
    return writeDoc(db,user,a,doc=>replaceText(doc,text),a.expected_rev);
  },
  async checkpoint(db,user,a) {
    await role(db,user,a.repo,'write');
    const repo=a.repo, branch=a.branch??'main', path=a.path;
    const message=string(a.message,200,'버전 메시지');
    const d=need(await first(db,selectDoc,repo,branch,path));
    if (d.rev===d.head_rev) fail('저장할 새 변경이 없습니다.',409);
    const commit=id(), stamp=now();
    const [created]=await db.batch([
      sql(db,'INSERT INTO commits SELECT ?,repo,branch,path,head,state,?,?,? FROM documents WHERE repo=? AND branch=? AND path=? AND rev=? AND head_rev IS ? AND '+permission,commit,message,user.email,stamp,repo,branch,path,d.rev,d.head_rev,repo,user.id),
      sql(db,'UPDATE documents SET head=?,head_rev=rev WHERE repo=? AND branch=? AND path=? AND EXISTS(SELECT 1 FROM commits WHERE id=?)',commit,repo,branch,path,commit),
      sql(db,"INSERT INTO activity SELECT ?,?,?,'document.checkpoint',?,? WHERE EXISTS(SELECT 1 FROM commits WHERE id=?)",id(),repo,user.email,path,stamp,commit),
    ]);
    if (!created.meta.changes) fail('문서가 갱신되었습니다. 다시 시도하세요.',409);
    return {commit,rev:d.rev,message};
  },
  async list_history(db,user,{repo,branch='main',path}) {
    await role(db,user,repo);
    return all(db,'SELECT id,parent,message,actor,created FROM commits WHERE repo=? AND branch=? AND path=? ORDER BY created DESC,id DESC LIMIT 100',repo,branch,path);
  },
  async compare_commits(db,user,{repo,base,target}) {
    await role(db,user,repo);
    const a=need(await first(db,'SELECT id,path,state FROM commits WHERE repo=? AND id=?',repo,base));
    const b=need(await first(db,'SELECT id,path,state FROM commits WHERE repo=? AND id=?',repo,target));
    return {base:{id:a.id,path:a.path,text:docState(a.state)},target:{id:b.id,path:b.path,text:docState(b.state)}};
  },
  async restore_commit(db,user,a) {
    const prev=await actions.compare_commits(db,user,{repo:a.repo,base:a.commit,target:a.commit});
    if (prev.base.path!==a.path) fail('같은 문서의 버전을 선택하세요.');
    return actions.edit_document(db,user,{...a,text:prev.base.text});
  },
  async create_branch(db,user,a) {
    await role(db,user,a.repo,'write');
    const name=named(a.name,'브랜치 이름'), parent=a.parent??'main';
    if ((await all(db,'SELECT name FROM branches WHERE repo=? LIMIT 50',a.repo)).length>=50) fail('브랜치 한도에 도달했습니다.');
    need(await first(db,'SELECT name FROM branches WHERE repo=? AND name=?',a.repo,parent),'원본 브랜치를 찾을 수 없습니다.');
    if (await first(db,'SELECT name FROM branches WHERE repo=? AND name=?',a.repo,name)) fail('이미 있는 브랜치입니다.',409);
    await db.batch([
      sql(db,'INSERT INTO branches VALUES(?,?,?,?)',a.repo,name,parent,now()),
      sql(db,'INSERT INTO documents(repo,branch,path,state) SELECT repo,?,path,state FROM documents WHERE repo=? AND branch=?',name,a.repo,parent),
      audit(db,user,a.repo,'branch.created',name),
    ]);
    return {name,parent};
  },
  async create_ticket(db,user,a) {
    await role(db,user,a.repo,'write');
    const kind=a.kind??'issue', title=string(a.title,120,'제목'), body=string(a.body??'',5000,'본문',true);
    if (!['issue','proposal'].includes(kind)) fail('종류를 확인하세요.');
    if (kind==='proposal') {
      if (a.source===a.target) fail('서로 다른 브랜치를 선택하세요.');
      await Promise.all([a.source,a.target].map(branch=>first(db,selectDoc,a.repo,branch??'',a.path??'').then(r=>need(r,'양쪽 브랜치에 문서가 필요합니다.'))));
    }
    const ticket=id();
    await db.batch([
      sql(db,'INSERT INTO tickets VALUES(?,?,?,?,?,?,?,?,?,?,?)',ticket,a.repo,kind,title,body,'open',a.source??null,a.target??null,a.path??null,user.email,now()),
      audit(db,user,a.repo,kind+'.opened',title)
    ]);
    return {id:ticket,kind,title};
  },
  async read_ticket(db,user,{repo,ticket}) {
    await role(db,user,repo);
    const t=need(await first(db,'SELECT * FROM tickets WHERE repo=? AND id=?',repo,ticket));
    const comments=await all(db,'SELECT body,actor,created FROM comments WHERE ticket=? ORDER BY created LIMIT 100',ticket);
    return {...t,comments};
  },
  async comment(db,user,a) {
    await role(db,user,a.repo,'write');
    await actions.read_ticket(db,user,a);
    await run(db,'INSERT INTO comments VALUES(?,?,?,?,?)',id(),a.ticket,string(a.body,5000,'댓글'),user.email,now());
    return {ok:true};
  },
  async set_ticket_status(db,user,a) {
    await role(db,user,a.repo,'write');
    if (!['open','closed'].includes(a.status)) fail('상태를 확인하세요.');
    const result=await run(db,"UPDATE tickets SET status=? WHERE repo=? AND id=? AND status!='merged'",a.status,a.repo,a.ticket);
    if (!result.meta.changes) fail('상태를 변경할 수 없습니다.',409);
    return {status:a.status};
  },
  async merge_proposal(db,user,{repo,ticket}) {
    await role(db,user,repo,'owner');
    const t=await actions.read_ticket(db,user,{repo,ticket});
    if (t.kind!=='proposal' || t.status!=='open') fail('열린 수정 제안만 병합할 수 있습니다.',409);
    const [source,target]=await Promise.all([t.source,t.target].map(b=>first(db,selectDoc,repo,b,t.path)));
    need(source); need(target);
    const doc=document(target.state);
    Y.applyUpdate(doc,decode(source.state));
    const state=snapshot(doc), commit=id(), stamp=now(), rev=target.rev+1;
    if (state.length>maxUpdate || doc.getText('body').length>maxText) fail('병합 결과가 문서 제한을 초과합니다.');
    const [created]=await db.batch([
      sql(db,"INSERT INTO commits SELECT ?,d.repo,d.branch,d.path,d.head,?,?,?,? FROM documents d JOIN documents s ON s.repo=d.repo AND s.path=d.path AND s.branch=? JOIN tickets t ON t.id=? AND t.status='open' WHERE d.repo=? AND d.branch=? AND d.path=? AND d.rev=? AND s.rev=?",commit,state,'Merge '+t.title,user.email,stamp,t.source,ticket,repo,t.target,t.path,target.rev,source.rev),
      sql(db,'UPDATE documents SET state=?,rev=?,head=?,head_rev=? WHERE repo=? AND branch=? AND path=? AND EXISTS(SELECT 1 FROM commits WHERE id=?)',state,rev,commit,rev,repo,t.target,t.path,commit),
      sql(db,"UPDATE tickets SET status='merged' WHERE id=? AND EXISTS(SELECT 1 FROM commits WHERE id=?)",ticket,commit),
      sql(db,"INSERT INTO activity SELECT ?,?,?,'proposal.merged',?,? WHERE EXISTS(SELECT 1 FROM commits WHERE id=?)",id(),repo,user.email,t.title,stamp,commit),
    ]);
    if (!created.meta.changes) fail('병합 중 문서가 바뀌었습니다. 다시 시도하세요.',409);
    return {commit,rev,status:'merged'};
  },
  async create_release(db,user,a) {
    await role(db,user,a.repo,'owner');
    const tag=named(a.tag,'태그'), notes=string(a.notes??'',5000,'노트',true);
    const result=await run(db,'INSERT OR IGNORE INTO releases SELECT repo,?,id,?,? FROM commits WHERE repo=? AND id=?',tag,notes,now(),a.repo,a.commit);
    if (!result.meta.changes) fail('태그가 이미 있거나 대상 커밋을 찾을 수 없습니다.',409);
    await audit(db,user,a.repo,'release.created',tag).run();
    return {tag};
  },
  async star_repository(db,user,{repo,star=true}) {
    await role(db,user,repo);
    await (star ? run(db,'INSERT OR IGNORE INTO stars VALUES(?,?)',repo,user.id) : run(db,'DELETE FROM stars WHERE repo=? AND uid=?',repo,user.id));
    return {starred:!!star};
  },
};

async function writeDoc(db,user,a,apply,expected=null) {
  const {repo,branch='main',path}=a;
  for (let attempt=0;attempt<8;attempt++) {
    const current=need(await first(db,selectDoc,repo,branch,path));
    if (expected!==null && expected!==current.rev) fail('최신 버전을 다시 읽어 주세요.',409);
    const doc=document(current.state);
    try { apply(doc); } catch { fail('편집 업데이트를 읽을 수 없습니다.'); }
    const state=snapshot(doc);
    if (doc.getText('body').length>maxText || state.length>maxUpdate) fail('문서 크기 제한을 초과했습니다.');
    if (state===current.state) return {rev:current.rev,state};
    const result=await run(db,'UPDATE documents SET state=?,rev=rev+1 WHERE repo=? AND branch=? AND path=? AND rev=? AND '+permission,state,repo,branch,path,current.rev,repo,user.id);
    if(result.meta.changes) return {rev:current.rev+1,state};
  }
  fail('다른 편집이 동시에 진행 중입니다. 다시 시도하세요.',409);
}

export const fields = {
  list_repositories: [],
  create_repository:['name','description?'],
  repository:['repo'],
  invite_member:['repo','email','role'],
  accept_invite:['invite'],
  set_member_role:['repo','email','role'],
  list_documents:['repo','branch?'],
  create_document:['repo','branch?','path','text?'],
  read_document:['repo','branch?','path'],
  edit_document:['repo','branch?','path','expected_rev:number','text'],
  checkpoint:['repo','branch?','path','message'],
  list_history:['repo','branch?','path'],
  compare_commits:['repo','base','target'],
  restore_commit:['repo','branch?','path','commit','expected_rev:number'],
  create_branch:['repo','name','parent?'],
  create_ticket:['repo','kind?','title','body?','source?','target?','path?'],
  read_ticket:['repo','ticket'],
  comment:['repo','ticket','body'],
  set_ticket_status:['repo','ticket','status'],
  merge_proposal:['repo','ticket'],
  create_release:['repo','tag','commit','notes?'],
  star_repository:['repo','star?:boolean'],
};

import {test} from 'node:test';
import assert from 'node:assert/strict';
import {database} from './database.mjs';
import worker, {openSharedDocument as document, encodeUpdate as encode} from '../dist/server/index.js';

const base='https://hub.test';
const owner={id:'owner',email:'owner@login.test'}, editor={id:'editor',email:'editor@login.test'}, viewer={id:'viewer',email:'viewer@login.test'};
const req=(path,user,body,origin=base)=>new Request(base+path,{method:body?'POST':'GET',headers:{'content-type':'application/json',origin,
  ...(user?{'oai-authenticated-user-id':user.id,'oai-authenticated-user-email':user.email}:{})},body:body&&JSON.stringify(body)});

test('two invited identities sync changes, and revisions and proposal merges stay authorized',async()=>{
  const env={DB:database()};
  const send=(user,action,args,origin)=>worker.fetch(req('/api/action',user,{action,args},origin),env);
  const call=async(user,action,args={})=>{const r=await send(user,action,args);const b=await r.json();assert.equal(r.status,200,JSON.stringify(b));return b.result;};
  const {id:repo}=await call(owner,'create_repository',{name:'문서 팀'});
  assert.deepEqual(await call(editor,'list_repositories'),[]);
  assert.equal((await send(editor,'repository',{repo})).status,403);
  assert.equal((await send(null,'list_repositories',{})).status,401);
  assert.equal((await send(owner,'repository',{repo},'https://foreign.test')).status,403);
  for(const [actor,role] of [[editor,'write'],[viewer,'read']]){
    const invitation=await call(owner,'invite_member',{repo,email:actor.email,role});
    assert.equal((await send({...actor,email:'wrong@login.test'},'accept_invite',{invite:invitation.invite})).status,404);
    assert.equal((await call(actor,'accept_invite',{invite:invitation.invite})).repo,repo);
  }
  assert.equal((await send(editor,'invite_member',{repo,email:'third@login.test',role:'read'})).status,403);
  const scope={repo,branch:'main',path:'manual.md'};
  await call(owner,'create_document',{...scope,text:'Body'});
  const seed=await call(owner,'read_document',scope);
  assert.equal((await send(viewer,'edit_document',{...scope,text:'bad',expected_rev:0})).status,403);
  function edit(action){const d=document(seed.state);let delta;d.on('update',u=>delta=u);action(d.getText('body'));return encode(delta);}
  await Promise.all([
    call(owner,'patch_document',{...scope,update:edit(t=>t.insert(0,'Lead '))}),
    call(editor,'patch_document',{...scope,update:edit(t=>t.insert(4,' tail'))})
  ]);
  const fresh=await call(viewer,'read_document',scope);
  assert.equal(fresh.text,'Lead Body tail');assert.equal(fresh.rev,2);
  assert.equal((await send(editor,'edit_document',{...scope,text:'stale',expected_rev:0})).status,409);
  const before=await call(owner,'checkpoint',{...scope,message:'Shared revision'});
  await call(editor,'create_branch',{repo,name:'review',parent:'main'});
  const branch={...scope,branch:'review'};
  await call(editor,'edit_document',{...branch,text:'Lead Body tail reviewed',expected_rev:0});
  const proposal=await call(editor,'create_ticket',{repo,kind:'proposal',title:'Review',source:'review',target:'main',path:scope.path});
  await call(editor,'comment',{repo,ticket:proposal.id,body:'Please review.'});
  assert.equal((await call(viewer,'read_ticket',{repo,ticket:proposal.id})).comments.length,1);
  assert.equal((await send(editor,'merge_proposal',{repo,ticket:proposal.id})).status,403);
  const merged=await call(owner,'merge_proposal',{repo,ticket:proposal.id});
  assert.equal((await call(owner,'compare_commits',{repo,base:before.commit,target:merged.commit})).target.text,'Lead Body tail reviewed');
  await call(owner,'create_release',{repo,tag:'v1.0.0',commit:merged.commit});
  await call(editor,'star_repository',{repo});
  assert.equal((await call(owner,'repository',{repo})).releases[0].tag,'v1.0.0');
  await call(owner,'set_member_role',{repo,email:editor.email,role:'remove'});
  assert.equal((await send(editor,'read_document',scope)).status,403);
});

test('stateless MCP discovers tools and enforces repository identity',async()=>{
 const env={DB:database()};
 const rpc=async(user,method,params={})=>(await (await worker.fetch(req('/mcp',user,{jsonrpc:'2.0',id:1,method,params}),env)).json()).result;
 const list=await rpc(owner,'tools/list');
 assert.ok(list.tools.find(t=>t.name==='invite_member'));
 assert.ok(list.tools.find(t=>t.name==='merge_proposal'));
 assert.equal((await rpc(null,'tools/call',{name:'list_repositories'})).isError,true);
 const created=await rpc(owner,'tools/call',{name:'create_repository',arguments:{name:'MCP only'}});
 assert.ok(JSON.parse(created.content[0].text).id);
 assert.deepEqual(JSON.parse((await rpc(editor,'tools/call',{name:'list_repositories'})).content[0].text),[]);
});

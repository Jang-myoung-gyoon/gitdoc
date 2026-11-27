import assets from 'hub-assets';
import { actions, fields } from './core.js';

const reads=new Set(['list_repositories','repository','list_documents','read_document','list_history','compare_commits','read_ticket']);
const titles={
  list_repositories:'List GitDoc repositories',repository:'Read repository, branches, members, proposals, and releases',
  invite_member:'Create an email-bound repository invitation link',accept_invite:'Accept a repository invitation',
  edit_document:'Replace text at the expected live document revision',checkpoint:'Save a document revision',
  create_ticket:'Open an issue or branch document proposal',merge_proposal:'Merge a document proposal',
};
function tools() {
  return Object.entries(fields).map(([name,keys])=>{
    const properties={},required=[];
    for(const key of keys){
      const [raw,type='string']=key.split(':');
      const clean=raw.replace(/\?$/,'');
      properties[clean]={type:type==='number'?'integer':type};
      if(!raw.endsWith('?')) required.push(clean);
    }
    return {name,description:titles[name]??name.replaceAll('_',' '),inputSchema:{type:'object',properties,required,additionalProperties:false},
      annotations:{readOnlyHint:reads.has(name),destructiveHint:!reads.has(name),openWorldHint:false}};
  });
}
const reply=(data,status=200)=>Response.json(data,{status,headers:{'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
async function payload(request) {
  if(!request.headers.get('content-type')?.includes('application/json')) throw Object.assign(Error('JSON 요청이 필요합니다.'),{status:415});
  const reader=request.body?.getReader();
  if(!reader) throw Object.assign(Error('요청 내용을 확인하세요.'),{status:400});
  let size=0,value='';
  const decoder=new TextDecoder();
  try {
    for(;;){
      const chunk=await reader.read();
      if(chunk.done) break;
      size+=chunk.value.length;
      if(size>1000000){await reader.cancel();throw Object.assign(Error('요청 크기 제한을 초과했습니다.'),{status:413});}
      value+=decoder.decode(chunk.value,{stream:true});
    }
    return JSON.parse(value+decoder.decode());
  } catch(error) {
    if(error.status)throw error;
    throw Object.assign(Error('JSON 요청을 읽을 수 없습니다.'),{status:400});
  }
}
const userFor=request=>{
  const id=request.headers.get('oai-authenticated-user-id');
  const email=request.headers.get('oai-authenticated-user-email')?.trim().toLowerCase();
  return id&&email?{id,email}:null;
};
async function invoke(env,user,name,args,mcp=false){
  if(!user) throw Object.assign(Error('ChatGPT 로그인 후 이용할 수 있습니다.'),{status:401});
  if(!(typeof name==='string' && Object.hasOwn(mcp?fields:actions,name))) throw Object.assign(Error('작업을 찾을 수 없습니다.'),{status:404});
  if(args===null || typeof args!=='object' || Array.isArray(args)) throw Object.assign(Error('작업 인수를 확인하세요.'),{status:400});
  const schema=fields[name]??['repo','branch?','path','update'];
  for(const field of schema){
    const [key,type='string']=field.split(':');
    const clean=key.replace(/\?$/,'');
    if(args[clean]===undefined && key.endsWith('?')) continue;
    if(typeof args[clean]!==type) throw Object.assign(Error(clean+' 인수를 확인하세요.'),{status:400});
  }
  return actions[name](env.DB,user,args);
}
async function mcp(request,env,user){
  const {id,method,params}=await payload(request);
  const rpc=result=>reply({jsonrpc:'2.0',id,result});
  if(method==='server/discover') return rpc({supportedVersions:['2026-07-28'],capabilities:{tools:{}}});
  if(method==='initialize') return rpc({protocolVersion:'2025-11-25',capabilities:{tools:{}},serverInfo:{name:'gitdoc-hub',version:'1.0.0'}});
  if(method==='notifications/initialized') return new Response(null,{status:202});
  if(method==='tools/list') return rpc({tools:tools()});
  if(method==='tools/call'){
    try{
      const result=await invoke(env,user,params?.name,params?.arguments??{},true);
      return rpc({content:[{type:'text',text:JSON.stringify(result)}]});
    }catch(error){
      if(!error.status) console.error(error.message);
      return rpc({isError:true,content:[{type:'text',text:error.status?error.message:'요청을 처리하지 못했습니다.'}]});
    }
  }
  return reply({jsonrpc:'2.0',id,error:{code:-32601,message:'Method not found'}});
}
export default {async fetch(request,env){
  const url=new URL(request.url),user=userFor(request);
  try{
    if(url.pathname==='/mcp' && request.method==='POST') return await mcp(request,env,user);
    if(url.pathname==='/api/session' && request.method==='GET') return reply({email:user?.email??null});
    if(url.pathname==='/api/action' && request.method==='POST'){
      if(request.headers.get('sec-fetch-site')==='cross-site' || (request.headers.get('origin') && request.headers.get('origin')!==url.origin)) return reply({error:'허용되지 않은 출처입니다.'},403);
      const body=await payload(request);
      return reply({result:await invoke(env,user,body.action,body.args??{})});
    }
    if(request.method!=='GET') return new Response(null,{status:405});
    const file={'/':['html','text/html'],'/hub.js':['js','text/javascript'],'/hub.css':['style','text/css']}[url.pathname];
    if(!file) return new Response(null,{status:404});
    return new Response(assets[file[0]],{headers:{'Content-Type':file[1]+'; charset=utf-8','Content-Security-Policy':"default-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'",'X-Content-Type-Options':'nosniff','Cache-Control':'no-cache'}});
  }catch(error){
    if(!error.status) console.error(error.message);
    return reply({error:error.status?error.message:'요청을 처리하지 못했습니다.'},error.status??500);
  }
}};

export { document as openSharedDocument, encode as encodeUpdate } from './crdt.js';

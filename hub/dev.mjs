import {createServer} from 'node:http';
import {mkdirSync} from 'node:fs';
import worker from './dist/server/index.js';
import {database} from './test/database.mjs';

mkdirSync('.local',{recursive:true});
const DB=database('.local/hub.sqlite');
const port=Number(process.env.GITDOC_HUB_PORT??4176);
createServer(async (incoming,out)=>{
  try{
    const headers=new Headers(incoming.headers);
    // The local loopback preview uses a fixture identity. Deployed requests use Sites identity.
    headers.set('oai-authenticated-user-id','local-owner');
    headers.set('oai-authenticated-user-email','owner@example.test');
    const body=incoming.method==='GET'||incoming.method==='HEAD'?undefined:incoming;
    const response=await worker.fetch(new Request('http://127.0.0.1:'+port+incoming.url,{method:incoming.method,headers,body,duplex:'half'}),{DB});
    out.writeHead(response.status,Object.fromEntries(response.headers));
    out.end(Buffer.from(await response.arrayBuffer()));
  }catch(error){out.writeHead(500);out.end(error.message);}
}).listen(port,'127.0.0.1',()=>console.log('Local http://127.0.0.1:'+port));

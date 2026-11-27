import { mkdir, copyFile, readFile, writeFile } from 'node:fs/promises';
import { build } from 'esbuild';

await mkdir('dist/server',{recursive:true});
await mkdir('dist/.openai/drizzle/meta',{recursive:true});
for (const name of ['0000_hub.sql','meta/_journal.json']) {
  await copyFile('drizzle/'+name,'dist/.openai/drizzle/'+name);
}
await copyFile('.openai/hosting.json','dist/.openai/hosting.json');
const browser=await build({entryPoints:['src/client.js'],bundle:true,write:false,minify:true});
const files={html:await readFile('src/index.html','utf8'),style:await readFile('src/style.css','utf8'),js:browser.outputFiles[0].text};
await build({entryPoints:['src/worker.js'],bundle:true,format:'esm',platform:'browser',outfile:'dist/server/index.js',
  plugins:[{name:'inline-assets',setup(b){b.onResolve({filter:/^hub-assets$/},()=>({path:'assets',namespace:'hub'}));b.onLoad({filter:/.*/,namespace:'hub'},()=>({contents:JSON.stringify(files),loader:'json'}));}}]});
const {default: worker}=await import('./dist/server/index.js');
if(typeof worker.fetch!=='function') throw Error('Missing Worker fetch handler');

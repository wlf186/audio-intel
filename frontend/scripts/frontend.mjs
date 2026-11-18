import {build,context} from 'esbuild'
import {createServer,request} from 'node:http'
import {cp,mkdir,mkdtemp,readFile,rm,writeFile} from 'node:fs/promises'
import {basename,join,resolve} from 'node:path'
import {fileURLToPath} from 'node:url'

const root=fileURLToPath(new URL('../',import.meta.url))
const mode=process.argv[2]
const dev=mode==='dev'
if(dev)await mkdir(join(root,'../tmp'),{recursive:true})
const outdir=dev?await mkdtemp(join(root,'../tmp/frontend-dev-')):join(root,'dist')
const worker='src/lib/videoHash.worker.ts'
const app='src/main.tsx'
const options={absWorkingDir:root,bundle:true,outdir,format:'esm',platform:'browser',target:'es2022',jsx:'automatic',metafile:true,minify:!dev,entryNames:dev?'assets/[name]':'assets/[name]-[hash]',define:{'process.env.NODE_ENV':JSON.stringify(dev?'development':'production')}}
const asset=path=>'/assets/'+basename(path)
async function shell(js,css){
 await mkdir(outdir,{recursive:true})
 await cp(join(root,'public'),outdir,{recursive:true})
 const template=await readFile(join(root,'index.html'),'utf8')
 const reload=dev?"<script>new EventSource('/esbuild').addEventListener('change',()=>location.reload())</script>":''
 await writeFile(join(outdir,'index.html'),template.replace('/src/main.tsx',js).replace('</head>',`<link rel="stylesheet" href="${css}"></head>`).replace('</body>',reload+'</body>'))
}
if(mode==='build'){
 await rm(outdir,{recursive:true,force:true})
 const compiled=await build({...options,entryPoints:[worker]})
 const workerPath=Object.keys(compiled.metafile.outputs)[0]
 const bundle=await build({...options,entryPoints:[app],define:{...options.define,VIDEO_HASH_WORKER_URL:JSON.stringify(asset(workerPath))}})
 const [js,entry]=Object.entries(bundle.metafile.outputs).find(([,v])=>v.entryPoint===app)
 await shell(asset(js),asset(entry.cssBundle))
}else if(dev||mode==='preview'){
 const argument=(name,fallback)=>{const idx=process.argv.indexOf(name);return idx<0?fallback:process.argv[idx+1]}
 const host=argument('--host','127.0.0.1'),port=Number(argument('--port',dev?5173:4173))
 if(dev)await shell('/assets/main.js','/assets/main.css')
 const ctx=await context(dev?{...options,entryPoints:[app,worker],define:{...options.define,VIDEO_HASH_WORKER_URL:'"/assets/videoHash.worker.js"'}}:{absWorkingDir:root,entryPoints:[],outdir:resolve(outdir)})
 if(dev)await ctx.watch()
 const files=await ctx.serve({host:'127.0.0.1',port:0,servedir:outdir})
 const server=createServer((req,res)=>{
  if(!['localhost','127.0.0.1','[::1]',host].some(h=>req.headers.host===h||req.headers.host===h+':'+port)){res.writeHead(403).end();return}
  const api=/^\/(api|v1)(\/|\?|$)/.test(req.url)
  const upstream=request({hostname:'127.0.0.1',port:api?20810:files.port,path:req.url,method:req.method,headers:api?req.headers:{...req.headers,host:'127.0.0.1:'+files.port}},reply=>{res.writeHead(reply.statusCode,reply.headers);reply.pipe(res)})
  upstream.on('error',()=>{if(!res.headersSent)res.writeHead(502);res.end()})
  res.on('close',()=>upstream.destroy());req.pipe(upstream)
 })
 server.listen(port,host,()=>console.log(`Frontend listening on http://${host}:${port}`))
 const stop=async()=>{server.close();await ctx.dispose();if(dev)await rm(outdir,{recursive:true,force:true});process.exit()}
 process.once('SIGINT',stop);process.once('SIGTERM',stop)
}else throw new Error('Expected build, dev or preview')

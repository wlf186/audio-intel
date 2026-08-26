import {appendFileSync,copyFileSync,existsSync,mkdirSync,readFileSync} from 'node:fs'
import {createRequire} from 'node:module'
import {dirname,join} from 'node:path'
import {fileURLToPath} from 'node:url'

const require=createRequire(import.meta.url)
const source=dirname(require.resolve('swagger-ui-dist/swagger-ui.css'))
const root=join(dirname(fileURLToPath(import.meta.url)),'..')
const target=join(root,'dist','docs-assets')
mkdirSync(target,{recursive:true})
for(const name of ['swagger-ui.css','swagger-ui.css.map','swagger-ui-bundle.js','swagger-ui-bundle.js.map']){
 const path=join(source,name)
 if(existsSync(path))copyFileSync(path,join(target,name))
}
appendFileSync(join(target,'swagger-ui.css'),`\n${readFileSync(join(root,'docs','swagger-overrides.css'),'utf8')}\n`)

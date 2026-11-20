// Bundle the real Reader; only native IPC and its custom-scheme transport differ.
import path from 'node:path'
import fs from 'node:fs/promises'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const out = path.resolve(process.env.LINKREAD_ESCAPE_OUT || path.join(repo, '..', 'reader-escape-verification'))
const site = path.join(out, 'site')
const require = createRequire(path.join(repo, 'desktop/package.json'))
const { build } = require('esbuild')
const data = JSON.parse(await fs.readFile(path.join(site, 'fixture.json'), 'utf8'))
const native = `
const fixture = window.escapeFixture = {calls:[],back:0,navigation:0,events:[],holdSaves:false,pendingSaves:[],
  releaseSaves(){this.holdSaves=false;for(const pending of this.pendingSaves.splice(0))pending()},
};
export class Channel { constructor(callback){this.onmessage=callback} }
export async function invoke(command,args={}){
 fixture.calls.push({command,args});
 if(command==='prepare_article')return {revision:'escape-fixture',readingKey:window.escapeData.readingKey};
 if(command==='load_reading_position')return null;
 if(command==='save_reading_position'){
   const save=()=>{fixture.events.push('saved');fixture.lastPosition=args.position};
   if(fixture.holdSaves)await new Promise(resolve=>fixture.pendingSaves.push(()=>{save();resolve()}));else save();
   return null;
 }
 if(['open_article','open_video','open_external','export_article','reveal_article'].includes(command))return null;
 throw Error('Unexpected native command: '+command);
}`
await build({
  stdin: { contents: `
import {createRoot} from 'react-dom/client';
import {Reader} from './Reader';
import './styles.css';
const fixture=window.escapeFixture;
createRoot(document.getElementById('root')).render(<Reader article={window.escapeData.article}
 onBack={()=>{fixture.events.push('back');fixture.back++}}
 onNavigationStart={()=>{const version=++fixture.navigation;return ()=>version===fixture.navigation}}
 onRegenerate={async()=>{throw Error('Generation is not part of this check')}} onStateChange={async()=>{}}/>);
`, resolveDir: path.join(repo, 'desktop/src'), loader: 'tsx', sourcefile: 'reader-escape-harness.tsx' },
  bundle: true, outfile: path.join(site, 'fixture.js'), jsx: 'automatic', format: 'iife', target: 'es2022',
  define: { 'process.env.NODE_ENV': '"production"' },
  plugins: [{ name: 'native-transport-only', setup(build) {
    build.onResolve({ filter: /^@tauri-apps\/api\/core$/ }, () => ({ path: 'native', namespace: 'escape-qa' }))
    build.onLoad({ filter: /.*/, namespace: 'escape-qa' }, () => ({ contents: native, loader: 'js' }))
    build.onLoad({ filter: /desktop\/src\/Reader\.tsx$/ }, async args => ({
      contents: (await fs.readFile(args.path, 'utf8')).replace('linkread-reader://localhost/', 'http://escape.linkread.test/native/'), loader: 'tsx',
    }))
  }}],
})
await fs.writeFile(path.join(site, 'index.html'), `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Reader Escape regression</title><link rel="stylesheet" href="fixture.css"><style>html,body,#root{height:100%;margin:0}#root{display:flex}</style></head><body><div id="root"></div><script>window.escapeData=${JSON.stringify(data).replaceAll('<', '\\u003c')}</script><script src="fixture.js"></script></body></html>`)
console.log(JSON.stringify({ site, actualSource: 'desktop/src/Reader.tsx', modelCalls: 0 }))

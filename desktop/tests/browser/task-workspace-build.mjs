// Bundle the real App. Only native IPC and the reader transport origin are replaced.
import path from 'node:path'
import fs from 'node:fs/promises'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const scripts = path.dirname(fileURLToPath(import.meta.url))
const repo = path.resolve(process.env.LINKREAD_REPO || path.join(scripts, '../../..'))
const out = path.resolve(process.env.LINKREAD_TASK_WORKSPACE_OUT || path.join(repo, '..', 'task-workspace-verification'))
const require = createRequire(path.join(repo, 'desktop/package.json'))
const { build } = require('esbuild')
await fs.mkdir(path.join(out, 'site'), { recursive: true })
await build({
  entryPoints: [path.join(repo, 'desktop/src/main.tsx')], bundle: true,
  outfile: path.join(out, 'site/fixture.js'), jsx: 'automatic', format: 'iife', target: 'es2022',
  define: { 'process.env.NODE_ENV': '"production"' },
  plugins: [{ name: 'task-workspace-native-transport-only', setup(builder) {
    builder.onResolve({ filter: /^@tauri-apps\/api\/(core|webview|window)$/ },
      () => ({ path: path.join(scripts, 'task-workspace-native.ts') }))
    builder.onLoad({ filter: /desktop\/src\/Reader\.tsx$/ }, async args => ({
      contents: (await fs.readFile(args.path, 'utf8')).replace(
        'linkread-reader://localhost/', 'http://task-workspace.linkread.test/native/'), loader: 'tsx',
    }))
  } }],
})
await fs.writeFile(path.join(out, 'site/index.html'), '<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LinkRead task workspace verification</title><link rel="stylesheet" href="fixture.css"></head><body><div id="root"></div><script src="fixture.js"></script></body></html>')
console.log(JSON.stringify({ site: path.join(out, 'site'), actualSource: path.join(repo, 'desktop/src/main.tsx'), networkCalls: 0, modelCalls: 0 }))

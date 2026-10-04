import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { once } from 'node:events'
import { readFile } from 'node:fs/promises'
import { createServer as httpServer } from 'node:http'
import { promisify } from 'node:util'
import { fileURLToPath } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'
import { test } from 'node:test'
import { createServer as viteServer, resolveConfig } from 'vite'
import { waitForBackend } from './wait-for-backend.mjs'

const frontend = fileURLToPath(new URL('../', import.meta.url))
const quiet = { log: () => {}, timeoutMs: 2000, intervalMs: 20 }
async function fixture(handler) {
    const server = httpServer(handler)
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    return { server, url: `http://127.0.0.1:${server.address().port}/api/auth/oidc/providers` }
}
async function close(server) {
    const finished = once(server, 'close')
    server.close(); server.closeAllConnections()
    await finished
}
function ready(res) { res.writeHead(200, { 'content-type': 'application/json' }); res.end('[]') }

test('waits through non-ready responses, not just an open port, without logging provider data', async () => {
    let requests = 0
    const responses = [[503,'application/json','{"detail":"starting"}'], [401,'application/json','{}'], [200,'text/html','<html>not backend</html>'], [200,'application/json','{}'], [200,'application/json','broken-json'], [302,'application/json','[]'], [200,'application/json','[{"name":"not-for-logs"}]']]
    const { server, url } = await fixture((_req, res) => { const [status, type, body] = responses[Math.min(requests++, responses.length - 1)]; res.writeHead(status, {'content-type':type}); res.end(body) })
    const logs = []
    try {
        await waitForBackend({ ...quiet, url, log: text => logs.push(text) })
        assert.equal(requests, responses.length)
        assert.equal(logs.length, 2)
        assert.match(logs[1], /ready/)
        assert.doesNotMatch(logs.join(''), /not-for-logs/)
    } finally { await close(server) }
})

test('retries a refused connection until a delayed backend actually listens', async () => {
    const { server, url } = await fixture((_req,res) => ready(res))
    const port = server.address().port
    await close(server)
    let listening = false
    const launch = delay(120).then(() => new Promise(resolve => server.listen(port,'127.0.0.1', () => {listening = true; resolve()})))
    try {
        await waitForBackend({ ...quiet, url })
        assert.equal(listening, true)
    } finally { await launch; await close(server) }
})

for (const mode of ['503', 'hung-response', 'hung-body']) {
    test(`bounded timeout when backend stays ${mode}`, async () => {
        const { server, url } = await fixture((_req,res) => {
            if (mode === '503') { res.writeHead(503); res.end('Unavailable') }
            if (mode === 'hung-body') { res.writeHead(200, {'content-type':'application/json'}); res.write('[') }
        })
        const started = Date.now()
        try {
            await assert.rejects(waitForBackend({ ...quiet, url, timeoutMs: 100 }), /Frontend was not started/)
            assert.ok(Date.now() - started < 2000)
        } finally { await close(server) }
    })
}

test('external cancellation stops waiting without reporting readiness', async () => {
    const { server, url } = await fixture((_req,res) => { res.writeHead(503); res.end() })
    const controller = new AbortController()
    const stop = setTimeout(() => controller.abort(), 50)
    const logs = []
    try {
        await assert.rejects(waitForBackend({ ...quiet, url, signal: controller.signal, log: text => logs.push(text) }), {name:'AbortError'})
        assert.equal(logs.length,1)
    } finally { clearTimeout(stop); await close(server) }
})

test('invalid CLI timeout fails with a nonzero exit before frontend startup', async () => {
    const execute = promisify(execFile)
    await assert.rejects(execute(process.execPath, ['scripts/wait-for-backend.mjs'], {cwd:frontend,env:{...process.env,DEV_BACKEND_WAIT_TIMEOUT_SECONDS:'0'}}), error => {
        assert.equal(error.code,1)
        assert.match(error.stderr,/must be greater than 0/)
        assert.equal(error.stdout,'')
        return true
    })
})

test('real Vite proxy starts only after delayed API readiness and keeps browser Host', {timeout:15000}, async () => {
    let readyNow = false
    const {server,url} = await fixture((req,res) => {
        if (!readyNow) {res.writeHead(503);res.end();return}
        if (req.url === '/api/auth/oidc/providers') {ready(res);return}
        res.writeHead(401,{'content-type':'application/json'});res.end(JSON.stringify({host:req.headers.host}))
    })
    const timer = setTimeout(() => {readyNow=true},100)
    let vite
    try {
        await waitForBackend({...quiet,url})
        const config = await resolveConfig({root:frontend,configFile:fileURLToPath(new URL('../vite.config.ts',import.meta.url)),logLevel:'silent'},'serve')
        assert.equal(config.server.proxy['/api'].target,'http://localhost:8000')
        assert.equal(config.server.proxy['/api'].changeOrigin,false)
        vite = await viteServer({root:frontend,configFile:false,logLevel:'silent',appType:'custom',server:{host:'127.0.0.1',port:0,proxy:{'/api':{target:new URL(url).origin,changeOrigin:false}}},optimizeDeps:{noDiscovery:true,include:[]}})
        const proxyErrors = []; vite.httpServer.on('error',error=>proxyErrors.push(error.message))
        await vite.listen()
        const port = vite.httpServer.address().port
        const response = await fetch(`http://127.0.0.1:${port}/api/auth/me`)
        assert.equal(response.status,401)
        assert.equal((await response.json()).host,`127.0.0.1:${port}`)
        assert.deepEqual(proxyErrors,[])
        const scripts=JSON.parse(await readFile(new URL('../package.json',import.meta.url),'utf8')).scripts
        assert.match(scripts.dev,/wait-for-backend\.mjs && vite/)
        assert.equal(scripts['dev:ui'],'vite')
        const make=await readFile(new URL('../../Makefile',import.meta.url),'utf8')
        assert.match(make,/cd frontend && \$\(NPM\) run dev/)
    } finally {clearTimeout(timer);await vite?.close();await close(server)}
})

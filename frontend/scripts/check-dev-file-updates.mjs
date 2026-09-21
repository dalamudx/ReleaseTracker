import assert from 'node:assert/strict'
import {mkdtemp, readFile, rm, writeFile} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {setTimeout as delay} from 'node:timers/promises'
import {test} from 'node:test'
import {createServer, resolveConfig} from 'vite'

async function until(check) {
  const deadline = Date.now() + 5000
  while (Date.now() < deadline) {
    if (await check()) return
    await delay(20)
  }
  throw new Error('Timed out waiting for a stable Vite module update')
}

test('development watcher ignores intermediate empty files and serves the completed module', {timeout: 15000}, async () => {
  const frontend = fileURLToPath(new URL('../', import.meta.url))
  const config = await resolveConfig({root: frontend, configFile: path.join(frontend, 'vite.config.ts'), logLevel: 'silent'}, 'serve')
  assert.equal(typeof config.server.watch.awaitWriteFinish, 'object')
  const root = await mkdtemp(path.join(tmpdir(), 'rt-vite-file-update-'))
  const filename = path.join(root, 'probe.js')
  let server
  try {
    await writeFile(filename, 'export const value = "initial";\n')
    server = await createServer({root, configFile: false, logLevel: 'silent', appType: 'custom', server: {middlewareMode: true, watch: config.server.watch}, optimizeDeps: {noDiscovery: true, include: []}})
    await until(() => Object.values(server.watcher.getWatched()).some(files => files.includes('probe.js')))
    assert.match((await server.transformRequest('/probe.js')).code, /initial/)
    const observed = []
    server.watcher.on('change', changed => {
      if (path.resolve(changed) === filename) observed.push(readFile(filename, 'utf8'))
    })
    for (const version of ['updated', 'restored']) {
      await writeFile(filename, '')
      await delay(60)
      // Requests during the write must keep the last valid module, not cache an empty one.
      assert.match((await server.transformRequest('/probe.js')).code, /export const value/)
      await writeFile(filename, `export const value = "${version}";\n`)
      await until(async () => (await server.transformRequest('/probe.js')).code.includes(version))
    }
    assert.equal(observed.length, 2)
    for (const content of await Promise.all(observed)) assert.match(content, /export const value/)
  } finally {
    await server?.close()
    await rm(root, {recursive: true, force: true})
  }
})

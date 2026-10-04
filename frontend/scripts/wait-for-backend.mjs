import { setTimeout as delay } from 'node:timers/promises'
import { pathToFileURL } from 'node:url'
import { performance } from 'node:perf_hooks'

const DEFAULT_URL = 'http://127.0.0.1:8000/api/auth/oidc/providers'

export async function waitForBackend({ url = DEFAULT_URL, timeoutMs = 60_000, intervalMs = 250, signal, log = console.log } = {}) {
    const endpoint = new URL(url)
    if (!['http:', 'https:'].includes(endpoint.protocol) || endpoint.username || endpoint.password) {
        throw new Error('Backend readiness URL must be HTTP(S) without credentials')
    }
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || !Number.isFinite(intervalMs) || intervalMs <= 0) {
        throw new Error('Backend readiness timeout and interval must be positive numbers')
    }
    log(`[dev] Waiting for backend initialization at ${endpoint.origin} (up to ${timeoutMs / 1000}s)…`)
    const deadline = performance.now() + timeoutMs
    while (performance.now() < deadline) {
        signal?.throwIfAborted()
        const remaining = Math.max(1, Math.ceil(deadline - performance.now()))
        const attemptSignal = AbortSignal.timeout(Math.min(1000, remaining))
        const requestSignal = signal ? AbortSignal.any([signal, attemptSignal]) : attemptSignal
        let response
        try {
            // This existing endpoint is public and reads initialized storage.
            // TCP listening, a reloader banner, 401, redirects or SPA HTML are
            // not readiness. Never log the returned provider/configuration data.
            response = await fetch(endpoint, { redirect: 'manual', signal: requestSignal, headers: { Accept: 'application/json' } })
            if (response.status === 200 && response.headers.get('content-type')?.includes('application/json') && Array.isArray(await response.json())) {
                signal?.throwIfAborted()
                log('[dev] Backend API ready. Starting frontend.')
                return
            }
        } catch {
            signal?.throwIfAborted()
        } finally {
            if (response?.body && !response.bodyUsed) await response.body.cancel().catch(() => {})
        }
        const wait = Math.min(intervalMs, deadline - performance.now())
        if (wait > 0) await delay(wait, undefined, { signal })
    }
    throw new Error(`Backend did not become ready within ${timeoutMs / 1000}s. Check backend startup logs, migrations and port ${endpoint.port || 'default'}. Frontend was not started.`)
}

async function main() {
    const seconds = Number(process.env.DEV_BACKEND_WAIT_TIMEOUT_SECONDS ?? '60')
    if (!Number.isFinite(seconds) || seconds <= 0 || seconds > 600) {
        throw new Error('DEV_BACKEND_WAIT_TIMEOUT_SECONDS must be greater than 0 and at most 600')
    }
    await waitForBackend({ timeoutMs: seconds * 1000 })
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
    main().catch(error => { console.error(`[dev] ${error.message}`); process.exitCode = 1 })
}

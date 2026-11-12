// Installs a fetch interceptor for `/api/*` backed by the in-memory mock server.
// Scenario: `?mock=empty` (fresh install) or `?mock=offline` (Yahoo + Ollama down); persisted in sessionStorage.
import { createServer, type Scenario } from './server'

function scenario(): Scenario {
  const fromUrl = new URLSearchParams(window.location.search).get('mock')
  if (fromUrl === 'empty' || fromUrl === 'offline' || fromUrl === 'default') {
    try { sessionStorage.setItem('vermoegen.mock', fromUrl) } catch { /* ignore */ }
    return fromUrl
  }
  try {
    const s = sessionStorage.getItem('vermoegen.mock')
    if (s === 'empty' || s === 'offline') return s
  } catch { /* ignore */ }
  return 'default'
}

export function installMock(): Scenario {
  const sc = scenario()
  const server = createServer(sc)
  const realFetch = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const url = new URL(raw, window.location.origin)
    if (!url.pathname.startsWith('/api/')) return realFetch(input, init)
    const method = (init?.method ?? 'GET').toUpperCase()
    let body: Record<string, unknown> | undefined
    let form: FormData | null = null
    if (init?.body instanceof FormData) form = init.body
    else if (typeof init?.body === 'string' && init.body) { try { body = JSON.parse(init.body) } catch { body = undefined } }
    await new Promise((r) => setTimeout(r, method === 'GET' ? 60 + Math.random() * 120 : 220))
    const { status, data } = await server.handle(method, url, body, form)
    if (data instanceof Blob) return new Response(data, { status, headers: { 'Content-Type': data.type || 'application/octet-stream' } })
    if (data === undefined) return new Response(null, { status: 204 })
    return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } })
  }
  console.info(`[mock] API-Mock aktiv (Szenario: ${sc})`)
  return sc
}

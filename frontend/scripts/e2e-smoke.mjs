// End-to-end smoke test against a RUNNING app (real backend + built frontend, default http://127.0.0.1:8000).
//   node scripts/e2e-smoke.mjs                      all routes, desktop + phone, light
//   node scripts/e2e-smoke.mjs --dark               additionally in dark mode
//   node scripts/e2e-smoke.mjs --shots .screens/e2e save a full-page screenshot per route/viewport/theme
//   node scripts/e2e-smoke.mjs depot kalender       only routes whose name contains one of the words
//   BASE_URL=http://127.0.0.1:8080 node scripts/e2e-smoke.mjs
// Every route is opened as a deep link (fresh page load). The run fails on: console errors, uncaught
// exceptions, failed requests, HTTP >= 400 on /api, "NaN"/"undefined"/"Invalid Date" in the visible text,
// an error boundary/"Seite nicht gefunden", or horizontal page overflow.
// Playwright + Chromium are expected to be installed globally (not a project dependency).
import { mkdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join, resolve } from 'node:path'

const require = createRequire(import.meta.url)
let chromium
try { ({ chromium } = require('playwright')) } catch {
  const { execSync } = await import('node:child_process')
  ;({ chromium } = require(join(execSync('npm root -g').toString().trim(), 'playwright')))
}

const BASE = (process.env.BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const args = process.argv.slice(2)
const dark = args.includes('--dark')
const shotsIdx = args.indexOf('--shots')
const shots = shotsIdx >= 0 ? resolve(args[shotsIdx + 1]) : null
const only = args.filter((a, i) => !a.startsWith('--') && i !== shotsIdx + 1)
if (shots) mkdirSync(shots, { recursive: true })

async function getJson(path) {
  const res = await fetch(BASE + '/api' + path, { signal: AbortSignal.timeout(15000) })
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`)
  return res.json()
}

let accounts, instruments, documents
try {
  const health = await getJson('/health')
  if (health.status !== 'ok') throw new Error(`/api/health meldet ${JSON.stringify(health)}`)
  ;[accounts, instruments, documents] = await Promise.all([getJson('/accounts'), getJson('/instruments'), getJson('/documents')])
} catch (e) {
  console.error(`App unter ${BASE} nicht erreichbar (${e.message}). Zuerst "make start" ausführen.`)
  process.exit(2)
}

const routes = [
  ['uebersicht', '/'], ['konten', '/konten'], ['depot', '/depot'], ['durchschau', '/durchschau'], ['dividenden', '/dividenden'], ['steuern', '/steuern'],
  ['kalender', '/kalender'], ['transaktionen', '/transaktionen'], ['haushaltsbuch', '/haushaltsbuch'], ['import', '/import'],
  ['prognose', '/prognose'], ['watchlist', '/watchlist'], ['einstellungen', '/einstellungen'],
]
const cash = accounts.find((a) => a.type !== 'brokerage')
const depot = accounts.find((a) => a.type === 'brokerage')
const stock = instruments.find((i) => i.type === 'stock')
const etf = instruments.find((i) => i.type === 'etf')
if (cash) routes.push(['konto-giro', `/konten/${cash.id}`])
if (depot) routes.push(['konto-depot', `/konten/${depot.id}`])
if (stock) routes.push(['wertpapier-aktie', `/wertpapier/${stock.id}`])
if (etf) routes.push(['wertpapier-etf', `/wertpapier/${etf.id}`])
if (documents[0]) routes.push(['import-pruefen', `/import/${documents[0].id}`])

const VIEWPORTS = { desktop: { width: 1440, height: 900 }, phone: { width: 390, height: 844 } }
const themes = dark ? ['light', 'dark'] : ['light']
const selected = only.length ? routes.filter(([name]) => only.some((o) => name.includes(o))) : routes
const problems = []
const browser = await chromium.launch()

async function visit(name, path, vp, theme) {
  const tag = `${name} [${vp}/${theme}]`
  const ctx = await browser.newContext({ viewport: VIEWPORTS[vp], deviceScaleFactor: 1, colorScheme: theme, locale: 'de-DE', timezoneId: 'Europe/Berlin', reducedMotion: 'reduce', isMobile: vp === 'phone', hasTouch: vp === 'phone' })
  const page = await ctx.newPage()
  page.on('console', (m) => { if (m.type() === 'error') problems.push(`${tag} console: ${m.text()}`) })
  page.on('pageerror', (e) => problems.push(`${tag} exception: ${e.message}`))
  page.on('requestfailed', (r) => { if (!/ERR_ABORTED/.test(r.failure()?.errorText ?? '')) problems.push(`${tag} request failed: ${r.method()} ${r.url()} (${r.failure()?.errorText})`) })
  page.on('response', (r) => { if (r.status() >= 400 && r.url().includes('/api/')) problems.push(`${tag} HTTP ${r.status()}: ${r.request().method()} ${r.url().replace(BASE, '')}`) })
  try {
    await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 30000 })
    await page.waitForSelector('main h1', { timeout: 10000 })
    await page.waitForFunction(() => !document.querySelector('main .skeleton'), null, { timeout: 15000 }).catch(() => problems.push(`${tag} Skeleton verschwindet nicht`))
    await page.waitForTimeout(250)
    const text = await page.evaluate(() => document.querySelector('main')?.innerText ?? '')
    for (const bad of ['NaN', 'undefined', 'Invalid Date', '[object Object]', 'Infinity']) if (text.includes(bad)) problems.push(`${tag} Text enthält "${bad}"`)
    if (/Seite nicht gefunden|Etwas ist schiefgelaufen/.test(text)) problems.push(`${tag} Fehlerseite statt Inhalt`)
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    if (overflow > 1) problems.push(`${tag} horizontaler Überlauf (${overflow}px)`)
    if (shots) await page.screenshot({ path: join(shots, `${name}-${vp}-${theme}.png`), fullPage: true })
  } catch (e) {
    problems.push(`${tag} ${e.message.split('\n')[0]}`)
  }
  await ctx.close()
}

for (const [name, path] of selected) for (const vp of Object.keys(VIEWPORTS)) for (const theme of themes) await visit(name, path, vp, theme)
await browser.close()

const count = selected.length * Object.keys(VIEWPORTS).length * themes.length
if (problems.length) {
  console.error(`${problems.length} Problem(e) bei ${count} Seitenaufrufen:`)
  for (const p of problems) console.error('  - ' + p)
  process.exit(1)
}
console.log(`OK – ${count} Seitenaufrufe (${selected.length} Routen) ohne Konsolenfehler, fehlgeschlagene Anfragen oder Überlauf.${shots ? ' Screenshots: ' + shots : ''}`)
